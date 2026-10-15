from typing import Annotated

from fastapi import APIRouter, Query

from app.security import SessionDep, TenantDep
from app.services.analytics import Summary, summary

router = APIRouter(prefix="/v1/analytics", tags=["analytics"])


@router.get("/summary", response_model=Summary, summary="Funnel, recovery and call insights")
async def get_summary(
    tenant: TenantDep,
    db: SessionDep,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> Summary:
    """Sessions started in the last `days` days: where users drop off, how many outreach
    brought back (and through which channel), what the calls revealed."""
    return await summary(db, tenant.id, days)
