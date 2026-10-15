"""Integration tests for calls: signed webhooks into the API, and end-to-end runs with the
simulator provider. Everything runs in one process: httpx's ASGITransport connects the
two apps directly, so there are no ports, no network, and no flakiness from either."""

import asyncio
import json
import random
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.channels import HttpVoiceChannel, OutboundMessage, TransientChannelError
from app.config import get_settings
from app.db import SessionFactory
from app.domain.policy import Channel
from app.main import app as revive_app
from app.models import Call, CallWebhookEvent
from app.services.delivery import deliver_attempt
from app.webhook_security import sign
from simulator.main import app as sim_app
from tests.test_outreach import FakeChannel, T, attempts, channels, claim, ev, post, stuck_on_otp

SECRET = get_settings().webhook_secret_list[0]
CALL_TIME = T + timedelta(minutes=50)  # the +30 min call is due by then


async def placed_call(client, api_key, phone="+919812345678") -> Call:
    """A dropped-off user whose first voice call has just been placed (via a fake channel)."""
    await stuck_on_otp(client, api_key, phone=phone)
    from app.services.detector import detect_dropoffs

    await detect_dropoffs(now=T + timedelta(minutes=16))
    for attempt_id in await claim(CALL_TIME):
        await deliver_attempt(attempt_id, channels(), now=CALL_TIME)
    async with SessionFactory() as db:
        return await db.scalar(select(Call))


def webhook(call: Call, type_: str, data=None, event_id=None, occurred_at=None) -> dict:
    return {
        "event_id": event_id or f"evt_{uuid.uuid4().hex[:10]}",
        "type": type_,
        "occurred_at": (occurred_at or datetime.now(UTC)).isoformat(),
        "call": {"id": call.provider_call_id or "prov_1", "client_reference": str(call.id)},
        "data": data or {},
    }


async def send(client, payload: dict, secret=SECRET, ts=None):
    body = json.dumps(payload).encode()
    return await client.post(
        "/webhooks/voice/revive-sim",
        content=body,
        headers={
            "x-voice-signature": sign(secret, ts or int(time.time()), body),
            "content-type": "application/json",
        },
    )


async def call_row(call_id) -> Call:
    async with SessionFactory() as db:
        return await db.get(Call, call_id)


async def event_count() -> int:
    async with SessionFactory() as db:
        return await db.scalar(select(func.count()).select_from(CallWebhookEvent))


# ---- webhook endpoint ---------------------------------------------------------------


async def test_answered_call_flow(client, api_key):
    call = await placed_call(client, api_key)
    assert call.status == "queued" and call.provider_call_id.startswith("fake_")

    turns = [{"speaker": "user", "text": "OTP nahi aaya", "start_ms": 0}]
    for type_, data in [
        ("call.ringing", {}),
        ("call.answered", {}),
        ("call.completed", {"duration_seconds": 64}),
        ("call.transcript_ready", {"language": "hinglish", "turns": turns}),
    ]:
        r = await send(client, webhook(call, type_, data))
        assert r.status_code == 200, r.text

    call = await call_row(call.id)
    assert (call.status, call.duration_seconds, call.language) == ("completed", 64, "hinglish")
    assert call.transcript == turns
    # We reached the user, so the next-day call is cancelled
    assert [(a.channel, a.status, a.status_reason) for a in await attempts()][2] == (
        "voice",
        "cancelled",
        "reached_on_call",
    )

    detail = (await client.get(f"/v1/calls/{call.id}", headers={"X-API-Key": api_key})).json()
    assert [e["effect"] for e in detail["webhook_events"]][:3] == [
        "status queued -> ringing",
        "status ringing -> in_progress",
        "status in_progress -> completed; reached user; cancelled 1 later call(s)",
    ]


async def test_bad_signature_is_rejected_and_nothing_stored(client, api_key):
    call = await placed_call(client, api_key)
    r = await send(client, webhook(call, "call.completed"), secret="attacker_guess")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_signature"
    assert await event_count() == 0
    assert (await call_row(call.id)).status == "queued"


async def test_replayed_request_is_rejected(client, api_key):
    call = await placed_call(client, api_key)
    r = await send(client, webhook(call, "call.ringing"), ts=int(time.time()) - 600)
    assert r.status_code == 401


