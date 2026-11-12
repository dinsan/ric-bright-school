import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.models import AuditEvent


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, event: AuditEvent) -> None:
        self._session.add(event)
        await self._session.flush()

    async def search(
        self,
        *,
        actor_id: uuid.UUID | None,
        action: str | None,
        entity_type: str | None,
        entity_id: str | None,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[AuditEvent], int]:
        conditions = []
        if actor_id is not None:
            conditions.append(AuditEvent.actor_id == actor_id)
        if action:
            conditions.append(AuditEvent.action == action)
        if entity_type:
            conditions.append(AuditEvent.entity_type == entity_type)
        if entity_id:
            conditions.append(AuditEvent.entity_id == entity_id)
        total = await self._session.scalar(select(func.count(AuditEvent.id)).where(*conditions))
        stmt = (
            select(AuditEvent)
            .where(*conditions)
            .order_by(AuditEvent.occurred_at.desc(), AuditEvent.id)
            .limit(limit)
            .offset(offset)
        )
        return (await self._session.scalars(stmt)).all(), total or 0
