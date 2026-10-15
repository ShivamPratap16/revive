"""The numbers a fintech pays for: where users drop off, how many we bring back, and why.

Everything is aggregated inside Postgres (GROUP BY, COUNT ... FILTER, percentile_cont).
Pulling raw rows into Python to count them would move thousands of rows over the network
to produce a dozen numbers. The database is built for exactly this, next to the data.
"""

import uuid
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel
from sqlalchemy import Float, and_, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.journey import STEP_INDEX, STEP_ORDER, Step
from app.models import Call, CallAnalysis, Customer, OnboardingSession, OutreachAttempt


class FunnelStep(BaseModel):
    step: str
    reached: int
    pct_of_started: float


class StepDropoff(BaseModel):
    step: str
    dropped: int
    recovered: int
    recovery_rate: float


class Bucket(BaseModel):
    key: str
    count: int


class LanguageRow(BaseModel):
    language: str
    dropped: int
    recovered: int
    recovery_rate: float


class SessionStats(BaseModel):
    started: int
    completed: int
    completed_without_help: int
    dropped_off: int
    recovered: int
    recovery_rate: float
    median_minutes_to_recover: float | None


class CallStats(BaseModel):
    placed: int
    answered: int
    answer_rate: float
    avg_duration_seconds: float | None
    by_status: list[Bucket]


class InsightStats(BaseModel):
    analysed: int
    top_reasons: list[Bucket]
    outcomes: list[Bucket]
    do_not_call_requests: int
    callbacks_requested: int
    by_source: list[Bucket]
    avg_llm_latency_ms: float | None


class Summary(BaseModel):
    period_days: int
    since: datetime
    sessions: SessionStats
    funnel: list[FunnelStep]
    dropoffs_by_step: list[StepDropoff]
    recovered_by_channel: list[Bucket]
    by_language: list[LanguageRow]
    calls: CallStats
    insights: InsightStats
    dead_letter: int


def _rate(part: int, whole: int) -> float:
    return round(part / whole, 4) if whole else 0.0


