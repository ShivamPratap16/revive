import asyncio
import json
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.api import analytics, calls, demo, events, health, outreach, sessions
from app.config import get_settings
from app.db import engine
from app.errors import ApiError, register_error_handlers
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


API_DESCRIPTION = """
Revive finds users who abandon KYC halfway through a fintech app, reaches them with an
in-app nudge and an AI voice call, understands the conversation with Gemini, and acts on it.

## Quick start

1. **Authorize** with an API key (top right). The public demo key is in the
   [README](https://github.com/ShivamPratap16/revive#live-demo).
2. **Report a user stuck at a step** with `POST /v1/events`. Retries are safe: events are
   idempotent per `event_id`.
3. Within a couple of minutes the worker detects the drop-off, sends a nudge, places a call,
   and analyses it. Follow along with `GET /v1/sessions/{id}` and `GET /v1/calls/{id}`.
4. See the outcome in `GET /v1/analytics/summary`.

No key? The **Public demo** endpoints need none.

## Conventions

* Errors: `{"error": {"code", "message", "request_id", "details"}}`, the same shape everywhere.
* Every response carries an `x-request-id` header matching the server logs.
* Timestamps are ISO 8601 with a time zone; naive timestamps are rejected.
"""

TAGS = [
    {"name": "events", "description": "Report onboarding progress from the fintech's backend."},
    {"name": "sessions", "description": "Where each user is in their KYC journey."},
    {"name": "outreach", "description": "Rules (policy), the do-not-disturb list, attempts and "
     "the dead-letter queue."},
    {"name": "calls", "description": "Voice calls with transcript, webhook timeline and AI "
     "analysis."},
    {"name": "analytics", "description": "Funnel, recovery and call insights."},
    {"name": "webhooks", "description": "Called by the voice provider (HMAC-signed, no API key)."},
    {"name": "public demo", "description": "Read-only views of the demo tenant, and a "
     "rate-limited way to start a live run. No key needed."},
    {"name": "health", "description": "Liveness and readiness probes."},
]  # fmt: skip

STATIC = Path(__file__).parent / "static"


class ImmutableAssets(StaticFiles):
    """Vite puts a content hash in every asset filename (index-Czf_ca9z.js), so a file at a
    given URL never changes: browsers may cache it for a year without checking again."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


# index.html is the opposite: it must be re-checked on every visit. Otherwise a browser
# keeps an old copy pointing at asset files the next deploy deleted, and shows a blank page.
NO_CACHE = {"Cache-Control": "no-cache"}
WEB_DIST = Path(__file__).parent.parent / "web" / "dist"  # built by `npm run build` in web/


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(
        title="Revive API",
        version="1.0.0",
        summary="Win back users who abandon fintech KYC.",
        description=API_DESCRIPTION,
        openapi_tags=TAGS,
        lifespan=lifespan,
        docs_url=None,  # replaced by the Scalar reference at /docs below
        redoc_url=None,
        license_info={"name": "MIT"},
        contact={"name": "Shivam Pratap Raj", "url": "https://github.com/ShivamPratap16/revive"},
    )
    app.add_middleware(RequestContextMiddleware)
    register_error_handlers(app)
    for router in (health.router, events.router, sessions.router, outreach.router,
                   calls.router, calls.webhooks, analytics.router, demo.router):  # fmt: skip
        app.include_router(router)

    if settings.mount_simulator:
        # All-in-one mode: the fake voice provider lives at /sim on the same server.
        from simulator.main import app as simulator_app

        app.mount("/sim", simulator_app)

    docs_html = (STATIC / "docs.html").read_text(encoding="utf-8")

    @app.get("/docs", include_in_schema=False)
    async def _docs() -> HTMLResponse:
        # Scalar API reference, themed to match the site. The demo key (if configured) is
        # pre-filled so "Test request" works straight away on the demo tenant.
        config = {"demoKey": settings.demo_public_key or ""}
        page = docs_html.replace("__REVIVE_CONFIG__", json.dumps(config))
        return HTMLResponse(page, headers=NO_CACHE)

    # The web app (React, built into web/dist). Unknown paths fall back to index.html so
    # client-side routes like /dashboard work on a hard refresh.
    if (WEB_DIST / "index.html").exists():
        app.mount("/assets", ImmutableAssets(directory=WEB_DIST / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def _spa(path: str) -> FileResponse:
            file = WEB_DIST / path
            if path and file.is_file() and WEB_DIST in file.resolve().parents:
                return FileResponse(file)  # favicon, og image, etc.
            if path.startswith(("v1/", "webhooks/", "health/", "sim/")):
                raise ApiError(404, "not_found", "No such endpoint")
            return FileResponse(WEB_DIST / "index.html", headers=NO_CACHE)
    else:

        @app.get("/", include_in_schema=False)
        async def _not_built() -> HTMLResponse:
            return HTMLResponse(
                "<h1>Revive API</h1><p>The web app isn't built. Run <code>npm ci && npm run "
                "build</code> in <code>web/</code>, or open <a href='/docs'>/docs</a>.</p>"
            )

    return app


app = create_app()
