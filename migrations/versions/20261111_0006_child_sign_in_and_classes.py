"""child sign-in methods and classes

Usernames and picture passwords for children (BE-AUTH-10), the sign-in method on each
session (decides the session length by age band), and classes with join codes and
enrolments (needed for "a teacher sees only own classes" and class-code sign-in).

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-11
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIMESTAMPED_TABLES = ("classes", "class_enrollments")


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


def upgrade() -> None:
    op.add_column("users", sa.Column("username", sa.String(length=30), nullable=True))
    op.create_unique_constraint(op.f("uq_users_username"), "users", ["username"])
    op.create_check_constraint(op.f("ck_users_username_lowercase"), "users", "username = lower(username)")
    op.add_column("student_profiles", sa.Column("picture_password_hash", sa.String(length=255), nullable=True))
    op.add_column("sessions", sa.Column("sign_in_method", sa.String(length=20), nullable=True))
    # Sessions from before this migration were started with email + password (or by an admin).
    op.execute(
        "UPDATE sessions SET sign_in_method = CASE WHEN impersonator_id IS NULL "
        "THEN 'email' ELSE 'impersonation' END"
    )

    op.create_table(
        "classes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("teacher_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("grade_level", sa.SmallInteger(), nullable=True),
        sa.Column("subject_id", sa.Uuid(), nullable=True),
        sa.Column("school_year", sa.String(length=9), nullable=True),
        sa.Column("join_code", sa.String(length=9), nullable=False),
        sa.Column("picture_sign_in", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("grade_level BETWEEN 1 AND 12", name=op.f("ck_classes_grade_level_range")),
        sa.ForeignKeyConstraint(["subject_id"], ["subjects.id"], name=op.f("fk_classes_subject_id_subjects"), ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["teacher_id"], ["users.id"], name=op.f("fk_classes_teacher_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_classes")),
        sa.UniqueConstraint("join_code", name=op.f("uq_classes_join_code")),
    )
    op.create_index(op.f("ix_classes_teacher_id"), "classes", ["teacher_id"])

    op.create_table(
        "class_enrollments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("class_id", sa.Uuid(), nullable=False),
        sa.Column("student_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("active", "removed", name="enrollment_status", native_enum=False, length=30),
            server_default="active",
            nullable=False,
        ),
        *_timestamps(),
        sa.ForeignKeyConstraint(["class_id"], ["classes.id"], name=op.f("fk_class_enrollments_class_id_classes"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["student_id"], ["users.id"], name=op.f("fk_class_enrollments_student_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_class_enrollments")),
        sa.UniqueConstraint("class_id", "student_id", name="uq_class_enrollments_class_id_student_id"),
    )
    op.create_index(op.f("ix_class_enrollments_student_id"), "class_enrollments", ["student_id"])

    for table in TIMESTAMPED_TABLES:
        op.execute(
            f"CREATE TRIGGER trg_{table}_set_updated_at BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )


def downgrade() -> None:
    for table in TIMESTAMPED_TABLES:
        op.execute(f"DROP TRIGGER trg_{table}_set_updated_at ON {table}")
    op.drop_index(op.f("ix_class_enrollments_student_id"), table_name="class_enrollments")
    op.drop_table("class_enrollments")
    op.drop_index(op.f("ix_classes_teacher_id"), table_name="classes")
    op.drop_table("classes")
    op.drop_column("sessions", "sign_in_method")
    op.drop_column("student_profiles", "picture_password_hash")
    op.drop_constraint(op.f("ck_users_username_lowercase"), "users", type_="check")
    op.drop_constraint(op.f("uq_users_username"), "users", type_="unique")
    op.drop_column("users", "username")
