import uuid
from datetime import datetime
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from app.modules.catalog.schemas import SubjectRead
from app.modules.classes.domain import SCHOOL_YEAR_PATTERN
from app.modules.identity.domain import AgeBand
from app.modules.identity.schemas import (
    DisplayName,
    GradeLevel,
    PicturePassword,
    StudentPassword,
    Username,
)

ClassName = Annotated[str, Field(min_length=1, max_length=100)]
SchoolYear = Annotated[str, Field(pattern=SCHOOL_YEAR_PATTERN, description="e.g. 2026-2027")]


class ClassCreate(BaseModel):
    name: ClassName
    grade_level: GradeLevel | None = None
    subject_id: uuid.UUID | None = None
    school_year: SchoolYear | None = None
    picture_sign_in: bool | None = Field(
        default=None,
        description="Let Grade 1-3 children sign in with the class code and pictures. "
        "Defaults to on for Grades 1 to 3.",
    )


class ClassUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: ClassName | None = None
    grade_level: GradeLevel | None = None
    subject_id: uuid.UUID | None = None
    school_year: SchoolYear | None = None
    picture_sign_in: bool | None = None

    @model_validator(mode="after")
    def _reject_nulls(self) -> Self:
        # grade_level, subject_id and school_year may be cleared with null.
        nulled = sorted(
            n
            for n in self.model_fields_set & {"name", "picture_sign_in"}
            if getattr(self, n) is None
        )
        if nulled:
            raise ValueError(f"These fields cannot be null: {', '.join(nulled)}.")
        return self


class ClassRead(BaseModel):
    id: uuid.UUID
    name: str
    grade_level: int | None
    subject: SubjectRead | None
    school_year: str | None
    teacher_id: uuid.UUID
    teacher_name: str
    picture_sign_in: bool
    archived: bool
    student_count: int
    join_code: str | None = Field(description="Only shown to the class's teacher (and admins).")
    created_at: datetime


class JoinClassRequest(BaseModel):
    code: str = Field(min_length=8, max_length=20)


class ClassStudentCreate(BaseModel):
    """A new student account created by the teacher, already in the class."""

    display_name: DisplayName
    grade_level: GradeLevel | None = Field(
        default=None, description="Defaults to the class's grade."
    )
    email: EmailStr | None = Field(
        default=None, description="Grade 4 and up: the student sets a password by email."
    )
    username: Username | None = None
    password: StudentPassword | None = None
    picture_password: PicturePassword | None = None


class RosterStudentRead(BaseModel):
    id: uuid.UUID
    display_name: str
    username: str | None
    email: str | None
    grade_level: int | None
    age_band: AgeBand
    sign_in_methods: list[str]
    managed_by_guardians: bool = Field(
        description="True if the student has no email: parents and teachers set up sign-in."
    )
    enrolled_at: datetime
