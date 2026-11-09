import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.core.pagination import Page
from app.modules.catalog.deps import CourseServiceDep, SubjectServiceDep
from app.modules.catalog.schemas import (
    CourseCreate,
    CourseListQuery,
    CourseRead,
    SubjectCreate,
    SubjectRead,
)

subjects_router = APIRouter(prefix="/subjects", tags=["subjects"])
courses_router = APIRouter(prefix="/courses", tags=["courses"])


@subjects_router.get("", response_model=list[SubjectRead], summary="List subjects")
async def list_subjects(service: SubjectServiceDep) -> list[SubjectRead]:
    return [SubjectRead.model_validate(s) for s in await service.list_subjects()]


@subjects_router.post(
    "", response_model=SubjectRead, status_code=status.HTTP_201_CREATED, summary="Create a subject"
)
async def create_subject(payload: SubjectCreate, service: SubjectServiceDep) -> SubjectRead:
    return SubjectRead.model_validate(await service.create(payload))


@courses_router.get("", response_model=Page[CourseRead], summary="Search courses")
async def list_courses(
    query: Annotated[CourseListQuery, Query()], service: CourseServiceDep
) -> Page[CourseRead]:
    courses, total = await service.search(
        status=query.status,
        grade_level=query.grade_level,
        subject_slug=query.subject,
        text=query.q,
        limit=query.limit,
        offset=query.offset,
    )
    return Page[CourseRead](
        items=[CourseRead.model_validate(c) for c in courses],
        total=total,
        limit=query.limit,
        offset=query.offset,
    )


@courses_router.post(
    "", response_model=CourseRead, status_code=status.HTTP_201_CREATED, summary="Create a course"
)
async def create_course(payload: CourseCreate, service: CourseServiceDep) -> CourseRead:
    return CourseRead.model_validate(await service.create(payload))


@courses_router.get("/slug/{slug}", response_model=CourseRead, summary="Get a course by slug")
async def get_course_by_slug(slug: str, service: CourseServiceDep) -> CourseRead:
    return CourseRead.model_validate(await service.get_by_slug(slug))


@courses_router.get("/{course_id}", response_model=CourseRead, summary="Get a course by id")
async def get_course(course_id: uuid.UUID, service: CourseServiceDep) -> CourseRead:
    return CourseRead.model_validate(await service.get(course_id))


router = APIRouter()
router.include_router(subjects_router)
router.include_router(courses_router)
