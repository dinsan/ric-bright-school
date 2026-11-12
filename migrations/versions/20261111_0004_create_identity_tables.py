"""create identity tables and course authors

Users (email sign-in), roles, student/teacher profiles, parent-child links, sessions with
rotating refresh tokens, single-use emailed tokens, login throttling, and `courses.author_id`.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-11
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Tables with `updated_at`: they get the set_updated_at() trigger from migration 0002.
TIMESTAMPED_TABLES = (
    "users",
    "student_profiles",
    "teacher_profiles",
    "parent_child_links",
    "sessions",
    "refresh_tokens",
    "user_tokens",
    "login_throttles",
)


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


def _enum(name: str, *values: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, length=30)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column(
            "role",
            _enum("user_role", "student", "parent", "teacher", "school_admin", "platform_admin"),
            nullable=False,
        ),
        sa.Column("locale", sa.String(length=16), server_default="en", nullable=False),
        sa.Column("timezone", sa.String(length=64), server_default="UTC", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("email = lower(email)", name=op.f("ck_users_email_lowercase")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_index(op.f("ix_users_role"), "users", ["role"])

    op.create_table(
        "student_profiles",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("grade_level", sa.SmallInteger(), nullable=True),
        sa.Column("date_of_birth", sa.Date(), nullable=True),
        sa.Column("school_name", sa.String(length=200), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("grade_level BETWEEN 1 AND 12", name=op.f("ck_student_profiles_grade_level_range")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_student_profiles_user_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_student_profiles")),
    )

    op.create_table(
        "teacher_profiles",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("bio", sa.Text(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_by_id", sa.Uuid(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_teacher_profiles_user_id_users"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["verified_by_id"], ["users.id"], name=op.f("fk_teacher_profiles_verified_by_id_users"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_teacher_profiles")),
    )

    op.create_table(
        "teacher_subjects",
        sa.Column("teacher_id", sa.Uuid(), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["subject_id"], ["subjects.id"], name=op.f("fk_teacher_subjects_subject_id_subjects"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["teacher_id"], ["teacher_profiles.user_id"], name=op.f("fk_teacher_subjects_teacher_id_teacher_profiles"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("teacher_id", "subject_id", name=op.f("pk_teacher_subjects")),
    )

    op.create_table(
        "parent_child_links",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=False),
        sa.Column("child_id", sa.Uuid(), nullable=False),
        sa.Column("status", _enum("link_status", "active", "revoked"), server_default="active", nullable=False),
        sa.Column("is_managed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("parent_id <> child_id", name=op.f("ck_parent_child_links_not_self")),
        sa.ForeignKeyConstraint(["child_id"], ["users.id"], name=op.f("fk_parent_child_links_child_id_users"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_id"], ["users.id"], name=op.f("fk_parent_child_links_parent_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_parent_child_links")),
        sa.UniqueConstraint("parent_id", "child_id", name="uq_parent_child_links_parent_id_child_id"),
    )
    op.create_index(op.f("ix_parent_child_links_child_id"), "parent_child_links", ["child_id"])
    op.create_index(op.f("ix_parent_child_links_parent_id"), "parent_child_links", ["parent_id"])

    op.create_table(
        "sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoke_reason", sa.String(length=40), nullable=True),
        sa.Column("ip", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.String(length=255), nullable=True),
        sa.Column("active_child_id", sa.Uuid(), nullable=True),
        sa.Column("impersonator_id", sa.Uuid(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["active_child_id"], ["users.id"], name=op.f("fk_sessions_active_child_id_users"), ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["impersonator_id"], ["users.id"], name=op.f("fk_sessions_impersonator_id_users"), ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_sessions_user_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sessions")),
    )
    op.create_index(op.f("ix_sessions_user_id"), "sessions", ["user_id"])

    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], name=op.f("fk_refresh_tokens_session_id_sessions"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refresh_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_refresh_tokens_token_hash")),
    )
    op.create_index(op.f("ix_refresh_tokens_session_id"), "refresh_tokens", ["session_id"])

    op.create_table(
        "user_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "purpose",
            _enum("token_purpose", "verify_email", "reset_password", "activate_account", "change_email", "parent_link"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("new_email", sa.String(length=320), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_user_tokens_user_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_user_tokens_token_hash")),
    )
    op.create_index("ix_user_tokens_user_id_purpose", "user_tokens", ["user_id", "purpose"])

    op.create_table(
        "login_throttles",
        sa.Column("key", sa.String(length=80), nullable=False),
        sa.Column("failures", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_login_throttles")),
    )

    op.add_column("courses", sa.Column("author_id", sa.Uuid(), nullable=True))
    op.create_index(op.f("ix_courses_author_id"), "courses", ["author_id"])
    op.create_foreign_key(
        op.f("fk_courses_author_id_users"), "courses", "users", ["author_id"], ["id"], ondelete="SET NULL"
    )

    for table in TIMESTAMPED_TABLES:
        op.execute(
            f"CREATE TRIGGER trg_{table}_set_updated_at BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )


def downgrade() -> None:
    for table in TIMESTAMPED_TABLES:
        op.execute(f"DROP TRIGGER trg_{table}_set_updated_at ON {table}")
    op.drop_constraint(op.f("fk_courses_author_id_users"), "courses", type_="foreignkey")
    op.drop_index(op.f("ix_courses_author_id"), table_name="courses")
    op.drop_column("courses", "author_id")
    op.drop_table("login_throttles")
    op.drop_index("ix_user_tokens_user_id_purpose", table_name="user_tokens")
    op.drop_table("user_tokens")
    op.drop_index(op.f("ix_refresh_tokens_session_id"), table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
    op.drop_index(op.f("ix_sessions_user_id"), table_name="sessions")
    op.drop_table("sessions")
    op.drop_index(op.f("ix_parent_child_links_parent_id"), table_name="parent_child_links")
    op.drop_index(op.f("ix_parent_child_links_child_id"), table_name="parent_child_links")
    op.drop_table("parent_child_links")
    op.drop_table("teacher_subjects")
    op.drop_table("teacher_profiles")
    op.drop_table("student_profiles")
    op.drop_index(op.f("ix_users_role"), table_name="users")
    op.drop_table("users")