async def test_duplicate_webhook_is_acknowledged_once(client, api_key):
    call = await placed_call(client, api_key)
    payload = webhook(call, "call.ringing", event_id="evt_same")
    assert (await send(client, payload)).json()["status"] == "processed"
    assert (await send(client, payload)).json() == {"status": "duplicate"}  # still 200
    assert await event_count() == 1


async def test_unknown_call_is_404(client, api_key):
    call = await placed_call(client, api_key)
    payload = webhook(call, "call.ringing")
    payload["call"]["client_reference"] = str(uuid.uuid4())
    assert (await send(client, payload)).status_code == 404


async def test_concurrent_webhooks_for_one_call(client, api_key):
    call = await placed_call(client, api_key)
    results = await asyncio.gather(
        send(client, webhook(call, "call.completed", {"duration_seconds": 40})),
        send(client, webhook(call, "call.ringing")),
        send(client, webhook(call, "call.answered")),
    )
    assert all(r.status_code == 200 for r in results)
    assert (await call_row(call.id)).status == "completed"


async def test_no_answer_redials_once(client, api_key):
    call = await placed_call(client, api_key)
    before = datetime.now(UTC)
    r = await send(client, webhook(call, "call.no_answer", {"reason": "no_answer"}))
    assert "redial scheduled" in r.json()["effect"]

    voice = (await attempts())[1]
    assert (voice.status, voice.redials, voice.status_reason) == (
        "scheduled",
        1,
        "redial_after_no_answer",
    )
    assert voice.scheduled_for >= before + timedelta(minutes=120)

    # The redial happens: a NEW call row (a new placement), then nobody answers again.
    await claim(voice.scheduled_for)
    await deliver_attempt(voice.id, channels(), now=voice.scheduled_for)
    async with SessionFactory() as db:
        calls = list(await db.scalars(select(Call).order_by(Call.created_at)))
    assert len(calls) == 2
    r = await send(client, webhook(calls[1], "call.no_answer"))
    assert r.json()["effect"].endswith("not redialling: redial limit reached")


async def test_no_redial_if_user_came_back(client, api_key):
    call = await placed_call(client, api_key)
    await post(client, api_key, ev("back", "step_completed", "aadhaar_otp", CALL_TIME))
    r = await send(client, webhook(call, "call.busy"))
    assert r.json()["effect"].endswith("not redialling: user is no longer dropped off")


async def test_retry_after_timeout_reuses_the_same_call(client, api_key):
    # The provider may have placed the call even though our request timed out. Retrying
    # with the same call id (= same idempotency key) prevents dialling the user twice.
    await stuck_on_otp(client, api_key)
    from app.services.detector import detect_dropoffs

    await detect_dropoffs(now=T + timedelta(minutes=16))
    ids = await claim(CALL_TIME)
    flaky = FakeChannel(TransientChannelError("read timeout"))
    for i in ids:
        await deliver_attempt(i, channels(voice=flaky), now=CALL_TIME)
    voice = (await attempts())[1]
    await claim(voice.scheduled_for)
    ok = FakeChannel()
    await deliver_attempt(voice.id, channels(voice=ok), now=voice.scheduled_for)

    async with SessionFactory() as db:
        calls = list(await db.scalars(select(Call)))
    assert len(calls) == 1
    assert ok.sent[0].idempotency_key == f"revive-{calls[0].id}"


# ---- end to end with the simulator --------------------------------------------------


@pytest.fixture
def sim():
    """The simulator, wired straight to the Revive app, with time running instantly."""
    cfg = sim_app.state.config
    saved = vars(cfg).copy()
    cfg.time_scale, cfg.duplicate_rate, cfg.reorder_rate = 0, 0, 0
    cfg.api_error_rate, cfg.rng = 0, random.Random(7)
    sim_app.state.webhook_client = AsyncClient(
        transport=ASGITransport(app=revive_app), base_url="http://revive"
    )
    voice = HttpVoiceChannel(
        "http://sim",
        cfg.api_key,
        "http://revive/webhooks/voice/revive-sim",
        client=AsyncClient(transport=ASGITransport(app=sim_app), base_url="http://sim"),
    )
    yield cfg, voice
    vars(cfg).update(saved)
    sim_app.state.webhook_client = None


