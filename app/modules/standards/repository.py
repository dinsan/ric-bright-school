import uuid
from collections.abc import Iterable, Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.modules.standards.models import Standard, StandardFramework


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class StandardsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, obj: object) -> None:
        self._session.add(obj)
        await self._session.flush()

    async def frameworks(self, country: str | None) -> Sequence[StandardFramework]:
        stmt = select(StandardFramework).order_by(
            StandardFramework.country_code, StandardFramework.code
        )
        if country:
            stmt = stmt.where(StandardFramework.country_code == country)
        return (await self._session.scalars(stmt)).all()

    async def framework_by_code(self, code: str) -> StandardFramework | None:
        return await self._session.scalar(
            select(StandardFramework).where(StandardFramework.code == code)
        )

    async def standards_by_code(self, framework_id: uuid.UUID) -> dict[str, Standard]:
        rows = await self._session.scalars(
            select(Standard).where(Standard.framework_id == framework_id)
        )
        return {s.code: s for s in rows}

    async def find_by_codes(
        self, pairs: Iterable[tuple[str, str]]
    ) -> dict[tuple[str, str], Standard]:
        """(framework code, standard code) -> standard, for imports between servers."""
        wanted = {(f.upper(), c) for f, c in pairs}
        if not wanted:
            return {}
        stmt = (
            select(Standard)
            .join(StandardFramework)
            .options(joinedload(Standard.framework))
            .where(StandardFramework.code.in_({f for f, _ in wanted}))
        )
        found = {
            (s.framework.code, s.code): s for s in (await self._session.scalars(stmt)).unique()
        }
        return {k: v for k, v in found.items() if k in wanted}

    async def get_many(self, ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, Standard]:
        wanted = list(set(ids))
        if not wanted:
            return {}
        rows = await self._session.scalars(
            select(Standard).options(joinedload(Standard.framework)).where(Standard.id.in_(wanted))
        )
        return {s.id: s for s in rows}

    async def search(
        self,
        *,
        framework: str | None,
        country: str | None,
        grade_level: int | None,
        text: str | None,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[Standard], int]:
        conditions = []
        if framework:
            conditions.append(StandardFramework.code == framework.upper())
        if country:
            conditions.append(StandardFramework.country_code == country.upper())
        if grade_level is not None:
            conditions.append(or_(Standard.grade_min.is_(None), Standard.grade_min <= grade_level))
            conditions.append(or_(Standard.grade_max.is_(None), Standard.grade_max >= grade_level))
        if text:
            pattern = f"%{_escape_like(text)}%"
            conditions.append(
                or_(
                    Standard.code.ilike(pattern, escape="\\"),
                    Standard.description.ilike(pattern, escape="\\"),
                )
            )
        base = select(Standard).join(StandardFramework).where(*conditions)
        total = await self._session.scalar(
            select(func.count(Standard.id)).join(StandardFramework).where(*conditions)
        )
        stmt = (
            base.options(joinedload(Standard.framework))
            .order_by(StandardFramework.code, Standard.code)
            .limit(limit)
            .offset(offset)
        )
        return (await self._session.scalars(stmt)).unique().all(), total or 0
