import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from app.core.pagination import Page
from app.modules.catalog import policies
from app.modules.catalog.deps import CourseServiceDep, PathServiceDep, SubjectServiceDep
from app.modules.catalog.domain import CourseStatus
from app.modules.catalog.models import LearningPath
from app.modules.catalog.schemas import (
    CourseCreate,
    CourseListQuery,
    CourseRead,
    CourseUpdate,
    PathCreate,
    PathQuery,
    PathRead,
    PathUpdate,
    PrerequisitesUpdate,
    RecommendedQuery,
    SubjectCreate,
    SubjectRead,
)
from app.modules.identity.deps import CurrentActor, OptionalActor, RequestInfoDep
from app.modules.identity.domain import Actor

# Reads are public and show published courses; signed-in authors and admins also see the
# drafts they may see. Writes need a signed-in user; the rules are in policies.py.

subjects_router = APIRouter(prefix="/subjects", tags=["subjects"])
courses_router = APIRouter(prefix="/courses", tags=["courses"])


@subjects_router.get("", response_model=list[SubjectRead], summary="List subjects")
async def list_subjects(service: SubjectServiceDep) -> list[SubjectRead]:
    return [SubjectRead.model_validate(s) for s in await service.list_subjects()]


@subjects_router.post(
    "",
    response_model=SubjectRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a subject",
    description="Teachers and platform admins.",
)
async def create_subject(
    payload: SubjectCreate, service: SubjectServiceDep, actor: CurrentActor
) -> SubjectRead:
    return SubjectRead.model_validate(await service.create(payload, actor))


@subjects_router.delete(
    "/{subject_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a subject",
    description="Platform admins. Fails with 409 while any course (archived included) "
    "still uses the subject.",
)
async def delete_subject(
    subject_id: uuid.UUID,
    service: SubjectServiceDep,
    actor: CurrentActor,
    request: RequestInfoDep,
) -> Response:
    await service.delete(subject_id, actor, request)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@courses_router.get(
    "",
    response_model=Page[CourseRead],
    summary="Search courses",
    description="Published courses for everyone. `status=draft` or `archived` needs a "
    "teacher (own courses only) or a platform admin (all).",
)
async def list_courses(
    query: Annotated[CourseListQuery, Query()], service: CourseServiceDep, actor: OptionalActor
) -> Page[CourseRead]:
    courses, total = await service.search(
        status=query.status,
        grade_level=query.grade_level,
        subject_slug=query.subject,
        text=query.q,
        limit=query.limit,
        offset=query.offset,
        actor=actor,
        tag=query.tag,
        difficulty=query.difficulty,
        language=query.language,
    )
    return Page[CourseRead](
        items=[CourseRead.model_validate(c) for c in courses],
        total=total,
        limit=query.limit,
        offset=query.offset,
    )


@courses_router.get(
    "/recommended",
    response_model=list[CourseRead],
    summary="Courses recommended for a grade",
    description="Published courses of the grade: those in curated learning paths first (in "
    "path order), then the rest. Without `grade_level`, a signed-in student's own grade.",
)
async def recommended_courses(
    query: Annotated[RecommendedQuery, Query()], service: CourseServiceDep, actor: OptionalActor
) -> list[CourseRead]:
    courses = await service.recommended(
        actor, grade_level=query.grade_level, subject_slug=query.subject, limit=query.limit
    )
    return [CourseRead.model_validate(c) for c in courses]


@courses_router.post(
    "",
    response_model=CourseRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a course",
    description="Teachers (who become the author) and platform admins. Creating it as "
    "`published` needs a verified teacher.",
)
async def create_course(
    payload: CourseCreate, service: CourseServiceDep, actor: CurrentActor
) -> CourseRead:
    return CourseRead.model_validate(await service.create(payload, actor))


@courses_router.get("/slug/{slug}", response_model=CourseRead, summary="Get a course by slug")
async def get_course_by_slug(
    slug: str, service: CourseServiceDep, actor: OptionalActor
) -> CourseRead:
    return CourseRead.model_validate(await service.get_by_slug(slug, actor))


@courses_router.get("/{course_id}", response_model=CourseRead, summary="Get a course by id")
async def get_course(
    course_id: uuid.UUID, service: CourseServiceDep, actor: OptionalActor
) -> CourseRead:
    return CourseRead.model_validate(await service.get(course_id, actor))


