import uuid
from datetime import datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.pagination import PageParams
from app.modules.media.domain import MediaKind, MediaStatus


class MediaCreate(BaseModel):
    kind: MediaKind
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=3, max_length=100)
    size_bytes: int = Field(gt=0, description="Exact size of the file to upload.")
    checksum_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    alt_text: str | None = Field(
        default=None, max_length=500, description="Required for images unless decorative."
    )
    decorative: bool = False


class MediaUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alt_text: str | None = Field(default=None, max_length=500)
    decorative: bool | None = None

    @model_validator(mode="after")
    def _something(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("Send `alt_text` and/or `decorative`.")
        return self


class MediaRead(BaseModel):
    id: uuid.UUID
    kind: MediaKind
    status: MediaStatus
    filename: str
    content_type: str
    size_bytes: int
    alt_text: str | None
    decorative: bool
    url: str | None = Field(description="Short-lived download link, once the file is ready.")
    rejection_reason: str | None
    created_at: datetime


class UploadForm(BaseModel):
    """POST the file to `url` as multipart/form-data: every field below, then `file` last."""

    url: str
    fields: dict[str, str]
    expires_in: int


class MediaUploadRead(BaseModel):
    media: MediaRead
    upload: UploadForm


class MediaListQuery(PageParams):
    kind: MediaKind | None = None
    status: MediaStatus | None = None
