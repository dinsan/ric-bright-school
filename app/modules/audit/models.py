import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import UUIDPrimaryKeyMixin, utcnow


class AuditEvent(UUIDPrimaryKeyMixin, Base):
    """Immutable: a database trigger rejects UPDATE and DELETE (migration 0005)."""

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_entity", "entity_type", "entity_id"),
        Index("ix_audit_events_occurred_at", "occurred_at"),
    )

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    # Who acted. Null for anonymous events such as a failed sign-in for an unknown email.
    # Plain (restricting) foreign keys: users are anonymised, never deleted, and SET NULL
    # would have to update audit rows, which the append-only trigger forbids.
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    # Set when an admin acted while impersonating `actor_id`.
    impersonator_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(64), index=True)
    entity_type: Mapped[str | None] = mapped_column(String(40))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    ip: Mapped[str | None] = mapped_column(String(45))
    # Small structured facts (e.g. {"from": "parent", "to": "teacher"}). Never secrets,
    # tokens, passwords or email addresses.
    details: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), default=dict, server_default="{}"
    )
