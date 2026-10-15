"""Call analysis: transcript -> redact -> Gemini (or rules) -> guardrails -> actions.

    webhook "transcript_ready" -> call_analyses row (pending)       [same tx as the webhook]
    worker: claim pending rows (lease) -> process_analysis() one by one

Why a queue and not "call Gemini inside the webhook request"? Gemini can take seconds,
the provider times out webhooks after a few, and the free tier allows ~10 requests a
minute. A queue absorbs bursts (50 calls ending at 6pm) and lets the rate limiter pace them.
"""

import logging
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import SessionFactory
from app.domain.insights import (
    PROMPT_VERSION,
    CallInsight,
    build_prompt,
    finalize,
    parse_callback,
    quote_is_real,
    rule_based_insight,
)
from app.domain.journey import SessionStatus
from app.domain.policy import Channel, policy_of
from app.domain.redaction import redact_turns
from app.domain.scheduling import AttemptStatus, backoff_seconds, next_allowed_time, window_for
from app.llm.gemini import GeminiAnalyzer, LlmInvalidOutput, LlmPermanent, LlmTransient
from app.llm.resilience import CircuitBreaker, DailyQuota, RateLimiter
from app.models import Call, CallAnalysis, OnboardingSession, OutreachAttempt, Tenant
from app.services.dnd import add_to_dnd

log = logging.getLogger(__name__)
LEASE = timedelta(minutes=5)


class RateLimited(Exception):
    def __init__(self, wait: float):
        super().__init__(f"rate limited, retry in {wait:.1f}s")
        self.wait = wait


@dataclass
class EngineResult:
    insight: CallInsight
    source: str  # "llm" | "rules"
    model: str | None = None
    latency_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    notes: list[str] = field(default_factory=list)


class AnalysisEngine:
    """Decides, per call, whether to ask Gemini or fall back to rules, and how to fail."""

    def __init__(
        self,
        llm: GeminiAnalyzer | None,
        limiter: RateLimiter | None,
        quota: DailyQuota | None,
        breaker: CircuitBreaker,
    ):
        self.llm, self.limiter, self.quota, self.breaker = llm, limiter, quota, breaker

    def _rules(self, turns, call_local, notes) -> EngineResult:
        return EngineResult(rule_based_insight(turns, call_local), "rules", notes=notes)

    async def analyze(self, prompt: str, turns: list[dict], call_local: datetime) -> EngineResult:
        notes: list[str] = []
        if self.llm is None:
            return self._rules(turns, call_local, ["no LLM configured"])
        if not self.breaker.allow():
            return self._rules(turns, call_local, ["circuit open: Gemini failing recently"])

        for attempt in (1, 2):
            if self.limiter and (wait := await self.limiter.try_acquire()) > 0:
                if attempt == 1:
                    raise RateLimited(wait)  # come back later; this isn't a failure
                notes.append("rate limited before retry")
                break
            if self.quota and not await self.quota.take():
                notes.append("daily Gemini quota used up")
                break
            try:
                r = await self.llm.analyze(prompt)
            except LlmInvalidOutput as exc:
                # The service works but gave an unusable answer. Not an outage, so the
                # breaker isn't told. Try once more; the second answer is often fine.
                notes.append(f"attempt {attempt}: {exc}"[:200])
                continue
            except LlmPermanent as exc:
                self.breaker.record_failure()
                notes.append(f"permanent error: {exc}"[:200])
                break
            except LlmTransient:
                self.breaker.record_failure()
                raise  # the caller schedules a retry with backoff
            self.breaker.record_success()
            return EngineResult(
                r.insight, "llm", r.model, r.latency_ms, r.input_tokens, r.output_tokens, notes
            )
        return self._rules(turns, call_local, notes)


def build_engine(redis) -> AnalysisEngine:
    s = get_settings()
    breaker = CircuitBreaker(s.breaker_failure_threshold, s.breaker_cooldown_seconds)
    if not s.gemini_api_key:
        log.warning("GEMINI_API_KEY not set: call analysis will use rules only")
        return AnalysisEngine(None, None, None, breaker)
    return AnalysisEngine(
        GeminiAnalyzer(s.gemini_api_key, s.gemini_model, s.gemini_timeout_seconds),
        RateLimiter(redis, "revive:ratelimit:gemini", s.gemini_rpm),
        DailyQuota(redis, "revive:quota:gemini", s.gemini_rpd),
        breaker,
    )


# ---- queue mechanics (same shape as outreach delivery) ------------------------------


