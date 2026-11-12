"""keep updated_at current with a database trigger

The ORM used to set ``updated_at`` only for updates made through a mapped object. Bulk
``UPDATE`` statements and raw SQL left it stale. A ``BEFORE UPDATE`` trigger now stamps it
for every update, whatever issued it.

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
