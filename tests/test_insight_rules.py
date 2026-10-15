"""Unit tests: redaction, guardrails, circuit breaker, and the Gemini client's error mapping
(Gemini is faked with httpx.MockTransport: the real request is built and inspected,
but nothing leaves the machine)."""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.domain.insights import (
    CallInsight,
    build_prompt,
    parse_callback,
    quote_is_real,
)
from app.domain.redaction import redact, redact_turns
from app.llm.gemini import GeminiAnalyzer, LlmInvalidOutput, LlmPermanent, LlmTransient
from app.llm.resilience import BreakerState, CircuitBreaker

IST = ZoneInfo("Asia/Kolkata")
CALL = datetime(2026, 10, 15, 10, 0, tzinfo=IST)


# ---- redaction ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("mera aadhaar 2345 6789 0123 hai", "mera aadhaar [AADHAAR] hai"),
        ("PAN is ABCDE1234F", "PAN is [PAN]"),
        ("call me on +91 98765 43210", "call me on [PHONE]"),
        ("number 9876543210 pe", "number [PHONE] pe"),
        ("OTP 482913 aaya", "OTP [NUMBER] aaya"),
        ("haan 482... daal diya", "haan [NUMBER]... daal diya"),
        ("card 4111 1111 1111 1111", "card [CARD]"),
        ("mail ravi.k@gmail.com", "mail [EMAIL]"),
        ("OTP ४८२९१३ आया", "OTP [NUMBER] आया"),  # Devanagari digits
        ("shaam 6 baje", "shaam 6 baje"),  # times are kept: the model needs them
    ],
)
def test_redaction(text, expected):
    assert redact(text).text == expected


def test_names_are_redacted_and_counted():
    turns, counts = redact_turns(
        [{"speaker": "agent", "text": "Namaste Ravi ji, OTP 4829 aaya?", "start_ms": 0}],
        names=["Ravi", "Kumar"],
    )
    assert turns[0]["text"] == "Namaste [NAME] ji, OTP [NUMBER] aaya?"
    assert counts == {"NAME": 1, "NUMBER": 1}


# ---- guardrails ---------------------------------------------------------------------

TURNS = [
    {"speaker": "agent", "text": "Kab call karun?", "start_ms": 0},
    {"speaker": "user", "text": "Shaam ko 6 baje ke baad karo, tab free rahunga.", "start_ms": 1},
]


def test_quote_must_come_from_the_user():
    assert quote_is_real("shaam ko 6 baje ke baad karo", TURNS)  # case/punctuation ignored
    assert not quote_is_real("Kab call karun", TURNS)  # the agent said that, not the user
    assert not quote_is_real("mujhe loan nahi chahiye", TURNS)  # invented


@pytest.mark.parametrize(
    "raw, ok, problem",
    [
        ("2026-10-15T18:00", True, None),
        ("2026-10-15T09:00", False, "not after the call"),
        ("2026-11-30T18:00", False, "more than a week"),
        ("tomorrow evening", False, "unparseable"),
        (None, False, None),
    ],
)
def test_callback_time_checks(raw, ok, problem):
    when, why = parse_callback(raw, CALL, "Asia/Kolkata")
    assert (when is not None) == ok
    assert (problem is None and why is None) or (problem in why)


def test_prompt_fences_the_transcript():
    prompt = build_prompt(TURNS, CALL, "aadhaar_otp", "OTP_TIMEOUT")
    assert "Thursday 2026-10-15 10:00" in prompt
    assert prompt.rstrip().endswith("</transcript>")


# ---- circuit breaker ----------------------------------------------------------------


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_breaker_opens_then_half_opens_then_closes():
    clock = Clock()
    b = CircuitBreaker(failure_threshold=3, cooldown_seconds=60, clock=clock)
    for _ in range(3):
        assert b.allow()
        b.record_failure()
    assert b.state == BreakerState.OPEN and not b.allow()  # fail fast

    clock.t = 61
    assert b.state == BreakerState.HALF_OPEN
    assert b.allow()  # one trial call...
    assert not b.allow()  # ...and only one
    b.record_success()
    assert b.state == BreakerState.CLOSED and b.allow()


