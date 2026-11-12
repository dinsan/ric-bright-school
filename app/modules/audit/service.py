import uuid
from collections.abc import Sequence
from typing import Any

from app.modules.audit.domain import AuditAction
from app.modules.audit.models import AuditEvent
from app.modules.audit.repository import AuditRepository
from app.modules.identity.domain import Actor, RequestInfo


class AuditLog:
    """Records audit events inside the caller's transaction (committed together with the
    change they describe, so the log never claims something that was rolled back)."""

    def __init__(self, repository: AuditRepository) -> None:
        self._repository = repository

    async def record(
        self,
        action: AuditAction,
        *,
        actor: Actor | None = None,
        actor_id: uuid.UUID | None = None,
        entity_type: str | None = None,
        entity_id: uuid.UUID | str | None = None,
        request: RequestInfo | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        await self._repository.add(
            AuditEvent(
                action=action.value,
                actor_id=actor.user_id if actor else actor_id,
                impersonator_id=actor.impersonator_id if actor else None,
                entity_type=entity_type,
                entity_id=str(entity_id) if entity_id is not None else None,
                ip=request.ip if request else None,
                details=details or {},
            )
        )

    async def search(
        self,
        *,
        actor_id: uuid.UUID | None = None,
        action: str | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[Sequence[AuditEvent], int]:
        return await self._repository.search(
            actor_id=actor_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            limit=limit,
            offset=offset,
        )
