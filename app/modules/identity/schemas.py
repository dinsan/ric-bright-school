import uuid
from datetime import UTC, date, datetime
from functools import lru_cache
from typing import Annotated, Literal, Self
from zoneinfo import available_timezones

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    EmailStr,
    Field,
    model_validator,
)

from app.core.pagination import PageParams
from app.modules.catalog.domain import MAX_GRADE_LEVEL, MIN_GRADE_LEVEL
from app.modules.catalog.schemas import SubjectRead
from app.modules.identity.domain import (
    PASSWORD_MAX_LENGTH,
    PASSWORD_MIN_LENGTH,
    PICTURE_PASSWORD_LENGTH,
    PICTURES,
    AgeBand,
    Permission,
    Role,
)


@lru_cache(maxsize=1)
def _timezones() -> frozenset[str]:
    return frozenset(available_timezones())


def _check_timezone(value: str) -> str:
    if value not in _timezones():
        raise ValueError("Unknown time zone; use an IANA name such as 'Europe/London'.")
    return value


def _check_birth_date(value: date | None) -> date | None:
    if value is not None and value > datetime.now(UTC).date():
        raise ValueError("The date of birth cannot be in the future.")
    return value


# Length over complexity (BE-AUTH-05): no composition rules, a generous maximum.
Password = Annotated[str, Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)]
# Only for checking an existing password: the policy may have changed since it was set.
ExistingPassword = Annotated[str, Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)]
DisplayName = Annotated[str, Field(min_length=1, max_length=100)]
Locale = Annotated[
    str, Field(max_length=16, pattern=r"^[a-z]{2,3}(-[A-Z][a-z]{3})?(-([A-Z]{2}|[0-9]{3}))?$")
]
TimeZone = Annotated[str, Field(max_length=64), AfterValidator(_check_timezone)]
GradeLevel = Annotated[int, Field(ge=MIN_GRADE_LEVEL, le=MAX_GRADE_LEVEL)]
BirthDate = Annotated[date | None, AfterValidator(_check_birth_date)]
SingleUseToken = Annotated[str, Field(min_length=1, max_length=200)]

# Students: the minimum depends on the age band (8, 10 or 12) and is checked by the service.
STUDENT_PASSWORD_MIN_LENGTH = 8
StudentPassword = Annotated[
    str, Field(min_length=STUDENT_PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)
]


def _lower_strip(value: object) -> object:
    return value.strip().lower() if isinstance(value, str) else value


def _check_picture(value: str) -> str:
    if value not in PICTURES:
        raise ValueError(f"Unknown picture; choose from: {', '.join(PICTURES)}.")
    return value


# Usernames are case-insensitive: letters, digits, dots, dashes and underscores, 3-30
# characters, starting and ending with a letter or digit. No "@": never confused with email.
Username = Annotated[
    str,
    BeforeValidator(_lower_strip),
    Field(min_length=3, max_length=30, pattern=r"^[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?$"),
]
PictureKey = Annotated[str, BeforeValidator(_lower_strip), AfterValidator(_check_picture)]
PicturePassword = Annotated[
    list[PictureKey],
    Field(
        min_length=PICTURE_PASSWORD_LENGTH,
        max_length=PICTURE_PASSWORD_LENGTH,
        description=f"Exactly {PICTURE_PASSWORD_LENGTH} picture keys, in order.",
    ),
]


class _PatchModel(BaseModel):
    """Partial update: unknown fields are errors, explicit null is rejected."""

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _reject_nulls(self) -> Self:
        nulled = sorted(n for n in self.model_fields_set if getattr(self, n) is None)
        if nulled:
            raise ValueError(f"These fields cannot be null: {', '.join(nulled)}.")
        return self


# --- authentication -----------------------------------------------------------------------


class RegisterRequest(BaseModel):
    email: EmailStr
    password: Password
    display_name: DisplayName
    role: Literal["parent", "teacher"] = Field(
        description="Self-registration is for adults. Students are added by a parent."
    )
    locale: Locale = "en"
    timezone: TimeZone = "UTC"


class AcceptedResponse(BaseModel):
    """Deliberately identical whether or not the email belongs to an account."""

    message: str


class EmailRequest(BaseModel):
    email: EmailStr


class TokenRequest(BaseModel):
    token: SingleUseToken


class LoginRequest(BaseModel):
    email: EmailStr
    password: ExistingPassword


