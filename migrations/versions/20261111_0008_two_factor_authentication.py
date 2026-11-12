"""two-factor authentication (TOTP) with recovery codes

The TOTP secret is stored encrypted (it must be readable to check codes); recovery codes
are stored as SHA-256 hashes and are single-use.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-11
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIMESTAMPED_TABLES = ("user_mfa", "mfa_recovery_codes")


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "user_mfa",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("secret_encrypted", sa.Text(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_step", sa.BigInteger(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_user_mfa_user_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_user_mfa")),
    )
    op.create_table(
        "mfa_recovery_codes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_mfa_recovery_codes_user_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mfa_recovery_codes")),
        sa.UniqueConstraint("code_hash", name=op.f("uq_mfa_recovery_codes_code_hash")),
    )
    op.create_index(op.f("ix_mfa_recovery_codes_user_id"), "mfa_recovery_codes", ["user_id"])

    for table in TIMESTAMPED_TABLES:
        op.execute(
            f"CREATE TRIGGER trg_{table}_set_updated_at BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )


def downgrade() -> None:
    for table in TIMESTAMPED_TABLES:
        op.execute(f"DROP TRIGGER trg_{table}_set_updated_at ON {table}")
    op.drop_index(op.f("ix_mfa_recovery_codes_user_id"), table_name="mfa_recovery_codes")
    op.drop_table("mfa_recovery_codes")
    op.drop_table("user_mfa")
