"""Integration tests for call analysis: webhook -> queue -> engine -> guardrails -> actions.
A fake LLM stands in for Gemini, so every path (success, garbage, outage) is testable."""

import asyncio
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from redis.asyncio import Redis
from sqlalchemy import func, select

from app.config import get_settings
from app.db import SessionFactory
from app.domain.insights import CallInsight
from app.llm.gemini import LlmInvalidOutput, LlmResult, LlmTransient
from app.llm.resilience import CircuitBreaker, DailyQuota, RateLimiter
from app.models import CallAnalysis, DndEntry
from app.services.analysis import (
    AnalysisEngine,
    claim_analyses,
    process_analysis,
    reap_analyses,
)
from tests.test_calls import call_row, placed_call, send, webhook
from tests.test_outreach import attempts

IST = ZoneInfo("Asia/Kolkata")
PHONE = "+919812345678"
TRANSCRIPT = [
    {"speaker": "agent", "text": "Namaste Ravi ji, aapka Aadhaar OTP pending hai.", "start_ms": 0},
    {"speaker": "user", "text": "Abhi meeting mein hoon. Mera number 9812345678 hai, "
                                "shaam ko 6 baje ke baad call karo.", "start_ms": 3000},
]  # fmt: skip


class FakeLlm:
    """Returns queued results in order; an Exception in the queue is raised instead."""

    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.prompts = []

    async def analyze(self, prompt):
        self.prompts.append(prompt)
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return LlmResult(CallInsight.model_validate(out), "fake-model", 120, 400, 60)


def insight(**overrides) -> dict:
    return {
        "outcome": "callback_requested",
        "dropoff_reason": "busy_no_time",
        "sentiment": "neutral",
        "spoken_language": "hinglish",
        "callback_requested_at": None,
        "do_not_call": False,
        "summary": "Busy in a meeting.",
        "key_quote": "Abhi meeting mein hoon",
        "confidence": 0.9,
        **overrides,
    }


def engine(llm=None, breaker=None, limiter=None) -> AnalysisEngine:
    return AnalysisEngine(llm, limiter, None, breaker or CircuitBreaker())


async def answered_call(client, api_key, turns=TRANSCRIPT):
    """A placed call that was answered and whose transcript arrived -> analysis queued."""
    call = await placed_call(client, api_key, phone=PHONE)
    for type_, data in [
        ("call.answered", {}),
        ("call.completed", {"duration_seconds": 50}),
        ("call.transcript_ready", {"language": "hinglish", "turns": turns}),
    ]:
        r = await send(client, webhook(call, type_, data))
        assert r.status_code == 200, r.text
    return await call_row(call.id)


async def analysis_row() -> CallAnalysis:
    async with SessionFactory() as db:
        return await db.scalar(select(CallAnalysis))


async def claim_one(now=None) -> list:
    async with SessionFactory() as db:
        return await claim_analyses(db, now or datetime.now(UTC), 10)


def tomorrow_6pm_local(call) -> str:
    local = call.answered_at.astimezone(IST) + timedelta(days=1)
    return local.replace(hour=18, minute=0).strftime("%Y-%m-%dT%H:%M")


# ---- queueing -----------------------------------------------------------------------


async def test_transcript_queues_one_analysis(client, api_key):
    call = await answered_call(client, api_key)
    # The provider re-sends the transcript under a new event id: still one analysis
    await send(client, webhook(call, "call.transcript_ready", {"turns": TRANSCRIPT}))
    async with SessionFactory() as db:
        assert await db.scalar(select(func.count()).select_from(CallAnalysis)) == 1
    assert (await analysis_row()).status == "pending"


# ---- the happy path, and what leaves the building -----------------------------------


async def test_llm_callback_is_scheduled_and_pii_never_sent(client, api_key):
    call = await answered_call(client, api_key)
    llm = FakeLlm(insight(callback_requested_at=tomorrow_6pm_local(call)))
    [aid] = await claim_one()
    assert await process_analysis(aid, engine(llm)) == "llm"

    prompt = llm.prompts[0]
    assert "9812345678" not in prompt and "[PHONE]" in prompt
    assert "Ravi" not in prompt and "[NAME]" in prompt

    a = await analysis_row()
    assert (a.status, a.source, a.model, a.prompt_version) == (
        "done",
        "llm",
        "fake-model",
        "insight-v1",
    )
    assert a.redactions == {"PHONE": 1, "NAME": 1}
    assert a.actions[0].startswith("callback scheduled for")
    callback = (await attempts())[-1]
    assert (callback.channel, callback.status, callback.status_reason) == (
        "voice",
        "scheduled",
        "callback_requested",
    )
    assert callback.scheduled_for.astimezone(IST).hour == 18


# ---- do-not-call: either the model or the rules is enough ---------------------------


async def test_llm_do_not_call_adds_dnd(client, api_key):
    call = await answered_call(client, api_key)
    llm = FakeLlm(
        insight(
            outcome="not_interested",
            do_not_call=True,
            callback_requested_at=tomorrow_6pm_local(call),
        )
    )
    [aid] = await claim_one()
    await process_analysis(aid, engine(llm))

    a = await analysis_row()
    assert a.actions[0].startswith("added number to DND")
    assert "callback ignored: user asked not to be called" in a.guardrail_notes
    async with SessionFactory() as db:
        assert (await db.scalar(select(DndEntry))).reason == "requested_on_call"


