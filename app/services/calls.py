"""Handling voice provider webhooks.

    verify signature  ->  parse  ->  lock the call row  ->  store event (inbox, dedupe)
      ->  apply to state machine  ->  side effects  ->  commit  ->  200

Rules webhook receivers live by:
  * Verify before trusting anything in the body.
  * Be idempotent: the provider WILL send some events more than once.
  * Answer fast. Providers time out after a few seconds and retry, so anything slow
    (like the Gemini analysis in Phase 4) goes to the worker queue, not into this request.
  * 2xx means "got it, stop retrying". Return it for duplicates too. Errors are for
    "not genuine" (401) or "can't process" (4xx); a 5xx makes the provider retry later.
"""

import logging
import uuid
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.domain.calls import (
    NOT_REACHED,
    TERMINAL,
    CallEventType,
    CallState,
    CallStatus,
    apply_call_event,
)
from app.domain.journey import SessionStatus
from app.domain.policy import Channel, policy_of
from app.domain.scheduling import AttemptStatus, next_allowed_time, window_for
from app.errors import ApiError
from app.models import Call, CallAnalysis, CallWebhookEvent, OutreachAttempt, Tenant
from app.schemas import AwareDatetime
from app.webhook_security import WebhookSignatureError, verify

log = logging.getLogger(__name__)


class WebhookCallRef(BaseModel):
    id: str = Field(min_length=1, max_length=128)  # the provider's call id
    client_reference: uuid.UUID  # our Call.id, which we sent when placing the call


class TranscriptTurn(BaseModel):
    speaker: Literal["agent", "user"]
    text: str = Field(max_length=4000)
    start_ms: int = Field(ge=0)


class VoiceWebhook(BaseModel):
    event_id: str = Field(min_length=1, max_length=128)
    type: CallEventType
    occurred_at: AwareDatetime
    call: WebhookCallRef
    data: dict = Field(default_factory=dict)


async def process_webhook(
    db: AsyncSession, provider: str, raw: bytes, signature: str | None, now: datetime
) -> dict:
    settings = get_settings()
    try:
        verify(
            raw, signature, settings.webhook_secret_list, now, settings.webhook_tolerance_seconds
        )
    except WebhookSignatureError as exc:
        log.warning("webhook rejected", extra={"fields": {"provider": provider, "why": str(exc)}})
        raise ApiError(401, "invalid_signature", str(exc)) from exc

    try:
        hook = VoiceWebhook.model_validate_json(raw)
    except ValidationError as exc:
        raise ApiError(
            422,
            "invalid_payload",
            "webhook body is invalid",
            details=exc.errors(include_url=False, include_context=False),
        ) from exc

    # Lock the call: events for the same call often arrive together (ringing, answered,
    # completed within a second) and must be applied one at a time.
    call = await db.scalar(
        select(Call).where(Call.id == hook.call.client_reference).with_for_update()
    )
    if call is None or call.provider != provider:
        raise ApiError(404, "unknown_call", "no call with that client_reference")

    stored = await db.scalar(
        insert(CallWebhookEvent)
        .values(
            id=uuid.uuid4(),
            call_id=call.id,
            provider=provider,
            provider_event_id=hook.event_id,
            type=hook.type,
            occurred_at=hook.occurred_at,
            payload=hook.model_dump(mode="json"),
        )
        .on_conflict_do_nothing(constraint="uq_webhook_provider_event")
        .returning(CallWebhookEvent.id)
    )
    if stored is None:
        await db.rollback()
        log.info("webhook duplicate ignored", extra={"fields": {"event_id": hook.event_id}})
        return {"status": "duplicate"}

    effect = await _apply(db, call, hook, now)
    await db.execute(
        update(CallWebhookEvent).where(CallWebhookEvent.id == stored).values(effect=effect[:200])
    )
    await db.commit()
    log.info(
        "webhook processed",
        extra={"fields": {"call_id": str(call.id), "type": hook.type, "effect": effect}},
    )
    return {"status": "processed", "effect": effect}


