import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from app.core.pagination import PageParams
from app.modules.catalog.domain import MAX_GRADE_LEVEL, MIN_GRADE_LEVEL, CourseStatus

GradeLevel = Annotated[int, Field(ge=MIN_GRADE_LEVEL, le=MAX_GRADE_LEVEL)]
Slug = Annotated[str, Field(max_length=80, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]


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
    slug: Annotated[str, Field(max_length=120, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")] | None = (
        Field(default=None, description="Derived from the title when omitted.")
    )
    summary: str = Field(min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=20_000)
    grade_level: GradeLevel
    subject_id: uuid.UUID
    status: CourseStatus = CourseStatus.DRAFT


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
    created_at: datetime
    updated_at: datetime
    subject: SubjectRead


class CourseListQuery(PageParams):
    grade_level: GradeLevel | None = None
    subject: str | None = Field(default=None, max_length=80, description="Subject slug.")
    q: str | None = Field(default=None, max_length=100, description="Title search.")
    status: CourseStatus = CourseStatus.PUBLISHED
