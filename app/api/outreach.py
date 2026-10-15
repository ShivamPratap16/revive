"""Tenant-facing controls for outreach: policy, do-not-disturb list, and the dead-letter queue."""

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert

from app.domain.policy import Channel, OutreachPolicy, policy_of
from app.domain.scheduling import AttemptStatus
from app.errors import ApiError
from app.logging_setup import mask_phone
from app.models import Customer, DndEntry, OutreachAttempt
from app.schemas import AttemptOut, DndIn, DndOut, Phone
from app.security import SessionDep, TenantDep

router = APIRouter(prefix="/v1", tags=["outreach"])


# ---- Policy -------------------------------------------------------------------------


@router.get("/policy", response_model=OutreachPolicy, summary="Current outreach rules")
async def get_policy(tenant: TenantDep) -> OutreachPolicy:
    return policy_of(tenant.policy)


@router.put("/policy", response_model=OutreachPolicy, summary="Replace outreach rules")
async def put_policy(body: OutreachPolicy, tenant: TenantDep, db: SessionDep) -> OutreachPolicy:
    # mode="json" turns times, enums and sets into plain JSON values for the JSONB column.
    tenant.policy = body.model_dump(mode="json")
    await db.commit()
    return body


# ---- Do-not-disturb -----------------------------------------------------------------


@router.post("/dnd", response_model=DndOut, status_code=201, summary="Never call this number")
async def add_dnd(body: DndIn, tenant: TenantDep, db: SessionDep) -> DndOut:
    await db.execute(
        insert(DndEntry)
        .values(id=uuid.uuid4(), tenant_id=tenant.id, phone=body.phone, reason=body.reason)
        .on_conflict_do_nothing(index_elements=["tenant_id", "phone"])  # adding twice is fine
    )
    # Take effect immediately: skip calls already scheduled for this number. (Delivery
    # re-checks DND anyway, but this keeps the visible state honest right away.)
    customer_ids = select(Customer.id).where(
        Customer.tenant_id == tenant.id, Customer.phone == body.phone
    )
    await db.execute(
        update(OutreachAttempt)
        .where(
            OutreachAttempt.customer_id.in_(customer_ids.scalar_subquery()),
            OutreachAttempt.channel == Channel.VOICE,
            OutreachAttempt.status == AttemptStatus.SCHEDULED,
        )
        .values(status=AttemptStatus.SKIPPED, status_reason="dnd")
    )
    await db.commit()
    entry = await db.scalar(
        select(DndEntry).where(DndEntry.tenant_id == tenant.id, DndEntry.phone == body.phone)
    )
    return DndOut(
        phone_masked=mask_phone(entry.phone), reason=entry.reason, created_at=entry.created_at
    )


@router.delete("/dnd/{phone}", status_code=204, summary="Allow calls to this number again")
async def remove_dnd(phone: Phone, tenant: TenantDep, db: SessionDep) -> None:
    result = await db.execute(
        delete(DndEntry).where(DndEntry.tenant_id == tenant.id, DndEntry.phone == phone)
    )
    await db.commit()
    if result.rowcount == 0:
        raise ApiError(404, "not_in_dnd", "That number is not on the DND list")


# ---- Attempts and the dead-letter queue ---------------------------------------------


@router.get("/outreach", response_model=list[AttemptOut], summary="List outreach attempts")
async def list_attempts(
    tenant: TenantDep,
    db: SessionDep,
    status: AttemptStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[AttemptOut]:
    """`?status=dead` is the dead-letter queue: attempts we gave up on, with the last error."""
    q = select(OutreachAttempt).where(OutreachAttempt.tenant_id == tenant.id)
    if status:
        q = q.where(OutreachAttempt.status == status)
    q = q.order_by(OutreachAttempt.scheduled_for.desc()).limit(limit)
    return [AttemptOut.model_validate(a) for a in await db.scalars(q)]


@router.post(
    "/outreach/{attempt_id}/retry",
    response_model=AttemptOut,
    summary="Put a dead attempt back in the queue",
)
async def retry_attempt(attempt_id: uuid.UUID, tenant: TenantDep, db: SessionDep) -> AttemptOut:
    """After fixing the cause (provider back up, number corrected), an operator replays it.
    It still goes through every delivery check, so a user who finished KYC meanwhile
    won't be called."""
    attempt = await db.scalar(
        update(OutreachAttempt)
        .where(
            OutreachAttempt.id == attempt_id,
            OutreachAttempt.tenant_id == tenant.id,
            OutreachAttempt.status == AttemptStatus.DEAD,
        )
        .values(
            status=AttemptStatus.SCHEDULED,
            status_reason="manual_retry",
            tries=0,
            scheduled_for=datetime.now(UTC),
        )
        .returning(OutreachAttempt)
    )
    await db.commit()
    if attempt is None:
        raise ApiError(404, "not_retryable", "No dead attempt with that id")
    return AttemptOut.model_validate(attempt)
