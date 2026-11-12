import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.pagination import PageParams


class AuditEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    occurred_at: datetime
    actor_id: uuid.UUID | None
    impersonator_id: uuid.UUID | None
    action: str
    entity_type: str | None
    entity_id: str | None
    ip: str | None
    details: dict[str, Any]


class AuditEventQuery(PageParams):
    actor_id: uuid.UUID | None = None
    action: str | None = Field(default=None, max_length=64)
    entity_type: str | None = Field(default=None, max_length=40)
    entity_id: str | None = Field(default=None, max_length=64)