def test_failed_trial_reopens_the_breaker():
    clock = Clock()
    b = CircuitBreaker(failure_threshold=1, cooldown_seconds=60, clock=clock)
    b.record_failure()
    clock.t = 61
    assert b.allow()
    b.record_failure()
    assert b.state == BreakerState.OPEN
    clock.t = 100
    assert not b.allow()  # cool-down restarted at 61


# ---- Gemini client, against a fake HTTP server --------------------------------------

GOOD = {
    "outcome": "callback_requested",
    "dropoff_reason": "busy_no_time",
    "sentiment": "neutral",
    "spoken_language": "hinglish",
    "callback_requested_at": "2026-10-15T18:00",
    "do_not_call": False,
    "summary": "User is busy and asked for a call after 6pm.",
    "key_quote": "Shaam ko 6 baje ke baad karo",
    "confidence": 0.9,
}


def gemini_with(handler) -> tuple[GeminiAnalyzer, list]:
    seen = []

    def record(request: httpx.Request):
        seen.append(request)
        return handler(request)

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(record), base_url=GeminiAnalyzer.BASE_URL
    )
    return GeminiAnalyzer("test-key", "gemini-test", client=client), seen


def ok_response(payload: dict, finish="STOP") -> httpx.Response:
    return httpx.Response(200, json={
        "candidates": [{"content": {"parts": [{"text": json.dumps(payload)}]},
                        "finishReason": finish}],
        "usageMetadata": {"promptTokenCount": 420, "candidatesTokenCount": 80},
    })  # fmt: skip


async def test_gemini_success_and_request_shape():
    gemini, seen = gemini_with(lambda r: ok_response(GOOD))
    raw = [{"speaker": "user", "text": "Mera number 9876543210 hai, OTP 482913", "start_ms": 0}]
    turns, _ = redact_turns(raw)
    result = await gemini.analyze(build_prompt(turns, CALL, "aadhaar_otp", None))

    assert result.insight == CallInsight.model_validate(GOOD)
    assert (result.input_tokens, result.output_tokens) == (420, 80)

    req = seen[0]
    assert req.url.path == "/v1beta/models/gemini-test:generateContent"
    assert req.headers["x-goog-api-key"] == "test-key"
    assert "key=" not in str(req.url)  # never in the URL, where it would be logged
    body = req.content.decode()
    # The point of redaction: the raw values never leave the machine.
    assert "9876543210" not in body and "482913" not in body
    sent = json.loads(body)
    assert sent["generationConfig"]["responseMimeType"] == "application/json"
    assert sent["generationConfig"]["temperature"] == 0


@pytest.mark.parametrize(
    "response, error",
    [
        (httpx.Response(429, json={"error": {"details": [{"retryDelay": "17s"}]}}), LlmTransient),
        (httpx.Response(503, json={}), LlmTransient),
        (httpx.Response(400, json={"error": {"message": "bad schema"}}), LlmPermanent),
        (httpx.Response(403, json={"error": {"message": "API key invalid"}}), LlmPermanent),
        (ok_response({**GOOD, "outcome": "made_up_value"}), LlmInvalidOutput),
        (ok_response(GOOD, finish="MAX_TOKENS"), LlmInvalidOutput),
        (httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}}), LlmInvalidOutput),
        (httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "{not json"}]},
                                                   "finishReason": "STOP"}]}), LlmInvalidOutput),
    ],
)  # fmt: skip
async def test_gemini_error_mapping(response, error):
    gemini, _ = gemini_with(lambda r: response)
    with pytest.raises(error) as exc:
        await gemini.analyze("prompt")
    if response.status_code == 429:
        assert exc.value.retry_after == 17.0


async def test_gemini_timeout_is_transient():
    def hang(request):
        raise httpx.ReadTimeout("slow", request=request)

    gemini, _ = gemini_with(hang)
    with pytest.raises(LlmTransient):
        await gemini.analyze("prompt")


# ---- eval as a regression guard -----------------------------------------------------


def test_rules_still_pass_every_seen_eval_case():
    """The rules were tuned on these conversations; any drop is a regression.
    (Held-out cases are reported by `python -m app.evals`, not enforced: they measure.)"""
    from app.domain.insights import rule_based_insight
    from app.evals import CALL_TIME, cases, score

    failures = [
        (name, [k for k, ok in score(rule_based_insight(turns, CALL_TIME), exp).items() if not ok])
        for group, name, _, turns, exp in cases()
        if group == "seen"
    ]
    assert [f for f in failures if f[1]] == []