async def run_voice_attempt(client, api_key, voice, phone="+919812345678") -> str:
    await stuck_on_otp(client, api_key, phone=phone)
    from app.services.detector import detect_dropoffs

    await detect_dropoffs(now=T + timedelta(minutes=16))
    outcomes = [
        await deliver_attempt(i, channels(voice=voice), now=CALL_TIME)
        for i in await claim(CALL_TIME)
    ]
    await asyncio.gather(*list(sim_app.state.tasks))  # let the simulator finish the call
    return sorted(outcomes)[-1]


async def test_e2e_call_with_chaos(client, api_key, sim):
    cfg, voice = sim
    cfg.force_scenario = "call_back_evening"
    cfg.duplicate_rate, cfg.reorder_rate = 1.0, 1.0  # every webhook twice, plus a swap

    assert await run_voice_attempt(client, api_key, voice) == "sent"

    async with SessionFactory() as db:
        call = await db.scalar(select(Call))
    assert call.status == "completed"
    assert call.language == "hinglish"
    assert any("6 baje" in t["text"] for t in call.transcript)
    assert call.answered_at is not None and call.duration_seconds >= 20
    assert await event_count() == 6  # 12 deliveries, 6 unique events


async def test_e2e_no_answer_schedules_redial(client, api_key, sim):
    cfg, voice = sim
    cfg.force_scenario = "no_answer"
    await run_voice_attempt(client, api_key, voice)
    assert [(a.status, a.redials) for a in await attempts()][1] == ("scheduled", 1)


async def test_e2e_invalid_number_goes_to_dead_letter(client, api_key, sim):
    _, voice = sim
    assert await run_voice_attempt(client, api_key, voice, phone="+919800000000") == "sent"
    voice_attempt = (await attempts())[1]
    assert (voice_attempt.status, voice_attempt.status_reason) == ("dead", "permanent_error")
    assert "422" in voice_attempt.last_error
    async with SessionFactory() as db:
        assert (await db.scalar(select(Call))).status == "failed"


async def test_e2e_provider_overloaded_is_retried(client, api_key, sim):
    cfg, voice = sim
    cfg.api_error_rate = 1.0
    await run_voice_attempt(client, api_key, voice)
    v = (await attempts())[1]
    assert (v.status, v.status_reason) == ("scheduled", "retrying")
    assert "503" in v.last_error


async def test_simulator_honours_idempotency_key(sim):
    cfg, voice = sim
    msg = OutboundMessage(
        attempt_id=uuid.uuid4(),
        channel=Channel.VOICE,
        customer_ref="u1",
        phone="+919812345678",
        language="en",
        text="Hi!",
        call_id=uuid.uuid4(),
    )
    first = await voice.send(msg)
    second = await voice.send(msg)  # e.g. our retry after a timeout
    assert first == second
    await asyncio.gather(*list(sim_app.state.tasks))


async def test_recovery_after_unanswered_call_credits_the_call(client, api_key):
    # Regression (found via the demo analytics): after no answer, the attempt is re-queued
    # for a re-dial and is no longer "sent". The call still happened and must get the credit,
    # not the in-app nudge sent 30 minutes before it.
    from app.models import OnboardingSession
    from app.services.detector import detect_dropoffs

    await stuck_on_otp(client, api_key)
    nudge_time = T + timedelta(minutes=16)
    await detect_dropoffs(now=nudge_time)
    for i in await claim(nudge_time):  # only the in-app nudge is due
        await deliver_attempt(i, channels(), now=nudge_time)
    for i in await claim(CALL_TIME):  # the voice call
        await deliver_attempt(i, channels(), now=CALL_TIME)
    async with SessionFactory() as db:
        call = await db.scalar(select(Call))

    await send(client, webhook(call, "call.no_answer"))
    assert (await attempts())[1].status == "scheduled"  # waiting to re-dial

    done = ev("done", "journey_completed", None, CALL_TIME + timedelta(minutes=20))
    await post(client, api_key, done)
    async with SessionFactory() as db:
        session = await db.scalar(select(OnboardingSession))
    assert session.recovered_by_attempt_id == call.attempt_id