async def summary(db: AsyncSession, tenant_id: uuid.UUID, days: int) -> Summary:
    since = datetime.now(UTC) - timedelta(days=days)
    S = OnboardingSession
    in_period = and_(S.tenant_id == tenant_id, S.started_at >= since)
    recovered = S.recovered_at.is_not(None)

    # --- sessions: one pass over the table, several counts at once (COUNT ... FILTER) ---
    row = (
        await db.execute(
            select(
                func.count(),
                func.count().filter(S.status == "completed"),
                func.count().filter(and_(S.status == "completed", S.drop_round == 0)),
                func.count().filter(S.drop_round > 0),
                func.count().filter(recovered),
                # Median, not average: one user who came back after 3 days would drag an
                # average far away from the typical case.
                func.percentile_cont(0.5)
                .within_group(func.extract("epoch", S.recovered_at - S.dropped_off_at))
                .filter(recovered),
            ).where(in_period)
        )
    ).one()
    started, completed, unaided, dropped, n_recovered, median_s = row
    sessions = SessionStats(
        started=started,
        completed=completed,
        completed_without_help=unaided,
        dropped_off=dropped,
        recovered=n_recovered,
        recovery_rate=_rate(n_recovered, dropped),
        median_minutes_to_recover=round(median_s / 60, 1) if median_s is not None else None,
    )

    # --- funnel: how many sessions got at least as far as each step ---
    by_step = dict(
        (await db.execute(select(S.current_step, func.count()).where(in_period)
                          .group_by(S.current_step))).all()
    )  # fmt: skip
    funnel = []
    for step in STEP_ORDER:
        reached = sum(n for s, n in by_step.items() if STEP_INDEX[Step(s)] >= STEP_INDEX[step])
        funnel.append(
            FunnelStep(step=step, reached=reached, pct_of_started=_rate(reached, started))
        )

    # --- where people drop, and how often outreach brings them back ---
    drop_rows = await db.execute(
        select(S.dropped_at_step, func.count(), func.count().filter(recovered))
        .where(in_period, S.dropped_at_step.is_not(None))
        .group_by(S.dropped_at_step)
    )
    order = {s.value: i for i, s in enumerate(STEP_ORDER)}
    dropoffs = sorted(
        (StepDropoff(step=s, dropped=d, recovered=r, recovery_rate=_rate(r, d))
         for s, d, r in drop_rows),
        key=lambda x: order.get(x.step, 99),
    )  # fmt: skip

    # --- which channel gets the credit (last-touch attribution from Phase 2) ---
    A = OutreachAttempt
    channel_rows = await db.execute(
        select(A.channel, func.count())
        .join(S, S.recovered_by_attempt_id == A.id)
        .where(in_period)
        .group_by(A.channel)
        .order_by(func.count().desc())
    )
    by_channel = [Bucket(key=c, count=n) for c, n in channel_rows]

    # --- recovery by the user's language ---
    lang = func.coalesce(Customer.preferred_language, "unknown")
    lang_rows = await db.execute(
        select(lang, func.count(), func.count().filter(recovered))
        .join(Customer, Customer.id == S.customer_id)
        .where(in_period, S.drop_round > 0)
        .group_by(lang)
        .order_by(func.count().desc())
    )
    by_language = [LanguageRow(language=lg, dropped=d, recovered=r, recovery_rate=_rate(r, d))
                   for lg, d, r in lang_rows]  # fmt: skip

    # --- calls ---
    call_filter = and_(Call.tenant_id == tenant_id, Call.created_at >= since,
                       Call.placed_at.is_not(None))  # fmt: skip
    placed, answered, avg_duration = (
        await db.execute(
            select(
                func.count(),
                func.count().filter(Call.answered_at.is_not(None)),
                func.avg(Call.duration_seconds).filter(Call.status == "completed"),
            ).where(call_filter)
        )
    ).one()
    status_rows = await db.execute(
        select(Call.status, func.count()).where(call_filter).group_by(Call.status)
        .order_by(func.count().desc())
    )  # fmt: skip
    calls = CallStats(
        placed=placed,
        answered=answered,
        answer_rate=_rate(answered, placed),
        avg_duration_seconds=round(float(avg_duration), 1) if avg_duration else None,
        by_status=[Bucket(key=s, count=n) for s, n in status_rows],
    )

    # --- what the calls told us (JSONB fields read with ->>) ---
    CA = CallAnalysis
    done = and_(CA.tenant_id == tenant_id, CA.created_at >= since, CA.status == "done")
    reason = CA.result["dropoff_reason"].astext
    outcome = CA.result["outcome"].astext

    async def buckets(col) -> list[Bucket]:
        rows = await db.execute(select(col, func.count()).where(done).group_by(col)
                                .order_by(func.count().desc()))  # fmt: skip
        return [Bucket(key=k or "none", count=n) for k, n in rows]

    analysed, dnc, callbacks, llm_latency = (
        await db.execute(
            select(
                func.count(),
                func.count().filter(CA.result["do_not_call"].astext == "true"),
                func.count().filter(CA.result["callback_requested_at"].astext.is_not(None)),
                func.avg(cast(CA.latency_ms, Float)).filter(CA.source == "llm"),
            ).where(done)
        )
    ).one()
    insights = InsightStats(
        analysed=analysed,
        top_reasons=await buckets(reason),
        outcomes=await buckets(outcome),
        do_not_call_requests=dnc,
        callbacks_requested=callbacks,
        by_source=await buckets(CA.source),
        avg_llm_latency_ms=round(llm_latency) if llm_latency else None,
    )

    dead = await db.scalar(
        select(func.count()).where(
            A.tenant_id == tenant_id, A.status == "dead", A.created_at >= since
        )  # fmt: skip
    )

    return Summary(
        period_days=days,
        since=since,
        sessions=sessions,
        funnel=funnel,
        dropoffs_by_step=dropoffs,
        recovered_by_channel=by_channel,
        by_language=by_language,
        calls=calls,
        insights=insights,
        dead_letter=dead,
    )
