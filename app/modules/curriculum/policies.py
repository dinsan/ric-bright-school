"""Who may author and read course content (BE-AUTHZ-03 style: in one place).

- Authoring (structure, content, publishing): the course's author, or a platform admin.
- Reading published content: anyone, while the course is published. The author and admins
  can also read versions of an unpublished course (preview, history).
- Media in a lesson: your own uploads, the course author's, or media the lesson already
  uses (so copied courses stay editable). Platform admins may use any media.
- Duplicating: authors copy their own drafts; any teacher may copy a published course as a
  starting point (the copy is theirs, a draft).
"""

import uuid

from app.core.exceptions import NotFoundError
from app.modules.catalog import policies as catalog_policies
from app.modules.catalog.domain import CourseStatus
from app.modules.catalog.models import Course
from app.modules.identity.domain import Actor, Permission
from app.modules.media.models import MediaAsset


def ensure_can_author(actor: Actor, course: Course) -> None:
    catalog_policies.ensure_can_edit(actor, course)


def can_edit(actor: Actor | None, course: Course) -> bool:
    if actor is None:
        return False
    return catalog_policies.is_course_manager(actor) or (
        actor.can(Permission.COURSES_AUTHOR) and course.author_id == actor.user_id
    )


def ensure_can_read_published(actor: Actor | None, course: Course) -> None:
    if course.status == CourseStatus.PUBLISHED or can_edit(actor, course):
        return
    raise NotFoundError("Course not found.")


def can_use_media(
    actor: Actor, course: Course, asset: MediaAsset, already_used: set[uuid.UUID]
) -> bool:
    return (
        asset.id in already_used
        or catalog_policies.is_course_manager(actor)
        or asset.owner_id in {actor.user_id, course.author_id}
    )
