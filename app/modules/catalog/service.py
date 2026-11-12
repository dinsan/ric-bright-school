import uuid
from collections.abc import Sequence
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, DomainValidationError, NotFoundError
from app.core.text import slugify
from app.db.mixins import utcnow
from app.db.transactions import conflict_on_integrity_error
from app.modules.audit.domain import AuditAction
from app.modules.audit.service import AuditLog
from app.modules.catalog import policies
from app.modules.catalog.domain import CourseStatus, Difficulty
from app.modules.catalog.models import Course, LearningPath, Subject
from app.modules.catalog.repository import CourseRepository, SubjectRepository
from app.modules.catalog.schemas import (
    CourseCreate,
    CourseUpdate,
    PathCreate,
    PathUpdate,
    SubjectCreate,
)
from app.modules.identity.domain import Actor, RequestInfo, Role
from app.modules.identity.models import StudentProfile


class SubjectService:
    """Application layer: enforces business rules and owns the transaction boundary."""

    def __init__(
        self, session: AsyncSession, subjects: SubjectRepository, audit: AuditLog | None = None
    ) -> None:
        self._session = session
        self._subjects = subjects
        self._audit = audit

    async def list_subjects(self) -> Sequence[Subject]:
        return await self._subjects.list_all()

    async def create(self, data: SubjectCreate, actor: Actor | None = None) -> Subject:
        """``actor=None`` is for trusted internal callers only (the seed script)."""
        if actor is not None:
            policies.ensure_can_create_subject(actor)
        slug = data.slug or slugify(data.name)
        if not slug:
            raise DomainValidationError("A slug could not be derived from the subject name.")
        conflict = f"A subject with slug '{slug}' already exists."
        if await self._subjects.get_by_slug(slug):
            raise ConflictError(conflict)

        # The check above is only a fast path; the unique constraint settles concurrent races.
        async with conflict_on_integrity_error(self._session, conflict):
            subject = await self._subjects.add(
                Subject(slug=slug, name=data.name.strip(), description=data.description)
            )
            await self._session.commit()
        return subject

    async def delete(
        self, subject_id: uuid.UUID, actor: Actor, request: RequestInfo | None = None
    ) -> None:
        """Hard delete. Refused while any course (archived included) still uses the subject."""
        policies.ensure_can_delete_subject(actor)
        subject = await self._subjects.get(subject_id)
        if subject is None:
            raise NotFoundError("Subject not found.")
        in_use = "The subject still has courses. Move or delete them first."
        if await self._subjects.has_courses(subject_id):
            raise ConflictError(in_use)

        # The foreign key (ON DELETE RESTRICT) catches a course added concurrently.
        async with conflict_on_integrity_error(self._session, in_use):
            if self._audit is not None:
                await self._audit.record(
                    AuditAction.SUBJECT_DELETED,
                    actor=actor,
                    entity_type="subject",
                    entity_id=subject.id,
                    request=request,
                    details={"slug": subject.slug},
                )
            await self._subjects.delete(subject)
            await self._session.commit()


class CourseLifecycle(Protocol):
    """What happens around publishing, implemented by the curriculum module (which owns the
    course content). None of these commit: the caller's transaction covers everything."""

    async def published(self, course: Course, actor: Actor | None) -> None:
        """Validate the draft, store a new immutable version, index it for search."""

    async def unpublished(self, course: Course) -> None:
        """Remove the course from search."""

    async def metadata_changed(self, course: Course) -> None:
        """Re-index a published course whose title, summary, ... changed."""


METADATA_FIELDS = {
    "title", "summary", "description", "grade_level", "subject_id", "language", "tags",
    "difficulty",
}  # fmt: skip


