"""Public demo endpoints, so a visitor can see Revive work without an API key.

Everything here is scoped to ONE tenant, the demo tenant (by name, see settings), and is
read-only except /try, which is rate-limited (per visitor and overall) so the public page
can't be used to burn the Gemini quota or flood the database.
"""

import asyncio
import hashlib
import json
import random
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionFactory
from app.errors import ApiError
from app.logging_setup import mask_phone
from app.models import (
    Call,
    CallAnalysis,
    CallWebhookEvent,
    OnboardingEvent,
    OnboardingSession,
    OutreachAttempt,
    Tenant,
)
from app.schemas import IngestEvent
from app.security import SessionDep
from app.services.analytics import Summary, summary
from app.services.detector import detect_for_tenant
from app.services.events import ingest_batch

router = APIRouter(prefix="/v1/demo", tags=["public demo"])

PERSONAS = [
    ("Ravi Kumar", "hinglish", "aadhaar_otp", "OTP_NOT_RECEIVED"),
    ("Priya Sharma", "hi", "aadhaar_otp", "OTP_TIMEOUT"),
    ("Arjun Mehta", "en", "video_kyc", "VKYC_DROPPED"),
    ("Neha Gupta", "hinglish", "pan_verification", "PAN_MISMATCH"),
    ("Imran Khan", "en", "aadhaar_otp", "OTP_NOT_RECEIVED"),
    ("Kavya Iyer", "hinglish", "video_kyc", "VKYC_AGENT_BUSY"),
]

_redis: Redis | None = None


def _redis_client() -> Redis:
    global _redis
    if _redis is None:
        _redis = Redis.from_url(get_settings().redis_url)
    return _redis


async def _demo_tenant(db) -> Tenant:
    tenant = await db.scalar(
        select(Tenant)
        .where(Tenant.name == get_settings().demo_tenant_name)
        .order_by(Tenant.created_at)
        .limit(1)
    )
    if tenant is None:
        raise ApiError(404, "demo_not_configured", "This server has no public demo tenant")
    return tenant


async def _allow(key: str, limit: int, window_seconds: int) -> bool:
    """Fixed-window counter in Redis: INCR is atomic, so concurrent visitors can't race it."""
    r = _redis_client()
    count = await r.incr(key)
    if count == 1:
        await r.expire(key, window_seconds)
    return count <= limit


def _visitor(request: Request) -> str:
    # Behind Render's proxy, the visitor's address is the first X-Forwarded-For entry.
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "?")


@router.get("/summary", response_model=Summary, summary="Demo tenant analytics (no key)")
async def demo_summary(db: SessionDep, days: int = 30) -> Summary:
    tenant = await _demo_tenant(db)
    return await summary(db, tenant.id, max(1, min(days, 365)))


@router.post("/try", summary="Create one stuck user in the demo tenant and watch it")
async def demo_try(request: Request, db: SessionDep) -> dict:
    settings = get_settings()
    tenant = await _demo_tenant(db)
    hour = datetime.now(UTC).strftime("%Y%m%d%H")
    if not await _allow(f"revive:demo:ip:{_visitor(request)}", settings.demo_try_per_visitor, 600):
        raise ApiError(429, "slow_down", "You've started a few already. Try again in 10 minutes.")
    if not await _allow(f"revive:demo:hour:{hour}", settings.demo_try_per_hour, 3600):
        raise ApiError(429, "demo_busy", "The demo is busy this hour. Try again later.")

    name, language, step, error = random.choice(PERSONAS)
    run = uuid.uuid4().hex[:10]
    # occurred_at 2 minutes ago: with the demo tenant's 1-minute idle limit, the very next
    # detector run (at most a minute away) picks this user up.
    at = datetime.now(UTC) - timedelta(minutes=2)
    event = IngestEvent.model_validate(
        {
            "event_id": f"try-{run}",
            "session_id": f"try-{run}",
            "type": "step_failed",
            "step": step,
            "error_code": error,
            "occurred_at": at.isoformat(),
            "user": {
                "id": f"visitor-{run}",
                # Ends in 1111: the simulator's "always answers" test number, so every
                # visitor sees a real conversation and its AI analysis.
                "phone": f"+9198{random.randint(1000, 9999)}1111",
                "name": name,
                "language": language,
            },
        }
    )
    await ingest_batch(db, tenant, [event])
    # Don't make the visitor wait for the next once-a-minute detector run: run it now.
    # Safe to run alongside the scheduled one: rows are locked with SKIP LOCKED and the
    # outreach plan has a unique constraint, so a session can't be planned twice.
    await detect_for_tenant(tenant.id, tenant.policy, datetime.now(UTC))
    session_id = await db.scalar(
        select(OnboardingSession.id).where(
            OnboardingSession.tenant_id == tenant.id,
            OnboardingSession.external_session_id == f"try-{run}",
        )
    )
    return {"session_id": str(session_id), "name": name, "language": language, "step": step}


