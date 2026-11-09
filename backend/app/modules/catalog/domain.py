"""Framework-free domain vocabulary for the catalog."""

from enum import StrEnum

MIN_GRADE_LEVEL = 1
MAX_GRADE_LEVEL = 12


class CourseStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"
