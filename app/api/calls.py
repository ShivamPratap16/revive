import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Header, Query, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.errors import ApiError
from app.models import Call, CallAnalysis, CallWebhookEvent
from app.security import SessionDep, TenantDep
from app.services.calls import process_webhook

webhooks = APIRouter(prefix="/webhooks", tags=["webhooks"])
router = APIRouter(prefix="/v1/calls", tags=["calls"])


@webhooks.post("/voice/{provider}", summary="Receive call events from the voice provider")
async def voice_webhook(
    provider: str,
    request: Request,
    db: SessionDep,
    x_voice_signature: Annotated[str | None, Header()] = None,
) -> dict:
    # No API key here: the provider proves who it is with the HMAC signature instead.
    # We read the RAW body, because the signature covers the exact bytes that were sent.
    raw = await request.body()
    return await process_webhook(db, provider, raw, x_voice_signature, datetime.now(UTC))


class CallOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    attempt_id: uuid.UUID
    session_id: uuid.UUID
    provider: str
    provider_call_id: str | None
    status: str
    placed_at: datetime | None
    answered_at: datetime | None
    ended_at: datetime | None
    duration_seconds: int | None
    end_reason: str | None
    language: str | None


class WebhookEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    provider_event_id: str
    type: str
    occurred_at: datetime
    received_at: datetime
    effect: str | None


class AnalysisOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    status: str
    source: str | None
    model: str | None
    prompt_version: str | None
    result: dict | None
    redactions: dict | None
    guardrail_notes: list | None
    actions: list | None
    latency_ms: int | None
    tries: int
    last_error: str | None


class CallDetailOut(CallOut):
    transcript: list | None
    recording_url: str | None
    webhook_events: list[WebhookEventOut]
    analysis: AnalysisOut | None


@router.get("", response_model=list[CallOut], summary="List calls, newest first")
async def list_calls(
    tenant: TenantDep,
    db: SessionDep,
    session_id: uuid.UUID | None = None,
    status: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[CallOut]:
    q = select(Call).where(Call.tenant_id == tenant.id)
    if session_id:
        q = q.where(Call.session_id == session_id)
    if status:
        q = q.where(Call.status == status)
    q = q.order_by(Call.created_at.desc()).limit(limit)
    return [CallOut.model_validate(c) for c in await db.scalars(q)]


@router.get("/{call_id}", response_model=CallDetailOut, summary="One call with transcript")
async def get_call(call_id: uuid.UUID, tenant: TenantDep, db: SessionDep) -> CallDetailOut:
    call = await db.scalar(select(Call).where(Call.id == call_id, Call.tenant_id == tenant.id))
    if call is None:
        raise ApiError(404, "call_not_found", "No such call")
    events = await db.scalars(
        select(CallWebhookEvent)
        .where(CallWebhookEvent.call_id == call.id)
        .order_by(CallWebhookEvent.received_at)
    )
    analysis = await db.scalar(select(CallAnalysis).where(CallAnalysis.call_id == call.id))
    return CallDetailOut(
        **CallOut.model_validate(call).model_dump(),
        transcript=call.transcript,
        recording_url=call.recording_url,
        webhook_events=[WebhookEventOut.model_validate(e) for e in events],
        analysis=AnalysisOut.model_validate(analysis) if analysis else None,
    )
