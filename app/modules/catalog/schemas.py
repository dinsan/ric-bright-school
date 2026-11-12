import uuid
from datetime import datetime
from typing import Annotated, Self

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from app.core.pagination import PageParams
from app.modules.catalog.domain import (
    MAX_GRADE_LEVEL,
    MAX_TAGS_PER_COURSE,
    MIN_GRADE_LEVEL,
    TAG_PATTERN,
    CourseStatus,
    Difficulty,
)

GradeLevel = Annotated[int, Field(ge=MIN_GRADE_LEVEL, le=MAX_GRADE_LEVEL)]
Slug = Annotated[str, Field(max_length=80, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]
CourseSlug = Annotated[str, Field(max_length=120, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]
Language = Annotated[
    str, Field(max_length=16, pattern=r"^[a-z]{2,3}(-[A-Z]{2})?$", description="e.g. en, es, fr-CA")
]


def _normalise_tag(value: object) -> object:
    return " ".join(value.strip().lower().split()) if isinstance(value, str) else value


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


Tag = Annotated[str, BeforeValidator(_normalise_tag), Field(pattern=TAG_PATTERN)]
Tags = Annotated[list[Tag], AfterValidator(_dedupe), Field(max_length=MAX_TAGS_PER_COURSE)]


class SubjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    slug: Slug | None = Field(default=None, description="Derived from the name when omitted.")
    description: str | None = Field(default=None, max_length=2000)


class SubjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    name: str
    description: str | None


class CourseCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    slug: CourseSlug | None = Field(
        default=None, description="Derived from the title when omitted."
    )
    summary: str = Field(min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=20_000)
    grade_level: GradeLevel
    subject_id: uuid.UUID
    status: CourseStatus = CourseStatus.DRAFT
    language: Language = "en"
    difficulty: Difficulty | None = None
    tags: Tags = Field(default_factory=list)


class CourseUpdate(BaseModel):
    """Partial update: only the fields present in the body change.

    The slug never follows a title change automatically, so published URLs stay stable.
    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    slug: CourseSlug | None = None
    summary: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=20_000)
    grade_level: GradeLevel | None = None
    subject_id: uuid.UUID | None = None
    status: CourseStatus | None = None
    language: Language | None = None
    difficulty: Difficulty | None = None
    tags: Tags | None = None

    @model_validator(mode="after")
    def _reject_null_for_required_fields(self) -> Self:
        # Only `description` and `difficulty` may be cleared with an explicit null.
        nulled = sorted(
            name
            for name in self.model_fields_set
            if name not in {"description", "difficulty"} and getattr(self, name) is None
        )
        if nulled:
            raise ValueError(f"These fields cannot be null: {', '.join(nulled)}.")
        return self


class CourseRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    title: str
    summary: str
    description: str | None
    grade_level: int
    status: CourseStatus
    published_at: datetime | None
    author_id: uuid.UUID | None
    language: str
    difficulty: Difficulty | None
    tags: list[str]
    estimated_minutes: int | None
    published_version_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    subject: SubjectRead


class CourseListQuery(PageParams):
    grade_level: GradeLevel | None = None
    subject: str | None = Field(default=None, max_length=80, description="Subject slug.")
    q: str | None = Field(default=None, max_length=100, description="Title search.")
    status: CourseStatus = Field(
        default=CourseStatus.PUBLISHED,
        description="Values other than `published` need a teacher (own courses) or an admin.",
    )
    tag: Tag | None = None
    difficulty: Difficulty | None = None
    language: Language | None = None


class PrerequisitesUpdate(BaseModel):
    course_ids: list[uuid.UUID] = Field(max_length=20)


class RecommendedQuery(BaseModel):
    grade_level: GradeLevel | None = Field(
        default=None, description="Defaults to the signed-in student's grade."
    )
    subject: str | None = Field(default=None, max_length=80, description="Subject slug.")
    limit: int = Field(default=12, ge=1, le=50)


# --- learning paths (BE-CAT-08) -----------------------------------------------------------


class PathCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    slug: CourseSlug | None = Field(default=None, description="Derived from the title if omitted.")
    description: str | None = Field(default=None, max_length=5000)
    grade_level: GradeLevel
    subject_id: uuid.UUID | None = None
    is_published: bool = False
    course_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)


class PathUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    grade_level: GradeLevel | None = None
    subject_id: uuid.UUID | None = None
    is_published: bool | None = None
    course_ids: list[uuid.UUID] | None = Field(default=None, max_length=50)


class PathRead(BaseModel):
    id: uuid.UUID
    slug: str
    title: str
    description: str | None
    grade_level: int
    subject: SubjectRead | None
    is_published: bool
    courses: list["CourseRead"] = Field(description="In order. Only published courses.")


class PathQuery(PageParams):
    grade_level: GradeLevel | None = None
    subject: str | None = Field(default=None, max_length=80, description="Subject slug.")
