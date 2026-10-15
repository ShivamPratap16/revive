"""Delivery channels: the code that actually reaches a user.

Each provider (push service, voice platform) sits behind the same small interface, so
the delivery logic never knows or cares which vendor is used. Swapping vendors, or
using a fake one in tests, means changing one line, not the business logic.

Errors are split in two because they need opposite handling:
  TransientChannelError: "try again later" (timeout, 503, rate limited) -> retry with backoff
  PermanentChannelError: "this will never work" (invalid number) -> straight to dead-letter
Retrying a permanent error just wastes money and annoys the provider.
"""

import logging
import random
import uuid
from dataclasses import dataclass
from typing import Protocol

import httpx

from app.config import get_settings
from app.domain.policy import Channel
from app.logging_setup import mask_phone

log = logging.getLogger(__name__)


class TransientChannelError(Exception):
    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        # Set when the provider tells us how long to wait (HTTP 429 Retry-After).
        self.retry_after = retry_after


class PermanentChannelError(Exception):
    pass


@dataclass(frozen=True)
class OutboundMessage:
    attempt_id: uuid.UUID
    channel: Channel
    customer_ref: str  # the fintech's own user id, so their app knows whom to notify
    phone: str | None
    language: str | None
    text: str
    call_id: uuid.UUID | None = None  # voice only: our id for this call placement

    @property
    def idempotency_key(self) -> str:
        # Sent to the provider with every request. If we send twice (we crashed after the
        # provider accepted but before we saved "sent"), the provider drops the duplicate.
        # Voice uses the call id: a re-dial is a NEW call and must not be deduplicated.
        return f"revive-{self.call_id or self.attempt_id}"


class DeliveryChannel(Protocol):
    """Any class with this method counts as a channel. No inheritance needed:
    typing.Protocol checks the shape ("duck typing" with type hints)."""

    async def send(self, msg: OutboundMessage) -> str:
        """Deliver the message and return the provider's reference id."""
        ...


class SimulatedPushChannel:
    """Stands in for FCM/APNs or the fintech's own in-app agent SDK."""

    def __init__(self, failure_rate: float = 0.0):
        self.failure_rate = failure_rate

    async def send(self, msg: OutboundMessage) -> str:
        if random.random() < self.failure_rate:
            raise TransientChannelError("push provider returned 503")
        log.info("push sent", extra={"fields": {"to": msg.customer_ref, "text": msg.text}})
        return f"push_{uuid.uuid4().hex[:12]}"


class SimulatedVoiceChannel:
    """Stands in for the voice platform. Phase 3 replaces this with a real call flow
    (place call -> provider sends webhooks as the call progresses)."""

    def __init__(self, failure_rate: float = 0.0):
        self.failure_rate = failure_rate

    async def send(self, msg: OutboundMessage) -> str:
        if not msg.phone:
            raise PermanentChannelError("no phone number")
        if msg.phone.endswith("0000"):  # test hook: numbers ending 0000 are "invalid"
            raise PermanentChannelError("number not in service")
        if random.random() < self.failure_rate:
            raise TransientChannelError("voice provider timeout")
        log.info("call placed", extra={"fields": {"to": mask_phone(msg.phone)}})
        return f"call_{uuid.uuid4().hex[:12]}"


class HttpVoiceChannel:
    """Places calls through a voice provider's HTTP API (our simulator, or a real one).

    The HTTP status code decides retry vs give up:
        2xx               -> call accepted, return its id
        429, 5xx, timeout -> TransientChannelError (honouring Retry-After if sent)
        other 4xx         -> PermanentChannelError (bad number, bad request: retrying won't help)
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        webhook_url: str,
        client: httpx.AsyncClient | None = None,
    ):
        self.api_key = api_key
        self.webhook_url = webhook_url
        # One client for the worker's lifetime: it keeps connections open between calls
        # (connection pooling) instead of a new TCP + TLS handshake per request.
        self.client = client or httpx.AsyncClient(base_url=base_url, timeout=8.0)

    async def send(self, msg: OutboundMessage) -> str:
        if not msg.phone or msg.call_id is None:
            raise PermanentChannelError("voice message needs a phone and a call id")
        try:
            r = await self.client.post(
                "/v1/calls",
                json={
                    "to": msg.phone,
                    "client_reference": str(msg.call_id),
                    "language": msg.language,
                    "opening_line": msg.text,
                    "webhook_url": self.webhook_url,
                },
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Idempotency-Key": msg.idempotency_key,
                },
            )
        except httpx.TimeoutException as exc:
            raise TransientChannelError(f"provider timeout: {exc!r}") from exc
        except httpx.TransportError as exc:  # connection refused, DNS failure, reset...
            raise TransientChannelError(f"provider unreachable: {exc!r}") from exc

        if r.status_code == 429 or r.status_code >= 500:
            retry_after = r.headers.get("retry-after")
            raise TransientChannelError(
                f"provider returned {r.status_code}",
                retry_after=float(retry_after) if retry_after and retry_after.isdigit() else None,
            )
        if r.status_code >= 400:
            raise PermanentChannelError(f"provider rejected call: {r.status_code} {r.text[:200]}")
        return r.json()["id"]

    async def aclose(self) -> None:
        await self.client.aclose()


def default_channels() -> dict[Channel, DeliveryChannel]:
    settings = get_settings()
    rate = settings.sim_failure_rate
    voice: DeliveryChannel = SimulatedVoiceChannel(rate)
    if settings.voice_api_url:
        voice = HttpVoiceChannel(
            settings.voice_api_url,
            settings.voice_api_key,
            f"{settings.public_base_url}/webhooks/voice/{settings.voice_provider}",
        )
    return {Channel.IN_APP: SimulatedPushChannel(rate), Channel.VOICE: voice}
