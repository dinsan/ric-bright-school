import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Enum, ForeignKey, String, false
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.modules.media.domain import MediaKind, MediaStatus


def _enum(enum_cls: type, name: str) -> Enum:
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        length=30,
        values_callable=lambda e: [m.value for m in e],
    )


class MediaAsset(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An uploaded file (BE-MEDIA-02). The bytes live in object storage under storage_key."""

    __tablename__ = "media_assets"

    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[MediaKind] = mapped_column(_enum(MediaKind, "media_kind"))
    status: Mapped[MediaStatus] = mapped_column(
        _enum(MediaStatus, "media_status"),
        default=MediaStatus.PENDING_UPLOAD,
        server_default=MediaStatus.PENDING_UPLOAD.value,
    )
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(BigInteger)  # declared, then checked
    # SHA-256 the client declares (hex); the storage's ETag is kept after the upload.
    checksum_sha256: Mapped[str | None] = mapped_column(String(64))
    etag: Mapped[str | None] = mapped_column(String(100))
    storage_key: Mapped[str] = mapped_column(String(300), unique=True)
    # Images: a text alternative is required, unless the image is purely decorative.
    alt_text: Mapped[str | None] = mapped_column(String(500))
    decorative: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_reason: Mapped[str | None] = mapped_column(String(200))
