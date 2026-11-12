import uuid
from typing import Annotated, Self

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from app.core.pagination import PageParams
from app.modules.catalog.schemas import GradeLevel


def _upper(value: object) -> object:
    return value.strip().upper() if isinstance(value, str) else value


FrameworkCode = Annotated[
    str,
    BeforeValidator(_upper),
    Field(min_length=2, max_length=40, pattern=r"^[A-Z0-9][A-Z0-9._-]*$"),
]
CountryCode = Annotated[str, BeforeValidator(_upper), Field(pattern=r"^[A-Z]{2}$")]


class FrameworkCreate(BaseModel):
    code: FrameworkCode
    name: str = Field(min_length=1, max_length=200)
    country_code: CountryCode = Field(description="ISO 3166-1 alpha-2, e.g. US, GB, IN.")
    subject_id: uuid.UUID | None = None
    description: str | None = Field(default=None, max_length=5000)


class FrameworkRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    country_code: str
    subject_id: uuid.UUID | None
    description: str | None


class StandardIn(BaseModel):
    code: str = Field(min_length=1, max_length=60)
    description: str = Field(min_length=1, max_length=5000)
    grade_min: GradeLevel | None = None
    grade_max: GradeLevel | None = None

    @model_validator(mode="after")
    def _order(self) -> Self:
        if self.grade_min and self.grade_max and self.grade_min > self.grade_max:
            raise ValueError("grade_min must not be above grade_max.")
        return self


class StandardsImport(BaseModel):
    standards: list[StandardIn] = Field(min_length=1, max_length=5000)


class ImportResult(BaseModel):
    created: int
    updated: int


class StandardRead(BaseModel):
    id: uuid.UUID
    framework: str  # the framework code
    code: str
    description: str
    grade_min: int | None
    grade_max: int | None


class StandardQuery(PageParams):
    framework: str | None = Field(default=None, max_length=40, description="Framework code.")
    country: str | None = Field(default=None, max_length=2)
    grade_level: GradeLevel | None = None
    q: str | None = Field(default=None, max_length=100, description="Code or description.")
