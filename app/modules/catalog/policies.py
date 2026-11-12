"""Who may see and change catalog content (BE-AUTHZ-02/03), in one place for auditing.

- Published courses are public.
- Drafts and archived courses: visible only to their author and course managers
  (platform admins). Everyone else gets "not found", so their existence does not leak.
- Teachers create courses and edit or archive their own; platform admins any course.
- Publishing needs a verified teacher (or a platform admin).
- Teachers and platform admins create subjects; only platform admins delete them.
"""

from app.core.exceptions import ForbiddenError
from app.modules.catalog.domain import CourseStatus
from app.modules.catalog.models import Course
from app.modules.identity.domain import Actor, Permission


def is_course_manager(actor: Actor | None) -> bool:
    return actor is not None and actor.can(Permission.COURSES_MANAGE)


def can_view(actor: Actor | None, course: Course) -> bool:
    if course.status == CourseStatus.PUBLISHED:
        return True
    return is_course_manager(actor) or (actor is not None and course.author_id == actor.user_id)


def ensure_can_create_course(actor: Actor) -> None:
    if not (actor.can(Permission.COURSES_AUTHOR) or is_course_manager(actor)):
        raise ForbiddenError("Only teachers and administrators can create courses.")


def ensure_can_edit(actor: Actor, course: Course) -> None:
    if is_course_manager(actor):
        return
    if actor.can(Permission.COURSES_AUTHOR) and course.author_id == actor.user_id:
        return
    raise ForbiddenError("You can only change courses you created.")


def ensure_can_publish(actor: Actor) -> None:
    if is_course_manager(actor) or (
        actor.can(Permission.COURSES_AUTHOR) and actor.is_verified_teacher
    ):
        return
    raise ForbiddenError(
        "Only verified teachers can publish courses. Save it as a draft until your "
        "teacher account is verified."
    )


def ensure_can_list_status(actor: Actor | None, status: CourseStatus) -> None:
    """Listing drafts/archived courses: managers see all, authors see only their own."""
    if status == CourseStatus.PUBLISHED:
        return
    if actor is None or not (actor.can(Permission.COURSES_AUTHOR) or is_course_manager(actor)):
        raise ForbiddenError("Only published courses are public.")


def ensure_can_create_subject(actor: Actor) -> None:
    if not actor.can(Permission.SUBJECTS_CREATE):
        raise ForbiddenError("Only teachers and administrators can create subjects.")


def ensure_can_delete_subject(actor: Actor) -> None:
    if not actor.can(Permission.SUBJECTS_DELETE):
        raise ForbiddenError("Only administrators can delete subjects.")


def can_manage_paths(actor: Actor | None) -> bool:
    return actor is not None and actor.can(Permission.PATHS_MANAGE)


def ensure_can_manage_paths(actor: Actor) -> None:
    if not can_manage_paths(actor):
        raise ForbiddenError("Only administrators curate learning paths.")
