"""Idempotent demo data: ``python -m app.scripts.seed``."""

import asyncio
import uuid

from app.core.config import get_settings
from app.core.exceptions import ConflictError
from app.core.storage import S3Storage
from app.db import registry  # noqa: F401  (all models: courses reference users)
from app.db.session import create_engine, create_session_factory
from app.modules.audit.repository import AuditRepository
from app.modules.audit.service import AuditLog
from app.modules.catalog.domain import CourseStatus
from app.modules.catalog.repository import CourseRepository, SubjectRepository
from app.modules.catalog.schemas import CourseCreate, SubjectCreate
from app.modules.catalog.service import CourseService, SubjectService
from app.modules.curriculum.content import LessonContent
from app.modules.curriculum.models import CourseLesson, CourseUnit
from app.modules.curriculum.service import PublishingService
from app.modules.media.scanner import NoScanner
from app.modules.media.service import MediaService
from app.modules.standards.service import StandardsService

SUBJECTS = {
    "mathematics": "Mathematics",
    "science": "Science",
    "english-language-arts": "English Language Arts",
    "social-studies": "Social Studies",
    "computer-science": "Computer Science",
}

# (subject slug, grade, title, summary)
COURSES = [
    (
        "mathematics",
        1,
        "Number Sense and Place Value",
        "Count, compare and build numbers up to 120.",
    ),
    (
        "mathematics",
        4,
        "Fractions Fundamentals",
        "Equivalent fractions, comparing and adding with like denominators.",
    ),
    (
        "mathematics",
        8,
        "Introduction to Algebra",
        "Expressions, linear equations and the idea of a function.",
    ),
    ("mathematics", 10, "Geometry", "Proof, congruence, similarity and trigonometry basics."),
    ("mathematics", 12, "Calculus I", "Limits, derivatives and the first ideas of integration."),
    (
        "science",
        2,
        "Life Cycles and Habitats",
        "Follow plants and animals from birth to adulthood.",
    ),
    ("science", 5, "Matter and Energy", "States of matter, mixtures and how energy moves."),
    ("science", 7, "Earth Systems", "Plate tectonics, weather and the water cycle."),
    ("science", 9, "Biology", "Cells, genetics, evolution and ecosystems."),
    ("science", 11, "Chemistry", "Atomic structure, reactions and the mole."),
    (
        "english-language-arts",
        1,
        "Phonics and Early Reading",
        "Letter sounds, blending and first sentences.",
    ),
    (
        "english-language-arts",
        5,
        "Narrative Writing Workshop",
        "Plan, draft and revise a story with a strong voice.",
    ),
    (
        "english-language-arts",
        10,
        "Literary Analysis",
        "Close reading, theme and evidence-based argument.",
    ),
    (
        "social-studies",
        3,
        "Our Community and Map Skills",
        "Read maps and explore how communities work.",
    ),
    ("social-studies", 6, "Ancient Civilizations", "Mesopotamia, Egypt, Greece and Rome."),
    (
        "social-studies",
        12,
        "Civics and Government",
        "How laws are made and how citizens take part.",
    ),
    (
        "computer-science",
        6,
        "Computational Thinking",
        "Break problems down, spot patterns and write algorithms.",
    ),
    (
        "computer-science",
        9,
        "Programming with Python",
        "Variables, control flow, functions and small projects.",
    ),
]


