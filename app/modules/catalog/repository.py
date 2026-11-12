import uuid
from collections.abc import Sequence

from sqlalchemy import Select, delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import contains_eager, joinedload, selectinload

from app.modules.catalog.domain import CourseStatus, Difficulty
from app.modules.catalog.models import (
    Course,
    LearningPath,
    LearningPathCourse,
    Subject,
    course_prerequisites,
)


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

    async def has_courses(self, subject_id: uuid.UUID) -> bool:
        """True if any course (in any status, archived included) references the subject."""
        stmt = select(select(Course.id).where(Course.subject_id == subject_id).exists())
        return bool(await self._session.scalar(stmt))

    async def delete(self, subject: Subject) -> None:
        await self._session.delete(subject)
        await self._session.flush()


class CourseRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, course_id: uuid.UUID) -> Course | None:
        stmt = select(Course).options(joinedload(Course.subject)).where(Course.id == course_id)
        return await self._session.scalar(stmt)

    async def get_by_slug(self, slug: str) -> Course | None:
        stmt = select(Course).options(joinedload(Course.subject)).where(Course.slug == slug)
        return await self._session.scalar(stmt)

    async def slug_exists(self, slug: str, *, exclude_id: uuid.UUID | None = None) -> bool:
        query = select(Course.id).where(Course.slug == slug)
        if exclude_id is not None:
            query = query.where(Course.id != exclude_id)
        return bool(await self._session.scalar(select(query.exists())))

    async def flush(self) -> None:
        """Send pending changes (e.g. attribute edits on a loaded course) to the database."""
        await self._session.flush()

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
        author_id: uuid.UUID | None = None,
        tag: str | None = None,
        difficulty: Difficulty | None = None,
        language: str | None = None,
    ) -> tuple[Sequence[Course], int]:
        conditions = [Course.status == status]
        if author_id is not None:
            conditions.append(Course.author_id == author_id)
        if tag is not None:
            conditions.append(Course.tags.contains([tag]))
        if difficulty is not None:
            conditions.append(Course.difficulty == difficulty)
        if language is not None:
            conditions.append(Course.language == language)
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

    async def get_many(self, ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, Course]:
        if not ids:
            return {}
        stmt = select(Course).options(joinedload(Course.subject)).where(Course.id.in_(list(ids)))
        return {c.id: c for c in (await self._session.scalars(stmt)).unique()}

    # --- prerequisites (BE-CAT-06) --------------------------------------------------------

    async def prerequisite_ids(self, course_id: uuid.UUID) -> list[uuid.UUID]:
        stmt = select(course_prerequisites.c.prerequisite_id).where(
            course_prerequisites.c.course_id == course_id
        )
        return list((await self._session.scalars(stmt)).all())

    async def prerequisite_graph(self) -> dict[uuid.UUID, set[uuid.UUID]]:
        """course -> its prerequisites, for cycle checks (small: one row per edge)."""
        graph: dict[uuid.UUID, set[uuid.UUID]] = {}
        for course_id, prerequisite_id in (
            await self._session.execute(select(course_prerequisites))
        ).all():
            graph.setdefault(course_id, set()).add(prerequisite_id)
        return graph

    async def set_prerequisites(self, course_id: uuid.UUID, ids: Sequence[uuid.UUID]) -> None:
        await self._session.execute(
            delete(course_prerequisites).where(course_prerequisites.c.course_id == course_id)
        )
        if ids:
            await self._session.execute(
                insert(course_prerequisites),
                [{"course_id": course_id, "prerequisite_id": p} for p in ids],
            )

    # --- learning paths and recommendations (BE-CAT-08) -----------------------------------

    async def add_path(self, path: LearningPath) -> None:
        self._session.add(path)
        await self._session.flush()

    def _path_query(self) -> Select[LearningPath]:
        return select(LearningPath).options(
            joinedload(LearningPath.subject),
            selectinload(LearningPath.items)
            .joinedload(LearningPathCourse.course)
            .joinedload(Course.subject),
        )

    async def get_path(self, path_id: uuid.UUID) -> LearningPath | None:
        stmt = self._path_query().where(LearningPath.id == path_id)
        return await self._session.scalar(stmt.execution_options(populate_existing=True))

    async def get_path_by_slug(self, slug: str) -> LearningPath | None:
        return await self._session.scalar(self._path_query().where(LearningPath.slug == slug))

    async def path_slug_exists(self, slug: str) -> bool:
        stmt = select(select(LearningPath.id).where(LearningPath.slug == slug).exists())
        return bool(await self._session.scalar(stmt))

    async def list_paths(
        self,
        *,
        grade_level: int | None,
        subject_slug: str | None,
        published_only: bool,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[LearningPath], int]:
        conditions = []
        if grade_level is not None:
            conditions.append(LearningPath.grade_level == grade_level)
        if subject_slug:
            conditions.append(
                LearningPath.subject_id.in_(select(Subject.id).where(Subject.slug == subject_slug))
            )
        if published_only:
            conditions.append(LearningPath.is_published.is_(True))
        total = await self._session.scalar(select(func.count(LearningPath.id)).where(*conditions))
        stmt = (
            self._path_query()
            .where(*conditions)
            .order_by(LearningPath.grade_level, LearningPath.title)
            .limit(limit)
            .offset(offset)
        )
        return (await self._session.scalars(stmt)).unique().all(), total or 0

    async def replace_path_courses(self, path_id: uuid.UUID, ids: Sequence[uuid.UUID]) -> None:
        await self._session.execute(
            delete(LearningPathCourse).where(LearningPathCourse.path_id == path_id)
        )
        if ids:
            await self._session.execute(
                insert(LearningPathCourse),
                [{"path_id": path_id, "course_id": c, "position": i} for i, c in enumerate(ids)],
            )

    async def delete_path(self, path: LearningPath) -> None:
        await self._session.delete(path)
        await self._session.flush()

    async def recommended(
        self, *, grade_level: int, subject_slug: str | None, limit: int
    ) -> Sequence[Course]:
        """Published courses for a grade: first those in published learning paths (in path
        order), then the others (by title)."""
        path_rank = (
            select(
                LearningPathCourse.course_id,
                func.min(LearningPathCourse.position).label("rank"),
            )
            .join(LearningPath, LearningPath.id == LearningPathCourse.path_id)
            .where(LearningPath.is_published.is_(True), LearningPath.grade_level == grade_level)
            .group_by(LearningPathCourse.course_id)
            .subquery()
        )
        conditions = [Course.status == CourseStatus.PUBLISHED, Course.grade_level == grade_level]
        if subject_slug:
            conditions.append(Subject.slug == subject_slug)
        stmt = (
            select(Course)
            .join(Subject, Course.subject_id == Subject.id)
            .outerjoin(path_rank, path_rank.c.course_id == Course.id)
            .options(contains_eager(Course.subject))
            .where(*conditions)
            .order_by(path_rank.c.rank.is_(None), path_rank.c.rank, Course.title, Course.id)
            .limit(limit)
        )
        return (await self._session.scalars(stmt)).unique().all()
