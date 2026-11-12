"""media assets and curriculum standards

Uploaded files (BE-MEDIA-02) and standards frameworks with their standards (BE-CAT-05).

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-11
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIMESTAMPED_TABLES = ("media_assets", "standard_frameworks", "standards")


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "media_assets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Enum("image", "video", "audio", "document", "captions", name="media_kind", native_enum=False, length=30), nullable=False),
        sa.Column("status", sa.Enum("pending_upload", "ready", "rejected", name="media_status", native_enum=False, length=30), server_default="pending_upload", nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=True),
        sa.Column("etag", sa.String(length=100), nullable=True),
        sa.Column("storage_key", sa.String(length=300), nullable=False),
        sa.Column("alt_text", sa.String(length=500), nullable=True),
        sa.Column("decorative", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.String(length=200), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name=op.f("fk_media_assets_owner_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_media_assets")),
        sa.UniqueConstraint("storage_key", name=op.f("uq_media_assets_storage_key")),
    )
    op.create_index(op.f("ix_media_assets_owner_id"), "media_assets", ["owner_id"])

    op.create_table(
        "standard_frameworks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("country_code ~ '^[A-Z]{2}$'", name=op.f("ck_standard_frameworks_country_code_iso")),
        sa.ForeignKeyConstraint(["subject_id"], ["subjects.id"], name=op.f("fk_standard_frameworks_subject_id_subjects"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_standard_frameworks")),
        sa.UniqueConstraint("code", name=op.f("uq_standard_frameworks_code")),
    )
    op.create_index(op.f("ix_standard_frameworks_country_code"), "standard_frameworks", ["country_code"])

    op.create_table(
        "standards",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("framework_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=60), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("grade_min", sa.SmallInteger(), nullable=True),
        sa.Column("grade_max", sa.SmallInteger(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("grade_max IS NULL OR grade_max BETWEEN 1 AND 12", name=op.f("ck_standards_grade_max_range")),
        sa.CheckConstraint("grade_min IS NULL OR grade_max IS NULL OR grade_min <= grade_max", name=op.f("ck_standards_grade_order")),
        sa.CheckConstraint("grade_min IS NULL OR grade_min BETWEEN 1 AND 12", name=op.f("ck_standards_grade_min_range")),
        sa.ForeignKeyConstraint(["framework_id"], ["standard_frameworks.id"], name=op.f("fk_standards_framework_id_standard_frameworks"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_standards")),
        sa.UniqueConstraint("framework_id", "code", name="uq_standards_framework_id_code"),
    )
    op.create_index(op.f("ix_standards_framework_id"), "standards", ["framework_id"])

    for table in TIMESTAMPED_TABLES:
        op.execute(
            f"CREATE TRIGGER trg_{table}_set_updated_at BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )


def downgrade() -> None:
    for table in TIMESTAMPED_TABLES:
        op.execute(f"DROP TRIGGER trg_{table}_set_updated_at ON {table}")
    op.drop_index(op.f("ix_standards_framework_id"), table_name="standards")
    op.drop_table("standards")
    op.drop_index(op.f("ix_standard_frameworks_country_code"), table_name="standard_frameworks")
    op.drop_table("standard_frameworks")
    op.drop_index(op.f("ix_media_assets_owner_id"), table_name="media_assets")
    op.drop_table("media_assets")
