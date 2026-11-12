"""Behaviour that depends on PostgreSQL itself: constraints, races, indexes."""

import asyncio
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.catalog.domain import CourseStatus
from app.modules.catalog.models import Course, Subject
from app.modules.catalog.repository import CourseRepository, SubjectRepository
from tests.conftest import Account, make_course, make_subject


async def _add_course(session: AsyncSession, grade_level: int) -> None:
    subject = Subject(slug=f"s-{uuid.uuid4().hex[:8]}", name="Subject")
    session.add_all(
        [
            subject,
            Course(
                slug=f"c-{uuid.uuid4().hex[:8]}",
                title="Course",
                summary="Summary",
                grade_level=grade_level,
                status=CourseStatus.DRAFT,
                subject=subject,
            ),
        ]
    )
    await session.commit()


# --- grade_level check constraint ---------------------------------------------------------


@pytest.mark.parametrize("grade_level", [0, 13, -1])
async def test_grade_level_check_constraint_rejects_out_of_range(
    session: AsyncSession, grade_level: int
) -> None:
    # Bypasses the Pydantic validation on purpose: the database is the last line of defence.
    with pytest.raises(IntegrityError, match="ck_courses_grade_level_range"):
        await _add_course(session, grade_level)


@pytest.mark.parametrize("grade_level", [1, 12])
async def test_grade_level_check_constraint_accepts_bounds(
    session: AsyncSession, grade_level: int
) -> None:
    await _add_course(session, grade_level)
    assert await session.scalar(select(Course.grade_level)) == grade_level


async def test_grade_level_check_constraint_applies_to_raw_sql(
    client: AsyncClient, admin: Account, session: AsyncSession
) -> None:
    subject = await make_subject(client, headers=admin.headers)
    course = await make_course(client, subject["id"], headers=admin.headers)
    with pytest.raises(IntegrityError, match="ck_courses_grade_level_range"):
        await session.execute(
            text("UPDATE courses SET grade_level = 13 WHERE id = :id"), {"id": course["id"]}
        )


# --- unique-slug race ---------------------------------------------------------------------


async def test_lost_subject_slug_race_is_409_not_500(
    client: AsyncClient, admin: Account, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pre-insert lookup passed, but another request inserted the slug first.

    PostgreSQL raises the unique violation at flush time, so this proves the conflict
    translation covers the flush, not only the commit.
    """
    await make_subject(client, "Science", headers=admin.headers)

    async def lookup_misses(self: SubjectRepository, slug: str) -> None:
        return None

    monkeypatch.setattr(SubjectRepository, "get_by_slug", lookup_misses)
    response = await client.post(
        "/api/v1/subjects", json={"name": "Science"}, headers=admin.headers
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "conflict"


async def test_lost_course_slug_race_is_409_not_500(
    client: AsyncClient, admin: Account, monkeypatch: pytest.MonkeyPatch
) -> None:
    subject = await make_subject(client, headers=admin.headers)
    await make_course(client, subject["id"], headers=admin.headers)

    async def never_exists(self: CourseRepository, slug: str, **_: object) -> bool:
        return False

    monkeypatch.setattr(CourseRepository, "slug_exists", never_exists)
    response = await client.post(
        "/api/v1/courses",
        json={
            "title": "Fractions Fundamentals",
            "summary": "Same slug",
            "grade_level": 4,
            "subject_id": subject["id"],
        },
        headers=admin.headers,
    )
    assert response.status_code == 409, response.text

    other = await make_course(client, subject["id"], headers=admin.headers, title="Other course")
    response = await client.patch(
        f"/api/v1/courses/{other['id']}",
        json={"slug": "fractions-fundamentals"},
        headers=admin.headers,
    )
    assert response.status_code == 409, response.text


async def test_concurrent_creates_with_same_slug_yield_one_winner(
    client: AsyncClient, admin: Account
) -> None:
    attempts = 8
    responses = await asyncio.gather(
        *(
            client.post("/api/v1/subjects", json={"name": "Geography"}, headers=admin.headers)
            for _ in range(attempts)
        )
    )
    statuses = sorted(r.status_code for r in responses)
    assert statuses == [201] + [409] * (attempts - 1)
    assert len((await client.get("/api/v1/subjects")).json()) == 1


# --- trigram index (pg_trgm) --------------------------------------------------------------


async def test_title_search_can_use_trigram_index(session: AsyncSession) -> None:
    # Same predicate shape the repository builds: ILIKE with ESCAPE and a bound parameter.
    # Seq scans are disabled because an empty table would otherwise (rightly) be scanned.
    await session.execute(text("SET LOCAL enable_seqscan = off"))
    explain = text("EXPLAIN SELECT id FROM courses WHERE title ILIKE :pattern ESCAPE '\\'")
    plan = (await session.execute(explain, {"pattern": "%alge%"})).scalars().all()
    assert any("ix_courses_title_trgm" in line for line in plan), "\n".join(plan)
