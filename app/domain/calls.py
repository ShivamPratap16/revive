"""The voice call state machine. Pure functions, no database.

A call's status comes from provider webhooks, and webhooks are messy:
  * duplicates: providers retry until they get a 2xx, so the same event can arrive twice
  * out of order: "completed" can overtake "answered" (different servers, network paths)
  * concurrent: several events for one call can arrive in the same millisecond

So, as with the KYC journey in Phase 1: status only moves forward, terminal states are
final, and details from late events (like the answer time) are still kept.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum


class CallStatus(StrEnum):
    INITIATING = "initiating"  # our row exists; the provider hasn't confirmed yet
    QUEUED = "queued"
    RINGING = "ringing"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"  # answered and ended normally
    NO_ANSWER = "no_answer"
    BUSY = "busy"
    FAILED = "failed"


TERMINAL = frozenset(
    {CallStatus.COMPLETED, CallStatus.NO_ANSWER, CallStatus.BUSY, CallStatus.FAILED}
)
NOT_REACHED = frozenset({CallStatus.NO_ANSWER, CallStatus.BUSY, CallStatus.FAILED})

RANK = {
    CallStatus.INITIATING: 0,
    CallStatus.QUEUED: 1,
    CallStatus.RINGING: 2,
    CallStatus.IN_PROGRESS: 3,
    **{s: 4 for s in TERMINAL},
}


class CallEventType(StrEnum):
    QUEUED = "call.queued"
    RINGING = "call.ringing"
    ANSWERED = "call.answered"
    COMPLETED = "call.completed"
    NO_ANSWER = "call.no_answer"
    BUSY = "call.busy"
    FAILED = "call.failed"
    # "Enrichment" events: they add data to the call but don't change its status.
    TRANSCRIPT_READY = "call.transcript_ready"
    RECORDING_READY = "call.recording_ready"


STATUS_FOR_EVENT = {
    CallEventType.QUEUED: CallStatus.QUEUED,
    CallEventType.RINGING: CallStatus.RINGING,
    CallEventType.ANSWERED: CallStatus.IN_PROGRESS,
    CallEventType.COMPLETED: CallStatus.COMPLETED,
    CallEventType.NO_ANSWER: CallStatus.NO_ANSWER,
    CallEventType.BUSY: CallStatus.BUSY,
    CallEventType.FAILED: CallStatus.FAILED,
}


@dataclass(frozen=True)
class CallState:
    status: CallStatus
    answered_at: datetime | None = None
    ended_at: datetime | None = None
    duration_seconds: int | None = None
    end_reason: str | None = None


def apply_call_event(
    state: CallState, event: CallEventType, occurred_at: datetime, data: dict
) -> CallState:
    target = STATUS_FOR_EVENT.get(event)
    if target is None:
        return state  # enrichment events are stored by the service, not here

    new = state
    # Facts are recorded even when the status can't change any more. A late
    # "answered" after "completed" still tells us when the user picked up.
    if event == CallEventType.ANSWERED and new.answered_at is None:
        new = replace(new, answered_at=occurred_at)
    if event == CallEventType.COMPLETED:
        duration = data.get("duration_seconds")
        new = replace(new, duration_seconds=duration)
        if new.answered_at is None and isinstance(duration, int):
            # "answered" hasn't arrived (yet): infer it from the duration.
            new = replace(new, answered_at=occurred_at - timedelta(seconds=duration))

    if state.status in TERMINAL:
        return new  # terminal is final: a duplicate "failed" after "completed" changes nothing

    if RANK[target] > RANK[state.status]:
        new = replace(new, status=target)
        if target in TERMINAL:
            new = replace(new, ended_at=occurred_at, end_reason=data.get("reason"))
    return new
