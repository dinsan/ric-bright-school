"""Curriculum use cases (BE-CAT-01..04, 06, 07, 10). Services own the transaction.

The course's units and lessons are its *draft*: authors change them freely. Publishing
freezes the draft into an immutable *version* (a JSON snapshot); learners read versions.
"""

import copy
import uuid
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import ConflictError, DomainValidationError, NotFoundError
from app.core.text import slugify
from app.db.mixins import utcnow
from app.db.transactions import conflict_on_integrity_error
from app.modules.audit.domain import AuditAction
from app.modules.audit.service import AuditLog
from app.modules.catalog import policies as catalog_policies
from app.modules.catalog.domain import CourseStatus, search_config
from app.modules.catalog.models import Course
from app.modules.catalog.repository import CourseRepository, SubjectRepository
from app.modules.curriculum import policies
from app.modules.curriculum.content import (
    ImageBlock,
    LessonContent,
    VideoBlock,
    parse_content,
    plain_text,
)
from app.modules.curriculum.models import CourseLesson, CourseUnit, CourseVersion
from app.modules.curriculum.repository import CurriculumRepository, SearchHit
from app.modules.curriculum.schemas import (
    CourseDocument,
    LessonContentUpdate,
    LessonCreate,
    LessonUpdate,
    UnitCreate,
    UnitUpdate,
)
from app.modules.identity.domain import Actor, RequestInfo
from app.modules.media.domain import MediaKind, MediaStatus
from app.modules.media.models import MediaAsset
from app.modules.media.service import MediaService
from app.modules.standards.service import StandardsService

SNAPSHOT_FORMAT = {"format": "lumina-course", "format_version": 1}

# Which kind of upload each media reference must point at.
_MEDIA_KINDS: dict[str, frozenset[MediaKind]] = {
    "image.media_id": frozenset({MediaKind.IMAGE}),
    "video.media_id": frozenset({MediaKind.VIDEO}),
    "video.captions_media_id": frozenset({MediaKind.CAPTIONS}),
    "file.media_id": frozenset({MediaKind.DOCUMENT, MediaKind.AUDIO}),
}


def _media_refs(block: dict[str, Any]) -> list[tuple[str, uuid.UUID]]:
    refs = []
    for attr in ("media_id", "captions_media_id"):
        if block.get(attr):
            refs.append((f"{block['type']}.{attr}", uuid.UUID(str(block[attr]))))
    return refs


