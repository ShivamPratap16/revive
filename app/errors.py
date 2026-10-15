"""One error format for the whole API.

Every error, whether it's our own, a validation failure or an unexpected crash, comes back as
  {"error": {"code": "...", "message": "...", "request_id": "..."}}
so client developers write one error handler, and the request_id lets support find the
exact log line for a failed call.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.logging_setup import request_id_var

log = logging.getLogger(__name__)


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str, details=None):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


def _body(code: str, message: str, details=None) -> dict:
    return {
        "error": {
            "code": code,
            "message": message,
            "request_id": request_id_var.get(),
            "details": details,
        }
    }


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        return JSONResponse(
            status_code=exc.status_code, content=_body(exc.code, exc.message, exc.details)
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        # Keep only the useful parts of Pydantic's error list (location + message).
        details = [
            {"field": ".".join(str(p) for p in e["loc"]), "message": e["msg"]} for e in exc.errors()
        ]
        return JSONResponse(
            status_code=422, content=_body("validation_error", "Request body is invalid", details)
        )

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception):
        # Log the full stack trace for us; never leak internals to the client.
        log.exception("unhandled error")
        return JSONResponse(
            status_code=500, content=_body("internal_error", "Something went wrong on our side")
        )