# A few courses get a real outline, so the demo shows structure, content and search.
# course title -> [(unit, [(lesson, minutes, blocks)])]
OUTLINES: dict[str, list[tuple[str, list[tuple[str, int, list[dict[str, object]]]]]]] = {
    "Introduction to Algebra": [
        (
            "Expressions",
            [
                (
                    "Variables",
                    20,
                    [
                        {
                            "type": "text",
                            "markdown": "A **variable** is a letter that stands for a "
                            "number we do not know yet, like *x* in `x + 3 = 7`.",
                        },
                        {
                            "type": "interactive",
                            "title": "Words and symbols",
                            "activity": {
                                "widget": "matching",
                                "pairs": [
                                    {"left": "three more than x", "right": "x + 3"},
                                    {"left": "twice x", "right": "2x"},
                                    {"left": "x less two", "right": "x - 2"},
                                ],
                            },
                        },
                    ],
                ),
                (
                    "Evaluating expressions",
                    25,
                    [
                        {
                            "type": "text",
                            "markdown": "To evaluate `2x + 1` when x = 4, replace x "
                            "with 4: 2 * 4 + 1 = 9.",
                        },
                    ],
                ),
            ],
        ),
        (
            "Equations",
            [
                (
                    "Solving one-step equations",
                    30,
                    [
                        {
                            "type": "text",
                            "markdown": "Do the same thing to both sides: "
                            "`x + 3 = 7`, so `x = 7 - 3 = 4`.",
                        },
                        {
                            "type": "interactive",
                            "title": "Check yourself",
                            "activity": {
                                "widget": "flashcards",
                                "cards": [
                                    {"front": "x + 5 = 12", "back": "x = 7"},
                                    {"front": "3x = 18", "back": "x = 6"},
                                ],
                            },
                        },
                    ],
                ),
            ],
        ),
    ],
    "Phonics and Early Reading": [
        (
            "Letter sounds",
            [
                (
                    "Short vowels",
                    15,
                    [
                        {
                            "type": "text",
                            "markdown": "The short vowels are **a** as in *cat*, "
                            "**e** as in *bed*, **i** as in *pig*, **o** as in *dog*, **u** as in *sun*.",
                        },
                        {
                            "type": "interactive",
                            "title": "Sort the words",
                            "activity": {
                                "widget": "sorting",
                                "categories": [
                                    {"name": "short a", "items": ["cat", "hat", "map"]},
                                    {"name": "short o", "items": ["dog", "hot", "top"]},
                                ],
                            },
                        },
                    ],
                ),
            ],
        ),
    ],
}


async def main() -> None:
    settings = get_settings()
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    created = 0

    async with factory() as session:
        publishing = PublishingService(
            session,
            settings,
            AuditLog(AuditRepository(session)),
            MediaService(session, settings, S3Storage(settings), NoScanner()),
            StandardsService(session),
        )
        subject_service = SubjectService(session, SubjectRepository(session))
        course_service = CourseService(
            session, CourseRepository(session), SubjectRepository(session), lifecycle=publishing
        )

        subject_ids: dict[str, uuid.UUID] = {}
        for slug, name in SUBJECTS.items():
            try:
                created_subject = await subject_service.create(SubjectCreate(name=name, slug=slug))
                subject_ids[slug] = created_subject.id
            except ConflictError:
                existing = await SubjectRepository(session).get_by_slug(slug)
                assert existing is not None
                subject_ids[slug] = existing.id

        for subject_slug, grade, title, summary in COURSES:
            try:
                course = await course_service.create(
                    CourseCreate(
                        title=title,
                        summary=summary,
                        grade_level=grade,
                        subject_id=subject_ids[subject_slug],
                        # Courses with an outline are published after it is added.
                        status=CourseStatus.DRAFT if title in OUTLINES else CourseStatus.PUBLISHED,
                    )
                )
            except ConflictError:
                continue
            created += 1
            if title in OUTLINES:
                for unit_position, (unit_title, lessons) in enumerate(OUTLINES[title]):
                    unit = CourseUnit(course_id=course.id, title=unit_title, position=unit_position)
                    session.add(unit)
                    await session.flush()
                    for position, (lesson_title, minutes, blocks) in enumerate(lessons):
                        content = LessonContent.model_validate({"blocks": blocks})
                        session.add(
                            CourseLesson(
                                course_id=course.id,
                                unit_id=unit.id,
                                title=lesson_title,
                                position=position,
                                estimated_minutes=minutes,
                                content=content.to_json(),
                            )
                        )
                await session.flush()
                loaded = await CourseRepository(session).get(course.id)
                assert loaded is not None
                await publishing.published(loaded, None)
                await session.commit()

    await engine.dispose()
    print(f"Seed complete: {created} new course(s), {len(COURSES) - created} already present.")


if __name__ == "__main__":
    asyncio.run(main())