def _snapshot_lessons(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    return [lesson for unit in snapshot.get("units", []) for lesson in unit["lessons"]]


class _CurriculumBase:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        audit: AuditLog,
        media: MediaService,
        standards: StandardsService,
    ) -> None:
        self._session = session
        self._settings = settings
        self._audit = audit
        self._media = media
        self._standards = standards
        self._courses = CourseRepository(session)
        self._subjects = SubjectRepository(session)
        self._repo = CurriculumRepository(session)

    # --- loading with the right checks ----------------------------------------------------

    async def _course(self, actor: Actor | None, course_id: uuid.UUID) -> Course:
        course = await self._courses.get(course_id)
        if course is None or not catalog_policies.can_view(actor, course):
            raise NotFoundError("Course not found.")
        return course

    async def _course_for_edit(self, actor: Actor, course_id: uuid.UUID) -> Course:
        course = await self._course(actor, course_id)
        policies.ensure_can_author(actor, course)
        return course

    # --- content checks -------------------------------------------------------------------

    def _url_problem(self, url: str) -> str | None:
        host = urlparse(url).hostname
        if host not in self._settings.embed_allowed_hosts:
            return f"{host} is not on the list of sites that can be embedded."
        return None

    async def _check_content(
        self,
        actor: Actor,
        course: Course,
        content: LessonContent,
        *,
        already_used: set[uuid.UUID],
        where: str,
    ) -> None:
        """At write time: allowed sites, and media the author may use, of the right kind.
        (Readiness and accessibility are checked at publish: drafts may be unfinished.)"""
        problems: list[dict[str, Any]] = []
        for url in content.external_urls():
            if problem := self._url_problem(url):
                problems.append({"field": where, "message": problem})
        assets = await self._media.get_many(content.media_ids())
        for index, block in enumerate(content.blocks):
            for ref, media_id in _media_refs(block.model_dump(mode="json")):
                asset = assets.get(media_id)
                field = f"{where}.blocks.{index}"
                if asset is None or not policies.can_use_media(actor, course, asset, already_used):
                    problems.append({"field": field, "message": f"Media {media_id} not found."})
                elif asset.kind not in _MEDIA_KINDS[ref]:
                    wanted = "/".join(sorted(k.value for k in _MEDIA_KINDS[ref]))
                    problems.append({"field": field, "message": f"Expected {wanted} media."})
        if problems:
            raise DomainValidationError("The lesson content has problems.", details=problems)

    # --- snapshots ------------------------------------------------------------------------

    async def _draft_snapshot(self, course: Course) -> dict[str, Any]:
        """The draft, in the exchange/version format (JSON-ready)."""
        units = await self._repo.units(course.id)
        lesson_ids = [lesson.id for unit in units for lesson in unit.lessons]
        standards_by_lesson = await self._repo.standards_by_lesson(lesson_ids)
        standards = await self._standards.get_many(
            [s for ids in standards_by_lesson.values() for s in ids]
        )
        prerequisites = await self._repo.prerequisites_by_lesson(course.id)
        total_minutes = 0
        unit_docs = []
        for unit in units:
            lesson_docs = []
            for lesson in unit.lessons:
                total_minutes += lesson.estimated_minutes or 0
                linked = sorted(
                    (
                        standards[s]
                        for s in standards_by_lesson.get(lesson.id, [])
                        if s in standards
                    ),
                    key=lambda s: (s.framework.code, s.code),
                )
                lesson_docs.append(
                    {
                        "id": str(lesson.id),
                        "title": lesson.title,
                        "summary": lesson.summary,
                        "estimated_minutes": lesson.estimated_minutes,
                        "difficulty": lesson.difficulty.value if lesson.difficulty else None,
                        "content": parse_content(lesson.content).to_json(),
                        "standards": [
                            {"id": str(s.id), "framework": s.framework.code, "code": s.code}
                            for s in linked
                        ],
                        "prerequisite_lesson_ids": sorted(
                            str(p) for p in prerequisites.get(lesson.id, set())
                        ),
                    }
                )
            unit_docs.append(
                {
                    "id": str(unit.id),
                    "title": unit.title,
                    "summary": unit.summary,
                    "lessons": lesson_docs,
                }
            )
        subject = course.subject
        return {
            **SNAPSHOT_FORMAT,
            "course": {
                "id": str(course.id),
                "slug": course.slug,
                "title": course.title,
                "summary": course.summary,
                "description": course.description,
                "grade_level": course.grade_level,
                "language": course.language,
                "difficulty": course.difficulty.value if course.difficulty else None,
                "tags": list(course.tags or []),
                "subject": {"id": str(subject.id), "slug": subject.slug, "name": subject.name},
                "estimated_minutes": total_minutes,
            },
            "units": unit_docs,
        }

    # --- rendering blocks for readers -----------------------------------------------------

    async def _render_blocks(self, blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Attach signed links and accessibility text to blocks that show media."""
        ids = [media_id for block in blocks for _, media_id in _media_refs(block)]
        assets = await self._media.get_many(ids)
        rendered = []
        for block in blocks:
            out = copy.deepcopy(block)
            for ref, media_id in _media_refs(block):
                asset = assets.get(media_id)
                info = self._media_info(asset, block) if asset else None
                out["captions" if ref.endswith("captions_media_id") else "media"] = info
            rendered.append(out)
        return rendered

    def _media_info(self, asset: MediaAsset, block: dict[str, Any]) -> dict[str, Any]:
        return {
            "url": self._media.url_for(asset),
            "kind": asset.kind.value,
            "content_type": asset.content_type,
            "filename": asset.filename,
            "status": asset.status.value,
            "alt_text": block.get("alt") or asset.alt_text,
            "decorative": asset.decorative,
        }


class AuthoringService(_CurriculumBase):
    """Building a course: units, lessons, content, order (BE-CAT-01..03, 05, 06)."""

    # --- reading the draft ----------------------------------------------------------------

    async def draft(
        self, actor: Actor, course_id: uuid.UUID
    ) -> tuple[
        Course,
        Sequence[CourseUnit],
        dict[uuid.UUID, list[uuid.UUID]],
        dict[uuid.UUID, set[uuid.UUID]],
        int | None,
        bool,
    ]:
        course = await self._course_for_edit(actor, course_id)
        units = await self._repo.units(course.id)
        lesson_ids = [lesson.id for unit in units for lesson in unit.lessons]
        standards = await self._repo.standards_by_lesson(lesson_ids)
        prerequisites = await self._repo.prerequisites_by_lesson(course.id)
        current = (
            await self._repo.get_version(course.published_version_id)
            if course.published_version_id
            else None
        )
        changed = current is None or current.snapshot != await self._draft_snapshot(course)
        return course, units, standards, prerequisites, current.number if current else None, changed

    async def lesson(
        self, actor: Actor, course_id: uuid.UUID, lesson_id: uuid.UUID
    ) -> tuple[CourseLesson, list[dict[str, Any]], list[uuid.UUID], list[uuid.UUID]]:
        course = await self._course_for_edit(actor, course_id)
        lesson = await self._repo.get_lesson(course.id, lesson_id)
        if lesson is None:
            raise NotFoundError("Lesson not found.")
        content = parse_content(lesson.content).to_json()
        blocks = await self._render_blocks(content["blocks"])
        standards = (await self._repo.standards_by_lesson([lesson.id])).get(lesson.id, [])
        prerequisites = (await self._repo.prerequisites_by_lesson(course.id)).get(lesson.id, set())
        return lesson, blocks, standards, sorted(prerequisites)

    # --- units ----------------------------------------------------------------------------

    async def create_unit(self, actor: Actor, course_id: uuid.UUID, data: UnitCreate) -> CourseUnit:
        course = await self._course_for_edit(actor, course_id)
        count = await self._repo.count_units(course.id)
        position = count if data.position is None else min(data.position, count)
        await self._repo.shift_units(course.id, from_position=position, by=1)
        unit = CourseUnit(
            course_id=course.id, title=data.title.strip(), summary=data.summary, position=position
        )
        await self._repo.add(unit)
        await self._session.commit()
        return unit

    async def update_unit(
        self, actor: Actor, course_id: uuid.UUID, unit_id: uuid.UUID, data: UnitUpdate
    ) -> CourseUnit:
        course = await self._course_for_edit(actor, course_id)
        unit = await self._get_unit(course, unit_id)
        if data.title is not None:
            unit.title = data.title.strip()
        if "summary" in data.model_fields_set:
            unit.summary = data.summary
        await self._session.commit()
        return unit

    async def delete_unit(self, actor: Actor, course_id: uuid.UUID, unit_id: uuid.UUID) -> None:
        """Deletes the unit and its lessons from the draft (published versions keep them)."""
        course = await self._course_for_edit(actor, course_id)
        unit = await self._get_unit(course, unit_id)
        position = unit.position
        await self._repo.delete(unit)
        await self._repo.shift_units(course.id, from_position=position + 1, by=-1)
        await self._session.commit()

    async def reorder_units(
        self, actor: Actor, course_id: uuid.UUID, unit_ids: Sequence[uuid.UUID]
    ) -> None:
        course = await self._course_for_edit(actor, course_id)
        units = {u.id: u for u in await self._repo.units(course.id)}
        _ensure_permutation(unit_ids, units, "unit")
        for position, unit_id in enumerate(unit_ids):
            units[unit_id].position = position
        await self._session.commit()

    async def _get_unit(self, course: Course, unit_id: uuid.UUID) -> CourseUnit:
        unit = await self._repo.get_unit(course.id, unit_id)
        if unit is None:
            raise NotFoundError("Unit not found.")
        return unit

    # --- lessons --------------------------------------------------------------------------

    async def create_lesson(
        self, actor: Actor, course_id: uuid.UUID, unit_id: uuid.UUID, data: LessonCreate
    ) -> CourseLesson:
        course = await self._course_for_edit(actor, course_id)
        unit = await self._get_unit(course, unit_id)
        content = data.content or LessonContent()
        await self._check_content(actor, course, content, already_used=set(), where="content")
        count = await self._repo.count_lessons(unit.id)
        position = count if data.position is None else min(data.position, count)
        await self._repo.shift_lessons(unit.id, from_position=position, by=1)
        lesson = CourseLesson(
            course_id=course.id,
            unit_id=unit.id,
            title=data.title.strip(),
            summary=data.summary,
            position=position,
            content=content.to_json(),
            estimated_minutes=data.estimated_minutes,
            difficulty=data.difficulty,
        )
        await self._repo.add(lesson)
        await self._session.commit()
        return lesson

    async def _lesson_for_edit(
        self, actor: Actor, course_id: uuid.UUID, lesson_id: uuid.UUID
    ) -> tuple[Course, CourseLesson]:
        course = await self._course_for_edit(actor, course_id)
        lesson = await self._repo.get_lesson(course.id, lesson_id, for_update=True)
        if lesson is None:
            raise NotFoundError("Lesson not found.")
        return course, lesson

    @staticmethod
    def _check_revision(lesson: CourseLesson, expected: int | None) -> None:
        if expected is not None and expected != lesson.revision:
            raise ConflictError(
                f"This lesson was changed meanwhile (now revision {lesson.revision}). "
                "Reload it, then apply your changes again."
            )

    async def update_lesson(
        self, actor: Actor, course_id: uuid.UUID, lesson_id: uuid.UUID, data: LessonUpdate
    ) -> CourseLesson:
        _, lesson = await self._lesson_for_edit(actor, course_id, lesson_id)
        self._check_revision(lesson, data.expected_revision)
        changes = data.model_dump(exclude_unset=True)
        changes.pop("expected_revision", None)
        for field, value in changes.items():
            setattr(lesson, field, value.strip() if field == "title" else value)
        lesson.revision += 1
        await self._session.commit()
        return lesson

    async def put_content(
        self,
        actor: Actor,
        course_id: uuid.UUID,
        lesson_id: uuid.UUID,
        data: LessonContentUpdate,
    ) -> CourseLesson:
        """Replace the lesson's blocks (add, edit, remove and reorder blocks in one save)."""
        course, lesson = await self._lesson_for_edit(actor, course_id, lesson_id)
        self._check_revision(lesson, data.expected_revision)
        already_used = parse_content(lesson.content).media_ids()
        await self._check_content(
            actor, course, data.content, already_used=already_used, where="content"
        )
        lesson.content = data.content.to_json()
        lesson.revision += 1
        await self._session.commit()
        return lesson

    async def delete_lesson(self, actor: Actor, course_id: uuid.UUID, lesson_id: uuid.UUID) -> None:
        _, lesson = await self._lesson_for_edit(actor, course_id, lesson_id)
        unit_id, position = lesson.unit_id, lesson.position
        await self._repo.delete(lesson)
        await self._repo.shift_lessons(unit_id, from_position=position + 1, by=-1)
        await self._session.commit()

    async def reorder_lessons(
        self,
        actor: Actor,
        course_id: uuid.UUID,
        unit_id: uuid.UUID,
        lesson_ids: Sequence[uuid.UUID],
    ) -> None:
        course = await self._course_for_edit(actor, course_id)
        unit = next((u for u in await self._repo.units(course.id) if u.id == unit_id), None)
        if unit is None:
            raise NotFoundError("Unit not found.")
        lessons = {lesson.id: lesson for lesson in unit.lessons}
        _ensure_permutation(lesson_ids, lessons, "lesson")
        # `unit.lessons` is a read-only view: update through the lesson rows themselves.
        for position, lesson_id in enumerate(lesson_ids):
            row = await self._repo.get_lesson(course.id, lesson_id)
            assert row is not None
            row.position = position
        await self._session.commit()

    async def move_lesson(
        self,
        actor: Actor,
        course_id: uuid.UUID,
        lesson_id: uuid.UUID,
        target_unit_id: uuid.UUID,
        position: int | None,
    ) -> CourseLesson:
        """Move a lesson to another place in the same unit or to another unit."""
        course, lesson = await self._lesson_for_edit(actor, course_id, lesson_id)
        target = await self._get_unit(course, target_unit_id)
        source_unit_id, source_position = lesson.unit_id, lesson.position
        # Close the gap where the lesson was...
        await self._repo.shift_lessons(source_unit_id, from_position=source_position + 1, by=-1)
        # ...then open one where it goes (the lesson itself no longer counts there).
        count = await self._repo.count_lessons(target.id) - (target.id == source_unit_id)
        new_position = count if position is None else min(position, count)
        await self._repo.shift_lessons(target.id, from_position=new_position, by=1)
        lesson.unit_id = target.id
        lesson.position = new_position
        lesson.revision += 1
        await self._session.commit()
        return lesson

    async def set_lesson_standards(
        self,
        actor: Actor,
        course_id: uuid.UUID,
        lesson_id: uuid.UUID,
        standard_ids: Sequence[uuid.UUID],
    ) -> list[uuid.UUID]:
        """Link a lesson to curriculum standards (BE-CAT-05)."""
        _, lesson = await self._lesson_for_edit(actor, course_id, lesson_id)
        ids = list(dict.fromkeys(standard_ids))
        found = await self._standards.get_many(ids)
        if len(found) != len(ids):
            raise NotFoundError("Standard not found.")
        await self._repo.set_lesson_standards(lesson.id, ids)
        lesson.revision += 1
        await self._session.commit()
        return ids

    async def set_lesson_prerequisites(
        self,
        actor: Actor,
        course_id: uuid.UUID,
        lesson_id: uuid.UUID,
        prerequisite_ids: Sequence[uuid.UUID],
    ) -> list[uuid.UUID]:
        """Lessons of the same course to finish first (BE-CAT-06). No circles."""
        course, lesson = await self._lesson_for_edit(actor, course_id, lesson_id)
        ids = list(dict.fromkeys(prerequisite_ids))
        if lesson.id in ids:
            raise DomainValidationError("A lesson cannot be its own prerequisite.")
        if not set(ids) <= await self._repo.lesson_ids(course.id):
            raise NotFoundError("Lesson not found in this course.")
        graph = await self._repo.prerequisites_by_lesson(course.id)
        graph[lesson.id] = set(ids)
        if _reaches(graph, lesson.id):
            raise DomainValidationError("These prerequisites would make a circle.")
        await self._repo.set_lesson_prerequisites(lesson.id, ids)
        lesson.revision += 1
        await self._session.commit()
        return ids


class PublishingService(_CurriculumBase):
    """Versions (BE-CAT-04) and what learners read. Also the catalog's CourseLifecycle:
    every way of publishing (this service, or PATCH status) goes through ``_publish``."""

    async def _publish_problems(self, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        """Everything that must be right before learners see it (BE-MEDIA-04 included):
        media uploaded and checked, image descriptions, video captions or transcripts."""
        problems: list[dict[str, Any]] = []
        lessons = _snapshot_lessons(snapshot)
        ids = [
            m for lesson in lessons for b in lesson["content"]["blocks"] for _, m in _media_refs(b)
        ]
        assets = await self._media.get_many(ids)
        for lesson in lessons:
            content = parse_content(lesson["content"])
            for index, block in enumerate(content.blocks):
                field = f"lessons.{lesson['id']}.blocks.{index}"
                label = f'Lesson "{lesson["title"]}", block {index + 1}'

                def problem(message: str, field: str = field, label: str = label) -> None:
                    problems.append({"field": field, "message": f"{label}: {message}"})

                raw = block.model_dump(mode="json")
                for ref, media_id in _media_refs(raw):
                    asset = assets.get(media_id)
                    if asset is None:
                        problem("the media file no longer exists.")
                    elif asset.status != MediaStatus.READY:
                        problem(f"the media file is not ready ({asset.status.value}).")
                    elif asset.kind not in _MEDIA_KINDS[ref]:
                        problem("the media file is of the wrong kind.")
                if isinstance(block, ImageBlock):
                    asset = assets.get(block.media_id)
                    if asset and not (block.alt or asset.alt_text or asset.decorative):
                        problem("the image needs a description (alt text).")
                if isinstance(block, VideoBlock) and not (
                    block.captions_media_id or (block.transcript and block.transcript.strip())
                ):
                    problem("the video needs captions or a transcript.")
            for url in content.external_urls():
                if message := self._url_problem(url):
                    problems.append({"field": f"lessons.{lesson['id']}", "message": message})
        return problems

    async def _publish(
        self, course: Course, actor: Actor | None, change_note: str | None
    ) -> CourseVersion:
        snapshot = await self._draft_snapshot(course)
        problems = await self._publish_problems(snapshot)
        if problems:
            raise DomainValidationError(
                "The course cannot be published yet. Fix the problems listed in `details`.",
                details=problems,
            )
        current = (
            await self._repo.get_version(course.published_version_id)
            if course.published_version_id
            else None
        )
        if current is not None and current.snapshot == snapshot:
            version = current  # nothing changed: no new version
        else:
            version = CourseVersion(
                course_id=course.id,
                number=await self._repo.latest_version_number(course.id) + 1,
                snapshot=snapshot,
                change_note=change_note,
                published_by_id=actor.user_id if actor else None,
            )
            await self._repo.add(version)
        course.published_version_id = version.id
        course.estimated_minutes = snapshot["course"]["estimated_minutes"] or None
        course.status = CourseStatus.PUBLISHED
        course.published_at = course.published_at or utcnow()
        await self._reindex(course, version.snapshot)
        return version

    # --- CourseLifecycle (called by catalog's CourseService; no commit) ------------------

    async def published(self, course: Course, actor: Actor | None) -> None:
        await self._publish(course, actor, None)

    async def unpublished(self, course: Course) -> None:
        await self._repo.clear_search(course.id)

    async def metadata_changed(self, course: Course) -> None:
        if course.published_version_id:
            version = await self._repo.get_version(course.published_version_id)
            if version is not None:
                await self._reindex(course, version.snapshot)

    # --- endpoints ------------------------------------------------------------------------

    async def publish(
        self,
        actor: Actor,
        course_id: uuid.UUID,
        change_note: str | None,
        request: RequestInfo,
    ) -> CourseVersion:
        course = await self._course_for_edit(actor, course_id)
        catalog_policies.ensure_can_publish(actor)
        previous = course.published_version_id
        version = await self._publish(course, actor, change_note)
        if version.id != previous:
            await self._audit.record(
                AuditAction.COURSE_PUBLISHED,
                actor=actor,
                entity_type="course",
                entity_id=course.id,
                request=request,
                details={"version": version.number},
            )
        await self._session.commit()
        return version

    async def unpublish(self, actor: Actor, course_id: uuid.UUID, request: RequestInfo) -> None:
        """Hide the course from the catalog. Its versions are kept for when it comes back."""
        course = await self._course_for_edit(actor, course_id)
        if course.status == CourseStatus.PUBLISHED:
            course.status = CourseStatus.DRAFT
            course.published_at = None
            await self._repo.clear_search(course.id)
            await self._audit.record(
                AuditAction.COURSE_UNPUBLISHED,
                actor=actor,
                entity_type="course",
                entity_id=course.id,
                request=request,
            )
        await self._session.commit()

    async def versions(
        self, actor: Actor, course_id: uuid.UUID
    ) -> tuple[Course, Sequence[CourseVersion]]:
        course = await self._course_for_edit(actor, course_id)
        return course, await self._repo.versions(course.id)

    async def _version(
        self, actor: Actor | None, course_id: uuid.UUID, number: int | None
    ) -> tuple[Course, CourseVersion]:
        course = await self._courses.get(course_id)
        if course is None:
            raise NotFoundError("Course not found.")
        policies.ensure_can_read_published(actor, course)
        if number is None:
            version = (
                await self._repo.get_version(course.published_version_id)
                if course.published_version_id
                else None
            )
        else:
            version = await self._repo.get_version_by_number(course.id, number)
        if version is None:
            raise NotFoundError("This course has no published content yet.")
        return course, version

    async def content(
        self, actor: Actor | None, course_id: uuid.UUID, number: int | None = None
    ) -> tuple[Course, CourseVersion]:
        """The published outline: the current version, or a given one (learners who started
        an older version keep reading it)."""
        return await self._version(actor, course_id, number)

    async def published_lesson(
        self,
        actor: Actor | None,
        course_id: uuid.UUID,
        lesson_id: uuid.UUID,
        number: int | None = None,
    ) -> tuple[CourseVersion, uuid.UUID, dict[str, Any], list[dict[str, Any]]]:
        _, version = await self._version(actor, course_id, number)
        for unit in version.snapshot["units"]:
            for lesson in unit["lessons"]:
                if lesson["id"] == str(lesson_id):
                    blocks = await self._render_blocks(lesson["content"]["blocks"])
                    return version, uuid.UUID(unit["id"]), lesson, blocks
        raise NotFoundError("Lesson not found in this version.")

    # --- search index ---------------------------------------------------------------------

    async def _reindex(self, course: Course, snapshot: dict[str, Any]) -> None:
        config = search_config(course.language)
        base = {
            "course_id": course.id,
            "config": config,
            "grade_level": course.grade_level,
            "subject_id": course.subject_id,
        }
        rows = [
            {
                **base,
                "id": uuid.uuid4(),
                "lesson_id": None,
                "kind": "course",
                "title": course.title,
                "summary": course.summary,
                "body": " ".join(filter(None, [course.description, " ".join(course.tags or [])])),
            }
        ]
        for lesson in _snapshot_lessons(snapshot):
            rows.append(
                {
                    **base,
                    "id": uuid.uuid4(),
                    "lesson_id": uuid.UUID(lesson["id"]),
                    "kind": "lesson",
                    "title": lesson["title"],
                    "summary": lesson.get("summary") or "",
                    "body": plain_text(parse_content(lesson["content"])),
                }
            )
        await self._repo.clear_search(course.id)
        await self._repo.index_documents(rows)

    async def reindex_all(self) -> int:
        """Rebuild the whole search index (python -m app.scripts.reindex_search)."""
        count = 0
        courses, _ = await self._courses.search(
            status=CourseStatus.PUBLISHED,
            grade_level=None,
            subject_slug=None,
            text=None,
            limit=100_000,
            offset=0,
        )
        for course in courses:
            if course.published_version_id:
                version = await self._repo.get_version(course.published_version_id)
                if version is not None:
                    await self._reindex(course, version.snapshot)
                    count += 1
        await self._session.commit()
        return count


class TransferService(_CurriculumBase):
    """Duplicate (BE-CAT-03), export and import (BE-CAT-10): one JSON document format."""

    async def export(
        self, actor: Actor | None, course_id: uuid.UUID, source: str
    ) -> dict[str, Any]:
        course = await self._courses.get(course_id)
        if course is None:
            raise NotFoundError("Course not found.")
        if source == "draft":
            if actor is None or not policies.can_edit(actor, course):
                raise NotFoundError("Course not found.")
            return await self._draft_snapshot(course)
        policies.ensure_can_read_published(actor, course)
        if not course.published_version_id:
            raise NotFoundError("This course has no published content yet.")
        version = await self._repo.get_version(course.published_version_id)
        assert version is not None
        return dict(version.snapshot)

    async def duplicate(
        self,
        actor: Actor,
        course_id: uuid.UUID,
        *,
        title: str | None,
        slug: str | None,
        request: RequestInfo,
    ) -> Course:
        """Authors copy their own draft; other teachers copy the published version."""
        catalog_policies.ensure_can_create_course(actor)
        course = await self._courses.get(course_id)
        if course is None or not catalog_policies.can_view(actor, course):
            raise NotFoundError("Course not found.")
        source = "draft" if policies.can_edit(actor, course) else "published"
        document = CourseDocument.model_validate(await self.export(actor, course_id, source))
        document.course.title = (title or f"{course.title} (copy)")[:200]
        copied = await self._create_from_document(
            actor, document, slug=slug, already_allowed=_document_media(document)
        )
        await self._audit.record(
            AuditAction.COURSE_DUPLICATED,
            actor=actor,
            entity_type="course",
            entity_id=copied.id,
            request=request,
            details={"from": str(course.id), "source": source},
        )
        await self._session.commit()
        return copied

    async def import_document(
        self,
        actor: Actor,
        document: CourseDocument,
        *,
        slug: str | None,
        request: RequestInfo,
    ) -> tuple[Course, list[str]]:
        catalog_policies.ensure_can_create_course(actor)
        warnings: list[str] = []
        course = await self._create_from_document(
            actor, document, slug=slug, already_allowed=set(), warnings=warnings
        )
        await self._audit.record(
            AuditAction.COURSE_IMPORTED,
            actor=actor,
            entity_type="course",
            entity_id=course.id,
            request=request,
        )
        await self._session.commit()
        return course, warnings

    async def _unique_slug(self, wanted: str | None, title: str) -> str:
        base = wanted or slugify(title)[:110] or "course"
        if wanted:
            if await self._courses.slug_exists(wanted):
                raise ConflictError(f"A course with slug '{wanted}' already exists.")
            return wanted
        candidate, n = base, 1
        while await self._courses.slug_exists(candidate):
            n += 1
            candidate = f"{base}-{n}"
        return candidate

    async def _create_from_document(
        self,
        actor: Actor,
        document: CourseDocument,
        *,
        slug: str | None,
        already_allowed: set[uuid.UUID],
        warnings: list[str] | None = None,
    ) -> Course:
        meta = document.course
        subject = await self._subjects.get_by_slug(str(meta.subject.get("slug", "")))
        if subject is None:
            raise DomainValidationError(
                f"Subject {meta.subject.get('slug')!r} does not exist here. Create it first."
            )
        course = Course(
            slug=await self._unique_slug(slug, meta.title),
            title=meta.title.strip(),
            summary=meta.summary.strip(),
            description=meta.description,
            grade_level=meta.grade_level,
            status=CourseStatus.DRAFT,
            subject=subject,
            author_id=actor.user_id,
            language=meta.language,
            difficulty=meta.difficulty,
            tags=meta.tags,
        )
        async with conflict_on_integrity_error(self._session, "A course with this slug exists."):
            await self._courses.add(course)

        pairs = {
            (s.framework, s.code)
            for u in document.units
            for lesson in u.lessons
            for s in lesson.standards
        }
        standards = await self._standards.find_by_codes(pairs)
        for framework, code in sorted(pairs - standards.keys()):
            if warnings is not None:
                warnings.append(f"Standard {framework} {code} does not exist here; link skipped.")

        new_ids: dict[uuid.UUID, uuid.UUID] = {}
        pending_prerequisites: list[tuple[CourseLesson, list[uuid.UUID]]] = []
        for unit_position, unit_doc in enumerate(document.units):
            unit = CourseUnit(
                course_id=course.id,
                title=unit_doc.title,
                summary=unit_doc.summary,
                position=unit_position,
            )
            await self._repo.add(unit)
            for lesson_position, lesson_doc in enumerate(unit_doc.lessons):
                await self._check_content(
                    actor,
                    course,
                    lesson_doc.content,
                    already_used=already_allowed,
                    where=f"units.{unit_position}.lessons.{lesson_position}.content",
                )
                lesson = CourseLesson(
                    course_id=course.id,
                    unit_id=unit.id,
                    title=lesson_doc.title,
                    summary=lesson_doc.summary,
                    position=lesson_position,
                    content=lesson_doc.content.to_json(),
                    estimated_minutes=lesson_doc.estimated_minutes,
                    difficulty=lesson_doc.difficulty,
                )
                await self._repo.add(lesson)
                if lesson_doc.id is not None:
                    new_ids[lesson_doc.id] = lesson.id
                linked = [
                    standards[(s.framework.upper(), s.code)].id
                    for s in lesson_doc.standards
                    if (s.framework.upper(), s.code) in standards
                ]
                await self._repo.set_lesson_standards(lesson.id, list(dict.fromkeys(linked)))
                pending_prerequisites.append((lesson, lesson_doc.prerequisite_lesson_ids))
        for lesson, old_ids in pending_prerequisites:
            mapped = [new_ids[i] for i in old_ids if i in new_ids and new_ids[i] != lesson.id]
            await self._repo.set_lesson_prerequisites(lesson.id, list(dict.fromkeys(mapped)))
        return course


class SearchService(_CurriculumBase):
    async def search(
        self,
        query: str,
        *,
        grade_level: int | None,
        subject_slug: str | None,
        kind: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[SearchHit], int]:
        subject_id = None
        if subject_slug:
            subject = await self._subjects.get_by_slug(subject_slug)
            if subject is None:
                return [], 0
            subject_id = subject.id
        return await self._repo.search(
            query.strip(),
            grade_level=grade_level,
            subject_id=subject_id,
            kind=kind,
            limit=limit,
            offset=offset,
        )


# --- helpers ---------------------------------------------------------------------------------


def _ensure_permutation(
    ids: Sequence[uuid.UUID], existing: dict[uuid.UUID, Any], what: str
) -> None:
    if len(ids) != len(existing) or set(ids) != set(existing):
        raise DomainValidationError(
            f"Send every {what} of the course exactly once, in the new order."
        )


def _reaches(graph: dict[uuid.UUID, set[uuid.UUID]], start: uuid.UUID) -> bool:
    """Does following prerequisites from ``start`` lead back to ``start``?"""
    stack, seen = list(graph.get(start, ())), set()
    while stack:
        node = stack.pop()
        if node == start:
            return True
        if node not in seen:
            seen.add(node)
            stack.extend(graph.get(node, ()))
    return False


def _document_media(document: CourseDocument) -> set[uuid.UUID]:
    return {
        m for unit in document.units for lesson in unit.lessons for m in lesson.content.media_ids()
    }
