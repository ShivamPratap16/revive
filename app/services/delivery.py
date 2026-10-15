"""Dispatcher and delivery: getting planned attempts out of the database and to the user.

    claim_due()       scheduled -> sending, with a lease          (one short transaction)
    deliver_attempt() final checks -> call provider -> record     (two short transactions,
                                                                   provider call in between)
    reap_expired()    sending with expired lease -> scheduled     (crash recovery)

Why not hold one transaction open while calling the provider? A voice API can take
seconds. A transaction open that long holds row locks and a pooled connection the whole
time, and under load the pool runs dry and the whole API stalls. So: claim, commit, call
the provider with no transaction open, then record the result in a fresh transaction.
The lease (status "sending" plus an expiry time) keeps other workers away in between.

Delivery is "at least once": if we crash after the provider accepted the message but
before we saved "sent", the reaper will retry it. The idempotency key on every provider
request turns that duplicate into a no-op on the provider's side, so the user hears
from us once.
"""

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels import (
    DeliveryChannel,
    OutboundMessage,
    PermanentChannelError,
    TransientChannelError,
)
from app.config import get_settings
from app.db import SessionFactory
from app.domain.journey import SessionStatus, Step
from app.domain.messages import render
from app.domain.policy import Channel, policy_of
from app.domain.scheduling import Action, AttemptStatus, backoff_seconds, decide
from app.models import Customer, DndEntry, OutreachAttempt, Tenant

log = logging.getLogger(__name__)


async def claim_due(db: AsyncSession, now: datetime, limit: int | None = None) -> list[tuple]:
    """Atomically take ownership of due attempts. Returns (id, lease_expires_at) pairs.

    UPDATE ... WHERE id IN (SELECT ... FOR UPDATE SKIP LOCKED) is the standard Postgres
    job-queue pattern: two dispatchers running this at the same moment get disjoint sets.
    """
    lease_until = now + timedelta(seconds=get_settings().lease_seconds)
    due = (
        select(OutreachAttempt.id)
        .where(
            OutreachAttempt.status == AttemptStatus.SCHEDULED,
            OutreachAttempt.scheduled_for <= now,
        )
        .order_by(OutreachAttempt.scheduled_for)
        .limit(limit or get_settings().dispatch_batch_size)
        .with_for_update(skip_locked=True)
    )
    result = await db.execute(
        update(OutreachAttempt)
        .where(OutreachAttempt.id.in_(due.scalar_subquery()))
        .values(status=AttemptStatus.SENDING, lease_expires_at=lease_until)
        .returning(OutreachAttempt.id, OutreachAttempt.lease_expires_at)
    )
    claimed = [tuple(r) for r in result.all()]
    await db.commit()
    return claimed


async def reap_expired(db: AsyncSession, now: datetime) -> int:
    """Return attempts whose worker died mid-delivery to the queue."""
    result = await db.execute(
        update(OutreachAttempt)
        .where(
            OutreachAttempt.status == AttemptStatus.SENDING,
            OutreachAttempt.lease_expires_at < now,
        )
        .values(
            status=AttemptStatus.SCHEDULED, lease_expires_at=None, status_reason="lease_expired"
        )
    )
    await db.commit()
    if result.rowcount:
        log.warning("reclaimed expired leases", extra={"fields": {"count": result.rowcount}})
    return result.rowcount


