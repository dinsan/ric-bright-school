"""social sign-in (Google, Microsoft, Apple) and pending sign-ins

Linked provider accounts, server-side state for each provider sign-in attempt (CSRF state,
PKCE verifier, nonce), and login challenges: a sign-in waiting for a two-factor code, or
for the web app to collect its tokens after a provider redirect.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-11
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIMESTAMPED_TABLES = ("oauth_identities", "oauth_states", "login_challenges")


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "oauth_identities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_oauth_identities_user_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_oauth_identities")),
        sa.UniqueConstraint("provider", "subject", name="uq_oauth_identities_provider_subject"),
        sa.UniqueConstraint("user_id", "provider", name="uq_oauth_identities_user_id_provider"),
    )
    op.create_table(
        "oauth_states",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("intent", sa.String(length=10), nullable=False),
        sa.Column("signup_role", sa.String(length=20), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("code_verifier", sa.String(length=128), nullable=False),
        sa.Column("nonce", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_oauth_states_user_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_oauth_states")),
        sa.UniqueConstraint("state_hash", name=op.f("uq_oauth_states_state_hash")),
    )
    op.create_table(
        "login_challenges",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("mfa", "oauth_handoff", name="challenge_kind", native_enum=False, length=30),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("sign_in_method", sa.String(length=20), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_login_challenges_user_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_login_challenges")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_login_challenges_token_hash")),
    )
    op.create_index(op.f("ix_login_challenges_user_id"), "login_challenges", ["user_id"])

    for table in TIMESTAMPED_TABLES:
        op.execute(
            f"CREATE TRIGGER trg_{table}_set_updated_at BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )


def downgrade() -> None:
    for table in TIMESTAMPED_TABLES:
        op.execute(f"DROP TRIGGER trg_{table}_set_updated_at ON {table}")
    op.drop_index(op.f("ix_login_challenges_user_id"), table_name="login_challenges")
    op.drop_table("login_challenges")
    op.drop_table("oauth_states")
    op.drop_table("oauth_identities")
