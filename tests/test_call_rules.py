"""Unit tests: webhook signatures and the call state machine."""

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.calls import CallEventType, CallState, CallStatus, apply_call_event
from app.webhook_security import WebhookSignatureError, sign, verify

NOW = datetime(2026, 10, 15, 6, 0, tzinfo=UTC)
TS = int(NOW.timestamp())
BODY = b'{"event_id":"evt_1","type":"call.ringing"}'
SECRET = "whsec_test"


# ---- signatures ---------------------------------------------------------------------


def test_valid_signature_passes():
    verify(BODY, sign(SECRET, TS, BODY), [SECRET], NOW)


@pytest.mark.parametrize(
    "header, why",
    [
        (sign("wrong_secret", TS, BODY), "mismatch"),
        (sign(SECRET, TS, BODY + b" "), "mismatch"),  # one extra byte in the body
        (sign(SECRET, TS - 301, BODY), "tolerance"),  # replay of an old request
        (sign(SECRET, TS + 301, BODY), "tolerance"),  # timestamp from the future
        (None, "missing"),
        ("v1=abc", "malformed"),
        ("t=notanumber,v1=abc", "malformed"),
    ],
)
def test_bad_signatures_are_rejected(header, why):
    body = BODY
    with pytest.raises(WebhookSignatureError, match=why):
        verify(body, header, [SECRET], NOW)


def test_tampered_timestamp_breaks_signature():
    # An attacker re-labels an old request with a fresh timestamp: the HMAC no longer matches.
    old = sign(SECRET, TS - 3600, BODY)
    forged = old.replace(f"t={TS - 3600}", f"t={TS}")
    with pytest.raises(WebhookSignatureError, match="mismatch"):
        verify(BODY, forged, [SECRET], NOW)


def test_secret_rotation_accepts_old_and_new():
    for secret in ("whsec_old", "whsec_new"):
        verify(BODY, sign(secret, TS, BODY), ["whsec_new", "whsec_old"], NOW)


# ---- call state machine -------------------------------------------------------------


def at(s: int) -> datetime:
    return NOW + timedelta(seconds=s)


def play(*events) -> CallState:
    state = CallState(CallStatus.QUEUED)
    for type_, second, data in events:
        state = apply_call_event(state, type_, at(second), data)
    return state


def test_normal_call():
    s = play(
        (CallEventType.RINGING, 2, {}),
        (CallEventType.ANSWERED, 9, {}),
        (CallEventType.COMPLETED, 70, {"duration_seconds": 61}),
    )
    assert (s.status, s.answered_at, s.ended_at, s.duration_seconds) == (
        CallStatus.COMPLETED,
        at(9),
        at(70),
        61,
    )


def test_completed_before_answered():
    s = play(
        (CallEventType.COMPLETED, 70, {"duration_seconds": 61}),
        (CallEventType.ANSWERED, 9, {}),  # arrives late
    )
    assert s.status == CallStatus.COMPLETED
    assert s.answered_at == at(9)  # inferred from duration, matches the real event


def test_late_ringing_does_not_move_back():
    s = play((CallEventType.ANSWERED, 9, {}), (CallEventType.RINGING, 2, {}))
    assert s.status == CallStatus.IN_PROGRESS


def test_terminal_is_final():
    s = play((CallEventType.NO_ANSWER, 30, {"reason": "no_answer"}), (CallEventType.FAILED, 31, {}))
    assert (s.status, s.end_reason) == (CallStatus.NO_ANSWER, "no_answer")


def test_enrichment_events_dont_change_status():
    s = play((CallEventType.RINGING, 2, {}), (CallEventType.TRANSCRIPT_READY, 3, {"turns": []}))
    assert s.status == CallStatus.RINGING


# ---- simulator scenario choice ------------------------------------------------------


def test_simulator_matches_scenario_to_the_step():
    import random

    from simulator import scenarios

    assert scenarios.topic_of("Namaste Imran! ... आपका वीडियो KYC दो मिनट में") == "video_kyc"
    assert scenarios.topic_of("Hi Ravi! Didn't get the OTP?") == "otp"
    rng = random.Random(1)
    picked = {scenarios.pick(rng, topic="video_kyc").name for _ in range(300)}
    assert "otp_resent_and_done" not in picked
    assert "pan_name_mismatch_confused" not in picked
    assert "language_switch" in picked
