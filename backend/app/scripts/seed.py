"""Idempotent demo data: ``python -m app.scripts.seed``."""

import asyncio
import uuid

from app.core.config import get_settings
from app.core.exceptions import ConflictError
from app.db.session import create_engine, create_session_factory
from app.modules.catalog.domain import CourseStatus
from app.modules.catalog.repository import CourseRepository, SubjectRepository
from app.modules.catalog.schemas import CourseCreate, SubjectCreate
from app.modules.catalog.service import CourseService, SubjectService

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


async def main() -> None:
    engine = create_engine(get_settings())
    factory = create_session_factory(engine)
    created = 0

    async with factory() as session:
        subject_service = SubjectService(session, SubjectRepository(session))
        course_service = CourseService(
            session, CourseRepository(session), SubjectRepository(session)
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
                await course_service.create(
                    CourseCreate(
                        title=title,
                        summary=summary,
                        grade_level=grade,
                        subject_id=subject_ids[subject_slug],
                        status=CourseStatus.PUBLISHED,
                    )
                )
                created += 1
            except ConflictError:
                continue

    await engine.dispose()
    print(f"Seed complete: {created} new course(s), {len(COURSES) - created} already present.")


if __name__ == "__main__":
    asyncio.run(main())