class CourseService:
    def __init__(
        self,
        session: AsyncSession,
        courses: CourseRepository,
        subjects: SubjectRepository,
        audit: AuditLog | None = None,
        lifecycle: CourseLifecycle | None = None,
    ) -> None:
        self._session = session
        self._courses = courses
        self._subjects = subjects
        self._audit = audit
        self._lifecycle = lifecycle

    async def search(
        self,
        *,
        status: CourseStatus,
        grade_level: int | None,
        subject_slug: str | None,
        text: str | None,
        limit: int,
        offset: int,
        actor: Actor | None = None,
        tag: str | None = None,
        difficulty: Difficulty | None = None,
        language: str | None = None,
    ) -> tuple[Sequence[Course], int]:
        policies.ensure_can_list_status(actor, status)
        # Authors listing drafts/archived courses only ever see their own.
        author_id = None
        if status != CourseStatus.PUBLISHED and not policies.is_course_manager(actor):
            assert actor is not None
            author_id = actor.user_id
        return await self._courses.search(
            status=status,
            grade_level=grade_level,
            subject_slug=subject_slug,
            text=text.strip() if text else None,
            limit=limit,
            offset=offset,
            author_id=author_id,
            tag=tag,
            difficulty=difficulty,
            language=language,
        )

    async def get(self, course_id: uuid.UUID, actor: Actor | None = None) -> Course:
        return self._visible(await self._courses.get(course_id), actor)

    async def get_by_slug(self, slug: str, actor: Actor | None = None) -> Course:
        return self._visible(await self._courses.get_by_slug(slug), actor)

    async def create(self, data: CourseCreate, actor: Actor | None = None) -> Course:
        """``actor=None`` is for trusted internal callers only (the seed script)."""
        if actor is not None:
            policies.ensure_can_create_course(actor)
            if data.status == CourseStatus.PUBLISHED:
                policies.ensure_can_publish(actor)
        subject = await self._subjects.get(data.subject_id)
        if subject is None:
            raise NotFoundError("Subject not found.")

        slug = data.slug or slugify(data.title)
        if not slug:
            raise DomainValidationError("A slug could not be derived from the course title.")
        conflict = f"A course with slug '{slug}' already exists."
        if await self._courses.slug_exists(slug):
            raise ConflictError(conflict)

        async with conflict_on_integrity_error(self._session, conflict):
            course = await self._courses.add(
                Course(
                    slug=slug,
                    title=data.title.strip(),
                    summary=data.summary.strip(),
                    description=data.description,
                    grade_level=data.grade_level,
                    status=data.status,
                    subject=subject,
                    author_id=actor.user_id if actor else None,
                    published_at=utcnow() if data.status == CourseStatus.PUBLISHED else None,
                    language=data.language,
                    difficulty=data.difficulty,
                    tags=data.tags,
                )
            )
            if data.status == CourseStatus.PUBLISHED and self._lifecycle is not None:
                await self._lifecycle.published(course, actor)
            await self._session.commit()
        return course

    async def update(self, course_id: uuid.UUID, data: CourseUpdate, actor: Actor) -> Course:
        course = await self.get(course_id, actor)
        policies.ensure_can_edit(actor, course)
        changes = data.model_dump(exclude_unset=True)
        was_published = course.status == CourseStatus.PUBLISHED
        new_status = changes.get("status", course.status)
        publishing = new_status == CourseStatus.PUBLISHED and not was_published
        if publishing:
            policies.ensure_can_publish(actor)

        if "subject_id" in changes and changes["subject_id"] != course.subject_id:
            subject = await self._subjects.get(changes["subject_id"])
            if subject is None:
                raise NotFoundError("Subject not found.")
            course.subject = subject

        slug = changes.get("slug", course.slug)
        if slug != course.slug and await self._courses.slug_exists(slug, exclude_id=course.id):
            raise ConflictError(f"A course with slug '{slug}' already exists.")

        for field in ("title", "summary"):
            if field in changes:
                setattr(course, field, changes[field].strip())
        for field in ("slug", "description", "grade_level", "language", "difficulty", "tags"):
            if field in changes:
                setattr(course, field, changes[field])
        if "status" in changes:
            self._apply_status(course, changes["status"])

        async with conflict_on_integrity_error(
            self._session, f"A course with slug '{slug}' already exists."
        ):
            await self._courses.flush()
            if self._lifecycle is not None:
                if publishing:
                    await self._lifecycle.published(course, actor)
                elif was_published and new_status != CourseStatus.PUBLISHED:
                    await self._lifecycle.unpublished(course)
                elif was_published and METADATA_FIELDS & changes.keys():
                    await self._lifecycle.metadata_changed(course)
            await self._session.commit()
        return course

    async def archive(
        self, course_id: uuid.UUID, actor: Actor, request: RequestInfo | None = None
    ) -> None:
        """Soft delete: the course leaves the public catalog but stays in the database."""
        course = await self.get(course_id, actor)
        policies.ensure_can_edit(actor, course)
        if course.status != CourseStatus.ARCHIVED:
            self._apply_status(course, CourseStatus.ARCHIVED)
            if self._lifecycle is not None:
                await self._lifecycle.unpublished(course)
            if self._audit is not None:
                await self._audit.record(
                    AuditAction.COURSE_ARCHIVED,
                    actor=actor,
                    entity_type="course",
                    entity_id=course.id,
                    request=request,
                )
            await self._session.commit()

    # --- prerequisites (BE-CAT-06) --------------------------------------------------------

    async def prerequisites(self, course_id: uuid.UUID, actor: Actor | None) -> list[Course]:
        await self.get(course_id, actor)
        ids = await self._courses.prerequisite_ids(course_id)
        found = await self._courses.get_many(ids)
        visible = [c for c in found.values() if policies.can_view(actor, c)]
        return sorted(visible, key=lambda c: (c.grade_level, c.title))

    async def set_prerequisites(
        self, course_id: uuid.UUID, prerequisite_ids: Sequence[uuid.UUID], actor: Actor
    ) -> list[Course]:
        course = await self.get(course_id, actor)
        policies.ensure_can_edit(actor, course)
        ids = list(dict.fromkeys(prerequisite_ids))
        if course.id in ids:
            raise DomainValidationError("A course cannot be its own prerequisite.")
        found = await self._courses.get_many(ids)
        if any(i not in found or not policies.can_view(actor, found[i]) for i in ids):
            raise NotFoundError("Course not found.")
        graph = await self._courses.prerequisite_graph()
        graph[course.id] = set(ids)
        if _reaches(graph, start=course.id, target=course.id):
            raise DomainValidationError(
                "These prerequisites would make a circle (a course would require itself)."
            )
        await self._courses.set_prerequisites(course.id, ids)
        await self._session.commit()
        return await self.prerequisites(course.id, actor)

    # --- recommendations (BE-CAT-08) ------------------------------------------------------

    async def recommended(
        self,
        actor: Actor | None,
        *,
        grade_level: int | None,
        subject_slug: str | None,
        limit: int,
    ) -> Sequence[Course]:
        if grade_level is None and actor is not None and actor.role == Role.STUDENT:
            profile = await self._session.get(StudentProfile, actor.user_id)
            grade_level = profile.grade_level if profile else None
        if grade_level is None:
            raise DomainValidationError("Give a `grade_level` (or sign in as a student).")
        return await self._courses.recommended(
            grade_level=grade_level, subject_slug=subject_slug, limit=limit
        )

    @staticmethod
    def _apply_status(course: Course, status: CourseStatus) -> None:
        course.status = status
        if status == CourseStatus.PUBLISHED and course.published_at is None:
            course.published_at = utcnow()
        elif status == CourseStatus.DRAFT:
            # A draft is unpublished, matching create(); archiving keeps the history.
            course.published_at = None

    @staticmethod
    def _visible(course: Course | None, actor: Actor | None) -> Course:
        # Unpublished courses look exactly like missing ones to everyone but their author
        # and course managers: no existence leak.
        if course is None or not policies.can_view(actor, course):
            raise NotFoundError("Course not found.")
        return course


