from fastapi import APIRouter
from sqlalchemy import text

from app.errors import ApiError
from app.security import SessionDep

router = APIRouter(tags=["health"])


@router.get("/health/live", summary="Is the process running?")
async def live() -> dict:
    # Liveness: if this fails, the orchestrator (Docker/Kubernetes) restarts the container.
    return {"status": "ok"}


@router.get("/health/ready", summary="Can it serve traffic?")
async def ready(db: SessionDep) -> dict:
    # Readiness: checks dependencies. If the DB is down we report "not ready", so the
    # load balancer stops sending traffic, but we don't restart: a restart can't fix the DB.
    try:
        await db.execute(text("SELECT 1"))
    except Exception as exc:
        raise ApiError(503, "not_ready", "database unavailable") from exc
    return {"status": "ready"}
