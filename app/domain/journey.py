"""The KYC journey and the rules for how events move a session through it.

Everything here is a pure function: no database and no network. Pure functions are the
easiest code to test and reason about, so the core business rules live here and the
service layer just loads state, calls apply_event(), and saves the result.
"""

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum


class Step(StrEnum):
    SIGNUP = "signup"
    PAN_VERIFICATION = "pan_verification"
    AADHAAR_OTP = "aadhaar_otp"
    VIDEO_KYC = "video_kyc"
    BANK_LINKING = "bank_linking"
    COMPLETED = "completed"


# The order users move through. Its index is how we compare "how far" someone got.
STEP_ORDER: list[Step] = list(Step)
STEP_INDEX: dict[Step, int] = {step: i for i, step in enumerate(STEP_ORDER)}


class EventType(StrEnum):
    STEP_STARTED = "step_started"
    STEP_COMPLETED = "step_completed"
    STEP_FAILED = "step_failed"
    JOURNEY_COMPLETED = "journey_completed"


class SessionStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    DROPPED_OFF = "dropped_off"  # set by the drop-off detector (Phase 2)
    COMPLETED = "completed"


@dataclass(frozen=True)
class SessionState:
    """The part of an onboarding session that events are allowed to change."""

    current_step: Step
    status: SessionStatus
    last_activity_at: datetime
    last_error_code: str | None = None
    failure_count: int = 0
    completed_at: datetime | None = None


@dataclass(frozen=True)
class IncomingEvent:
    type: EventType
    step: Step | None
    occurred_at: datetime
    error_code: str | None = None


def next_step(step: Step) -> Step:
    i = STEP_INDEX[step]
    return STEP_ORDER[min(i + 1, len(STEP_ORDER) - 1)]


def position_after(event: IncomingEvent) -> Step:
    """Where the user stands once this event has happened."""
    if event.type == EventType.JOURNEY_COMPLETED:
        return Step.COMPLETED
    assert event.step is not None
    if event.type == EventType.STEP_COMPLETED:
        return next_step(event.step)  # finished PAN -> now on Aadhaar OTP
    return event.step  # started or failed: still on that step


def apply_event(state: SessionState, event: IncomingEvent) -> SessionState:
    """Return the new session state after one event.

    Rules (each one handles a real-world edge case):
    1. Completed is terminal. A late event can't reopen a finished journey.
    2. Progress only moves forward. Events can arrive out of order (mobile apps batch
       and retry), so an old "started PAN" arriving after "completed Aadhaar" must
       not drag the user backwards.
    3. last_activity_at is the max ever seen, never simply the latest one received.
    4. Any new activity on a dropped-off session means the user came back.
    5. Failures are counted even when they're old: three OTP failures is a signal that
       the agent should mention ("having trouble with the OTP?").
    """
    if state.status == SessionStatus.COMPLETED:
        return state

    new_position = position_after(event)
    moved_forward = STEP_INDEX[new_position] > STEP_INDEX[state.current_step]
    is_newer = event.occurred_at >= state.last_activity_at

    updated = replace(
        state,
        last_activity_at=max(state.last_activity_at, event.occurred_at),
    )

    if moved_forward:
        updated = replace(updated, current_step=new_position, last_error_code=None)

    if event.type == EventType.STEP_FAILED:
        updated = replace(updated, failure_count=state.failure_count + 1)
        # Only a failure on the step the user is currently on is "the current problem".
        if event.step == updated.current_step and is_newer:
            updated = replace(updated, last_error_code=event.error_code)

    if new_position == Step.COMPLETED:
        return replace(
            updated,
            status=SessionStatus.COMPLETED,
            current_step=Step.COMPLETED,
            completed_at=event.occurred_at,
        )

    if state.status == SessionStatus.DROPPED_OFF and is_newer:
        updated = replace(updated, status=SessionStatus.IN_PROGRESS)

    return updated