class TokenPair(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(description="Access token lifetime in seconds.")
    refresh_token: str


class MfaRequired(BaseModel):
    """The password was right, but the account has two-factor authentication: send a code
    from the authenticator app (or a recovery code) to `POST /auth/login/mfa`."""

    mfa_required: Literal[True] = True
    mfa_token: str
    expires_in: int
    methods: list[Literal["totp", "recovery_code"]] = ["totp", "recovery_code"]


# What every sign-in endpoint returns: tokens, or a request for the second factor.
LoginResponse = TokenPair | MfaRequired


class UsernameLoginRequest(BaseModel):
    username: Annotated[str, BeforeValidator(_lower_strip), Field(min_length=1, max_length=30)]
    password: ExistingPassword


class MfaLoginRequest(BaseModel):
    mfa_token: SingleUseToken
    code: str | None = Field(default=None, max_length=10, description="6-digit app code.")
    recovery_code: str | None = Field(default=None, max_length=20)

    @model_validator(mode="after")
    def _exactly_one(self) -> Self:
        if (self.code is None) == (self.recovery_code is None):
            raise ValueError("Send either `code` or `recovery_code`.")
        return self


class PictureLoginRequest(BaseModel):
    class_code: str = Field(min_length=8, max_length=20)
    student_id: uuid.UUID
    pictures: PicturePassword


class ClassSignInStudent(BaseModel):
    id: uuid.UUID
    display_name: str


class ClassSignInRead(BaseModel):
    """What a classroom device shows: the class, the children to tap, the pictures."""

    class_name: str
    students: list[ClassSignInStudent]
    pictures: list[str]
    picture_password_length: int = PICTURE_PASSWORD_LENGTH


class OAuthProvidersRead(BaseModel):
    providers: list[str]


class OAuthStartRead(BaseModel):
    authorization_url: str


class OAuthExchangeRequest(BaseModel):
    code: SingleUseToken


class LinkedIdentityRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    provider: str
    created_at: datetime
    last_used_at: datetime | None


class TotpSetupRead(BaseModel):
    secret: str = Field(description="Base32, for typing into the app by hand.")
    otpauth_uri: str = Field(description="Show as a QR code.")


class TotpConfirmRequest(BaseModel):
    code: str = Field(min_length=6, max_length=10)


class RecoveryCodesRead(BaseModel):
    recovery_codes: list[str] = Field(description="Shown once. Each works one time.")


class TotpDisableRequest(BaseModel):
    code: str = Field(min_length=6, max_length=20, description="App code or recovery code.")
    password: ExistingPassword | None = Field(
        default=None, description="Required when the account has a password."
    )


class RefreshRequest(BaseModel):
    refresh_token: SingleUseToken


class ResetPasswordRequest(BaseModel):
    token: SingleUseToken
    new_password: Password


# --- the current user (/me) ---------------------------------------------------------------


class StudentProfileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    grade_level: int | None
    date_of_birth: date | None
    school_name: str | None


class TeacherProfileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    bio: str | None
    is_verified: bool
    subjects: list[SubjectRead]


class MeRead(BaseModel):
    id: uuid.UUID
    email: str | None
    username: str | None
    display_name: str
    role: Role
    age_band: AgeBand
    permissions: list[Permission]
    sign_in_methods: list[str] = Field(
        description="Ways this account can sign in now (email, username, picture, google, ...)."
    )
    mfa_enabled: bool
    locale: str
    timezone: str
    email_verified: bool
    created_at: datetime
    student_profile: StudentProfileRead | None = None
    teacher_profile: TeacherProfileRead | None = None
    active_child_id: uuid.UUID | None = None
    impersonated_by: uuid.UUID | None = None


class MeUpdate(_PatchModel):
    display_name: DisplayName | None = None
    locale: Locale | None = None
    timezone: TimeZone | None = None


class ChangePasswordRequest(BaseModel):
    current_password: ExistingPassword
    new_password: Password


class ChangeEmailRequest(BaseModel):
    new_email: EmailStr
    password: ExistingPassword | None = Field(
        default=None,
        description="Required when the account has a password. Accounts that only use "
        "Google/Microsoft/Apple must have signed in within the last 10 minutes instead.",
    )


class DeleteAccountRequest(BaseModel):
    password: ExistingPassword | None = Field(
        default=None,
        description="Required when the account has a password. Accounts that only use "
        "Google/Microsoft/Apple must have signed in within the last 10 minutes instead.",
    )


class SessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    last_used_at: datetime
    expires_at: datetime
    ip: str | None
    user_agent: str | None
    sign_in_method: str | None
    current: bool = False


class TeacherProfileUpdate(_PatchModel):
    bio: str | None = Field(default=None, max_length=2000)
    subject_ids: list[uuid.UUID] | None = Field(default=None, max_length=20)


# --- parents and children -----------------------------------------------------------------


class ChildCreate(BaseModel):
    display_name: DisplayName
    grade_level: GradeLevel | None = None
    date_of_birth: BirthDate = None
    school_name: str | None = Field(default=None, max_length=200)
    email: EmailStr | None = Field(
        default=None,
        description="Optional, Grade 4 and up. The child gets an email to set a password "
        "and then signs in with it.",
    )
    username: Username | None = Field(
        default=None, description="Optional sign-in name (with `password`)."
    )
    password: StudentPassword | None = None
    picture_password: PicturePassword | None = Field(
        default=None, description="Grades 1-3: sign in at school with the class code."
    )


class StudentSignInUpdate(_PatchModel):
    """Set up or change how a child signs in. Sent by a parent or a teacher of the child."""

    username: Username | None = None
    password: StudentPassword | None = None
    picture_password: PicturePassword | None = None


class ChildRead(BaseModel):
    id: uuid.UUID
    display_name: str
    email: str | None
    grade_level: int | None
    date_of_birth: date | None
    school_name: str | None
    is_managed: bool
    username: str | None
    age_band: AgeBand
    sign_in_methods: list[str]
    can_sign_in: bool


class LinkCodeRead(BaseModel):
    code: str
    expires_at: datetime


class LinkChildRequest(BaseModel):
    code: str = Field(min_length=8, max_length=20)


class ActiveChildRequest(BaseModel):
    child_id: uuid.UUID | None = Field(description="null clears the active child.")


# --- administration -----------------------------------------------------------------------


class UserAdminRead(BaseModel):
    id: uuid.UUID
    email: str | None
    display_name: str
    role: Role
    is_active: bool
    email_verified: bool
    teacher_verified: bool | None
    deleted: bool
    created_at: datetime


class UserAdminQuery(PageParams):
    q: str | None = Field(default=None, max_length=100, description="Email or name contains.")
    role: Role | None = None
    is_active: bool | None = None


class UserAdminUpdate(_PatchModel):
    role: Role | None = None
    is_active: bool | None = None


class TeacherVerificationRequest(BaseModel):
    verified: bool


class ImpersonationRead(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    user_id: uuid.UUID
