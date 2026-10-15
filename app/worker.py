"""The background worker. Start it with:   arq app.worker.WorkerSettings

arq is an async job queue on Redis. This worker does three things:
  * detect_job    every minute   -> find drop-offs, plan outreach (writes to Postgres)
  * dispatch_job  every 10s      -> reclaim expired leases, claim due attempts,
                                    enqueue one deliver_job per attempt (to Redis)
  * deliver_job   on demand      -> deliver one attempt

Postgres is the source of truth and Redis only carries "go deliver attempt X" messages.
If Redis loses a message, the attempt sits in "sending" until its lease expires, then
the reaper puts it back, and it's delivered on the next tick. Nothing is lost for good.
"""

import logging
import uuid
from datetime import UTC, datetime

from arq import cron
from arq.connections import RedisSettings

from app.channels import default_channels
from app.config import get_settings
from app.db import SessionFactory, engine
from app.logging_setup import configure_logging
from app.services.analysis import (
    build_engine,
    claim_analyses,
    process_analysis,
    reap_analyses,
)
from app.services.delivery import claim_due, deliver_attempt, reap_expired
from app.services.detector import detect_dropoffs

log = logging.getLogger("revive.worker")


async def startup(ctx: dict) -> None:
    configure_logging(get_settings().log_level)
    ctx["channels"] = default_channels()
    # arq's Redis connection doubles as the store for the shared Gemini rate limiter.
    ctx["engine"] = build_engine(ctx["redis"])
    log.info("worker started")


async def shutdown(ctx: dict) -> None:
    await engine.dispose()


async def detect_job(ctx: dict) -> int:
    return await detect_dropoffs()


async def dispatch_job(ctx: dict) -> int:
    now = datetime.now(UTC)
    async with SessionFactory() as db:
        await reap_expired(db, now)
        claimed = await claim_due(db, now)
    for attempt_id, lease in claimed:
        # The job id includes the lease time, so the same claim is never queued twice,
        # but a later re-claim of the same attempt (after a retry) gets a fresh job.
        await ctx["redis"].enqueue_job(
            "deliver_job", str(attempt_id), _job_id=f"deliver:{attempt_id}:{lease.timestamp()}"
        )
    return len(claimed)


async def deliver_job(ctx: dict, attempt_id: str) -> str:
    outcome = await deliver_attempt(uuid.UUID(attempt_id), ctx["channels"])
    log.info("delivery", extra={"fields": {"attempt_id": attempt_id, "outcome": outcome}})
    return outcome


async def analysis_job(ctx: dict) -> int:
    """Analyse finished calls. One at a time on purpose: the rate limiter paces Gemini, and
    a long queue simply drains over the next ticks."""
    now = datetime.now(UTC)
    async with SessionFactory() as db:
        await reap_analyses(db, now)
        claimed = await claim_analyses(db, now, get_settings().analysis_batch_size)
    for analysis_id in claimed:
        await process_analysis(analysis_id, ctx["engine"])
    return len(claimed)


class WorkerSettings:
    functions = [deliver_job]
    cron_jobs = [
        # unique=True (the default): if two workers run, each tick still runs only once.
        cron(detect_job, second={0}, run_at_startup=True),
        cron(dispatch_job, second={0, 10, 20, 30, 40, 50}),
        cron(analysis_job, second={5, 15, 25, 35, 45, 55}),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = 20  # deliveries in flight at once per worker process
    job_timeout = 60
