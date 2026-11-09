import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, DomainValidationError, NotFoundError
from app.core.text import slugify
from app.db.mixins import utcnow
from app.db.transactions import commit_or_conflict
from app.modules.catalog.domain import CourseStatus
from app.modules.catalog.models import Course, Subject
from app.modules.catalog.repository import CourseRepository, SubjectRepository
from app.modules.catalog.schemas import CourseCreate, SubjectCreate


class SubjectService:
    """Application layer: enforces business rules and owns the transaction boundary."""

    def __init__(self, session: AsyncSession, subjects: SubjectRepository) -> None:
        self._session = session
        self._subjects = subjects

    async def list_subjects(self) -> Sequence[Subject]:
        return await self._subjects.list_all()

    async def create(self, data: SubjectCreate) -> Subject:
        slug = data.slug or slugify(data.name)
        if not slug:
            raise DomainValidationError("A slug could not be derived from the subject name.")
        if await self._subjects.get_by_slug(slug):
            raise ConflictError(f"A subject with slug '{slug}' already exists.")

        subject = await self._subjects.add(
            Subject(slug=slug, name=data.name.strip(), description=data.description)
        )
        await commit_or_conflict(self._session, f"A subject with slug '{slug}' already exists.")
        return subject


class CourseService:
    def __init__(
        self,
        session: AsyncSession,
        courses: CourseRepository,
        subjects: SubjectRepository,
    ) -> None:
        self._session = session
        self._courses = courses
        self._subjects = subjects

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
        return await self._courses.search(
            status=status,
            grade_level=grade_level,
            subject_slug=subject_slug,
            text=text.strip() if text else None,
            limit=limit,
            offset=offset,
        )

    async def get(self, course_id: uuid.UUID) -> Course:
        course = await self._courses.get(course_id)
        if course is None:
            raise NotFoundError("Course not found.")
        return course

    async def get_by_slug(self, slug: str) -> Course:
        course = await self._courses.get_by_slug(slug)
        if course is None:
            raise NotFoundError("Course not found.")
        return course

    async def create(self, data: CourseCreate) -> Course:
        subject = await self._subjects.get(data.subject_id)
        if subject is None:
            raise NotFoundError("Subject not found.")

        slug = data.slug or slugify(data.title)
        if not slug:
            raise DomainValidationError("A slug could not be derived from the course title.")
        if await self._courses.slug_exists(slug):
            raise ConflictError(f"A course with slug '{slug}' already exists.")

        course = await self._courses.add(
            Course(
                slug=slug,
                title=data.title.strip(),
                summary=data.summary.strip(),
                description=data.description,
                grade_level=data.grade_level,
                status=data.status,
                subject=subject,
                published_at=utcnow() if data.status == CourseStatus.PUBLISHED else None,
            )
        )
        await commit_or_conflict(self._session, f"A course with slug '{slug}' already exists.")
        return course
