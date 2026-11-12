"""Framework-free domain vocabulary for the catalog."""

from enum import StrEnum

MIN_GRADE_LEVEL = 1
MAX_GRADE_LEVEL = 12


class CourseStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class Difficulty(StrEnum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"


MAX_TAGS_PER_COURSE = 10
TAG_PATTERN = r"^[a-z0-9][a-z0-9 -]{0,38}[a-z0-9]$|^[a-z0-9]$"

# Course language -> PostgreSQL text-search configuration (stemming and stop words).
# Anything else is searched with "simple" (no stemming), which works for every language.
SEARCH_CONFIGS: dict[str, str] = {
    "en": "english",
    "es": "spanish",
    "fr": "french",
    "de": "german",
    "pt": "portuguese",
    "it": "italian",
    "nl": "dutch",
    "sv": "swedish",
    "da": "danish",
    "fi": "finnish",
    "no": "norwegian",
    "ru": "russian",
    "tr": "turkish",
}


def search_config(language: str) -> str:
    return SEARCH_CONFIGS.get(language.split("-")[0].lower(), "simple")
