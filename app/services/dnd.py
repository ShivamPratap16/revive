"""Adding a number to the do-not-disturb list. Used by the API (a tenant uploads numbers)
and by call analysis (the user said "don't call me again" on a call)."""

import uuid

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.policy import Channel
from app.domain.scheduling import AttemptStatus
from app.models import Customer, DndEntry, OutreachAttempt


async def add_to_dnd(db: AsyncSession, tenant_id: uuid.UUID, phone: str, reason: str) -> int:
    """Add the number (adding twice is fine) and skip calls already scheduled for it.
    Returns how many scheduled calls were skipped. The caller commits."""
    await db.execute(
        insert(DndEntry)
        .values(id=uuid.uuid4(), tenant_id=tenant_id, phone=phone, reason=reason)
        .on_conflict_do_nothing(index_elements=["tenant_id", "phone"])
    )
    customer_ids = select(Customer.id).where(
        Customer.tenant_id == tenant_id, Customer.phone == phone
    )
    result = await db.execute(
        update(OutreachAttempt)
        .where(
            OutreachAttempt.customer_id.in_(customer_ids.scalar_subquery()),
            OutreachAttempt.channel == Channel.VOICE,
            OutreachAttempt.status == AttemptStatus.SCHEDULED,
        )
        .values(status=AttemptStatus.SKIPPED, status_reason="dnd")
    )
    return result.rowcount
