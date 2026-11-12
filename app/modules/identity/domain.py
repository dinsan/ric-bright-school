"""Framework-free vocabulary for identity: roles, permissions, token purposes, rules."""

import uuid
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum


class Role(StrEnum):
    STUDENT = "student"
    PARENT = "parent"
    TEACHER = "teacher"
    SCHOOL_ADMIN = "school_admin"
    PLATFORM_ADMIN = "platform_admin"


# Roles anyone may pick when signing up. Students are added by a parent (see the children
# endpoints); school and platform admins are appointed by a platform admin.
SELF_REGISTRATION_ROLES = frozenset({Role.PARENT, Role.TEACHER})

# Roles a platform admin may assign with "change role". Student accounts belong to the
# parent/child model and are never converted to or from another role.
ASSIGNABLE_ROLES = frozenset({Role.PARENT, Role.TEACHER, Role.SCHOOL_ADMIN, Role.PLATFORM_ADMIN})


class Permission(StrEnum):
    COURSES_AUTHOR = "courses:author"  # create courses and edit/archive one's own
    COURSES_MANAGE = "courses:manage"  # edit, publish or archive any course, see all drafts
    SUBJECTS_CREATE = "subjects:create"
    SUBJECTS_DELETE = "subjects:delete"
    USERS_MANAGE = "users:manage"  # list users, change roles, (de)activate, verify teachers
    USERS_IMPERSONATE = "users:impersonate"
    AUDIT_READ = "audit:read"
    CHILDREN_MANAGE = "children:manage"  # add/link children, switch the active child
    CLASSES_TEACH = "classes:teach"  # create classes and manage their own rosters
    CLASSES_MANAGE = "classes:manage"  # see and change every class
    STANDARDS_MANAGE = "standards:manage"  # curriculum standards and frameworks
    PATHS_MANAGE = "paths:manage"  # curated learning paths


# Permissions are derived from the role in code, not stored in the database (BE-AUTH-02).
ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.STUDENT: frozenset(),
    Role.PARENT: frozenset({Permission.CHILDREN_MANAGE}),
    Role.TEACHER: frozenset(
        {Permission.COURSES_AUTHOR, Permission.SUBJECTS_CREATE, Permission.CLASSES_TEACH}
    ),
    # School administration arrives with schools (Phase 6); no extra rights yet.
    Role.SCHOOL_ADMIN: frozenset(),
    Role.PLATFORM_ADMIN: frozenset(Permission),
}


class SignInMethod(StrEnum):
    """How a session was started. Stored on the session and in the audit log."""

    EMAIL = "email"  # email + password
    USERNAME = "username"  # username + password (students)
    PICTURE = "picture"  # class code + picture password (Grades 1-3)
    GOOGLE = "google"
    MICROSOFT = "microsoft"
    APPLE = "apple"
    IMPERSONATION = "impersonation"


SOCIAL_METHODS = frozenset({SignInMethod.GOOGLE, SignInMethod.MICROSOFT, SignInMethod.APPLE})


class AgeBand(StrEnum):
    """Sign-in rules depend on age: younger children get simpler, more supervised methods."""

    EARLY = "early"  # Grades 1-3
    MIDDLE = "middle"  # Grades 4-8 (and students whose grade is not known)
    TEEN = "teen"  # Grades 9-12
    ADULT = "adult"  # parents, teachers, admins


@dataclass(frozen=True)
class SignInPolicy:
    methods: frozenset[SignInMethod]
    password_min_length: int
    session_lifetime: timedelta
    # Sliding sessions are extended at every refresh; fixed ones end at session_lifetime
    # after sign-in whatever happens (shared classroom devices).
    sliding: bool


SIGN_IN_POLICIES: dict[AgeBand, SignInPolicy] = {
    AgeBand.EARLY: SignInPolicy(
        methods=frozenset({SignInMethod.PICTURE, SignInMethod.USERNAME}),
        password_min_length=8,
        session_lifetime=timedelta(hours=8),  # one school day
        sliding=False,
    ),
    AgeBand.MIDDLE: SignInPolicy(
        methods=frozenset({SignInMethod.USERNAME, SignInMethod.EMAIL}),
        password_min_length=8,
        session_lifetime=timedelta(days=7),
        sliding=True,
    ),
    AgeBand.TEEN: SignInPolicy(
        methods=frozenset({SignInMethod.USERNAME, SignInMethod.EMAIL} | SOCIAL_METHODS),
        password_min_length=10,
        session_lifetime=timedelta(days=14),
        sliding=True,
    ),
    AgeBand.ADULT: SignInPolicy(
        methods=frozenset({SignInMethod.EMAIL} | SOCIAL_METHODS),
        password_min_length=12,
        session_lifetime=timedelta(days=30),  # capped by LUMINA_REFRESH_TOKEN_TTL_DAYS
        sliding=True,
    ),
}


