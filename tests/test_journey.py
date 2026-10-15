"""Unit tests for the pure journey rules. No database, so they run in milliseconds."""

from datetime import UTC, datetime, timedelta

from app.domain.journey import (
    EventType,
    IncomingEvent,
    SessionState,
    SessionStatus,
    Step,
    apply_event,
)

T0 = datetime(2026, 10, 15, 10, 0, tzinfo=UTC)


def at(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


def fresh() -> SessionState:
    return SessionState(Step.SIGNUP, SessionStatus.IN_PROGRESS, T0)


def ev(type_: EventType, step: Step | None, minute: int, error: str | None = None):
    return IncomingEvent(type_, step, at(minute), error)


def test_completing_a_step_moves_to_the_next_one():
    s = apply_event(fresh(), ev(EventType.STEP_COMPLETED, Step.PAN_VERIFICATION, 1))
    assert s.current_step == Step.AADHAAR_OTP


def test_out_of_order_event_never_moves_user_backwards():
    s = apply_event(fresh(), ev(EventType.STEP_COMPLETED, Step.AADHAAR_OTP, 5))
    # An older "started PAN" event arrives late
    s = apply_event(s, ev(EventType.STEP_STARTED, Step.PAN_VERIFICATION, 2))
    assert s.current_step == Step.VIDEO_KYC
    assert s.last_activity_at == at(5)  # max timestamp is kept


def test_failure_records_error_and_count():
    s = apply_event(fresh(), ev(EventType.STEP_STARTED, Step.AADHAAR_OTP, 1))
    s = apply_event(s, ev(EventType.STEP_FAILED, Step.AADHAAR_OTP, 2, "OTP_TIMEOUT"))
    s = apply_event(s, ev(EventType.STEP_FAILED, Step.AADHAAR_OTP, 3, "OTP_INVALID"))
    assert s.failure_count == 2
    assert s.last_error_code == "OTP_INVALID"


def test_moving_forward_clears_the_old_error():
    s = apply_event(fresh(), ev(EventType.STEP_FAILED, Step.AADHAAR_OTP, 1, "OTP_TIMEOUT"))
    s = apply_event(s, ev(EventType.STEP_COMPLETED, Step.AADHAAR_OTP, 2))
    assert s.last_error_code is None


def test_late_failure_for_an_earlier_step_is_not_the_current_problem():
    s = apply_event(fresh(), ev(EventType.STEP_STARTED, Step.VIDEO_KYC, 5))
    s = apply_event(s, ev(EventType.STEP_FAILED, Step.PAN_VERIFICATION, 1, "PAN_MISMATCH"))
    assert s.last_error_code is None
    assert s.failure_count == 1  # still counted


def test_completed_is_terminal():
    s = apply_event(fresh(), ev(EventType.JOURNEY_COMPLETED, None, 10))
    assert s.status == SessionStatus.COMPLETED
    after = apply_event(s, ev(EventType.STEP_FAILED, Step.BANK_LINKING, 11, "X"))
    assert after == s


def test_completing_last_step_completes_journey():
    s = apply_event(fresh(), ev(EventType.STEP_COMPLETED, Step.BANK_LINKING, 3))
    assert s.status == SessionStatus.COMPLETED
    assert s.completed_at == at(3)


def test_new_activity_resumes_a_dropped_off_session():
    dropped = SessionState(Step.VIDEO_KYC, SessionStatus.DROPPED_OFF, at(10))
    s = apply_event(dropped, ev(EventType.STEP_STARTED, Step.VIDEO_KYC, 60))
    assert s.status == SessionStatus.IN_PROGRESS


def test_stale_event_does_not_resume_a_dropped_off_session():
    dropped = SessionState(Step.VIDEO_KYC, SessionStatus.DROPPED_OFF, at(10))
    s = apply_event(dropped, ev(EventType.STEP_STARTED, Step.VIDEO_KYC, 5))
    assert s.status == SessionStatus.DROPPED_OFF
