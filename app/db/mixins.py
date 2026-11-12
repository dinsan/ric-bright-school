import uuid
from datetime import UTC, datetime
from typing import Any, ClassVar

from sqlalchemy import DateTime, FetchedValue, func
from sqlalchemy.orm import Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


class UUIDPrimaryKeyMixin:
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    """``created_at`` / ``updated_at`` columns.

    ``updated_at`` is maintained by the ``set_updated_at()`` database trigger, so it also
    moves for bulk ``UPDATE`` statements and raw SQL, not only ORM flushes. Every table that
    uses this mixin must attach the trigger in its migration (see migration 0002);
    ``tests/test_timestamps.py`` fails if one is missing.
    """

    # Fetch server-generated values (the trigger's updated_at) with RETURNING after a flush,
    # so they are readable without a lazy refresh (impossible with async sessions).
    __mapper_args__: ClassVar[dict[str, Any]] = {"eager_defaults": True}

    # Python-side defaults keep values available after flush without an extra round trip
    # (important with async sessions); server defaults cover inserts made outside the ORM.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        server_default=func.now(),
        server_onupdate=FetchedValue(),
    )
