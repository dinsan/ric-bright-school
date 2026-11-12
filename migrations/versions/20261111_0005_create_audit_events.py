"""create the append-only audit log

A trigger rejects UPDATE and DELETE on audit_events, so entries cannot be rewritten even by
application bugs or ad-hoc SQL. (Dropping the table, as the downgrade does, still works.)

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-11
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("impersonator_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("entity_type", sa.String(length=40), nullable=True),
        sa.Column("entity_id", sa.String(length=64), nullable=True),
        sa.Column("ip", sa.String(length=45), nullable=True),
        sa.Column(
            "details",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            server_default="{}",
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], name=op.f("fk_audit_events_actor_id_users")),
        sa.ForeignKeyConstraint(["impersonator_id"], ["users.id"], name=op.f("fk_audit_events_impersonator_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_events")),
    )
    op.create_index(op.f("ix_audit_events_action"), "audit_events", ["action"])
    op.create_index(op.f("ix_audit_events_actor_id"), "audit_events", ["actor_id"])
    op.create_index("ix_audit_events_entity", "audit_events", ["entity_type", "entity_id"])
    op.create_index("ix_audit_events_occurred_at", "audit_events", ["occurred_at"])

    op.execute(
        """
        CREATE FUNCTION audit_events_are_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_events is append-only: % is not allowed', TG_OP
                USING ERRCODE = 'insufficient_privilege';
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_audit_events_immutable BEFORE UPDATE OR DELETE ON audit_events "
        "FOR EACH ROW EXECUTE FUNCTION audit_events_are_immutable()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_audit_events_immutable ON audit_events")
    op.execute("DROP FUNCTION audit_events_are_immutable()")
    op.drop_index("ix_audit_events_occurred_at", table_name="audit_events")
    op.drop_index("ix_audit_events_entity", table_name="audit_events")
    op.drop_index(op.f("ix_audit_events_actor_id"), table_name="audit_events")
    op.drop_index(op.f("ix_audit_events_action"), table_name="audit_events")
    op.drop_table("audit_events")
