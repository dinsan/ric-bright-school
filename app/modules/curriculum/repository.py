import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.modules.curriculum.models import (
    CourseLesson,
    CourseUnit,
    CourseVersion,
    lesson_prerequisites,
    lesson_standards,
)


@dataclass(frozen=True)
class SearchHit:
    kind: str
    course_id: uuid.UUID
    course_slug: str
    course_title: str
    lesson_id: uuid.UUID | None
    lesson_title: str | None
    snippet: str
    rank: float


class CurriculumRepository:
    """Data access only: no business rules, no commits."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, obj: object) -> None:
        self._session.add(obj)
        await self._session.flush()

    async def delete(self, obj: object) -> None:
        await self._session.delete(obj)
        await self._session.flush()

    # --- structure ------------------------------------------------------------------------

    async def units(self, course_id: uuid.UUID) -> Sequence[CourseUnit]:
        """The course's units, in order, each with its lessons in order."""
        stmt = (
            select(CourseUnit)
            .options(selectinload(CourseUnit.lessons))
            .where(CourseUnit.course_id == course_id)
            .order_by(CourseUnit.position)
            .execution_options(populate_existing=True)
        )
        return (await self._session.scalars(stmt)).all()

    async def get_unit(self, course_id: uuid.UUID, unit_id: uuid.UUID) -> CourseUnit | None:
        stmt = select(CourseUnit).where(CourseUnit.id == unit_id, CourseUnit.course_id == course_id)
        return await self._session.scalar(stmt)

    async def get_lesson(
        self, course_id: uuid.UUID, lesson_id: uuid.UUID, *, for_update: bool = False
    ) -> CourseLesson | None:
        stmt = select(CourseLesson).where(
            CourseLesson.id == lesson_id, CourseLesson.course_id == course_id
        )
        if for_update:
            stmt = stmt.with_for_update()
        return await self._session.scalar(stmt)

    async def count_units(self, course_id: uuid.UUID) -> int:
        stmt = select(func.count(CourseUnit.id)).where(CourseUnit.course_id == course_id)
        return int(await self._session.scalar(stmt) or 0)

    async def count_lessons(self, unit_id: uuid.UUID) -> int:
        stmt = select(func.count(CourseLesson.id)).where(CourseLesson.unit_id == unit_id)
        return int(await self._session.scalar(stmt) or 0)

    async def shift_units(self, course_id: uuid.UUID, *, from_position: int, by: int) -> None:
        """Move every unit at ``from_position`` or later by ``by`` (+1 opens a gap, -1
        closes one). Position uniqueness is checked at commit (deferred constraint)."""
        await self._session.execute(
            update(CourseUnit)
            .where(CourseUnit.course_id == course_id, CourseUnit.position >= from_position)
            .values(position=CourseUnit.position + by)
        )

    async def shift_lessons(self, unit_id: uuid.UUID, *, from_position: int, by: int) -> None:
        await self._session.execute(
            update(CourseLesson)
            .where(CourseLesson.unit_id == unit_id, CourseLesson.position >= from_position)
            .values(position=CourseLesson.position + by)
        )

    async def lesson_ids(self, course_id: uuid.UUID) -> set[uuid.UUID]:
        stmt = select(CourseLesson.id).where(CourseLesson.course_id == course_id)
        return set((await self._session.scalars(stmt)).all())

    # --- standards and prerequisites of lessons -------------------------------------------

    async def standards_by_lesson(
        self, lesson_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, list[uuid.UUID]]:
        if not lesson_ids:
            return {}
        rows = await self._session.execute(
            select(lesson_standards).where(lesson_standards.c.lesson_id.in_(list(lesson_ids)))
        )
        result: dict[uuid.UUID, list[uuid.UUID]] = {}
        for lesson_id, standard_id in rows.all():
            result.setdefault(lesson_id, []).append(standard_id)
        return result

    async def set_lesson_standards(
        self, lesson_id: uuid.UUID, standard_ids: Sequence[uuid.UUID]
    ) -> None:
        await self._session.execute(
            delete(lesson_standards).where(lesson_standards.c.lesson_id == lesson_id)
        )
        if standard_ids:
            await self._session.execute(
                insert(lesson_standards),
                [{"lesson_id": lesson_id, "standard_id": s} for s in standard_ids],
            )

    async def prerequisites_by_lesson(
        self, course_id: uuid.UUID
    ) -> dict[uuid.UUID, set[uuid.UUID]]:
        stmt = (
            select(lesson_prerequisites)
            .join(CourseLesson, CourseLesson.id == lesson_prerequisites.c.lesson_id)
            .where(CourseLesson.course_id == course_id)
        )
        result: dict[uuid.UUID, set[uuid.UUID]] = {}
        for lesson_id, prerequisite_id in (await self._session.execute(stmt)).all():
            result.setdefault(lesson_id, set()).add(prerequisite_id)
        return result

    async def set_lesson_prerequisites(
        self, lesson_id: uuid.UUID, prerequisite_ids: Sequence[uuid.UUID]
    ) -> None:
        await self._session.execute(
            delete(lesson_prerequisites).where(lesson_prerequisites.c.lesson_id == lesson_id)
        )
        if prerequisite_ids:
            await self._session.execute(
                insert(lesson_prerequisites),
                [{"lesson_id": lesson_id, "prerequisite_id": p} for p in prerequisite_ids],
            )

    # --- versions -------------------------------------------------------------------------

    async def latest_version_number(self, course_id: uuid.UUID) -> int:
        stmt = select(func.max(CourseVersion.number)).where(CourseVersion.course_id == course_id)
        return int(await self._session.scalar(stmt) or 0)

    async def get_version(self, version_id: uuid.UUID) -> CourseVersion | None:
        return await self._session.get(CourseVersion, version_id)

    async def get_version_by_number(
        self, course_id: uuid.UUID, number: int
    ) -> CourseVersion | None:
        stmt = select(CourseVersion).where(
            CourseVersion.course_id == course_id, CourseVersion.number == number
        )
        return await self._session.scalar(stmt)

    async def versions(self, course_id: uuid.UUID) -> Sequence[CourseVersion]:
        stmt = (
            select(CourseVersion)
            .where(CourseVersion.course_id == course_id)
            .order_by(CourseVersion.number.desc())
        )
        return (await self._session.scalars(stmt)).all()

    # --- search index (BE-CAT-07) ---------------------------------------------------------

    async def clear_search(self, course_id: uuid.UUID) -> None:
        await self._session.execute(
            text("DELETE FROM search_documents WHERE course_id = :course_id"),
            {"course_id": course_id},
        )

    async def index_documents(self, rows: Sequence[dict[str, Any]]) -> None:
        """Titles weigh more than summaries, which weigh more than the body text."""
        if not rows:
            return
        await self._session.execute(
            text(
                """
                INSERT INTO search_documents
                    (id, course_id, lesson_id, kind, config, title, body, grade_level,
                     subject_id, tsv)
                VALUES
                    (:id, :course_id, :lesson_id, :kind, :config, :title, :body, :grade_level,
                     :subject_id,
                     setweight(to_tsvector(CAST(CAST(:config AS varchar) AS regconfig), :title), 'A')
                     || setweight(to_tsvector(CAST(CAST(:config AS varchar) AS regconfig), :summary), 'B')
                     || setweight(to_tsvector(CAST(CAST(:config AS varchar) AS regconfig), :body), 'C'))
                """
            ),
            list(rows),
        )

    async def search(
        self,
        query: str,
        *,
        grade_level: int | None,
        subject_id: uuid.UUID | None,
        kind: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[SearchHit], int]:
        """Each document is matched with its own language's dictionary, so "fractions"
        finds "fraction" in English courses and "fracciones" in Spanish ones."""
        filters = ["d.tsv @@ q", "c.status = 'published'"]
        params: dict[str, Any] = {"query": query, "limit": limit, "offset": offset}
        if grade_level is not None:
            filters.append("d.grade_level = :grade_level")
            params["grade_level"] = grade_level
        if subject_id is not None:
            filters.append("d.subject_id = :subject_id")
            params["subject_id"] = subject_id
        if kind is not None:
            filters.append("d.kind = :kind")
            params["kind"] = kind
        where = " AND ".join(filters)
        source = f"""
            FROM search_documents d
            JOIN courses c ON c.id = d.course_id
            CROSS JOIN LATERAL websearch_to_tsquery(CAST(d.config AS regconfig), :query) AS q
            WHERE {where}
        """
        total = await self._session.scalar(text(f"SELECT count(*) {source}"), params)
        rows = await self._session.execute(
            text(
                f"""
                SELECT d.kind, d.course_id, c.slug AS course_slug, c.title AS course_title,
                       d.lesson_id, CASE WHEN d.kind = 'lesson' THEN d.title END AS lesson_title,
                       ts_headline(CAST(d.config AS regconfig), d.body, q,
                                   'MaxWords=30, MinWords=10, MaxFragments=1,
                                    StartSel=[[, StopSel=]]') AS snippet,
                       ts_rank_cd(d.tsv, q) AS rank
                {source}
                ORDER BY rank DESC, d.kind, c.title
                LIMIT :limit OFFSET :offset
                """
            ),
            params,
        )
        hits = [
            SearchHit(
                kind=r.kind,
                course_id=r.course_id,
                course_slug=r.course_slug,
                course_title=r.course_title,
                lesson_id=r.lesson_id,
                lesson_title=r.lesson_title,
                # Plain text only: the markers are removed so no markup ever reaches clients.
                snippet=r.snippet.replace("[[", "").replace("]]", ""),
                rank=float(r.rank),
            )
            for r in rows.all()
        ]
        return hits, int(total or 0)