async def claim_analyses(db: AsyncSession, now: datetime, limit: int) -> list[uuid.UUID]:
    due = (
        select(CallAnalysis.id)
        .where(CallAnalysis.status == "pending", CallAnalysis.available_at <= now)
        .order_by(CallAnalysis.available_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    result = await db.execute(
        update(CallAnalysis)
        .where(CallAnalysis.id.in_(due.scalar_subquery()))
        .values(status="processing", lease_expires_at=now + LEASE)
        .returning(CallAnalysis.id)
    )
    ids = list(result.scalars())
    await db.commit()
    return ids


async def reap_analyses(db: AsyncSession, now: datetime) -> int:
    result = await db.execute(
        update(CallAnalysis)
        .where(CallAnalysis.status == "processing", CallAnalysis.lease_expires_at < now)
        .values(status="pending", lease_expires_at=None)
    )
    await db.commit()
    return result.rowcount


async def _requeue(analysis_id: uuid.UUID, at: datetime, error: str, count_try: bool) -> None:
    async with SessionFactory() as db:
        values = {
            "status": "pending",
            "available_at": at,
            "lease_expires_at": None,
            "last_error": error[:500],
        }
        if count_try:
            values["tries"] = CallAnalysis.tries + 1
        await db.execute(
            update(CallAnalysis).where(CallAnalysis.id == analysis_id).values(**values)
        )
        await db.commit()


# ---- one analysis -------------------------------------------------------------------


async def process_analysis(
    analysis_id: uuid.UUID, engine: AnalysisEngine, now: datetime | None = None
) -> str:
    now = now or datetime.now(UTC)
    settings = get_settings()

    async with SessionFactory() as db:
        analysis = await db.get(CallAnalysis, analysis_id)
        if analysis is None or analysis.status != "processing":
            return "not_claimed"
        call = await db.get(Call, analysis.call_id)
        session = await db.get(OnboardingSession, call.session_id)
        tenant = await db.get(Tenant, call.tenant_id)
        policy = policy_of(tenant.policy)
        customer = session.customer
        tries = analysis.tries

    # 1. Redact before anything leaves our system.
    names = [n for n in (customer.name or "").split() if n]
    turns, redactions = redact_turns(call.transcript or [], names)
    call_local = (call.answered_at or call.placed_at or now).astimezone(ZoneInfo(policy.timezone))
    prompt = build_prompt(turns, call_local, session.current_step, session.last_error_code)

    # 2. Ask Gemini (or fall back to rules).
    try:
        result = await engine.analyze(prompt, turns, call_local)
    except RateLimited as exc:
        await _requeue(analysis_id, now + timedelta(seconds=exc.wait), str(exc), count_try=False)
        return "rate_limited"
    except LlmTransient as exc:
        if tries + 1 < settings.analysis_max_tries:
            delay = max(backoff_seconds(tries + 1, 15, 600), exc.retry_after or 0)
            await _requeue(analysis_id, now + timedelta(seconds=delay), str(exc), count_try=True)
            return "retry"
        result = EngineResult(
            rule_based_insight(turns, call_local),
            "rules",
            notes=[f"Gemini kept failing ({exc}); used rules"],
        )

    # 3. Guardrails and actions, in one transaction with the stored result.
    async with SessionFactory() as db:
        insight, notes = finalize(result.insight, turns)
        notes, actions = list(result.notes) + notes, []

        if insight.key_quote and not quote_is_real(insight.key_quote, turns):
            notes.append("key_quote not found in transcript: lowered confidence")
            insight = insight.model_copy(update={"confidence": min(insight.confidence, 0.3)})

        # Asymmetric risk: wrongly calling someone who said "stop" is a compliance breach;
        # wrongly not calling someone costs one call. So EITHER the LLM or the rules
        # saying "do not call" is enough.
        rules_dnc = rule_based_insight(turns, call_local).do_not_call
        do_not_call = insight.do_not_call or rules_dnc
        if rules_dnc and not insight.do_not_call:
            notes.append("rules detected a do-not-call request the model missed")

        session = await db.scalar(
            select(OnboardingSession)
            .where(OnboardingSession.id == call.session_id)
            .with_for_update(of=OnboardingSession)
        )
        if do_not_call and customer.phone:
            skipped = await add_to_dnd(db, call.tenant_id, customer.phone, "requested_on_call")
            note = f"; cancelled {skipped} scheduled call(s)" if skipped else ""
            actions.append(f"added number to DND{note}")

        callback_local, problem = parse_callback(
            insight.callback_requested_at, call_local, policy.timezone
        )
        if problem:
            notes.append(f"callback ignored: {problem}")
        elif callback_local and do_not_call:
            notes.append("callback ignored: user asked not to be called")
        elif callback_local:
            actions.append(await _schedule_callback(db, call, session, callback_local, policy))

        await db.execute(
            update(CallAnalysis)
            .where(CallAnalysis.id == analysis_id, CallAnalysis.status == "processing")
            .values(
                status="done",
                source=result.source,
                model=result.model,
                prompt_version=PROMPT_VERSION if result.source == "llm" else "rules-v1",
                result=insight.model_dump(mode="json"),
                redactions=dict(redactions or Counter()),
                guardrail_notes=notes,
                actions=actions,
                latency_ms=result.latency_ms,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                lease_expires_at=None,
                completed_at=now,
            )
        )
        await db.commit()

    log.info(
        "call analysed",
        extra={
            "fields": {
                "call_id": str(call.id),
                "source": result.source,
                "outcome": insight.outcome,
                "reason": insight.dropoff_reason,
                "actions": actions,
            }
        },
    )
    return result.source


async def _schedule_callback(db, call: Call, session, callback_local: datetime, policy) -> str:
    attempt = await db.get(OutreachAttempt, call.attempt_id)
    if session.status != SessionStatus.DROPPED_OFF or session.drop_round != attempt.drop_round:
        return "callback not scheduled: user is no longer dropped off"
    window, days = window_for(Channel.VOICE, policy)
    when = next_allowed_time(callback_local.astimezone(UTC), window, days, policy.timezone)
    next_seq = await db.scalar(
        select(func.max(OutreachAttempt.sequence_no)).where(
            OutreachAttempt.session_id == session.id,
            OutreachAttempt.drop_round == attempt.drop_round,
        )
    )
    db.add(
        OutreachAttempt(
            tenant_id=call.tenant_id,
            session_id=session.id,
            customer_id=call.customer_id,
            drop_round=attempt.drop_round,
            sequence_no=(next_seq or 0) + 1,
            channel=Channel.VOICE,
            status=AttemptStatus.SCHEDULED,
            status_reason="callback_requested",
            scheduled_for=when,
            tries=0,
        )
    )
    return f"callback scheduled for {when.isoformat()}"
