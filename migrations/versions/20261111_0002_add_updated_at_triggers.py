"""keep updated_at current with a database trigger

Bulk ``UPDATE`` statements and raw SQL bypass the ORM, so an ORM-side ``onupdate`` never
fires for them. A ``BEFORE UPDATE`` trigger makes the database the single source of truth.

New tables that use ``TimestampMixin`` must attach the same trigger in their own migration:

    CREATE TRIGGER trg_<table>_set_updated_at BEFORE UPDATE ON <table>
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-11
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("subjects", "courses")


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION set_updated_at() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            NEW.updated_at := now();
            RETURN NEW;
        END;
        $$
        """
    )
    for table in TABLES:
        op.execute(
            f"CREATE TRIGGER trg_{table}_set_updated_at BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"DROP TRIGGER trg_{table}_set_updated_at ON {table}")
    op.execute("DROP FUNCTION set_updated_at()")
