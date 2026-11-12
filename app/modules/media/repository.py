import uuid
from collections.abc import Iterable, Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.media.domain import MediaKind, MediaStatus
from app.modules.media.models import MediaAsset


class MediaRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, asset: MediaAsset) -> None:
        self._session.add(asset)
        await self._session.flush()

    async def get(self, asset_id: uuid.UUID) -> MediaAsset | None:
        return await self._session.get(MediaAsset, asset_id)

    async def get_for_update(self, asset_id: uuid.UUID) -> MediaAsset | None:
        stmt = select(MediaAsset).where(MediaAsset.id == asset_id).with_for_update()
        return await self._session.scalar(stmt)

    async def get_many(self, ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, MediaAsset]:
        wanted = list(set(ids))
        if not wanted:
            return {}
        rows = await self._session.scalars(select(MediaAsset).where(MediaAsset.id.in_(wanted)))
        return {asset.id: asset for asset in rows}

    async def list_for_owner(
        self,
        owner_id: uuid.UUID | None,
        *,
        kind: MediaKind | None,
        status: MediaStatus | None,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[MediaAsset], int]:
        conditions = []
        if owner_id is not None:
            conditions.append(MediaAsset.owner_id == owner_id)
        if kind is not None:
            conditions.append(MediaAsset.kind == kind)
        if status is not None:
            conditions.append(MediaAsset.status == status)
        total = await self._session.scalar(select(func.count(MediaAsset.id)).where(*conditions))
        stmt = (
            select(MediaAsset)
            .where(*conditions)
            .order_by(MediaAsset.created_at.desc(), MediaAsset.id)
            .limit(limit)
            .offset(offset)
        )
        return (await self._session.scalars(stmt)).all(), total or 0
