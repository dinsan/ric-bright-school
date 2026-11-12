"""Who may see and change classes (BE-AUTHZ-03), in one place so it is easy to audit.

- A teacher sees and manages only their own classes. Other teachers' classes answer
  "not found", so their existence does not leak.
- A student sees the classes they are enrolled in (read-only).
- Platform admins (``classes:manage``) see and manage every class.
- A teacher manages a child's sign-in only while the child is enrolled in one of their
  classes, and only for children without their own email (identity policy).
"""

from app.core.exceptions import ForbiddenError, NotFoundError
from app.modules.classes.domain import EnrollmentStatus
from app.modules.classes.models import ClassEnrollment, SchoolClass
from app.modules.identity.domain import Actor, Permission, Role


def is_class_manager(actor: Actor) -> bool:
    return actor.can(Permission.CLASSES_MANAGE)


def is_teacher_role(actor: Actor) -> bool:
    return actor.can(Permission.CLASSES_TEACH)


def is_teacher_of(actor: Actor, school_class: SchoolClass) -> bool:
    return actor.can(Permission.CLASSES_TEACH) and school_class.teacher_id == actor.user_id


def ensure_can_create_class(actor: Actor) -> None:
    if not actor.can(Permission.CLASSES_TEACH):
        raise ForbiddenError("Only teachers can create classes.")


def ensure_can_list_classes(actor: Actor) -> None:
    if not (actor.can(Permission.CLASSES_TEACH) or actor.role == Role.STUDENT):
        raise ForbiddenError("Only teachers and students have classes.")


def ensure_can_view(
    actor: Actor, school_class: SchoolClass | None, enrollment: ClassEnrollment | None
) -> SchoolClass:
    if school_class is not None and (
        is_class_manager(actor)
        or is_teacher_of(actor, school_class)
        or (enrollment is not None and enrollment.status == EnrollmentStatus.ACTIVE)
    ):
        return school_class
    raise NotFoundError("Class not found.")


def ensure_can_manage(actor: Actor, school_class: SchoolClass | None) -> SchoolClass:
    """Change the class or its roster. Students of the class get 403, everyone else 404."""
    if school_class is None:
        raise NotFoundError("Class not found.")
    if is_class_manager(actor) or is_teacher_of(actor, school_class):
        if school_class.is_archived:
            raise ForbiddenError("This class is archived.")
        return school_class
    raise NotFoundError("Class not found.")


def ensure_enrolled(enrollment: ClassEnrollment | None) -> ClassEnrollment:
    if enrollment is None or enrollment.status != EnrollmentStatus.ACTIVE:
        raise NotFoundError("Student not found in this class.")
    return enrollment


def ensure_student_can_join(actor: Actor) -> None:
    if actor.role != Role.STUDENT:
        raise ForbiddenError("Only student accounts join classes with a code.")
