import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Response, status
from pydantic import BaseModel

from app.core.pagination import Page
from app.modules.catalog.schemas import CourseRead
from app.modules.curriculum.deps import AuthoringDep, PublishingDep, SearchDep, TransferDep
from app.modules.curriculum.models import CourseLesson, CourseUnit, CourseVersion
from app.modules.curriculum.schemas import (
    CourseDocument,
    DraftRead,
    DuplicateRequest,
    ImportRequest,
    LessonContentUpdate,
    LessonCreate,
    LessonDraftRead,
    LessonMove,
    LessonOutline,
    LessonPrerequisitesUpdate,
    LessonStandardsUpdate,
    LessonUpdate,
    OrderUpdate,
    PublishedCourseRead,
    PublishedLessonOutline,
    PublishedLessonRead,
    PublishedUnitOutline,
    PublishRequest,
    SearchQuery,
    SearchResultRead,
    UnitCreate,
    UnitOutline,
    UnitUpdate,
    VersionRead,
)
from app.modules.identity.deps import CurrentActor, OptionalActor, RequestInfoDep

authoring = APIRouter(prefix="/courses/{course_id}", tags=["course authoring"])
reading = APIRouter(prefix="/courses/{course_id}", tags=["course content"])
transfer = APIRouter(prefix="/courses", tags=["course authoring"])
search_router = APIRouter(prefix="/search", tags=["search"])

NO_CONTENT = status.HTTP_204_NO_CONTENT


# --- response builders ---------------------------------------------------------------------


def _lesson_outline(
    lesson: CourseLesson, standards: list[uuid.UUID], prerequisites: list[uuid.UUID]
) -> LessonOutline:
    return LessonOutline(
        id=lesson.id,
        title=lesson.title,
        summary=lesson.summary,
        position=lesson.position,
        estimated_minutes=lesson.estimated_minutes,
        difficulty=lesson.difficulty,
        block_count=len(lesson.content.get("blocks", [])),
        standard_ids=standards,
        prerequisite_lesson_ids=prerequisites,
        revision=lesson.revision,
        updated_at=lesson.updated_at,
    )


async def _draft(service: Any, actor: Any, course_id: uuid.UUID) -> DraftRead:
    course, units, standards, prerequisites, version, changed = await service.draft(
        actor, course_id
    )

    def unit_outline(unit: CourseUnit) -> UnitOutline:
        return UnitOutline(
            id=unit.id,
            title=unit.title,
            summary=unit.summary,
            position=unit.position,
            lessons=[
                _lesson_outline(
                    lesson,
                    standards.get(lesson.id, []),
                    sorted(prerequisites.get(lesson.id, set())),
                )
                for lesson in unit.lessons
            ],
        )

    outlines = [unit_outline(u) for u in units]
    lessons = [lesson for u in outlines for lesson in u.lessons]
    return DraftRead(
        course_id=course.id,
        units=outlines,
        lesson_count=len(lessons),
        estimated_minutes=sum(lesson.estimated_minutes or 0 for lesson in lessons),
        published_version=version,
        has_unpublished_changes=changed,
    )


async def _lesson(
    service: Any, actor: Any, course_id: uuid.UUID, lesson_id: uuid.UUID
) -> LessonDraftRead:
    lesson, blocks, standards, prerequisites = await service.lesson(actor, course_id, lesson_id)
    outline = _lesson_outline(lesson, standards, prerequisites)
    return LessonDraftRead(
        **outline.model_dump(),
        unit_id=lesson.unit_id,
        content={"schema_version": lesson.content.get("schema_version", 1), "blocks": blocks},
    )


def _published_lesson_outline(lesson: dict[str, Any]) -> PublishedLessonOutline:
    return PublishedLessonOutline(
        id=lesson["id"],
        title=lesson["title"],
        summary=lesson.get("summary"),
        estimated_minutes=lesson.get("estimated_minutes"),
        difficulty=lesson.get("difficulty"),
        block_count=len(lesson["content"]["blocks"]),
        standards=lesson.get("standards", []),
        prerequisite_lesson_ids=lesson.get("prerequisite_lesson_ids", []),
    )


def _version_read(version: CourseVersion, current_id: uuid.UUID | None) -> VersionRead:
    return VersionRead(
        number=version.number,
        published_at=version.published_at,
        published_by_id=version.published_by_id,
        change_note=version.change_note,
        is_current=version.id == current_id,
        lesson_count=sum(len(u["lessons"]) for u in version.snapshot.get("units", [])),
    )


