import base64
import json
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import and_, or_, select

from app.domain.journey import SessionStatus, Step
from app.errors import ApiError
from app.logging_setup import mask_phone
from app.models import OnboardingEvent, OnboardingSession
from app.schemas import CustomerOut, EventOut, SessionDetailOut, SessionOut
from app.security import SessionDep, TenantDep

router = APIRouter(prefix="/v1/sessions", tags=["sessions"])


class SessionPage(BaseModel):
    items: list[SessionOut]
    next_cursor: str | None


def _to_out(s: OnboardingSession) -> dict:
    return {
        "id": s.id,
        "external_session_id": s.external_session_id,
        "journey": s.journey,
        "current_step": s.current_step,
        "status": s.status,
        "last_error_code": s.last_error_code,
        "failure_count": s.failure_count,
        "started_at": s.started_at,
        "last_activity_at": s.last_activity_at,
        "completed_at": s.completed_at,
        "customer": CustomerOut(
            external_user_id=s.customer.external_user_id,
            name=s.customer.name,
            phone_masked=mask_phone(s.customer.phone),  # never return raw phone numbers
            preferred_language=s.customer.preferred_language,
        ),
    }


# Keyset ("cursor") pagination. OFFSET 10000 makes Postgres read and throw away 10,000
# rows, and rows shift if new ones arrive while you page. Instead the cursor remembers
# the last row's (last_activity_at, id), and the next page starts strictly after it.
def _encode_cursor(s: OnboardingSession) -> str:
    raw = json.dumps([s.last_activity_at.isoformat(), str(s.id)])
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        at, sid = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        return datetime.fromisoformat(at), uuid.UUID(sid)
    except (ValueError, TypeError) as exc:
        raise ApiError(400, "invalid_cursor", "cursor is malformed") from exc


@router.get("", response_model=SessionPage, summary="List onboarding sessions, newest first")
async def list_sessions(
    tenant: TenantDep,
    db: SessionDep,
    status: SessionStatus | None = None,
    step: Step | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: str | None = None,
) -> SessionPage:
    q = select(OnboardingSession).where(OnboardingSession.tenant_id == tenant.id)
    if status:
        q = q.where(OnboardingSession.status == status)
    if step:
        q = q.where(OnboardingSession.current_step == step)
    if cursor:
        at, sid = _decode_cursor(cursor)
        q = q.where(
            or_(
                OnboardingSession.last_activity_at < at,
                and_(OnboardingSession.last_activity_at == at, OnboardingSession.id < sid),
            )
        )
    q = q.order_by(OnboardingSession.last_activity_at.desc(), OnboardingSession.id.desc())
    rows = list(await db.scalars(q.limit(limit + 1)))  # fetch one extra to know if more exist

    has_more = len(rows) > limit
    rows = rows[:limit]
    return SessionPage(
        items=[SessionOut(**_to_out(s)) for s in rows],
        next_cursor=_encode_cursor(rows[-1]) if has_more else None,
    )


@router.get("/{session_id}", response_model=SessionDetailOut, summary="One session with its events")
async def get_session_detail(session_id: uuid.UUID, tenant: TenantDep, db: SessionDep):
    s = await db.scalar(
        select(OnboardingSession).where(
            OnboardingSession.id == session_id,
            # Filtering by tenant here is the multi-tenant safety net: guessing another
            # tenant's session UUID returns 404, exactly as if it didn't exist.
            OnboardingSession.tenant_id == tenant.id,
        )
    )
    if s is None:
        raise ApiError(404, "session_not_found", "No such session")

    events = await db.scalars(
        select(OnboardingEvent)
        .where(OnboardingEvent.session_id == s.id)
        .order_by(OnboardingEvent.occurred_at, OnboardingEvent.received_at)
    )
    return SessionDetailOut(**_to_out(s), events=[EventOut.model_validate(e) for e in events])
