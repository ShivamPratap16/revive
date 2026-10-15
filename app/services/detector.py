"""Drop-off detector: finds users stuck on a step and plans the follow-up.

Runs every minute in the worker. Each run, per tenant:
  1. Lock idle in-progress sessions with FOR UPDATE SKIP LOCKED.
  2. Mark them dropped_off and bump drop_round.
  3. Insert the outreach plan (one row per cadence step).
  4. Commit. Steps 2 and 3 happen together or not at all.

SKIP LOCKED is what lets several workers run this at once safely: a row another worker
(or an incoming event) is holding is simply skipped, instead of waited on. Plain
FOR UPDATE would make workers queue behind each other and process the same sessions.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert

from app.config import get_settings
from app.db import SessionFactory
from app.domain.journey import SessionStatus
from app.domain.policy import OutreachPolicy, policy_of
from app.domain.scheduling import AttemptStatus, plan_attempts
from app.models import DndEntry, OnboardingSession, OutreachAttempt, Tenant

log = logging.getLogger(__name__)


def _idle_condition(policy: OutreachPolicy, now: datetime):
    """WHERE (step = 'aadhaar_otp' AND last_activity_at < now-15m) OR (step = ... ) ..."""
    return or_(
        *(
            and_(
                OnboardingSession.current_step == step,
                OnboardingSession.last_activity_at < now - timedelta(minutes=minutes),
            )
            for step, minutes in policy.idle_minutes.items()
        )
    )


async def detect_for_tenant(tenant_id: uuid.UUID, raw_policy: dict | None, now: datetime) -> int:
    policy = policy_of(raw_policy)
    async with SessionFactory() as db:
        sessions = list(
            await db.scalars(
                select(OnboardingSession)
                .where(
                    OnboardingSession.tenant_id == tenant_id,
                    OnboardingSession.status == SessionStatus.IN_PROGRESS,
                    OnboardingSession.drop_round < policy.max_drop_rounds,
                    _idle_condition(policy, now),
                )
                .order_by(OnboardingSession.last_activity_at)  # longest-waiting first
                .limit(get_settings().detector_batch_size)
                .with_for_update(of=OnboardingSession, skip_locked=True)
            )
        )
        if not sessions:
            return 0

        # One query for all DND numbers in this batch, not one query per session
        # (avoiding the "N+1 queries" problem).
        phones = {s.customer.phone for s in sessions if s.customer.phone}
        dnd = (
            set(
                await db.scalars(
                    select(DndEntry.phone).where(
                        DndEntry.tenant_id == tenant_id, DndEntry.phone.in_(phones)
                    )
                )
            )
            if phones
            else set()
        )

        rows = []
        for s in sessions:
            s.status = SessionStatus.DROPPED_OFF
            s.drop_round += 1
            s.dropped_off_at = now
            phone = s.customer.phone
            for p in plan_attempts(policy=policy, now=now, phone=phone, is_dnd=phone in dnd):
                rows.append(
                    {
                        "id": uuid.uuid4(),
                        "tenant_id": tenant_id,
                        "session_id": s.id,
                        "customer_id": s.customer_id,
                        "drop_round": s.drop_round,
                        "sequence_no": p.sequence_no,
                        "channel": p.channel,
                        "status": AttemptStatus.SKIPPED
                        if p.skip_reason
                        else AttemptStatus.SCHEDULED,
                        "status_reason": p.skip_reason,
                        "scheduled_for": p.scheduled_for,
                        "tries": 0,
                    }
                )

        await db.execute(
            insert(OutreachAttempt)
            .values(rows)
            .on_conflict_do_nothing(constraint="uq_attempt_plan")
        )
        await db.commit()

    log.info(
        "drop-offs detected",
        extra={"fields": {"tenant_id": str(tenant_id), "sessions": len(sessions)}},
    )
    return len(sessions)


async def detect_dropoffs(now: datetime | None = None) -> int:
    """Run the detector for every tenant. Returns how many sessions were marked."""
    now = now or datetime.now(UTC)
    async with SessionFactory() as db:
        tenants = (await db.execute(select(Tenant.id, Tenant.policy))).all()
    total = 0
    for tenant_id, raw_policy in tenants:
        # Each tenant in its own transaction: one tenant's bad data can't block the others.
        try:
            total += await detect_for_tenant(tenant_id, raw_policy, now)
        except Exception:
            log.exception(
                "detector failed for tenant", extra={"fields": {"tenant_id": str(tenant_id)}}
            )
    return total
