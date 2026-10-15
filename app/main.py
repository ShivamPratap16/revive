import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from app.api import analytics, calls, demo, events, health, outreach, sessions
from app.config import get_settings
from app.db import engine
from app.errors import register_error_handlers
from app.logging_setup import RequestContextMiddleware, configure_logging


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Code before `yield` runs at startup, code after it at shutdown.
    worker, task = None, None
    if get_settings().run_worker_in_process:
        # All-in-one mode: run the arq worker as a background task in this process.
        # handle_signals=False: the web server owns SIGTERM/SIGINT, not the worker.
        from arq.worker import create_worker

        from app.worker import WorkerSettings

        worker = create_worker(WorkerSettings, handle_signals=False)
        task = asyncio.create_task(worker.async_run())
        logging.getLogger("revive").info("worker running in-process")
    yield
    if task is not None:
        # Cancel rather than worker.close(): close() raises SIGUSR1, which Windows lacks.
        # Anything interrupted mid-delivery is safe: its lease expires and it's re-claimed.
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        with suppress(Exception):
            await worker.pool.close(close_connection_pool=True)
    await engine.dispose()  # close pooled DB connections cleanly


def create_app() -> FastAPI:
    configure_logging(get_settings().log_level)
    app = FastAPI(
        title="Revive",
        version="0.1.0",
        description="Recovers users who drop off during fintech KYC onboarding.",
        lifespan=lifespan,
    )
    app.add_middleware(RequestContextMiddleware)
    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(events.router)
    app.include_router(sessions.router)
    app.include_router(outreach.router)
    app.include_router(calls.router)
    app.include_router(calls.webhooks)
    app.include_router(analytics.router)
    app.include_router(demo.router)

    if get_settings().mount_simulator:
        # All-in-one mode: the fake voice provider lives at /sim on the same server.
        from simulator.main import app as simulator_app

        app.mount("/sim", simulator_app)

    static = Path(__file__).parent / "static"

    @app.get("/", include_in_schema=False)
    async def _home() -> FileResponse:
        # Landing page: what Revive is, live demo numbers, and a "watch it work" button.
        return FileResponse(static / "index.html")

    @app.get("/dashboard", include_in_schema=False)
    async def _dashboard() -> FileResponse:
        # Shows the public demo by default; paste a tenant key to see your own numbers.
        return FileResponse(static / "dashboard.html")

    return app


app = create_app()