async def _prepare(attempt_id: uuid.UUID, now: datetime) -> OutboundMessage | str:
    """Transaction 1: final checks. Returns the message to send, or the outcome if not sending."""
    async with SessionFactory() as db:
        a = await db.get(OutreachAttempt, attempt_id)
        if a is None or a.status != AttemptStatus.SENDING:
            return "not_claimed"  # cancelled meanwhile, or another worker finished it

        s = a.session
        customer: Customer = s.customer
        tenant = await db.get(Tenant, a.tenant_id)
        policy = policy_of(tenant.policy)
        channel = Channel(a.channel)

        is_dnd = bool(
            customer.phone
            and await db.scalar(
                select(DndEntry.id).where(
                    DndEntry.tenant_id == a.tenant_id, DndEntry.phone == customer.phone
                )
            )
        )
        recent = list(
            await db.scalars(
                select(OutreachAttempt.sent_at).where(
                    OutreachAttempt.customer_id == customer.id,
                    OutreachAttempt.channel == Channel.VOICE,
                    OutreachAttempt.status == AttemptStatus.SENT,
                    OutreachAttempt.sent_at > now - timedelta(hours=24),
                )
            )
        )
        d = decide(
            channel=channel,
            attempt_round=a.drop_round,
            session_status=SessionStatus(s.status),
            session_round=s.drop_round,
            phone=customer.phone,
            is_dnd=is_dnd,
            recent_voice_sends=recent,
            policy=policy,
            now=now,
        )

        if d.action != Action.SEND:
            a.lease_expires_at = None
            a.status_reason = d.reason
            a.status = {
                Action.CANCEL: AttemptStatus.CANCELLED,
                Action.SKIP: AttemptStatus.SKIPPED,
                Action.DEFER: AttemptStatus.SCHEDULED,
            }[d.action]
            if d.action == Action.DEFER:
                a.scheduled_for = d.retry_at
            await db.commit()
            return d.action.value

        text = render(
            channel,
            customer.preferred_language,
            Step(s.current_step),
            customer.name,
            s.last_error_code,
        )
        a.message = text
        a.tries += 1
        await db.commit()
        return OutboundMessage(
            attempt_id=a.id,
            channel=channel,
            customer_ref=customer.external_user_id,
            phone=customer.phone,
            language=customer.preferred_language,
            text=text,
        )


async def _record(attempt_id: uuid.UUID, values: dict) -> bool:
    """Transaction 2: save the outcome, but only if we still own the attempt."""
    async with SessionFactory() as db:
        result = await db.execute(
            update(OutreachAttempt)
            .where(
                OutreachAttempt.id == attempt_id,
                # Conditional update: if our lease expired and the reaper handed the attempt
                # to someone else, don't overwrite their state.
                OutreachAttempt.status == AttemptStatus.SENDING,
            )
            .values(lease_expires_at=None, **values)
        )
        await db.commit()
        return result.rowcount == 1


async def deliver_attempt(
    attempt_id: uuid.UUID,
    channels: dict[Channel, DeliveryChannel],
    now: datetime | None = None,
) -> str:
    """Deliver one claimed attempt. Returns what happened (for logs and tests)."""
    now = now or datetime.now(UTC)
    settings = get_settings()

    prepared = await _prepare(attempt_id, now)
    if isinstance(prepared, str):
        return prepared
    msg = prepared

    try:
        # wait_for: a provider that hangs must not hang the worker forever.
        ref = await asyncio.wait_for(
            channels[msg.channel].send(msg), timeout=settings.delivery_timeout_seconds
        )
    except PermanentChannelError as exc:
        await _record(
            attempt_id,
            {
                "status": AttemptStatus.DEAD,
                "last_error": str(exc)[:500],
                "status_reason": "permanent_error",
            },
        )
        log.warning(
            "attempt dead", extra={"fields": {"attempt_id": str(attempt_id), "error": str(exc)}}
        )
        return "dead"
    except (TransientChannelError, TimeoutError) as exc:
        return await _handle_transient(attempt_id, exc, now)

    await _record(
        attempt_id,
        {
            "status": AttemptStatus.SENT,
            "provider_ref": ref,
            "sent_at": now,
            "status_reason": None,
            "last_error": None,
        },
    )
    return "sent"


async def _handle_transient(attempt_id: uuid.UUID, exc: Exception, now: datetime) -> str:
    settings = get_settings()
    async with SessionFactory() as db:
        a = await db.get(OutreachAttempt, attempt_id)
        tenant = await db.get(Tenant, a.tenant_id)
        max_tries = policy_of(tenant.policy).max_delivery_tries
        tries = a.tries
    error = f"{type(exc).__name__}: {exc}"[:500]

    if tries >= max_tries:
        await _record(
            attempt_id,
            {"status": AttemptStatus.DEAD, "last_error": error, "status_reason": "max_tries"},
        )
        log.warning("attempt dead after retries", extra={"fields": {"attempt_id": str(attempt_id)}})
        return "dead"

    delay = backoff_seconds(tries, settings.retry_base_seconds, settings.retry_cap_seconds)
    await _record(
        attempt_id,
        {
            "status": AttemptStatus.SCHEDULED,
            "last_error": error,
            "status_reason": "retrying",
            "scheduled_for": now + timedelta(seconds=delay),
        },
    )
    return "retry"