def age_band(role: "Role", grade_level: int | None) -> AgeBand:
    if role != Role.STUDENT:
        return AgeBand.ADULT
    if grade_level is None:
        return AgeBand.MIDDLE
    if grade_level <= 3:
        return AgeBand.EARLY
    if grade_level <= 8:
        return AgeBand.MIDDLE
    return AgeBand.TEEN


# Picture passwords: a sequence of PICTURE_PASSWORD_LENGTH pictures (repeats allowed) from
# this set. The web app shows one image per key. 16^3 = 4096 combinations, so picture
# sign-in is only possible through a class code and is locked hard after repeated failures.
PICTURES = (
    "apple", "ball", "bird", "boat", "cake", "car", "cat", "dog",
    "fish", "flower", "hat", "house", "moon", "star", "sun", "tree",
)  # fmt: skip
PICTURE_PASSWORD_LENGTH = 3
PICTURE_THROTTLE_THRESHOLD = 3  # failures before the backoff starts
PICTURE_HARD_LOCK_FAILURES = 10  # then only a teacher or parent can unlock (new pictures)

# Two-factor authentication (BE-AUTH-12) is offered to these roles.
TOTP_ROLES = frozenset({Role.TEACHER, Role.SCHOOL_ADMIN, Role.PLATFORM_ADMIN})
RECOVERY_CODE_COUNT = 10
MFA_CHALLENGE_TTL = timedelta(minutes=5)
MFA_CHALLENGE_MAX_ATTEMPTS = 5
OAUTH_STATE_TTL = timedelta(minutes=10)
OAUTH_HANDOFF_TTL = timedelta(minutes=2)
# Actions that need a password, for accounts without one (social sign-in only), need a
# sign-in at most this long ago instead.
RECENT_SIGN_IN = timedelta(minutes=10)


class ChallengeKind(StrEnum):
    MFA = "mfa"  # password was right; a TOTP or recovery code is still needed
    OAUTH_HANDOFF = "oauth_handoff"  # provider sign-in done; the web app collects tokens


class TokenPurpose(StrEnum):
    """What a single-use emailed token (or code) is for."""

    VERIFY_EMAIL = "verify_email"
    RESET_PASSWORD = "reset_password"
    ACTIVATE_ACCOUNT = "activate_account"  # a child account a parent created with an email
    CHANGE_EMAIL = "change_email"
    PARENT_LINK = "parent_link"  # code a student gives a parent to link accounts


TOKEN_TTL: dict[TokenPurpose, timedelta] = {
    TokenPurpose.VERIFY_EMAIL: timedelta(hours=48),
    TokenPurpose.RESET_PASSWORD: timedelta(hours=1),
    TokenPurpose.ACTIVATE_ACCOUNT: timedelta(days=7),
    TokenPurpose.CHANGE_EMAIL: timedelta(hours=24),
    TokenPurpose.PARENT_LINK: timedelta(hours=24),
}

# Minimum gap between two emails of the same kind to the same account (anti email-bombing).
EMAIL_RESEND_COOLDOWN = timedelta(seconds=60)


class LinkStatus(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"


class SessionEndReason(StrEnum):
    LOGOUT = "logout"
    REVOKED_BY_USER = "revoked_by_user"
    REFRESH_TOKEN_REUSE = "refresh_token_reuse"
    PASSWORD_CHANGED = "password_changed"
    PASSWORD_RESET = "password_reset"
    ACCOUNT_DEACTIVATED = "account_deactivated"
    ACCOUNT_DELETED = "account_deleted"
    ROLE_CHANGED = "role_changed"


# --- Password policy (BE-AUTH-05): length over complexity --------------------------------
PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 128  # bounds hashing work per request

# --- Login throttling (BE-AUTH-08) ---------------------------------------------------------
# Failures within FAILURE_WINDOW count; after the threshold each further failure locks the
# key for BASE * 2^(extra failures), capped at MAX. Per-IP limits are much higher because
# a whole school can share one IP address.
THROTTLE_FAILURE_WINDOW = timedelta(hours=1)
THROTTLE_ACCOUNT_THRESHOLD = 5
THROTTLE_IP_THRESHOLD = 50
THROTTLE_BASE_LOCKOUT = timedelta(seconds=30)
THROTTLE_MAX_LOCKOUT = timedelta(minutes=15)


@dataclass(frozen=True)
class Actor:
    """The authenticated caller, as other modules see it (no ORM or HTTP types)."""

    user_id: uuid.UUID
    role: Role
    session_id: uuid.UUID
    is_verified_teacher: bool = False
    impersonator_id: uuid.UUID | None = None

    @property
    def permissions(self) -> frozenset[Permission]:
        return ROLE_PERMISSIONS[self.role]

    def can(self, permission: Permission) -> bool:
        return permission in self.permissions

    @property
    def is_impersonated(self) -> bool:
        return self.impersonator_id is not None


@dataclass(frozen=True)
class RequestInfo:
    """Where a request came from, for throttling and the audit log."""

    ip: str | None = None
    user_agent: str | None = None
