"""Each tenant's outreach rules: when a user counts as dropped off, and how we follow up.

Stored as JSON on the tenant row and validated by Pydantic on the way in AND on the way
out, so a hand-edited row in the database can't crash the worker with a bad value.
"""

from datetime import time
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator, model_validator

from app.domain.journey import Step


class Channel(StrEnum):
    IN_APP = "in_app"  # a nudge inside the fintech's app (push / in-app agent)
    VOICE = "voice"  # an AI voice call


class Window(BaseModel):
    """A daily time window in the tenant's local time zone, e.g. 09:00-21:00."""

    start: time
    end: time

    @model_validator(mode="after")
    def _start_before_end(self) -> "Window":
        if self.start >= self.end:
            raise ValueError("window start must be before end")
        return self


class CadenceStep(BaseModel):
    channel: Channel
    delay_minutes: int = Field(ge=0, le=7 * 24 * 60)  # after the drop-off is detected


class OutreachPolicy(BaseModel):
    timezone: str = "Asia/Kolkata"

    # How long a user can sit on a step before we call it a drop-off. Aadhaar OTP is
    # short (OTPs expire in ~10 minutes); video KYC is long (it needs a quiet room).
    idle_minutes: dict[Step, int] = Field(
        default_factory=lambda: {
            Step.SIGNUP: 30,
            Step.PAN_VERIFICATION: 20,
            Step.AADHAAR_OTP: 15,
            Step.VIDEO_KYC: 45,
            Step.BANK_LINKING: 20,
        }
    )

    # The follow-up plan: nudge in the app first (cheap), call later (expensive).
    cadence: list[CadenceStep] = Field(
        default_factory=lambda: [
            CadenceStep(channel=Channel.IN_APP, delay_minutes=0),
            CadenceStep(channel=Channel.VOICE, delay_minutes=30),
            CadenceStep(channel=Channel.VOICE, delay_minutes=24 * 60),
        ],
        min_length=1,
        max_length=6,
    )

    # TRAI rules allow commercial calls only 09:00-21:00. Tenants may be stricter.
    voice_window: Window = Window(start=time(9), end=time(21))
    voice_days: set[int] = Field(default={0, 1, 2, 3, 4, 5}, min_length=1)  # Mon-Sat
    in_app_window: Window = Window(start=time(8), end=time(22))

    max_voice_calls_per_day: int = Field(default=2, ge=1, le=10)
    max_drop_rounds: int = Field(default=2, ge=1, le=5)  # stop chasing after this
    attribution_window_hours: int = Field(default=72, ge=1, le=24 * 30)
    max_delivery_tries: int = Field(default=5, ge=1, le=20)
    # No answer / busy: dial the same attempt again after a gap, at most this many times.
    max_redials: int = Field(default=1, ge=0, le=3)
    redial_after_minutes: int = Field(default=120, ge=5, le=24 * 60)

    @field_validator("timezone")
    @classmethod
    def _valid_tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown time zone {v!r}") from exc
        return v

    @field_validator("voice_days")
    @classmethod
    def _valid_days(cls, v: set[int]) -> set[int]:
        if not v <= set(range(7)):
            raise ValueError("days are 0 (Monday) to 6 (Sunday)")
        return v

    @field_validator("idle_minutes")
    @classmethod
    def _positive(cls, v: dict[Step, int]) -> dict[Step, int]:
        if Step.COMPLETED in v:
            raise ValueError("'completed' has no idle limit")
        if any(m < 1 for m in v.values()):
            raise ValueError("idle minutes must be at least 1")
        return v


def policy_of(raw: dict | None) -> OutreachPolicy:
    return OutreachPolicy.model_validate(raw or {})
