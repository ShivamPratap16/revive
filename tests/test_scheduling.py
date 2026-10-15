"""Unit tests for calling windows, backoff, the pre-send decision and message text.

Reference dates (IST = UTC+5:30): 2026-10-15 is a Thursday, 10-17 a Saturday, 10-18 a Sunday.
"""

import random
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app.domain.journey import SessionStatus, Step
from app.domain.messages import render
from app.domain.policy import Channel, OutreachPolicy, Window
from app.domain.scheduling import (
    ALL_DAYS,
    Action,
    backoff_seconds,
    decide,
    next_allowed_time,
    plan_attempts,
)

IST = ZoneInfo("Asia/Kolkata")
VOICE = Window(start=time(9), end=time(21))
MON_SAT = {0, 1, 2, 3, 4, 5}


def ist(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=IST)


def in_ist(dt: datetime) -> tuple:
    local = dt.astimezone(IST)
    return local.day, local.hour, local.minute


# ---- calling windows ----------------------------------------------------------------


def test_inside_window_is_allowed_now():
    assert in_ist(next_allowed_time(ist(15, 10, 30), VOICE, MON_SAT, "Asia/Kolkata")) == (
        15,
        10,
        30,
    )


def test_early_morning_waits_for_window_start():
    assert in_ist(next_allowed_time(ist(15, 7, 30), VOICE, MON_SAT, "Asia/Kolkata")) == (15, 9, 0)


def test_late_night_moves_to_next_morning():
    assert in_ist(next_allowed_time(ist(15, 21, 30), VOICE, MON_SAT, "Asia/Kolkata")) == (16, 9, 0)


def test_window_end_is_exclusive():
    assert in_ist(next_allowed_time(ist(15, 21, 0), VOICE, MON_SAT, "Asia/Kolkata")) == (16, 9, 0)


def test_saturday_night_skips_sunday_for_calls():
    assert in_ist(next_allowed_time(ist(17, 21, 30), VOICE, MON_SAT, "Asia/Kolkata")) == (19, 9, 0)


def test_in_app_nudges_allowed_on_sunday():
    window = Window(start=time(8), end=time(22))
    assert in_ist(next_allowed_time(ist(17, 23, 0), window, ALL_DAYS, "Asia/Kolkata")) == (18, 8, 0)


def test_result_is_utc():
    result = next_allowed_time(ist(15, 7, 0), VOICE, MON_SAT, "Asia/Kolkata")
    assert result.utcoffset() == timedelta(0)
    assert result == datetime(2026, 10, 15, 3, 30, tzinfo=UTC)  # 09:00 IST


# ---- backoff ------------------------------------------------------------------------


def test_backoff_grows_and_is_capped():
    rng = random.Random(42)
    for tries, ceiling in [(1, 30), (2, 60), (3, 120), (4, 240), (10, 3600)]:
        delay = backoff_seconds(tries, base=30, cap=3600, rng=rng)
        assert ceiling * 0.5 <= delay <= ceiling


# ---- the pre-send decision ----------------------------------------------------------


def _decide(**overrides):
    args = dict(
        channel=Channel.VOICE,
        attempt_round=1,
        session_status=SessionStatus.DROPPED_OFF,
        session_round=1,
        phone="+919876543210",
        is_dnd=False,
        recent_voice_sends=[],
        policy=OutreachPolicy(),
        now=ist(15, 11, 0).astimezone(UTC),
    )
    args.update(overrides)
    return decide(**args)


def test_sends_when_everything_allows_it():
    assert _decide().action == Action.SEND


def test_cancels_when_user_finished():
    d = _decide(session_status=SessionStatus.COMPLETED)
    assert (d.action, d.reason) == (Action.CANCEL, "completed")


def test_cancels_when_user_came_back():
    d = _decide(session_status=SessionStatus.IN_PROGRESS)
    assert (d.action, d.reason) == (Action.CANCEL, "user_resumed")


def test_cancels_attempt_from_an_older_drop_round():
    # Dropped, came back, dropped again: round-1 calls must not fire for round 2.
    d = _decide(attempt_round=1, session_round=2)
    assert d.action == Action.CANCEL


def test_skips_voice_for_dnd_and_missing_phone():
    assert _decide(is_dnd=True).reason == "dnd"
    assert _decide(phone=None).reason == "no_phone"


def test_dnd_does_not_block_in_app_nudges():
    assert _decide(channel=Channel.IN_APP, is_dnd=True).action == Action.SEND


def test_defers_call_outside_hours():
    d = _decide(now=ist(15, 22, 0).astimezone(UTC))
    assert d.action == Action.DEFER and d.reason == "outside_window"
    assert in_ist(d.retry_at) == (16, 9, 0)


def test_daily_call_limit():
    sends = [ist(15, 9, 30).astimezone(UTC), ist(15, 10, 0).astimezone(UTC)]
    d = _decide(recent_voice_sends=sends)
    assert d.reason == "daily_call_limit"
    assert in_ist(d.retry_at) == (16, 9, 30)  # 24h after the oldest call


# ---- planning -----------------------------------------------------------------------


def test_plan_respects_windows():
    plan = plan_attempts(
        policy=OutreachPolicy(),
        now=ist(15, 20, 50).astimezone(UTC),
        phone="+919876543210",
        is_dnd=False,
    )
    assert [(p.channel, in_ist(p.scheduled_for)) for p in plan] == [
        (Channel.IN_APP, (15, 20, 50)),  # nudge right away
        (Channel.VOICE, (16, 9, 0)),  # +30m is 21:20, after hours -> next morning
        (Channel.VOICE, (16, 20, 50)),  # +24h
    ]


def test_plan_records_skipped_voice_steps():
    plan = plan_attempts(
        policy=OutreachPolicy(), now=ist(15, 11).astimezone(UTC), phone=None, is_dnd=False
    )
    assert [p.skip_reason for p in plan] == [None, "no_phone", "no_phone"]


# ---- policy validation --------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        {"voice_window": {"start": "21:00", "end": "09:00"}},
        {"timezone": "Mars/Olympus"},
        {"voice_days": [7]},
        {"idle_minutes": {"aadhaar_otp": 0}},
        {"cadence": []},
    ],
)
def test_policy_rejects_bad_values(bad):
    with pytest.raises(ValidationError):
        OutreachPolicy.model_validate(bad)


# ---- messages -----------------------------------------------------------------------


def test_message_mentions_the_actual_problem_in_users_language():
    text = render(Channel.IN_APP, "hinglish", Step.AADHAAR_OTP, "Ravi Kumar", "OTP_NOT_RECEIVED")
    assert text == (
        "Namaste Ravi! OTP nahi aaya? Hum naya bhej sakte hain. "
        "Bas 3 step baaki hain. Apna Aadhaar OTP continue karein."
    )


def test_message_without_name_or_known_language():
    text = render(Channel.IN_APP, None, Step.BANK_LINKING, None, None)
    assert text.startswith("Namaste! Bas 1 step")


def test_english_grammar():
    assert render(Channel.IN_APP, "en", Step.VIDEO_KYC, "Arjun Mehta", None) == (
        "Hi Arjun! You're 2 steps away from finishing. Continue your video KYC."
    )
    assert "1 step away" in render(Channel.IN_APP, "en", Step.BANK_LINKING, "A", None)
