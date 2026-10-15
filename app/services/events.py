"""Event ingestion: the most important write path in the system.

Guarantees, and how each one is enforced:
  * Idempotent: a retried event is stored once.
      -> UNIQUE(tenant_id, external_event_id) + INSERT ... ON CONFLICT DO NOTHING
  * Safe under concurrency: two requests touching the same session never lose updates.
      -> SELECT ... FOR UPDATE row lock on the session while we change it
  * No deadlocks between concurrent batches.
      -> rows are always locked in the same order (sorted by external id)
  * Out-of-order events handled correctly.
      -> domain.journey.apply_event only moves forward and keeps the max timestamp
  * All-or-nothing per request: one transaction, committed once at the end.
"""

import logging
import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.domain.journey import (
    EventType,
    IncomingEvent,
    SessionState,
    SessionStatus,
    Step,
    apply_event,
)
from app.models import Customer, OnboardingEvent, OnboardingSession
from app.schemas import EventResult, EventResultStatus, IngestEvent, IngestResponse

log = logging.getLogger(__name__)


def normalise_time(occurred_at: datetime, now: datetime) -> tuple[datetime, bool]:
    """Convert to UTC and clamp timestamps too far in the future (wrong phone clock)."""
    occurred_at = occurred_at.astimezone(UTC)
    max_skew = timedelta(seconds=get_settings().max_clock_skew_seconds)
    if occurred_at > now + max_skew:
        return now, True
    return occurred_at, False


async def _upsert_customers(
    db: AsyncSession, tenant_id: uuid.UUID, events: list[IngestEvent]
) -> dict[str, uuid.UUID]:
    """Create or update every customer in the batch with one SQL statement."""
    # A user can appear in several events of one batch, often with partial details
    # (only the first event carries the phone). Merge them oldest -> newest so that the
    # newest *non-empty* value of each field wins. Postgres also refuses to upsert the
    # same row twice in one statement, so we have to collapse duplicates anyway.
    merged: dict[str, dict] = {}
    for ev in sorted(events, key=lambda e: e.occurred_at):
        row = merged.setdefault(
            ev.user.id,
            {
                "tenant_id": tenant_id,
                "external_user_id": ev.user.id,
                "phone": None,
                "name": None,
                "preferred_language": None,
            },
        )
        for field, value in (
            ("phone", ev.user.phone),
            ("name", ev.user.name),
            ("preferred_language", ev.user.language),
        ):
            if value is not None:
                row[field] = value

    rows = [merged[uid] for uid in sorted(merged)]  # sorted -> consistent lock order
    stmt = insert(Customer).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["tenant_id", "external_user_id"],
        set_={
            # COALESCE: an event that omits a field must not wipe out what we already have.
            "phone": func.coalesce(stmt.excluded.phone, Customer.phone),
            "name": func.coalesce(stmt.excluded.name, Customer.name),
            "preferred_language": func.coalesce(
                stmt.excluded.preferred_language, Customer.preferred_language
            ),
            "updated_at": datetime.now(UTC),
        },
    ).returning(Customer.external_user_id, Customer.id)
    result = await db.execute(stmt)
    return {ext_id: cid for ext_id, cid in result.all()}


async def _lock_session(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    external_session_id: str,
    customer_id: uuid.UUID,
    first_seen: datetime,
) -> OnboardingSession:
    """Get the session row, creating it if needed, and hold a lock on it until commit."""
    await db.execute(
        insert(OnboardingSession)
        .values(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            customer_id=customer_id,
            external_session_id=external_session_id,
            journey="kyc",
            current_step=Step.SIGNUP,
            status=SessionStatus.IN_PROGRESS,
            failure_count=0,
            started_at=first_seen,
            last_activity_at=first_seen,
        )
        # If another request created it a millisecond ago, that's fine: do nothing.
        .on_conflict_do_nothing(index_elements=["tenant_id", "external_session_id"])
    )
    # FOR UPDATE: any other transaction that wants this row now waits for our commit.
    # populate_existing: refresh the object even if this DB session already cached it.
    return await db.scalar(
        select(OnboardingSession)
        .where(
            OnboardingSession.tenant_id == tenant_id,
            OnboardingSession.external_session_id == external_session_id,
        )
        # OF onboarding_sessions: lock only the session row. The customer is loaded via an
        # outer join, and Postgres refuses to lock the "maybe-missing" side of one.
        .with_for_update(of=OnboardingSession)
        .execution_options(populate_existing=True)
    )


def _state_of(s: OnboardingSession) -> SessionState:
    return SessionState(
        current_step=Step(s.current_step),
        status=SessionStatus(s.status),
        last_activity_at=s.last_activity_at,
        last_error_code=s.last_error_code,
        failure_count=s.failure_count,
        completed_at=s.completed_at,
    )


def _save_state(s: OnboardingSession, state: SessionState) -> None:
    s.current_step = state.current_step
    s.status = state.status
    s.last_activity_at = state.last_activity_at
    s.last_error_code = state.last_error_code
    s.failure_count = state.failure_count
    s.completed_at = state.completed_at


async def ingest_batch(
    db: AsyncSession, tenant_id: uuid.UUID, events: list[IngestEvent], now: datetime | None = None
) -> IngestResponse:
    now = now or datetime.now(UTC)
    customer_ids = await _upsert_customers(db, tenant_id, events)

    # Group by session. Within a session, apply events in the order they happened,
    # not the order they arrived. That keeps the state changes easy to follow.
    by_session: dict[str, list[tuple[int, IngestEvent, datetime, bool]]] = defaultdict(list)
    for i, ev in enumerate(events):
        at, corrected = normalise_time(ev.occurred_at, now)
        by_session[ev.session_id].append((i, ev, at, corrected))

    results: list[EventResult | None] = [None] * len(events)

    for ext_session_id in sorted(by_session):  # sorted -> no deadlocks
        items = sorted(by_session[ext_session_id], key=lambda t: t[2])
        first_event = items[0][1]
        session = await _lock_session(
            db, tenant_id, ext_session_id, customer_ids[first_event.user.id], items[0][2]
        )
        state = _state_of(session)

        for index, ev, at, corrected in items:
            inserted_id = await db.scalar(
                insert(OnboardingEvent)
                .values(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    session_id=session.id,
                    external_event_id=ev.event_id,
                    type=ev.type,
                    step=ev.step,
                    error_code=ev.error_code,
                    occurred_at=at,
                    clock_skew_corrected=corrected,
                    metadata=ev.metadata,
                )
                .on_conflict_do_nothing(constraint="uq_events_tenant_event_id")
                .returning(OnboardingEvent.id)
            )
            if inserted_id is None:  # already stored by an earlier request: a retry
                results[index] = EventResult(
                    event_id=ev.event_id, status=EventResultStatus.DUPLICATE
                )
                continue

            state = apply_event(
                state,
                IncomingEvent(
                    type=EventType(ev.type),
                    step=Step(ev.step) if ev.step else None,
                    occurred_at=at,
                    error_code=ev.error_code,
                ),
            )
            session.started_at = min(session.started_at, at)
            results[index] = EventResult(
                event_id=ev.event_id,
                status=EventResultStatus.ACCEPTED,
                clock_skew_corrected=corrected,
            )

        _save_state(session, state)

    await db.commit()

    accepted = sum(r.status == EventResultStatus.ACCEPTED for r in results)
    log.info(
        "events ingested",
        extra={
            "fields": {
                "tenant_id": str(tenant_id),
                "accepted": accepted,
                "duplicates": len(results) - accepted,
            }
        },
    )
    return IngestResponse(accepted=accepted, duplicates=len(results) - accepted, results=results)
