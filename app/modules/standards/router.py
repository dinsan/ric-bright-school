from typing import Annotated

from fastapi import APIRouter, Query, status

from app.core.pagination import Page
from app.modules.identity.deps import CurrentActor
from app.modules.standards.deps import StandardsServiceDep as ServiceDep
from app.modules.standards.models import Standard
from app.modules.standards.schemas import (
    FrameworkCreate,
    FrameworkRead,
    ImportResult,
    StandardQuery,
    StandardRead,
    StandardsImport,
)

router = APIRouter(prefix="/standards", tags=["standards"])


def standard_read(standard: Standard) -> StandardRead:
    return StandardRead(
        id=standard.id,
        framework=standard.framework.code,
        code=standard.code,
        description=standard.description,
        grade_min=standard.grade_min,
        grade_max=standard.grade_max,
    )


@router.get(
    "/frameworks",
    response_model=list[FrameworkRead],
    summary="Standards frameworks",
    description="Filter by `country` (ISO code, e.g. `US`) to show the frameworks used there.",
)
async def list_frameworks(
    service: ServiceDep, country: Annotated[str | None, Query(max_length=2)] = None
) -> list[FrameworkRead]:
    return [FrameworkRead.model_validate(f) for f in await service.frameworks(country)]


@router.post(
    "/frameworks",
    response_model=FrameworkRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add a framework (admins)",
)
async def create_framework(
    payload: FrameworkCreate, actor: CurrentActor, service: ServiceDep
) -> FrameworkRead:
    return FrameworkRead.model_validate(await service.create_framework(actor, payload))


@router.put(
    "/frameworks/{code}/standards",
    response_model=ImportResult,
    summary="Import a framework's standards (admins)",
    description="Creates new codes and updates existing ones (safe to run again with a new "
    "release). Up to 5000 per request.",
)
async def import_standards(
    code: str, payload: StandardsImport, actor: CurrentActor, service: ServiceDep
) -> ImportResult:
    created, updated = await service.import_standards(actor, code, payload)
    return ImportResult(created=created, updated=updated)


@router.get("", response_model=Page[StandardRead], summary="Search standards")
async def search_standards(
    query: Annotated[StandardQuery, Query()], service: ServiceDep
) -> Page[StandardRead]:
    items, total = await service.search(
        framework=query.framework,
        country=query.country,
        grade_level=query.grade_level,
        text=query.q,
        limit=query.limit,
        offset=query.offset,
    )
    return Page[StandardRead](
        items=[standard_read(s) for s in items], total=total, limit=query.limit, offset=query.offset
    )
