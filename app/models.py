"""Database tables, written as SQLAlchemy 2.0 typed models.

Multi-tenancy: Revive serves many fintechs (each one is a "tenant"). Every row carries a
tenant_id, and every query filters by it, so KreditBee can never see PhonePe's users.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # We store only a SHA-256 hash of the API key. A DB leak doesn't leak working keys.
    api_key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    # First characters of the key (e.g. "rv_live_a1b2"), safe to show in dashboards and logs.
    api_key_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = _now()


class Customer(Base):
    """An end user of the fintech app (the person going through KYC)."""

    __tablename__ = "customers"
    __table_args__ = (UniqueConstraint("tenant_id", "external_user_id"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    external_user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(20))
    name: Mapped[str | None] = mapped_column(String(200))
    # NULL = not known yet. Better than guessing "en" and calling a Tamil speaker in English.
    preferred_language: Mapped[str | None] = mapped_column(String(8))
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class OnboardingSession(Base):
    """One attempt by one customer to finish the KYC journey."""

    __tablename__ = "onboarding_sessions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "external_session_id"),
        # The drop-off detector (Phase 2) asks: "in-progress sessions idle since before X".
        # This index answers that without scanning the whole table.
        Index("ix_sessions_status_last_activity", "status", "last_activity_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id"), nullable=False)
    external_session_id: Mapped[str] = mapped_column(String(128), nullable=False)
    journey: Mapped[str] = mapped_column(String(32), nullable=False, default="kyc")
    current_step: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()

    customer: Mapped[Customer] = relationship(lazy="joined")


class OnboardingEvent(Base):
    """Append-only log of everything the app told us. Never updated, never deleted.

    Keeping the raw log means we can rebuild session state from scratch if a bug in
    apply_event() is ever found (replay every event in occurred_at order).
    """

    __tablename__ = "onboarding_events"
    __table_args__ = (
        # The idempotency guarantee: the same event_id from the same tenant is stored once,
        # no matter how many times the app retries sending it.
        UniqueConstraint("tenant_id", "external_event_id", name="uq_events_tenant_event_id"),
        Index("ix_events_session_occurred", "session_id", "occurred_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("onboarding_sessions.id"), nullable=False
    )
    external_event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    step: Mapped[str | None] = mapped_column(String(32))
    error_code: Mapped[str | None] = mapped_column(String(64))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = _now()
    clock_skew_corrected: Mapped[bool] = mapped_column(nullable=False, default=False)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)