@courses_router.patch(
    "/{course_id}",
    response_model=CourseRead,
    summary="Update a course",
    description="Partial update: only the fields you send change. The author or a platform "
    "admin; publishing needs a verified teacher.",
)
async def update_course(
    course_id: uuid.UUID, payload: CourseUpdate, service: CourseServiceDep, actor: CurrentActor
) -> CourseRead:
    return CourseRead.model_validate(await service.update(course_id, payload, actor))


@courses_router.delete(
    "/{course_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Archive a course",
    description="Soft delete: sets the status to `archived`, which hides the course from the "
    "public catalog. The row is kept. Repeating the call is harmless. The author or a "
    "platform admin.",
)
async def archive_course(
    course_id: uuid.UUID,
    service: CourseServiceDep,
    actor: CurrentActor,
    request: RequestInfoDep,
) -> Response:
    await service.archive(course_id, actor, request)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@courses_router.get(
    "/{course_id}/prerequisites",
    response_model=list[CourseRead],
    summary="Courses to finish before this one",
)
async def get_prerequisites(
    course_id: uuid.UUID, service: CourseServiceDep, actor: OptionalActor
) -> list[CourseRead]:
    return [CourseRead.model_validate(c) for c in await service.prerequisites(course_id, actor)]


@courses_router.put(
    "/{course_id}/prerequisites",
    response_model=list[CourseRead],
    summary="Set the prerequisite courses",
    description="Replaces the list. Circles (A needs B needs A) are refused.",
)
async def set_prerequisites(
    course_id: uuid.UUID,
    payload: PrerequisitesUpdate,
    service: CourseServiceDep,
    actor: CurrentActor,
) -> list[CourseRead]:
    courses = await service.set_prerequisites(course_id, payload.course_ids, actor)
    return [CourseRead.model_validate(c) for c in courses]


# --- learning paths (BE-CAT-08) -----------------------------------------------------------

paths_router = APIRouter(prefix="/learning-paths", tags=["learning paths"])


def _path(path: LearningPath, actor: Actor | None) -> PathRead:
    show_all = policies.can_manage_paths(actor)
    return PathRead(
        id=path.id,
        slug=path.slug,
        title=path.title,
        description=path.description,
        grade_level=path.grade_level,
        subject=SubjectRead.model_validate(path.subject) if path.subject else None,
        is_published=path.is_published,
        courses=[
            CourseRead.model_validate(item.course)
            for item in path.items
            if show_all or item.course.status == CourseStatus.PUBLISHED
        ],
    )


@paths_router.get("", response_model=Page[PathRead], summary="Learning paths")
async def list_paths(
    query: Annotated[PathQuery, Query()], service: PathServiceDep, actor: OptionalActor
) -> Page[PathRead]:
    paths, total = await service.list_paths(
        actor,
        grade_level=query.grade_level,
        subject_slug=query.subject,
        limit=query.limit,
        offset=query.offset,
    )
    return Page[PathRead](
        items=[_path(p, actor) for p in paths],
        total=total,
        limit=query.limit,
        offset=query.offset,
    )


@paths_router.post(
    "",
    response_model=PathRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a learning path (admins)",
)
async def create_path(
    payload: PathCreate, service: PathServiceDep, actor: CurrentActor
) -> PathRead:
    return _path(await service.create(actor, payload), actor)


@paths_router.get("/{slug}", response_model=PathRead, summary="One learning path")
async def get_path(slug: str, service: PathServiceDep, actor: OptionalActor) -> PathRead:
    return _path(await service.get(actor, slug), actor)


@paths_router.patch(
    "/{slug}",
    response_model=PathRead,
    summary="Change a learning path (admins)",
    description="`course_ids` replaces the whole ordered list.",
)
async def update_path(
    slug: str, payload: PathUpdate, service: PathServiceDep, actor: CurrentActor
) -> PathRead:
    return _path(await service.update(actor, slug, payload), actor)


@paths_router.delete(
    "/{slug}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a learning path (admins)"
)
async def delete_path(slug: str, service: PathServiceDep, actor: CurrentActor) -> Response:
    await service.delete(actor, slug)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


router = APIRouter()
router.include_router(subjects_router)
router.include_router(courses_router)
router.include_router(paths_router)