# --- the draft: structure ------------------------------------------------------------------


@authoring.get(
    "/draft",
    response_model=DraftRead,
    summary="The course's draft outline (authors)",
    description="Units and lessons in order. `has_unpublished_changes` tells whether it "
    "differs from what learners see.",
)
async def get_draft(course_id: uuid.UUID, actor: CurrentActor, service: AuthoringDep) -> DraftRead:
    return await _draft(service, actor, course_id)


@authoring.post(
    "/units", response_model=DraftRead, status_code=status.HTTP_201_CREATED, summary="Add a unit"
)
async def create_unit(
    course_id: uuid.UUID, payload: UnitCreate, actor: CurrentActor, service: AuthoringDep
) -> DraftRead:
    await service.create_unit(actor, course_id, payload)
    return await _draft(service, actor, course_id)


@authoring.put(
    "/units/order",
    response_model=DraftRead,
    summary="Reorder the units",
    description="`ids`: every unit of the course exactly once, in the new order.",
)
async def reorder_units(
    course_id: uuid.UUID, payload: OrderUpdate, actor: CurrentActor, service: AuthoringDep
) -> DraftRead:
    await service.reorder_units(actor, course_id, payload.ids)
    return await _draft(service, actor, course_id)


@authoring.patch("/units/{unit_id}", response_model=DraftRead, summary="Rename a unit")
async def update_unit(
    course_id: uuid.UUID,
    unit_id: uuid.UUID,
    payload: UnitUpdate,
    actor: CurrentActor,
    service: AuthoringDep,
) -> DraftRead:
    await service.update_unit(actor, course_id, unit_id, payload)
    return await _draft(service, actor, course_id)


@authoring.delete(
    "/units/{unit_id}",
    status_code=NO_CONTENT,
    summary="Delete a unit and its lessons (draft only)",
    description="Published versions keep them.",
)
async def delete_unit(
    course_id: uuid.UUID, unit_id: uuid.UUID, actor: CurrentActor, service: AuthoringDep
) -> Response:
    await service.delete_unit(actor, course_id, unit_id)
    return Response(status_code=NO_CONTENT)


@authoring.post(
    "/units/{unit_id}/lessons",
    response_model=LessonDraftRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add a lesson to a unit",
)
async def create_lesson(
    course_id: uuid.UUID,
    unit_id: uuid.UUID,
    payload: LessonCreate,
    actor: CurrentActor,
    service: AuthoringDep,
) -> LessonDraftRead:
    lesson = await service.create_lesson(actor, course_id, unit_id, payload)
    return await _lesson(service, actor, course_id, lesson.id)


@authoring.put(
    "/units/{unit_id}/lessons/order",
    response_model=DraftRead,
    summary="Reorder the lessons of a unit",
    description="`ids`: every lesson of the unit exactly once, in the new order.",
)
async def reorder_lessons(
    course_id: uuid.UUID,
    unit_id: uuid.UUID,
    payload: OrderUpdate,
    actor: CurrentActor,
    service: AuthoringDep,
) -> DraftRead:
    await service.reorder_lessons(actor, course_id, unit_id, payload.ids)
    return await _draft(service, actor, course_id)


# --- the draft: lessons --------------------------------------------------------------------


@authoring.get(
    "/draft/lessons/{lesson_id}",
    response_model=LessonDraftRead,
    summary="A draft lesson with its content (authors)",
)
async def get_draft_lesson(
    course_id: uuid.UUID, lesson_id: uuid.UUID, actor: CurrentActor, service: AuthoringDep
) -> LessonDraftRead:
    return await _lesson(service, actor, course_id, lesson_id)


@authoring.patch(
    "/lessons/{lesson_id}",
    response_model=LessonDraftRead,
    summary="Change a lesson's title, summary, duration or difficulty",
)
async def update_lesson(
    course_id: uuid.UUID,
    lesson_id: uuid.UUID,
    payload: LessonUpdate,
    actor: CurrentActor,
    service: AuthoringDep,
) -> LessonDraftRead:
    await service.update_lesson(actor, course_id, lesson_id, payload)
    return await _lesson(service, actor, course_id, lesson_id)


