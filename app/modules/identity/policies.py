"""Authorization rules for identity, in one place so they are easy to audit (BE-AUTHZ-03).

Each function raises a domain error when the rule is broken. Services call them; routers
only check coarse permissions (see deps.py).
"""

from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.modules.identity.domain import (
    ASSIGNABLE_ROLES,
    SIGN_IN_POLICIES,
    TOTP_ROLES,
    Actor,
    AgeBand,
    LinkStatus,
    Role,
    SignInMethod,
    age_band,
)
from app.modules.identity.models import ParentChildLink, User


def ensure_not_impersonated(actor: Actor) -> None:
    """Account-security actions are for the real account owner only, never an admin
    acting as them: password, email, deleting the account, sessions."""
    if actor.is_impersonated:
        raise ForbiddenError("This action is not available while impersonating a user.")


def ensure_parent_of(actor: Actor, link: ParentChildLink | None) -> ParentChildLink:
    """A parent only sees children linked to them. Anything else looks like "not found"."""
    if link is None or link.parent_id != actor.user_id or link.status != LinkStatus.ACTIVE:
        raise NotFoundError("Child not found.")
    return link


def ensure_student(actor: Actor) -> None:
    if actor.role != Role.STUDENT:
        raise ForbiddenError("Only student accounts can create a parent link code.")


def ensure_teacher(actor: Actor) -> None:
    if actor.role != Role.TEACHER:
        raise ForbiddenError("Only teachers have a teacher profile.")


def ensure_can_change_user(actor: Actor, target: User) -> None:
    """Admins cannot lock themselves out, and student roles are not reassigned."""
    if target.id == actor.user_id:
        raise ForbiddenError("You cannot change your own role or deactivate yourself.")
    if target.deleted_at is not None:
        raise ForbiddenError("Deleted accounts cannot be changed.")


def ensure_assignable_role(target: User, role: Role) -> None:
    if role not in ASSIGNABLE_ROLES or target.role == Role.STUDENT:
        raise ForbiddenError(
            "Student accounts keep the student role, and the student role cannot be assigned."
        )


def ensure_can_impersonate(actor: Actor, target: User) -> None:
    if actor.is_impersonated:
        raise ForbiddenError("You are already impersonating a user.")
    if target.id == actor.user_id:
        raise ForbiddenError("You cannot impersonate yourself.")
    if target.role == Role.PLATFORM_ADMIN:
        raise ForbiddenError("Platform admins cannot be impersonated.")
    if not target.is_active or target.deleted_at is not None:
        raise ForbiddenError("Inactive or deleted accounts cannot be impersonated.")


# --- age bands and sign-in methods (BE-AUTH-10) --------------------------------------------


def band_of(user: User) -> AgeBand:
    """Needs ``user.student_profile`` loaded (it is, wherever users are loaded for sign-in)."""
    profile = user.student_profile if user.role == Role.STUDENT else None
    return age_band(user.role, profile.grade_level if profile else None)


def ensure_method_allowed(user: User, method: SignInMethod) -> None:
    """Younger children get simpler, supervised methods; see SIGN_IN_POLICIES."""
    if method not in SIGN_IN_POLICIES[band_of(user)].methods:
        raise ForbiddenError("This way of signing in is not available for this account.")


def ensure_guardian_manages_sign_in(student: User) -> None:
    """Parents and teachers set up sign-in only for children without their own email;
    a student with an email address manages their own password (and can reset it)."""
    if student.role != Role.STUDENT:
        raise ForbiddenError("Only student accounts can be set up this way.")
    if student.email is not None:
        raise ForbiddenError(
            "This student has their own email address and manages their own password."
        )


# --- two-factor authentication (BE-AUTH-12) ------------------------------------------------


def ensure_can_use_totp(actor: Actor) -> None:
    ensure_not_impersonated(actor)
    if actor.role not in TOTP_ROLES:
        raise ForbiddenError("Two-factor authentication is available to teachers and admins.")


# --- social sign-in (BE-AUTH-11) -----------------------------------------------------------


def ensure_can_unlink(user: User, provider: str, linked: list[str]) -> None:
    """Never remove the last way into an account."""
    if provider not in linked:
        raise NotFoundError("This sign-in method is not linked.")
    others = [p for p in linked if p != provider]
    if not user.password_hash and not others:
        raise ConflictError(
            "This is the only way to sign in to this account. Set a password first "
            '("forgot password" sends a link), or link another provider.'
        )
