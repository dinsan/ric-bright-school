import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Table,
    Text,
    UniqueConstraint,
    false,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.modules.catalog.domain import MAX_GRADE_LEVEL, MIN_GRADE_LEVEL
from app.modules.catalog.models import Subject
from app.modules.identity.domain import ChallengeKind, LinkStatus, Role, TokenPurpose


def _enum(enum_cls: type, name: str) -> Enum:
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        length=30,
        values_callable=lambda e: [member.value for member in e],
    )


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        # Emails are stored normalised to lower case, which makes the unique constraint
        # case-insensitive without the citext extension.
        CheckConstraint("email = lower(email)", name="email_lowercase"),
        CheckConstraint("username = lower(username)", name="username_lowercase"),
    )

    # Nullable: a child account managed by a parent has no email (and cannot sign in).
    email: Mapped[str | None] = mapped_column(String(320), unique=True)
    # Students only: a sign-in name for children without an email (school-issued or chosen
    # by a parent). Stored lower case, like emails.
    username: Mapped[str | None] = mapped_column(String(30), unique=True)
    # Nullable: no password yet (invited child account), social sign-in only, or deleted.
    password_hash: Mapped[str | None] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(100))
    role: Mapped[Role] = mapped_column(_enum(Role, "user_role"), index=True)
    locale: Mapped[str] = mapped_column(String(16), default="en", server_default="en")
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", server_default="UTC")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set when the account was deleted: personal data is scrubbed, the row stays so that
    # audit entries and authored content keep a valid reference.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    student_profile: Mapped["StudentProfile | None"] = relationship(lazy="raise")
    # teacher_profiles has two foreign keys to users (user_id, verified_by_id): name the one
    # that makes the profile belong to this user.
    teacher_profile: Mapped["TeacherProfile | None"] = relationship(
        foreign_keys="TeacherProfile.user_id", lazy="raise"
    )

    @property
    def is_email_verified(self) -> bool:
        return self.email_verified_at is not None


