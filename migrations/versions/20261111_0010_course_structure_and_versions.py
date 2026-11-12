"""course structure, content, versions, search, learning paths

- Units and lessons with ordered positions (BE-CAT-01) and block content (BE-CAT-02).
- Immutable published versions (BE-CAT-04): an append-only trigger, like the audit log.
- Course metadata: language, difficulty, tags, duration, prerequisites (BE-CAT-06).
- Full-text search documents with a GIN index (BE-CAT-07); learning paths (BE-CAT-08).
- Data: every course already published gets version 1 (an empty outline) and a search
  entry, so the new content endpoints work for existing courses straight away.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-11
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIMESTAMPED_TABLES = ("course_units", "course_lessons", "learning_paths")


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


def _difficulty() -> sa.Enum:
    return sa.Enum("beginner", "intermediate", "advanced", name="difficulty", native_enum=False, length=20)


def upgrade() -> None:
    # --- course metadata ----------------------------------------------------------------
    op.add_column("courses", sa.Column("language", sa.String(length=16), server_default="en", nullable=False))
    op.add_column("courses", sa.Column("difficulty", _difficulty(), nullable=True))
    op.add_column("courses", sa.Column("tags", postgresql.ARRAY(sa.String(length=40)), server_default="{}", nullable=False))
    op.add_column("courses", sa.Column("estimated_minutes", sa.Integer(), nullable=True))
    op.add_column("courses", sa.Column("published_version_id", sa.Uuid(), nullable=True))
    op.create_index("ix_courses_tags", "courses", ["tags"], postgresql_using="gin")
    op.create_check_constraint(op.f("ck_courses_tags_limit"), "courses", "cardinality(tags) <= 10")

    op.create_table(
        "course_prerequisites",
        sa.Column("course_id", sa.Uuid(), nullable=False),
        sa.Column("prerequisite_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("course_id <> prerequisite_id", name=op.f("ck_course_prerequisites_not_self")),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], name=op.f("fk_course_prerequisites_course_id_courses"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["prerequisite_id"], ["courses.id"], name=op.f("fk_course_prerequisites_prerequisite_id_courses"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("course_id", "prerequisite_id", name=op.f("pk_course_prerequisites")),
    )

    # --- structure ----------------------------------------------------------------------
    op.create_table(
        "course_units",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("course_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("summary", sa.String(length=1000), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], name=op.f("fk_course_units_course_id_courses"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_course_units")),
        sa.UniqueConstraint("course_id", "position", deferrable=True, initially="DEFERRED", name="uq_course_units_course_id_position"),
    )
    op.create_index(op.f("ix_course_units_course_id"), "course_units", ["course_id"])

    op.create_table(
        "course_lessons",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("course_id", sa.Uuid(), nullable=False),
        sa.Column("unit_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("summary", sa.String(length=1000), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("estimated_minutes", sa.SmallInteger(), nullable=True),
        sa.Column("difficulty", _difficulty(), nullable=True),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        *_timestamps(),
        sa.CheckConstraint("estimated_minutes BETWEEN 1 AND 600", name=op.f("ck_course_lessons_estimated_minutes_range")),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], name=op.f("fk_course_lessons_course_id_courses"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["unit_id"], ["course_units.id"], name=op.f("fk_course_lessons_unit_id_course_units"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_course_lessons")),
        sa.UniqueConstraint("unit_id", "position", deferrable=True, initially="DEFERRED", name="uq_course_lessons_unit_id_position"),
    )
    op.create_index(op.f("ix_course_lessons_course_id"), "course_lessons", ["course_id"])
    op.create_index(op.f("ix_course_lessons_unit_id"), "course_lessons", ["unit_id"])

    op.create_table(
        "lesson_prerequisites",
        sa.Column("lesson_id", sa.Uuid(), nullable=False),
        sa.Column("prerequisite_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("lesson_id <> prerequisite_id", name=op.f("ck_lesson_prerequisites_not_self")),
        sa.ForeignKeyConstraint(["lesson_id"], ["course_lessons.id"], name=op.f("fk_lesson_prerequisites_lesson_id_course_lessons"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["prerequisite_id"], ["course_lessons.id"], name=op.f("fk_lesson_prerequisites_prerequisite_id_course_lessons"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("lesson_id", "prerequisite_id", name=op.f("pk_lesson_prerequisites")),
    )
    op.create_table(
        "lesson_standards",
        sa.Column("lesson_id", sa.Uuid(), nullable=False),
        sa.Column("standard_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["lesson_id"], ["course_lessons.id"], name=op.f("fk_lesson_standards_lesson_id_course_lessons"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["standard_id"], ["standards.id"], name=op.f("fk_lesson_standards_standard_id_standards"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("lesson_id", "standard_id", name=op.f("pk_lesson_standards")),
    )

    # --- versions -----------------------------------------------------------------------
    op.create_table(
        "course_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("course_id", sa.Uuid(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("change_note", sa.String(length=500), nullable=True),
        sa.Column("published_by_id", sa.Uuid(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], name=op.f("fk_course_versions_course_id_courses")),
        sa.ForeignKeyConstraint(["published_by_id"], ["users.id"], name=op.f("fk_course_versions_published_by_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_course_versions")),
        sa.UniqueConstraint("course_id", "number", name="uq_course_versions_course_id_number"),
    )
    op.create_index(op.f("ix_course_versions_course_id"), "course_versions", ["course_id"])
    op.create_foreign_key(
        "fk_courses_published_version_id_course_versions", "courses", "course_versions",
        ["published_version_id"], ["id"],
    )
    op.execute(
        """
        CREATE FUNCTION course_versions_are_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'course_versions is append-only: % is not allowed', TG_OP
                USING ERRCODE = 'insufficient_privilege';
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_course_versions_immutable BEFORE UPDATE OR DELETE ON course_versions "
        "FOR EACH ROW EXECUTE FUNCTION course_versions_are_immutable()"
    )

    # --- search -------------------------------------------------------------------------
    op.create_table(
        "search_documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("course_id", sa.Uuid(), nullable=False),
        sa.Column("lesson_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("config", sa.String(length=20), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("grade_level", sa.SmallInteger(), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("tsv", postgresql.TSVECTOR(), nullable=False),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], name=op.f("fk_search_documents_course_id_courses"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_search_documents")),
    )
    op.create_index(op.f("ix_search_documents_course_id"), "search_documents", ["course_id"])
    op.create_index("ix_search_documents_tsv", "search_documents", ["tsv"], postgresql_using="gin")

    # --- learning paths -----------------------------------------------------------------
    op.create_table(
        "learning_paths",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("grade_level", sa.SmallInteger(), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=True),
        sa.Column("is_published", sa.Boolean(), server_default="false", nullable=False),
        *_timestamps(),
        sa.CheckConstraint("grade_level BETWEEN 1 AND 12", name=op.f("ck_learning_paths_grade_level_range")),
        sa.ForeignKeyConstraint(["subject_id"], ["subjects.id"], name=op.f("fk_learning_paths_subject_id_subjects"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_learning_paths")),
        sa.UniqueConstraint("slug", name=op.f("uq_learning_paths_slug")),
    )
    op.create_index("ix_learning_paths_grade_level_subject_id", "learning_paths", ["grade_level", "subject_id"])
    op.create_table(
        "learning_path_courses",
        sa.Column("path_id", sa.Uuid(), nullable=False),
        sa.Column("course_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], name=op.f("fk_learning_path_courses_course_id_courses"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["path_id"], ["learning_paths.id"], name=op.f("fk_learning_path_courses_path_id_learning_paths"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("path_id", "course_id", name=op.f("pk_learning_path_courses")),
        sa.UniqueConstraint("path_id", "position", deferrable=True, initially="DEFERRED", name="uq_learning_path_courses_path_id_position"),
    )

    for table in TIMESTAMPED_TABLES:
        op.execute(
            f"CREATE TRIGGER trg_{table}_set_updated_at BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )

    # --- data: existing published courses get version 1 and a search entry ---------------
    # The snapshot has exactly the shape the application builds (curriculum/service.py).
    op.execute(
        """
        INSERT INTO course_versions (id, course_id, number, snapshot, change_note, published_at)
        SELECT gen_random_uuid(), c.id, 1,
               jsonb_build_object(
                   'format', 'lumina-course', 'format_version', 1,
                   'course', jsonb_build_object(
                       'id', c.id::text, 'slug', c.slug, 'title', c.title, 'summary', c.summary,
                       'description', c.description, 'grade_level', c.grade_level,
                       'language', c.language, 'difficulty', c.difficulty,
                       'tags', to_jsonb(c.tags),
                       'subject', jsonb_build_object('id', s.id::text, 'slug', s.slug, 'name', s.name),
                       'estimated_minutes', 0),
                   'units', '[]'::jsonb),
               'Published before versioning existed', COALESCE(c.published_at, now())
        FROM courses c JOIN subjects s ON s.id = c.subject_id
        WHERE c.status = 'published'
        """
    )
    op.execute(
        "UPDATE courses SET published_version_id = v.id FROM course_versions v "
        "WHERE v.course_id = courses.id"
    )
    op.execute(
        """
        INSERT INTO search_documents
            (id, course_id, lesson_id, kind, config, title, body, grade_level, subject_id, tsv)
        SELECT gen_random_uuid(), c.id, NULL, 'course', 'english', c.title,
               COALESCE(c.description, ''), c.grade_level, c.subject_id,
               setweight(to_tsvector('english', c.title), 'A')
               || setweight(to_tsvector('english', c.summary), 'B')
               || setweight(to_tsvector('english', COALESCE(c.description, '')), 'C')
        FROM courses c
        WHERE c.status = 'published'
        """
    )


def downgrade() -> None:
    for table in TIMESTAMPED_TABLES:
        op.execute(f"DROP TRIGGER trg_{table}_set_updated_at ON {table}")
    op.drop_table("learning_path_courses")
    op.drop_index("ix_learning_paths_grade_level_subject_id", table_name="learning_paths")
    op.drop_table("learning_paths")
    op.drop_index("ix_search_documents_tsv", table_name="search_documents")
    op.drop_index(op.f("ix_search_documents_course_id"), table_name="search_documents")
    op.drop_table("search_documents")
    op.drop_constraint("fk_courses_published_version_id_course_versions", "courses", type_="foreignkey")
    op.execute("DROP TRIGGER trg_course_versions_immutable ON course_versions")
    op.execute("DROP FUNCTION course_versions_are_immutable()")
    op.drop_index(op.f("ix_course_versions_course_id"), table_name="course_versions")
    op.drop_table("course_versions")
    op.drop_table("lesson_standards")
    op.drop_table("lesson_prerequisites")
    op.drop_index(op.f("ix_course_lessons_unit_id"), table_name="course_lessons")
    op.drop_index(op.f("ix_course_lessons_course_id"), table_name="course_lessons")
    op.drop_table("course_lessons")
    op.drop_index(op.f("ix_course_units_course_id"), table_name="course_units")
    op.drop_table("course_units")
    op.drop_table("course_prerequisites")
    op.drop_constraint(op.f("ck_courses_tags_limit"), "courses", type_="check")
    op.drop_index("ix_courses_tags", table_name="courses")
    for column in ("published_version_id", "estimated_minutes", "tags", "difficulty", "language"):
        op.drop_column("courses", column)
