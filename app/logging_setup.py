"""Structured JSON logs with a request ID on every line.

In production, logs go to a system like Loki or CloudWatch that searches JSON fields.
With a request_id on every line, "show me everything that happened in this one failed
request" is a single search.
"""

import json
import logging
import time
import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

# A ContextVar is like a global variable, but each concurrent request (each asyncio task)
# sees its own value. Safe with async, unlike threading.local.
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        # Anything passed as logger.info("...", extra={"fields": {...}}) is merged in.
        entry.update(getattr(record, "fields", {}))
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    logging.getLogger("uvicorn.access").disabled = True  # our middleware logs requests


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Gives every request an ID (or reuses the caller's) and logs one line per request."""

    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        token = request_id_var.set(rid)
        start = time.perf_counter()
        try:
            response = await call_next(request)
            response.headers["x-request-id"] = rid
            logging.getLogger("revive.http").info(
                "request",
                extra={
                    "fields": {
                        "method": request.method,
                        "path": request.url.path,
                        "status": response.status_code,
                        "ms": round((time.perf_counter() - start) * 1000, 1),
                    }
                },
            )
            return response
        finally:
            request_id_var.reset(token)


def mask_phone(phone: str | None) -> str | None:
    """+919876543210 -> +91******3210. Use whenever a phone number leaves the DB."""
    if not phone:
        return None
    return phone[:3] + "*" * (len(phone) - 7) + phone[-4:]