class StudentProfile(TimestampMixin, Base):
    __tablename__ = "student_profiles"
    __table_args__ = (
        CheckConstraint(
            f"grade_level BETWEEN {MIN_GRADE_LEVEL} AND {MAX_GRADE_LEVEL}",
            name="grade_level_range",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    grade_level: Mapped[int | None] = mapped_column(SmallInteger)
    date_of_birth: Mapped[date | None] = mapped_column(Date)
    # Free text until schools exist as records (Phase 6).
    school_name: Mapped[str | None] = mapped_column(String(200))
    # Grades 1-3: Argon2id hash of the picture sequence (e.g. "cat sun apple").
    picture_password_hash: Mapped[str | None] = mapped_column(String(255))


teacher_subjects = Table(
    "teacher_subjects",
    Base.metadata,
    Column(
        "teacher_id",
        ForeignKey("teacher_profiles.user_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("subject_id", ForeignKey("subjects.id", ondelete="CASCADE"), primary_key=True),
)


class TeacherProfile(TimestampMixin, Base):
    __tablename__ = "teacher_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    bio: Mapped[str | None] = mapped_column(Text)
    # The "verified" flag: set by a platform admin. Only verified teachers may publish.
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    subjects: Mapped[list[Subject]] = relationship(
        secondary=teacher_subjects, lazy="raise", order_by=Subject.name
    )

    @property
    def is_verified(self) -> bool:
        return self.verified_at is not None


class ParentChildLink(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "parent_child_links"
    __table_args__ = (
        UniqueConstraint("parent_id", "child_id", name="uq_parent_child_links_parent_id_child_id"),
        CheckConstraint("parent_id <> child_id", name="not_self"),
    )

    parent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    child_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[LinkStatus] = mapped_column(
        _enum(LinkStatus, "link_status"),
        default=LinkStatus.ACTIVE,
        server_default=LinkStatus.ACTIVE.value,
    )
    # True when the parent created the child account (parent-managed), False when an
    # existing student account was linked with a code.
    is_managed: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())

    child: Mapped[User] = relationship(foreign_keys=[child_id], lazy="raise")


class Session(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One sign-in on one device: the family of its rotating refresh tokens."""

    __tablename__ = "sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoke_reason: Mapped[str | None] = mapped_column(String(40))
    ip: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(255))
    # Parent "switch active child": remembered per session (per device).
    active_child_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    # Set for admin impersonation sessions (short-lived, no refresh token).
    impersonator_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    # A SignInMethod value; decides whether refreshing extends the session (age band).
    sign_in_method: Mapped[str | None] = mapped_column(String(20))

    user: Mapped[User] = relationship(foreign_keys=[user_id], lazy="raise")


class RefreshToken(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Every refresh token ever issued for a session, so an old one can be recognised."""

    __tablename__ = "refresh_tokens"

    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)  # SHA-256 hex
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    session: Mapped[Session] = relationship(lazy="raise")


class UserToken(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Single-use, expiring token sent by email (or shown as a link code)."""

    __tablename__ = "user_tokens"
    __table_args__ = (Index("ix_user_tokens_user_id_purpose", "user_id", "purpose"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    purpose: Mapped[TokenPurpose] = mapped_column(_enum(TokenPurpose, "token_purpose"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)  # SHA-256 hex
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Only for CHANGE_EMAIL: the address being verified.
    new_email: Mapped[str | None] = mapped_column(String(320))

    user: Mapped[User] = relationship(lazy="raise")


class LoginThrottle(TimestampMixin, Base):
    """Failed sign-in counter per account or IP. Keys are hashed: no emails or IPs stored."""

    __tablename__ = "login_throttles"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    failures: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_failure_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OAuthIdentity(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A Google / Microsoft / Apple account linked to a Lumina user."""

    __tablename__ = "oauth_identities"
    __table_args__ = (
        UniqueConstraint("provider", "subject", name="uq_oauth_identities_provider_subject"),
        UniqueConstraint("user_id", "provider", name="uq_oauth_identities_user_id_provider"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(20))
    # The provider's stable user id ("sub" claim). Emails can change; this does not.
    subject: Mapped[str] = mapped_column(String(255))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(lazy="raise")


class OAuthState(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One sign-in attempt with a provider: CSRF state, PKCE verifier, OIDC nonce.

    Kept on the server (never in the browser) and usable once, within 10 minutes.
    """

    __tablename__ = "oauth_states"

    state_hash: Mapped[str] = mapped_column(String(64), unique=True)
    provider: Mapped[str] = mapped_column(String(20))
    intent: Mapped[str] = mapped_column(String(10))  # login | signup | link
    signup_role: Mapped[str | None] = mapped_column(String(20))
    # For "link": the signed-in user who started it.
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    code_verifier: Mapped[str] = mapped_column(String(128))
    nonce: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LoginChallenge(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A sign-in that is not finished yet: waiting for a 2FA code, or for the web app to
    collect the tokens after a provider redirect. Single-use, short-lived, hashed."""

    __tablename__ = "login_challenges"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[ChallengeKind] = mapped_column(_enum(ChallengeKind, "challenge_kind"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    sign_in_method: Mapped[str] = mapped_column(String(20))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    user: Mapped[User] = relationship(lazy="raise")


class UserMfa(TimestampMixin, Base):
    """TOTP two-factor authentication (BE-AUTH-12). The secret is stored encrypted: unlike a
    password it must be readable to check codes."""

    __tablename__ = "user_mfa"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    secret_encrypted: Mapped[str] = mapped_column(Text)
    # Null while set up but not yet confirmed with a first code (2FA is off until then).
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 30-second step of the last accepted code: the same code cannot be used twice.
    last_used_step: Mapped[int | None] = mapped_column(BigInteger)

    @property
    def is_enabled(self) -> bool:
        return self.confirmed_at is not None


class MfaRecoveryCode(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "mfa_recovery_codes"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
