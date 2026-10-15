from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from app.api import analytics, calls, events, health, outreach, sessions
from app.config import get_settings
from app.db import engine
from app.errors import register_error_handlers
from app.logging_setup import RequestContextMiddleware, configure_logging


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Code before `yield` runs at startup, code after it at shutdown.
    yield
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

    dashboard = Path(__file__).parent / "static" / "dashboard.html"

    @app.get("/dashboard", include_in_schema=False)
    async def _dashboard() -> FileResponse:
        # A static page; it calls /v1/analytics/summary with the key the user pastes in.
        return FileResponse(dashboard)

    return app


app = create_app()
