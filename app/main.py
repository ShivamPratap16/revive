from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import events, health, sessions
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
    return app


app = create_app()