def _reaches(
    graph: dict[uuid.UUID, set[uuid.UUID]], *, start: uuid.UUID, target: uuid.UUID
) -> bool:
    """Is ``target`` reachable from ``start`` by following prerequisites?"""
    stack, seen = list(graph.get(start, ())), set()
    while stack:
        node = stack.pop()
        if node == target:
            return True
        if node not in seen:
            seen.add(node)
            stack.extend(graph.get(node, ()))
    return False


class LearningPathService:
    """Curated, ordered course sets per grade and subject (BE-CAT-08)."""

    def __init__(self, session: AsyncSession, courses: CourseRepository) -> None:
        self._session = session
        self._courses = courses

    async def list_paths(
        self,
        actor: Actor | None,
        *,
        grade_level: int | None,
        subject_slug: str | None,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[LearningPath], int]:
        return await self._courses.list_paths(
            grade_level=grade_level,
            subject_slug=subject_slug,
            published_only=not policies.can_manage_paths(actor),
            limit=limit,
            offset=offset,
        )

    async def get(self, actor: Actor | None, slug: str) -> LearningPath:
        path = await self._courses.get_path_by_slug(slug)
        if path is None or not (path.is_published or policies.can_manage_paths(actor)):
            raise NotFoundError("Learning path not found.")
        return path

    async def _check_courses(self, ids: Sequence[uuid.UUID]) -> list[uuid.UUID]:
        unique = list(dict.fromkeys(ids))
        found = await self._courses.get_many(unique)
        if len(found) != len(unique):
            raise NotFoundError("Course not found.")
        return unique

    async def create(self, actor: Actor, data: PathCreate) -> LearningPath:
        policies.ensure_can_manage_paths(actor)
        slug = data.slug or slugify(data.title)
        if not slug:
            raise DomainValidationError("A slug could not be derived from the title.")
        if await self._courses.path_slug_exists(slug):
            raise ConflictError(f"A learning path with slug '{slug}' already exists.")
        course_ids = await self._check_courses(data.course_ids)
        path = LearningPath(
            slug=slug,
            title=data.title.strip(),
            description=data.description,
            grade_level=data.grade_level,
            subject_id=data.subject_id,
            is_published=data.is_published,
        )
        async with conflict_on_integrity_error(self._session, "Invalid subject or slug taken."):
            await self._courses.add_path(path)
            await self._courses.replace_path_courses(path.id, course_ids)
            await self._session.commit()
        result = await self._courses.get_path(path.id)
        assert result is not None
        return result

    async def update(self, actor: Actor, slug: str, data: PathUpdate) -> LearningPath:
        policies.ensure_can_manage_paths(actor)
        path = await self.get(actor, slug)
        changes = data.model_dump(exclude_unset=True)
        course_ids = changes.pop("course_ids", None)
        for field, value in changes.items():
            setattr(path, field, value.strip() if field == "title" else value)
        async with conflict_on_integrity_error(self._session, "Invalid subject."):
            if course_ids is not None:
                await self._courses.replace_path_courses(
                    path.id, await self._check_courses(course_ids)
                )
            await self._session.commit()
        result = await self._courses.get_path(path.id)
        assert result is not None
        return result

    async def delete(self, actor: Actor, slug: str) -> None:
        policies.ensure_can_manage_paths(actor)
        path = await self.get(actor, slug)
        await self._courses.delete_path(path)
        await self._session.commit()