async def _session_view(db, tenant_id: uuid.UUID, session_id: uuid.UUID) -> dict | None:
    """One demo session as a timeline of everything that happened to it."""
    s = await db.scalar(
        select(OnboardingSession).where(
            OnboardingSession.id == session_id, OnboardingSession.tenant_id == tenant_id
        )
    )
    if s is None:
        return None

    timeline: list[dict] = []

    def add(at, kind: str, title: str, detail=None):
        if at is not None:
            timeline.append({"at": at, "kind": kind, "title": title, "detail": detail})

    for e in await db.scalars(select(OnboardingEvent).where(OnboardingEvent.session_id == s.id)):
        what = f"{e.type.replace('_', ' ')}: {(e.step or '').replace('_', ' ')}".strip(": ")
        add(e.received_at, "event", f"App reported {what}", e.error_code)
    add(s.dropped_off_at, "detect", "Worker noticed the user is stuck",
        f"idle on {(s.dropped_at_step or '').replace('_', ' ')}; outreach planned")  # fmt: skip

    attempts = list(
        await db.scalars(
            select(OutreachAttempt)
            .where(OutreachAttempt.session_id == s.id)
            .order_by(OutreachAttempt.sequence_no)
        )
    )
    for a in attempts:
        if a.channel == "in_app" and a.sent_at:
            add(a.sent_at, "nudge", "In-app nudge sent", a.message)
        elif a.status in ("cancelled", "skipped"):
            add(a.updated_at, "skip", f"{a.channel.replace('_', '-')} attempt {a.status}",
                (a.status_reason or "").replace("_", " "))  # fmt: skip

    calls = list(await db.scalars(select(Call).where(Call.session_id == s.id)
                                  .order_by(Call.created_at)))  # fmt: skip
    analysis_out = None
    for c in calls:
        attempt = next((a for a in attempts if a.id == c.attempt_id), None)
        add(c.placed_at, "call", "Voice call placed", attempt.message if attempt else None)
        hooks = await db.scalars(select(CallWebhookEvent).where(CallWebhookEvent.call_id == c.id)
                                 .order_by(CallWebhookEvent.received_at))  # fmt: skip
        for h in hooks:
            add(h.received_at, "webhook", f"Signed webhook: {h.type}", h.effect)
        analysis = await db.scalar(select(CallAnalysis).where(CallAnalysis.call_id == c.id))
        if analysis and analysis.status == "done":
            add(analysis.completed_at, "ai",
                f"Call analysed by {'Gemini' if analysis.source == 'llm' else 'rules (fallback)'}",
                None)  # fmt: skip
            analysis_out = {
                "source": analysis.source,
                "latency_ms": analysis.latency_ms,
                "result": analysis.result,
                "actions": analysis.actions,
                "notes": analysis.guardrail_notes,
                "redactions": analysis.redactions,
                "transcript": c.transcript,
            }
            for action in analysis.actions or []:
                add(analysis.completed_at, "action", "Revive acted on it", action)
    add(s.recovered_at, "done", "User completed KYC: recovered", None)

    timeline.sort(key=lambda x: x["at"])
    return {
        "session": {
            "name": s.customer.name,
            "phone": mask_phone(s.customer.phone),
            "language": s.customer.preferred_language,
            "step": s.dropped_at_step or s.current_step,
            "status": s.status,
            "started_at": s.started_at,
        },
        "timeline": timeline,
        "analysis": analysis_out,
        # The latest call, so a viewer can show the conversation as soon as it exists
        "call": None
        if not calls
        else {
            "status": calls[-1].status,
            "transcript": calls[-1].transcript or [],
            "duration_seconds": calls[-1].duration_seconds,
        },
        "finished": analysis_out is not None,
    }


@router.get("/sessions/{session_id}", summary="Everything that happened to one demo session")
async def demo_session(session_id: uuid.UUID, db: SessionDep) -> dict:
    tenant = await _demo_tenant(db)
    view = await _session_view(db, tenant.id, session_id)
    if view is None:
        raise ApiError(404, "session_not_found", "No such demo session")
    return view


