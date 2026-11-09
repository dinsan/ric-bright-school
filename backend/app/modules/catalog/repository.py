import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import contains_eager, joinedload

from app.modules.catalog.domain import CourseStatus
from app.modules.catalog.models import Course, Subject


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class SubjectRepository:
    """Data access only — no business rules, no commits (the service owns the transaction)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_all(self) -> Sequence[Subject]:
        result = await self._session.scalars(select(Subject).order_by(Subject.name))
        return result.all()

    async def get(self, subject_id: uuid.UUID) -> Subject | None:
        return await self._session.get(Subject, subject_id)

    async def get_by_slug(self, slug: str) -> Subject | None:
        return await self._session.scalar(select(Subject).where(Subject.slug == slug))

    async def add(self, subject: Subject) -> Subject:
        self._session.add(subject)
        await self._session.flush()
        return subject


class CourseRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, course_id: uuid.UUID) -> Course | None:
        stmt = select(Course).options(joinedload(Course.subject)).where(Course.id == course_id)
        return await self._session.scalar(stmt)

    async def get_by_slug(self, slug: str) -> Course | None:
        stmt = select(Course).options(joinedload(Course.subject)).where(Course.slug == slug)
        return await self._session.scalar(stmt)

    async def slug_exists(self, slug: str) -> bool:
        stmt = select(select(Course.id).where(Course.slug == slug).exists())
        return bool(await self._session.scalar(stmt))

    async def add(self, course: Course) -> Course:
        self._session.add(course)
        await self._session.flush()
        return course

    async def search(
        self,
        *,
        status: CourseStatus,
        grade_level: int | None,
        subject_slug: str | None,
        text: str | None,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[Course], int]:
        conditions = [Course.status == status]
        if grade_level is not None:
            conditions.append(Course.grade_level == grade_level)
        if subject_slug:
            conditions.append(Subject.slug == subject_slug)
        if text:
            conditions.append(Course.title.ilike(f"%{_escape_like(text)}%", escape="\\"))

        total = await self._session.scalar(
            select(func.count(Course.id))
            .select_from(Course)
            .join(Subject, Course.subject_id == Subject.id)
            .where(*conditions)
        )

        stmt = (
            select(Course)
            .join(Subject, Course.subject_id == Subject.id)
            .options(contains_eager(Course.subject))
            .where(*conditions)
            .order_by(Course.grade_level, Course.title, Course.id)
            .limit(limit)
            .offset(offset)
        )
        rows = await self._session.scalars(stmt)
        return rows.unique().all(), total or 0
