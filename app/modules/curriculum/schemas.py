import uuid
from datetime import datetime
from typing import Any, ClassVar, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.pagination import PageParams
from app.modules.catalog.domain import Difficulty
from app.modules.catalog.schemas import CourseSlug, GradeLevel, Language, Tags
from app.modules.curriculum.content import LessonContent

Title = Field(min_length=1, max_length=200)
Summary = Field(default=None, max_length=1000)
Minutes = Field(default=None, ge=1, le=600)


class _Patch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    _nullable: ClassVar[frozenset[str]] = frozenset()

    @model_validator(mode="after")
    def _reject_nulls(self) -> Self:
        nullable = type(self)._nullable
        nulled = sorted(
            n for n in self.model_fields_set if n not in nullable and getattr(self, n) is None
        )
        if nulled:
            raise ValueError(f"These fields cannot be null: {', '.join(nulled)}.")
        return self


# --- structure -----------------------------------------------------------------------------


class UnitCreate(BaseModel):
    title: str = Title
    summary: str | None = Summary
    position: int | None = Field(default=None, ge=0, description="Default: at the end.")


class UnitUpdate(_Patch):
    _nullable: ClassVar[frozenset[str]] = frozenset({"summary"})
    title: str | None = Field(default=None, min_length=1, max_length=200)
    summary: str | None = Summary


class OrderUpdate(BaseModel):
    """The complete new order: every id exactly once."""

    ids: list[uuid.UUID] = Field(max_length=500)


class LessonCreate(BaseModel):
    title: str = Title
    summary: str | None = Summary
    position: int | None = Field(default=None, ge=0, description="Default: at the end.")
    estimated_minutes: int | None = Minutes
    difficulty: Difficulty | None = None
    content: LessonContent | None = None


class LessonUpdate(_Patch):
    _nullable: ClassVar[frozenset[str]] = frozenset(
        {"summary", "estimated_minutes", "difficulty", "expected_revision"}
    )
    title: str | None = Field(default=None, min_length=1, max_length=200)
    summary: str | None = Summary
    estimated_minutes: int | None = Minutes
    difficulty: Difficulty | None = None
    expected_revision: int | None = Field(
        default=None,
        description="If given, the update fails (409) when someone else changed "
        "the lesson since you loaded it.",
    )


class LessonContentUpdate(BaseModel):
    content: LessonContent
    expected_revision: int = Field(
        description="The `revision` you loaded. 409 if the lesson changed meanwhile."
    )


class LessonMove(BaseModel):
    unit_id: uuid.UUID
    position: int | None = Field(default=None, ge=0, description="Default: at the end.")


class LessonStandardsUpdate(BaseModel):
    standard_ids: list[uuid.UUID] = Field(max_length=30)


class LessonPrerequisitesUpdate(BaseModel):
    lesson_ids: list[uuid.UUID] = Field(max_length=20)


class LessonOutline(BaseModel):
    id: uuid.UUID
    title: str
    summary: str | None
    position: int
    estimated_minutes: int | None
    difficulty: Difficulty | None
    block_count: int
    standard_ids: list[uuid.UUID]
    prerequisite_lesson_ids: list[uuid.UUID]
    revision: int
    updated_at: datetime


class UnitOutline(BaseModel):
    id: uuid.UUID
    title: str
    summary: str | None
    position: int
    lessons: list[LessonOutline]


class DraftRead(BaseModel):
    course_id: uuid.UUID
    units: list[UnitOutline]
    lesson_count: int
    estimated_minutes: int
    published_version: int | None
    has_unpublished_changes: bool = Field(
        description="The draft differs from the published version (or nothing is published)."
    )


class LessonDraftRead(LessonOutline):
    unit_id: uuid.UUID
    content: dict[str, Any] = Field(
        description="Blocks as stored; blocks with media get a `media` preview (url, status, "
        "alt text)."
    )


# --- publishing ----------------------------------------------------------------------------


class PublishRequest(BaseModel):
    change_note: str | None = Field(default=None, max_length=500)


class VersionRead(BaseModel):
    number: int
    published_at: datetime
    published_by_id: uuid.UUID | None
    change_note: str | None
    is_current: bool
    lesson_count: int


class PublishedLessonOutline(BaseModel):
    id: uuid.UUID
    title: str
    summary: str | None
    estimated_minutes: int | None
    difficulty: Difficulty | None
    block_count: int
    standards: list[dict[str, Any]]
    prerequisite_lesson_ids: list[uuid.UUID]


class PublishedUnitOutline(BaseModel):
    id: uuid.UUID
    title: str
    summary: str | None
    lessons: list[PublishedLessonOutline]


class PublishedCourseRead(BaseModel):
    course_id: uuid.UUID
    version: int
    published_at: datetime
    estimated_minutes: int
    units: list[PublishedUnitOutline]


class PublishedLessonRead(PublishedLessonOutline):
    course_id: uuid.UUID
    version: int
    unit_id: uuid.UUID
    blocks: list[dict[str, Any]] = Field(
        description="Blocks with media also carry `media` (signed `url`, `alt_text`, ...)."
    )


# --- duplicate, export, import (BE-CAT-03, BE-CAT-10) -------------------------------------


class DuplicateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    slug: CourseSlug | None = None


class ExportedStandard(BaseModel):
    framework: str
    code: str


class ExportedLesson(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: uuid.UUID | None = None
    title: str = Title
    summary: str | None = Summary
    estimated_minutes: int | None = Minutes
    difficulty: Difficulty | None = None
    content: LessonContent = Field(default_factory=LessonContent)
    standards: list[ExportedStandard] = Field(default_factory=list, max_length=30)
    prerequisite_lesson_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)


class ExportedUnit(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str = Title
    summary: str | None = Summary
    lessons: list[ExportedLesson] = Field(default_factory=list, max_length=200)


class ExportedCourse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str = Title
    summary: str = Field(min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=20_000)
    grade_level: GradeLevel
    language: Language = "en"
    difficulty: Difficulty | None = None
    tags: Tags = Field(default_factory=list)
    subject: dict[str, Any] = Field(description="At least `slug`; matched to a local subject.")


class CourseDocument(BaseModel):
    """The exchange format (also the shape of published versions): one JSON file."""

    model_config = ConfigDict(extra="ignore")

    format: Literal["lumina-course"] = "lumina-course"
    format_version: Literal[1] = 1
    course: ExportedCourse
    units: list[ExportedUnit] = Field(default_factory=list, max_length=100)


class ImportRequest(BaseModel):
    document: CourseDocument
    slug: CourseSlug | None = Field(default=None, description="Default: from the title.")


# --- search (BE-CAT-07) --------------------------------------------------------------------


class SearchQuery(PageParams):
    q: str = Field(min_length=1, max_length=200, description='Words, "phrases", -excluded.')
    grade_level: GradeLevel | None = None
    subject: str | None = Field(default=None, max_length=80, description="Subject slug.")
    kind: Literal["course", "lesson"] | None = None


class SearchResultRead(BaseModel):
    kind: Literal["course", "lesson"]
    course_id: uuid.UUID
    course_slug: str
    course_title: str
    lesson_id: uuid.UUID | None
    lesson_title: str | None
    snippet: str = Field(description="Plain text around the match (no markup).")
    rank: float