async def test_rules_catch_a_stop_request_the_model_missed(client, api_key):
    turns = [{"speaker": "user", "text": "Please mujhe dobara call mat karna.", "start_ms": 0}]
    await answered_call(client, api_key, turns)
    llm = FakeLlm(insight(outcome="unclear", key_quote=""))  # the model says nothing about it
    [aid] = await claim_one()
    await process_analysis(aid, engine(llm))
    a = await analysis_row()
    assert "rules detected a do-not-call request the model missed" in a.guardrail_notes
    assert any(x.startswith("added number to DND") for x in a.actions)


async def test_invented_quote_lowers_confidence(client, api_key):
    await answered_call(client, api_key)
    llm = FakeLlm(insight(key_quote="Mujhe loan nahi chahiye"))  # never said
    [aid] = await claim_one()
    await process_analysis(aid, engine(llm))
    a = await analysis_row()
    assert a.result["confidence"] == 0.3
    assert "key_quote not found in transcript: lowered confidence" in a.guardrail_notes


async def test_callback_in_the_past_is_ignored(client, api_key):
    await answered_call(client, api_key)
    llm = FakeLlm(insight(callback_requested_at="2020-01-01T18:00"))
    [aid] = await claim_one()
    await process_analysis(aid, engine(llm))
    assert (
        "callback ignored: callback time is not after the call"
        in (await analysis_row()).guardrail_notes
    )


# ---- failure handling ---------------------------------------------------------------


async def test_garbage_twice_falls_back_to_rules(client, api_key):
    await answered_call(client, api_key)
    llm = FakeLlm(LlmInvalidOutput("bad json"), LlmInvalidOutput("bad json again"))
    [aid] = await claim_one()
    assert await process_analysis(aid, engine(llm)) == "rules"
    a = await analysis_row()
    assert a.source == "rules" and a.prompt_version == "rules-v1"
    assert a.result["outcome"] == "callback_requested"  # the rules still read "6 baje"
    assert len(a.guardrail_notes) >= 2


async def test_outage_retries_with_backoff_then_falls_back(client, api_key, monkeypatch):
    monkeypatch.setattr(get_settings(), "analysis_max_tries", 2)
    await answered_call(client, api_key)
    now = datetime.now(UTC)

    [aid] = await claim_one(now)
    assert await process_analysis(aid, engine(FakeLlm(LlmTransient("503"))), now) == "retry"
    a = await analysis_row()
    assert (a.status, a.tries) == ("pending", 1) and a.available_at > now

    [aid] = await claim_one(a.available_at)
    assert await process_analysis(aid, engine(FakeLlm(LlmTransient("503"))), now) == "rules"
    assert (await analysis_row()).status == "done"


async def test_open_breaker_skips_the_llm(client, api_key):
    await answered_call(client, api_key)
    breaker = CircuitBreaker(failure_threshold=1)
    breaker.record_failure()
    llm = FakeLlm()  # would raise IndexError if called
    [aid] = await claim_one()
    assert await process_analysis(aid, engine(llm, breaker)) == "rules"
    assert llm.prompts == []
    assert "circuit open" in (await analysis_row()).guardrail_notes[0]


async def test_rate_limited_is_deferred_not_failed(client, api_key):
    class Full:
        async def try_acquire(self):
            return 4.5

    await answered_call(client, api_key)
    now = datetime.now(UTC)
    [aid] = await claim_one(now)
    assert await process_analysis(aid, engine(FakeLlm(), limiter=Full()), now) == "rate_limited"
    a = await analysis_row()
    assert (a.status, a.tries) == ("pending", 0)  # waiting isn't a failure
    assert a.available_at == now + timedelta(seconds=4.5)


async def test_crashed_analysis_lease_is_reclaimed(client, api_key):
    await answered_call(client, api_key)
    now = datetime.now(UTC)
    await claim_one(now)
    async with SessionFactory() as db:
        assert await reap_analyses(db, now + timedelta(minutes=6)) == 1
    assert (await analysis_row()).status == "pending"


async def test_analysis_shown_on_call_detail(client, api_key):
    call = await answered_call(client, api_key)
    [aid] = await claim_one()
    await process_analysis(aid, engine(None))  # no LLM configured -> rules
    r = await client.get(f"/v1/calls/{call.id}", headers={"X-API-Key": api_key})
    analysis = r.json()["analysis"]
    assert analysis["source"] == "rules"
    assert analysis["guardrail_notes"] == ["no LLM configured"]


# ---- Redis rate limiter and quota (real Redis) --------------------------------------


@pytest.fixture
async def redis():
    r = Redis.from_url(get_settings().redis_url.rsplit("/", 1)[0] + "/15")  # test db
    await r.flushdb()
    yield r
    await r.flushdb()
    await r.aclose()


async def test_token_bucket_allows_burst_then_paces(redis):
    limiter = RateLimiter(redis, "test:bucket", per_minute=60, burst=3)  # 1 per second
    waits = [await limiter.try_acquire() for _ in range(4)]
    assert waits[:3] == [0, 0, 0]
    assert 0.9 < waits[3] <= 1.0


async def test_bucket_is_shared_between_workers(redis):
    # Two limiter objects = two worker processes. Together they get only 3 tokens.
    a = RateLimiter(redis, "test:shared", per_minute=60, burst=3)
    b = RateLimiter(redis, "test:shared", per_minute=60, burst=3)
    waits = await asyncio.gather(*[x.try_acquire() for x in (a, b, a, b, a, b)])
    assert sum(w == 0 for w in waits) == 3


async def test_daily_quota(redis):
    quota = DailyQuota(redis, "test:quota", per_day=2)
    assert [await quota.take() for _ in range(3)] == [True, True, False]
