"""Request and response shapes (Pydantic models).

Pydantic validates every incoming request before our code runs. A bad request gets a
clear 422 error listing exactly which field is wrong, and our functions can trust their
inputs: if an IngestEvent exists, its step and type are already valid.
"""

import re
import uuid
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from app.domain.journey import EventType, SessionStatus, Step

# Indian mobile in E.164 form: +91 then 10 digits starting 6-9.
_PHONE_RE = re.compile(r"^\+91[6-9]\d{9}$")
SUPPORTED_LANGUAGES = {"en", "hi", "hinglish", "ta", "te", "kn", "mr", "bn"}


def _check_phone(v: str) -> str:
    v = v.replace(" ", "").replace("-", "")
    if not _PHONE_RE.match(v):
        raise ValueError("phone must be an Indian mobile number like +919876543210")
    return v


def _check_language(v: str) -> str:
    v = v.lower()
    if v not in SUPPORTED_LANGUAGES:
        raise ValueError(f"language must be one of {sorted(SUPPORTED_LANGUAGES)}")
    return v


def _require_tz(v: datetime) -> datetime:
    # A timestamp without a timezone is ambiguous (IST? UTC?). Refuse it.
    if v.tzinfo is None:
        raise ValueError("timestamp must include a timezone, e.g. 2026-10-15T10:00:00+05:30")
    return v


Phone = Annotated[str, AfterValidator(_check_phone)]
Language = Annotated[str, AfterValidator(_check_language)]
AwareDatetime = Annotated[datetime, AfterValidator(_require_tz)]
ExternalId = Annotated[str, Field(min_length=1, max_length=128)]


class UserIn(BaseModel):
    id: ExternalId
    phone: Phone | None = None
    name: str | None = Field(default=None, max_length=200)
    language: Language | None = None  # None = not sent, keep what we know


class IngestEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")  # unknown fields are a client bug: say so

    event_id: ExternalId
    session_id: ExternalId
    type: EventType
    step: Step | None = None
    occurred_at: AwareDatetime
    user: UserIn
    error_code: str | None = Field(default=None, max_length=64)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _step_matches_type(self) -> "IngestEvent":
        # Rules that involve two fields at once go in a model validator.
        if self.type == EventType.JOURNEY_COMPLETED:
            if self.step not in (None, Step.COMPLETED):
                raise ValueError("journey_completed must not name a step")
        else:
            if self.step is None:
                raise ValueError(f"{self.type} needs a step")
            if self.step == Step.COMPLETED:
                raise ValueError("'completed' is not a step; send journey_completed")
        if self.error_code and self.type != EventType.STEP_FAILED:
            raise ValueError("error_code is only allowed on step_failed")
        return self


class IngestBatch(BaseModel):
    # Mobile SDKs buffer events and send them together, so the API takes a batch.
    events: list[IngestEvent] = Field(min_length=1)


class EventResultStatus(StrEnum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"


class EventResult(BaseModel):
    event_id: str
    status: EventResultStatus
    clock_skew_corrected: bool = False


class IngestResponse(BaseModel):
    accepted: int
    duplicates: int
    results: list[EventResult]


class CustomerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    external_user_id: str
    name: str | None
    phone_masked: str | None
    preferred_language: str | None  # None = unknown; the voice agent detects it on the call


class SessionOut(BaseModel):
    id: uuid.UUID
    external_session_id: str
    journey: str
    current_step: Step
    status: SessionStatus
    last_error_code: str | None
    failure_count: int
    started_at: datetime
    last_activity_at: datetime
    completed_at: datetime | None
    customer: CustomerOut


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    external_event_id: str
    type: EventType
    step: Step | None
    error_code: str | None
    occurred_at: datetime
    received_at: datetime
    clock_skew_corrected: bool


class SessionDetailOut(SessionOut):
    events: list[EventOut]


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str | None = None
    details: Any = None


class ErrorResponse(BaseModel):
    error: ErrorBody
