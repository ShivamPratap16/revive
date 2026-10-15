"""Pure scheduling rules: calling windows, retry backoff, and the last check before sending.

Every function takes `now` as an argument instead of reading the clock itself. That one
habit makes time-based logic testable: a test can say "it's 8:59pm on a Saturday"
without waiting for one.
"""

import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from app.domain.journey import SessionStatus
from app.domain.policy import Channel, OutreachPolicy, Window

ALL_DAYS = frozenset(range(7))


def next_allowed_time(
    at: datetime, window: Window, days: set[int] | frozenset[int], tz: str
) -> datetime:
    """The earliest moment >= `at` that falls inside the window on an allowed day.

    Works in the tenant's local time (a 9pm limit means 9pm in India, not 9pm UTC) and
    returns UTC, because everything stored in the database is UTC.
    """
    zone = ZoneInfo(tz)
    local = at.astimezone(zone)
    for offset in range(8):  # within 8 days we always reach an allowed weekday
        day = local.date() + timedelta(days=offset)
        if day.weekday() not in days:
            continue
        start = datetime.combine(day, window.start, tzinfo=zone)
        end = datetime.combine(day, window.end, tzinfo=zone)
        if offset == 0:
            if local < start:
                return start.astimezone(UTC)
            if local < end:
                return at.astimezone(UTC)  # already inside the window
            continue  # today's window is over
        return start.astimezone(UTC)
    raise ValueError("policy allows no days")  # unreachable: days is validated non-empty


def window_for(channel: Channel, policy: OutreachPolicy) -> tuple[Window, frozenset[int]]:
    if channel == Channel.VOICE:
        return policy.voice_window, frozenset(policy.voice_days)
    return policy.in_app_window, ALL_DAYS


def backoff_seconds(
    tries: int, base: float = 30, cap: float = 3600, rng: random.Random | None = None
) -> float:
    """Exponential backoff with jitter: about 30s, 60s, 120s, 240s... capped at an hour.

    Why exponential: if the provider is down, hammering it every second makes things worse.
    Why jitter: if 500 calls failed at the same moment, retrying them all at exactly
    +60s recreates the same spike (the "thundering herd"). A random factor spreads them out.
    """
    rng = rng or random
    ceiling = min(cap, base * 2 ** (tries - 1))
    return ceiling * rng.uniform(0.5, 1.0)


class Action(StrEnum):
    SEND = "send"
    CANCEL = "cancel"  # no longer needed (user came back / finished)
    SKIP = "skip"  # not allowed for this user (DND, no phone)
    DEFER = "defer"  # allowed later (outside calling hours, daily cap reached)


@dataclass(frozen=True)
class Decision:
    action: Action
    reason: str | None = None
    retry_at: datetime | None = None


def decide(
    *,
    channel: Channel,
    attempt_round: int,
    session_status: SessionStatus,
    session_round: int,
    phone: str | None,
    is_dnd: bool,
    recent_voice_sends: list[datetime],
    policy: OutreachPolicy,
    now: datetime,
) -> Decision:
    """The final check right before contacting a user. The order of checks matters:
    the cheapest and most final reasons come first."""
    if session_status == SessionStatus.COMPLETED:
        return Decision(Action.CANCEL, "completed")
    if session_status != SessionStatus.DROPPED_OFF or session_round != attempt_round:
        return Decision(Action.CANCEL, "user_resumed")

    if channel == Channel.VOICE:
        if not phone:
            return Decision(Action.SKIP, "no_phone")
        if is_dnd:
            # Checked again here, not only when planning: the user may have asked
            # "don't call me" after the call was scheduled.
            return Decision(Action.SKIP, "dnd")

    window, days = window_for(channel, policy)
    allowed = next_allowed_time(now, window, days, policy.timezone)
    if allowed > now:
        return Decision(Action.DEFER, "outside_window", allowed)

    if channel == Channel.VOICE and len(recent_voice_sends) >= policy.max_voice_calls_per_day:
        # Wait until the oldest call in the last 24h is more than 24h old.
        free_at = min(recent_voice_sends) + timedelta(hours=24)
        return Decision(
            Action.DEFER,
            "daily_call_limit",
            next_allowed_time(free_at, window, days, policy.timezone),
        )

    return Decision(Action.SEND)


@dataclass(frozen=True)
class PlannedAttempt:
    sequence_no: int
    channel: Channel
    scheduled_for: datetime
    skip_reason: str | None  # set when the step is skipped at planning time


def plan_attempts(
    *, policy: OutreachPolicy, now: datetime, phone: str | None, is_dnd: bool
) -> list[PlannedAttempt]:
    """Turn the tenant's cadence into concrete attempts with real times.

    Skipped steps are still recorded (with a reason) rather than left out. An audit trail
    that says "voice call skipped: DND" is what a compliance team asks for.
    """
    plan = []
    for i, step in enumerate(policy.cadence, start=1):
        window, days = window_for(step.channel, policy)
        due = next_allowed_time(
            now + timedelta(minutes=step.delay_minutes), window, days, policy.timezone
        )
        skip = None
        if step.channel == Channel.VOICE:
            skip = "no_phone" if not phone else ("dnd" if is_dnd else None)
        plan.append(PlannedAttempt(i, step.channel, due, skip))
    return plan


class AttemptStatus(StrEnum):
    SCHEDULED = "scheduled"
    SENDING = "sending"
    SENT = "sent"
    DEAD = "dead"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"