@authoring.put(
    "/lessons/{lesson_id}/content",
    response_model=LessonDraftRead,
    summary="Save the lesson's blocks",
    description="Replaces all blocks (so adding, editing, removing and reordering blocks is "
    "one save). Send the `revision` you loaded as `expected_revision`: 409 if someone saved "
    "in between. See docs/content.md for the block types.",
)
async def put_lesson_content(
    course_id: uuid.UUID,
    lesson_id: uuid.UUID,
    payload: LessonContentUpdate,
    actor: CurrentActor,
    service: AuthoringDep,
) -> LessonDraftRead:
    await service.put_content(actor, course_id, lesson_id, payload)
    return await _lesson(service, actor, course_id, lesson_id)


@authoring.post(
    "/lessons/{lesson_id}/move",
    response_model=DraftRead,
    summary="Move a lesson (within its unit or to another unit)",
)
async def move_lesson(
    course_id: uuid.UUID,
    lesson_id: uuid.UUID,
    payload: LessonMove,
    actor: CurrentActor,
    service: AuthoringDep,
) -> DraftRead:
    await service.move_lesson(actor, course_id, lesson_id, payload.unit_id, payload.position)
    return await _draft(service, actor, course_id)


@authoring.delete("/lessons/{lesson_id}", status_code=NO_CONTENT, summary="Delete a lesson")
async def delete_lesson(
    course_id: uuid.UUID, lesson_id: uuid.UUID, actor: CurrentActor, service: AuthoringDep
) -> Response:
    await service.delete_lesson(actor, course_id, lesson_id)
    return Response(status_code=NO_CONTENT)


@authoring.put(
    "/lessons/{lesson_id}/standards",
    response_model=LessonDraftRead,
    summary="Link a lesson to curriculum standards",
)
async def set_lesson_standards(
    course_id: uuid.UUID,
    lesson_id: uuid.UUID,
    payload: LessonStandardsUpdate,
    actor: CurrentActor,
    service: AuthoringDep,
) -> LessonDraftRead:
    await service.set_lesson_standards(actor, course_id, lesson_id, payload.standard_ids)
    return await _lesson(service, actor, course_id, lesson_id)


@authoring.put(
    "/lessons/{lesson_id}/prerequisites",
    response_model=LessonDraftRead,
    summary="Lessons of this course to finish first",
)
async def set_lesson_prerequisites(
    course_id: uuid.UUID,
    lesson_id: uuid.UUID,
    payload: LessonPrerequisitesUpdate,
    actor: CurrentActor,
    service: AuthoringDep,
) -> LessonDraftRead:
    await service.set_lesson_prerequisites(actor, course_id, lesson_id, payload.lesson_ids)
    return await _lesson(service, actor, course_id, lesson_id)


# --- publishing ----------------------------------------------------------------------------


@authoring.post(
    "/publish",
    response_model=VersionRead,
    summary="Publish the draft as a new version",
    description="Checks the whole draft first (media uploaded, image descriptions, video "
    "captions or transcripts, allowed embeds): 422 lists every problem in `details`. "
    "Publishing an unchanged draft returns the current version. Verified teachers and admins.",
)
async def publish(
    course_id: uuid.UUID,
    payload: PublishRequest,
    actor: CurrentActor,
    service: PublishingDep,
    request: RequestInfoDep,
) -> VersionRead:
    version = await service.publish(actor, course_id, payload.change_note, request)
    return _version_read(version, version.id)


@authoring.post(
    "/unpublish",
    status_code=NO_CONTENT,
    summary="Take the course out of the catalog",
    description="Versions are kept; publishing again brings it back.",
)
async def unpublish(
    course_id: uuid.UUID, actor: CurrentActor, service: PublishingDep, request: RequestInfoDep
) -> Response:
    await service.unpublish(actor, course_id, request)
    return Response(status_code=NO_CONTENT)


@authoring.get("/versions", response_model=list[VersionRead], summary="Published versions")
async def list_versions(
    course_id: uuid.UUID, actor: CurrentActor, service: PublishingDep
) -> list[VersionRead]:
    course, versions = await service.versions(actor, course_id)
    return [_version_read(v, course.published_version_id) for v in versions]


# --- reading published content -------------------------------------------------------------

VersionParam = Annotated[
    int | None,
    Query(ge=1, description="A specific version (default: the current one)."),
]


