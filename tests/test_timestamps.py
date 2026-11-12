"""updated_at is maintained by a database trigger, so every kind of UPDATE moves it."""

import uuid
from datetime import datetime

from httpx import AsyncClient
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.db.base import Base
from app.modules.catalog.models import Course, Subject
from tests.conftest import Account, make_course, make_subject


async def _timestamps(engine: AsyncEngine, table: str, row_id: str) -> tuple[datetime, datetime]:
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(f"SELECT created_at, updated_at FROM {table} WHERE id = :id"), {"id": row_id}
            )
        ).one()
    return row.created_at, row.updated_at


async def test_raw_sql_update_moves_updated_at(
    client: AsyncClient, admin: Account, engine: AsyncEngine
) -> None:
    subject = await make_subject(client, headers=admin.headers)
    course = await make_course(client, subject["id"], headers=admin.headers)
    created_at, before = await _timestamps(engine, "courses", course["id"])

    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE courses SET summary = 'raw' WHERE id = :id"), {"id": course["id"]}
        )

    created_after, after = await _timestamps(engine, "courses", course["id"])
    assert after > before
    assert created_after == created_at


async def test_bulk_orm_update_moves_updated_at(
    client: AsyncClient, admin: Account, engine: AsyncEngine
) -> None:
    subject = await make_subject(client, headers=admin.headers)
    first = await make_course(client, subject["id"], headers=admin.headers, title="First")
    second = await make_course(client, subject["id"], headers=admin.headers, title="Second")
    before = {c["id"]: (await _timestamps(engine, "courses", c["id"]))[1] for c in (first, second)}
    _, subject_before = await _timestamps(engine, "subjects", subject["id"])

    async with engine.begin() as conn:
        await conn.execute(update(Course).values(grade_level=7))  # bypasses ORM onupdate hooks
        await conn.execute(update(Subject).values(description="bulk"))

    for course_id, old in before.items():
        assert (await _timestamps(engine, "courses", course_id))[1] > old
    _, subject_after = await _timestamps(engine, "subjects", subject["id"])
    assert subject_after > subject_before


async def test_orm_update_returns_the_database_value(
    client: AsyncClient, admin: Account, engine: AsyncEngine
) -> None:
    subject = await make_subject(client, headers=admin.headers)
    course = await make_course(client, subject["id"], headers=admin.headers)

    response = await client.patch(
        f"/api/v1/courses/{course['id']}", json={"title": "Renamed"}, headers=admin.headers
    )
    _, stored = await _timestamps(engine, "courses", course["id"])
    # The API shows exactly what the trigger wrote (fetched with RETURNING), not a local guess.
    assert datetime.fromisoformat(response.json()["updated_at"]) == stored


async def test_every_timestamped_table_has_the_trigger(session: AsyncSession) -> None:
    """Guard for future tables: using TimestampMixin without the trigger fails here."""
    timestamped = {t.name for t in Base.metadata.sorted_tables if "updated_at" in t.c}
    rows = await session.execute(
        text(
            "SELECT c.relname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
            "JOIN pg_proc p ON p.oid = t.tgfoid "
            "WHERE NOT t.tgisinternal AND p.proname = 'set_updated_at'"
        )
    )
    assert timestamped
    assert timestamped <= set(rows.scalars())


async def test_raw_insert_gets_server_timestamps(session: AsyncSession) -> None:
    subject_id = uuid.uuid4()
    await session.execute(
        text("INSERT INTO subjects (id, slug, name) VALUES (:id, 'raw', 'Raw')"),
        {"id": subject_id},
    )
    stored = await session.scalar(select(Subject).where(Subject.id == subject_id))
    assert stored is not None
    assert stored.created_at is not None
    assert stored.updated_at is not None
