"""add trigram index for course title search

The catalog search filters with ``title ILIKE '%term%'``. A leading wildcard cannot use a
B-tree index, so without this every search scans the whole table. A GIN index with
``gin_trgm_ops`` supports ILIKE with wildcards on both sides.

``pg_trgm`` is a trusted extension (PostgreSQL 13+), so the database owner can create it
without superuser rights.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-11
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_index(
        "ix_courses_title_trgm",
        "courses",
        ["title"],
        postgresql_using="gin",
        postgresql_ops={"title": "gin_trgm_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_courses_title_trgm", table_name="courses")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