async def _apply(db: AsyncSession, call: Call, hook: VoiceWebhook, now: datetime) -> str:
    if call.provider_call_id is None:
        call.provider_call_id = hook.call.id  # webhook beat our own "placed" record

    if hook.type == CallEventType.TRANSCRIPT_READY:
        turns = [TranscriptTurn.model_validate(t) for t in hook.data.get("turns", [])]
        call.transcript = [t.model_dump() for t in turns]
        call.language = hook.data.get("language")
        if not turns:
            return "transcript stored (empty, not analysed)"
        # Queue the analysis in the same transaction (the outbox pattern again). Gemini is
        # far too slow to call inside a webhook request. If the provider re-sends the
        # transcript, the unique call_id keeps it to one analysis.
        await db.execute(
            insert(CallAnalysis)
            .values(
                id=uuid.uuid4(),
                tenant_id=call.tenant_id,
                call_id=call.id,
                status="pending",
                available_at=now,
                tries=0,
            )
            .on_conflict_do_nothing(index_elements=["call_id"])
        )
        return f"transcript stored ({len(turns)} turns); analysis queued"
    if hook.type == CallEventType.RECORDING_READY:
        call.recording_url = hook.data.get("url")
        return "recording stored"

    before = CallStatus(call.status)
    state = apply_call_event(
        CallState(
            status=before,
            answered_at=call.answered_at,
            ended_at=call.ended_at,
            duration_seconds=call.duration_seconds,
            end_reason=call.end_reason,
        ),
        hook.type,
        hook.occurred_at,
        hook.data,
    )
    call.status = state.status
    call.answered_at = state.answered_at
    call.ended_at = state.ended_at
    call.duration_seconds = state.duration_seconds
    call.end_reason = state.end_reason

    if state.status == before:
        return f"no status change (still {before})"
    effect = f"status {before} -> {state.status}"
    if state.status in TERMINAL:
        effect += "; " + await _on_call_ended(db, call, state.status, now)
    return effect


async def _on_call_ended(db: AsyncSession, call: Call, status: CallStatus, now: datetime) -> str:
    attempt = await db.scalar(
        select(OutreachAttempt)
        .where(OutreachAttempt.id == call.attempt_id)
        .with_for_update(of=OutreachAttempt)
    )
    session = attempt.session
    policy = policy_of((await db.get(Tenant, call.tenant_id)).policy)

    if status == CallStatus.COMPLETED:
        # We reached them. Don't ring them again this round: cancel the remaining calls
        # (in-app nudges stay; they're cheap and not intrusive).
        result = await db.execute(
            update(OutreachAttempt)
            .where(
                OutreachAttempt.session_id == attempt.session_id,
                OutreachAttempt.drop_round == attempt.drop_round,
                OutreachAttempt.channel == Channel.VOICE,
                OutreachAttempt.status == AttemptStatus.SCHEDULED,
            )
            .values(status=AttemptStatus.CANCELLED, status_reason="reached_on_call")
        )
        return f"reached user; cancelled {result.rowcount} later call(s)"

    assert status in NOT_REACHED
    still_relevant = (
        session.status == SessionStatus.DROPPED_OFF and session.drop_round == attempt.drop_round
    )
    if not still_relevant:
        return "not redialling: user is no longer dropped off"
    if attempt.status != AttemptStatus.SENT:
        return f"not redialling: attempt is {attempt.status}"
    if attempt.redials >= policy.max_redials:
        return "not redialling: redial limit reached"

    window, days = window_for(Channel.VOICE, policy)
    attempt.status = AttemptStatus.SCHEDULED
    attempt.status_reason = f"redial_after_{status}"
    attempt.redials += 1
    attempt.scheduled_for = next_allowed_time(
        now + timedelta(minutes=policy.redial_after_minutes), window, days, policy.timezone
    )
    return f"redial scheduled for {attempt.scheduled_for.isoformat()}"
