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

from app.config import get_settings
from app.domain.policy import Channel
from app.logging_setup import mask_phone

log = logging.getLogger(__name__)


class TransientChannelError(Exception):
    pass


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

    @property
    def idempotency_key(self) -> str:
        # Sent to the provider with every request. If we send twice (we crashed after the
        # provider accepted but before we saved "sent"), the provider drops the duplicate.
        return f"revive-{self.attempt_id}"


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


def default_channels() -> dict[Channel, DeliveryChannel]:
    rate = get_settings().sim_failure_rate
    return {
        Channel.IN_APP: SimulatedPushChannel(rate),
        Channel.VOICE: SimulatedVoiceChannel(rate),
    }
