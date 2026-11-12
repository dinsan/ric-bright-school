import uuid
from collections.abc import Iterable, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.db.transactions import conflict_on_integrity_error
from app.modules.identity.domain import Actor, Permission
from app.modules.standards.models import Standard, StandardFramework
from app.modules.standards.repository import StandardsRepository
from app.modules.standards.schemas import FrameworkCreate, StandardsImport


def ensure_can_manage(actor: Actor) -> None:
    if not actor.can(Permission.STANDARDS_MANAGE):
        raise ForbiddenError("Only administrators manage curriculum standards.")


class StandardsService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = StandardsRepository(session)

    async def frameworks(self, country: str | None) -> Sequence[StandardFramework]:
        return await self._repo.frameworks(country.upper() if country else None)

    async def create_framework(self, actor: Actor, data: FrameworkCreate) -> StandardFramework:
        ensure_can_manage(actor)
        if await self._repo.framework_by_code(data.code):
            raise ConflictError(f"A framework with code {data.code} already exists.")
        framework = StandardFramework(**data.model_dump())
        async with conflict_on_integrity_error(
            self._session, f"A framework with code {data.code} already exists."
        ):
            await self._repo.add(framework)
            await self._session.commit()
        return framework

    async def import_standards(
        self, actor: Actor, framework_code: str, data: StandardsImport
    ) -> tuple[int, int]:
        """Create or update standards by code (re-importing a new release is safe)."""
        ensure_can_manage(actor)
        framework = await self._repo.framework_by_code(framework_code.upper())
        if framework is None:
            raise NotFoundError("Framework not found.")
        existing = await self._repo.standards_by_code(framework.id)
        created = updated = 0
        seen: set[str] = set()
        for item in data.standards:
            if item.code in seen:
                continue  # the last duplicate in the file would be ambiguous; keep the first
            seen.add(item.code)
            found = existing.get(item.code)
            if found is None:
                self._session.add(Standard(framework_id=framework.id, **item.model_dump()))
                created += 1
            else:
                found.description = item.description
                found.grade_min, found.grade_max = item.grade_min, item.grade_max
                updated += 1
        await self._session.commit()
        return created, updated

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
        return await self._repo.search(
            framework=framework,
            country=country,
            grade_level=grade_level,
            text=text.strip() if text else None,
            limit=limit,
            offset=offset,
        )

    async def get_many(self, ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, Standard]:
        return await self._repo.get_many(ids)

    async def find_by_codes(
        self, pairs: Iterable[tuple[str, str]]
    ) -> dict[tuple[str, str], Standard]:
        return await self._repo.find_by_codes(pairs)
