import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.modules.catalog.domain import MAX_GRADE_LEVEL, MIN_GRADE_LEVEL, CourseStatus, Difficulty


def _enum(enum_cls: type, name: str) -> Enum:
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        length=20,
        values_callable=lambda e: [m.value for m in e],
    )


class Subject(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "subjects"

    slug: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)


class Course(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "courses"
    __table_args__ = (
        CheckConstraint(
            f"grade_level BETWEEN {MIN_GRADE_LEVEL} AND {MAX_GRADE_LEVEL}",
            name="grade_level_range",
        ),
        Index("ix_courses_status_grade_level", "status", "grade_level"),
        # Trigram GIN index so the title search (`ILIKE '%term%'`) does not scan the table.
        # Needs the pg_trgm extension (created in migration 0003).
        Index(
            "ix_courses_title_trgm",
            "title",
            postgresql_using="gin",
            postgresql_ops={"title": "gin_trgm_ops"},
        ),
        # "Courses tagged X" without a join table.
        Index("ix_courses_tags", "tags", postgresql_using="gin"),
        CheckConstraint("cardinality(tags) <= 10", name="tags_limit"),
    )

    slug: Mapped[str] = mapped_column(String(120), unique=True)
    title: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text)
    grade_level: Mapped[int] = mapped_column(SmallInteger)
    status: Mapped[CourseStatus] = mapped_column(
        Enum(
            CourseStatus,
            name="course_status",
            native_enum=False,
            length=20,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        default=CourseStatus.DRAFT,
        server_default=CourseStatus.DRAFT.value,
    )
    subject_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("subjects.id", ondelete="RESTRICT"), index=True
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Who created the course. Null for platform content (e.g. the seeded catalog), which only
    # platform admins can change. Users are anonymised, never deleted, so the reference holds.
    author_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    # BCP 47 code of the course's language; decides the full-text search dictionary.
    language: Mapped[str] = mapped_column(String(16), default="en", server_default="en")
    difficulty: Mapped[Difficulty | None] = mapped_column(_enum(Difficulty, "difficulty"))
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(40)), default=list, server_default="{}")
    # Sum of the lessons' estimates, computed when a version is published.
    estimated_minutes: Mapped[int | None] = mapped_column(Integer)
    # The version learners see (curriculum module). Set on every publish.
    published_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "course_versions.id",
            use_alter=True,
            name="fk_courses_published_version_id_course_versions",
        )
    )

    # lazy="raise": async sessions cannot lazy-load, so any accidental access fails loudly.
    # Queries that need the subject load it explicitly (see CourseRepository).
    subject: Mapped[Subject] = relationship(lazy="raise")


# Courses a learner should finish first (BE-CAT-06). No cycles (checked by the service).
course_prerequisites = Table(
    "course_prerequisites",
    Base.metadata,
    Column("course_id", ForeignKey("courses.id", ondelete="CASCADE"), primary_key=True),
    Column("prerequisite_id", ForeignKey("courses.id", ondelete="CASCADE"), primary_key=True),
    CheckConstraint("course_id <> prerequisite_id", name="not_self"),
)


class LearningPath(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An ordered set of courses for a grade and subject (BE-CAT-08), curated by admins."""

    __tablename__ = "learning_paths"
    __table_args__ = (
        CheckConstraint(
            f"grade_level BETWEEN {MIN_GRADE_LEVEL} AND {MAX_GRADE_LEVEL}",
            name="grade_level_range",
        ),
        Index("ix_learning_paths_grade_level_subject_id", "grade_level", "subject_id"),
    )

    slug: Mapped[str] = mapped_column(String(120), unique=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    grade_level: Mapped[int] = mapped_column(SmallInteger)
    subject_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("subjects.id", ondelete="SET NULL")
    )
    is_published: Mapped[bool] = mapped_column(default=False, server_default="false")

    subject: Mapped[Subject | None] = relationship(lazy="raise")
    items: Mapped[list["LearningPathCourse"]] = relationship(
        lazy="raise", order_by="LearningPathCourse.position", cascade="all, delete-orphan"
    )


class LearningPathCourse(Base):
    __tablename__ = "learning_path_courses"
    __table_args__ = (
        UniqueConstraint(
            "path_id",
            "position",
            name="uq_learning_path_courses_path_id_position",
            deferrable=True,
            initially="DEFERRED",
        ),
    )

    path_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("learning_paths.id", ondelete="CASCADE"), primary_key=True
    )
    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer)

    course: Mapped[Course] = relationship(lazy="raise")