@router.get(
    "/sessions/{session_id}/stream",
    summary="The same timeline, pushed live (Server-Sent Events)",
    response_class=StreamingResponse,
)
async def demo_session_stream(session_id: uuid.UUID, db: SessionDep) -> StreamingResponse:
    """Server-Sent Events: one long HTTP response the server keeps writing to. The browser
    gets each new step the moment the worker records it, without polling.

    Each check opens a short-lived DB session, so a slow viewer never holds a connection.
    The stream ends when the analysis is done, or after 6 minutes."""
    tenant = await _demo_tenant(db)
    if await _session_view(db, tenant.id, session_id) is None:
        raise ApiError(404, "session_not_found", "No such demo session")

    async def events():
        last, waited = None, 0.0
        while waited < get_settings().demo_stream_seconds:
            async with SessionFactory() as s:
                view = await _session_view(s, tenant.id, session_id)
            # jsonable_encoder: ISO 8601 timestamps ("2026-10-15T20:40:55Z"), the same as the JSON
            # routes. str(datetime) gives "2026-10-15 20:40:55", which Safari can't parse.
            payload = json.dumps(jsonable_encoder(view))
            digest = hashlib.sha1(payload.encode()).hexdigest()
            if digest != last:  # send only when something changed
                last = digest
                yield f"event: update\ndata: {payload}\n\n"
            if view and view["finished"]:
                yield "event: done\ndata: {}\n\n"
                return
            await asyncio.sleep(1)
            waited += 1
            if int(waited) % 15 == 0:
                yield ": keep-alive\n\n"  # a comment line: stops proxies closing an idle stream

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/calls", summary="Recent analysed calls in the demo tenant")
async def demo_calls(db: SessionDep, limit: int = 25) -> list[dict]:
    tenant = await _demo_tenant(db)
    rows = await db.execute(
        select(Call, CallAnalysis, OnboardingSession)
        .join(CallAnalysis, CallAnalysis.call_id == Call.id)
        .join(OnboardingSession, OnboardingSession.id == Call.session_id)
        .where(Call.tenant_id == tenant.id, CallAnalysis.status == "done")
        .order_by(Call.placed_at.desc())
        .limit(max(1, min(limit, 100)))
    )
    out = []
    for call, analysis, session in rows:
        r = analysis.result or {}
        out.append({
            "id": str(call.id),
            "name": session.customer.name,
            "language": r.get("spoken_language") or session.customer.preferred_language,
            "step": session.dropped_at_step,
            "placed_at": call.placed_at,
            "duration_seconds": call.duration_seconds,
            "outcome": r.get("outcome"),
            "reason": r.get("dropoff_reason"),
            "do_not_call": r.get("do_not_call"),
            "callback_requested_at": r.get("callback_requested_at"),
            "summary": r.get("summary"),
            "source": analysis.source,
            "recovered": session.recovered_at is not None,
        })  # fmt: skip
    return out


@router.get("/calls/{call_id}", summary="One demo call: transcript, analysis, webhooks")
async def demo_call(call_id: uuid.UUID, db: SessionDep) -> dict:
    tenant = await _demo_tenant(db)
    call = await db.scalar(select(Call).where(Call.id == call_id, Call.tenant_id == tenant.id))
    if call is None:
        raise ApiError(404, "call_not_found", "No such demo call")
    analysis = await db.scalar(select(CallAnalysis).where(CallAnalysis.call_id == call.id))
    hooks = await db.scalars(select(CallWebhookEvent).where(CallWebhookEvent.call_id == call.id)
                             .order_by(CallWebhookEvent.received_at))  # fmt: skip
    session = await db.get(OnboardingSession, call.session_id)
    return {
        "id": str(call.id),
        "name": session.customer.name,
        "phone": mask_phone(session.customer.phone),
        "step": session.dropped_at_step,
        "status": call.status,
        "placed_at": call.placed_at,
        "duration_seconds": call.duration_seconds,
        "transcript": call.transcript or [],
        "webhooks": [{"type": h.type, "at": h.received_at, "effect": h.effect} for h in hooks],
        "analysis": None if analysis is None else {
            "source": analysis.source, "model": analysis.model,
            "prompt_version": analysis.prompt_version, "latency_ms": analysis.latency_ms,
            "result": analysis.result, "actions": analysis.actions,
            "notes": analysis.guardrail_notes, "redactions": analysis.redactions,
        },
    }  # fmt: skip
