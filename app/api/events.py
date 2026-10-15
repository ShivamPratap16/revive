from fastapi import APIRouter

from app.config import get_settings
from app.errors import ApiError
from app.schemas import ErrorResponse, IngestBatch, IngestResponse
from app.security import SessionDep, TenantDep
from app.services.events import ingest_batch

router = APIRouter(prefix="/v1/events", tags=["events"])


@router.post(
    "",
    response_model=IngestResponse,
    status_code=202,
    responses={401: {"model": ErrorResponse}, 413: {"model": ErrorResponse}},
    summary="Report onboarding events from the app (batched, idempotent)",
)
async def post_events(batch: IngestBatch, tenant: TenantDep, db: SessionDep) -> IngestResponse:
    """Send any number of events up to the batch limit. Retrying the same request is safe:
    events already stored come back with status "duplicate" and change nothing."""
    limit = get_settings().max_events_per_batch
    if len(batch.events) > limit:
        raise ApiError(413, "batch_too_large", f"Send at most {limit} events per request")
    return await ingest_batch(db, tenant.id, batch.events)