@reading.get(
    "/content",
    response_model=PublishedCourseRead,
    summary="The published outline of a course",
    description="What learners see. Anyone can read it while the course is published.",
)
async def published_content(
    course_id: uuid.UUID,
    service: PublishingDep,
    actor: OptionalActor,
    version: VersionParam = None,
) -> PublishedCourseRead:
    _, found = await service.content(actor, course_id, version)
    snapshot = found.snapshot
    return PublishedCourseRead(
        course_id=course_id,
        version=found.number,
        published_at=found.published_at,
        estimated_minutes=snapshot["course"].get("estimated_minutes") or 0,
        units=[
            PublishedUnitOutline(
                id=unit["id"],
                title=unit["title"],
                summary=unit.get("summary"),
                lessons=[_published_lesson_outline(lesson) for lesson in unit["lessons"]],
            )
            for unit in snapshot["units"]
        ],
    )


@reading.get(
    "/content/lessons/{lesson_id}",
    response_model=PublishedLessonRead,
    summary="A published lesson with its blocks",
    description="Media blocks carry short-lived signed links.",
)
async def published_lesson(
    course_id: uuid.UUID,
    lesson_id: uuid.UUID,
    service: PublishingDep,
    actor: OptionalActor,
    version: VersionParam = None,
) -> PublishedLessonRead:
    found, unit_id, lesson, blocks = await service.published_lesson(
        actor, course_id, lesson_id, version
    )
    return PublishedLessonRead(
        **_published_lesson_outline(lesson).model_dump(),
        course_id=course_id,
        version=found.number,
        unit_id=unit_id,
        blocks=blocks,
    )


# --- duplicate, export, import -------------------------------------------------------------


class ImportResult(BaseModel):
    course: CourseRead
    warnings: list[str]


@transfer.post(
    "/import",
    response_model=ImportResult,
    status_code=status.HTTP_201_CREATED,
    summary="Create a draft course from an exported document",
    description="The subject is matched by slug; standards by framework and code (unknown "
    "ones are skipped and listed in `warnings`). Media must be uploads you can use here.",
)
async def import_course(
    payload: ImportRequest, actor: CurrentActor, service: TransferDep, request: RequestInfoDep
) -> ImportResult:
    course, warnings = await service.import_document(
        actor, payload.document, slug=payload.slug, request=request
    )
    return ImportResult(course=CourseRead.model_validate(course), warnings=warnings)


@authoring.post(
    "/duplicate",
    response_model=CourseRead,
    status_code=status.HTTP_201_CREATED,
    summary="Copy a course into a new draft",
    description="Authors copy their own draft. Any teacher can copy a published course as a "
    "starting point: the copy is theirs.",
)
async def duplicate(
    course_id: uuid.UUID,
    payload: DuplicateRequest,
    actor: CurrentActor,
    service: TransferDep,
    request: RequestInfoDep,
) -> CourseRead:
    course = await service.duplicate(
        actor, course_id, title=payload.title, slug=payload.slug, request=request
    )
    return CourseRead.model_validate(course)


@reading.get(
    "/export",
    response_model=CourseDocument,
    summary="Export a course as a JSON document",
    description="`source=published` (anyone, for published courses) or `draft` (authors).",
)
async def export_course(
    course_id: uuid.UUID,
    service: TransferDep,
    actor: OptionalActor,
    source: Literal["published", "draft"] = "published",
) -> dict[str, Any]:
    return await service.export(actor, course_id, source)


# --- search --------------------------------------------------------------------------------


@search_router.get(
    "",
    response_model=Page[SearchResultRead],
    summary="Search published courses and lessons",
    description="Full-text search with ranking: words in titles count most. Supports "
    '`"exact phrases"`, `or`, and `-excluded` words. Each course is searched in its own '
    "language.",
)
async def search(
    query: Annotated[SearchQuery, Query()], service: SearchDep
) -> Page[SearchResultRead]:
    hits, total = await service.search(
        query.q,
        grade_level=query.grade_level,
        subject_slug=query.subject,
        kind=query.kind,
        limit=query.limit,
        offset=query.offset,
    )
    return Page[SearchResultRead](
        items=[SearchResultRead(**hit.__dict__) for hit in hits],
        total=total,
        limit=query.limit,
        offset=query.offset,
    )


router = APIRouter()
router.include_router(transfer)
router.include_router(authoring)
router.include_router(reading)
router.include_router(search_router)
