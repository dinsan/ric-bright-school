import uuid
from datetime import datetime
from typing import Any

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
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin, utcnow
from app.modules.catalog.domain import Difficulty
from app.modules.curriculum.content import empty_content


class CourseUnit(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A chapter of a course's draft. Positions are 0, 1, 2, ... within the course."""

    __tablename__ = "course_units"
    __table_args__ = (
        # Deferred: a reorder can swap positions inside one transaction.
        UniqueConstraint(
            "course_id",
            "position",
            name="uq_course_units_course_id_position",
            deferrable=True,
            initially="DEFERRED",
        ),
    )

    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str | None] = mapped_column(String(1000))
    position: Mapped[int] = mapped_column(Integer)

    lessons: Mapped[list["CourseLesson"]] = relationship(
        lazy="raise", order_by="CourseLesson.position", viewonly=True
    )


lesson_prerequisites = Table(
    "lesson_prerequisites",
    Base.metadata,
    Column("lesson_id", ForeignKey("course_lessons.id", ondelete="CASCADE"), primary_key=True),
    Column(
        "prerequisite_id", ForeignKey("course_lessons.id", ondelete="CASCADE"), primary_key=True
    ),
    CheckConstraint("lesson_id <> prerequisite_id", name="not_self"),
)

lesson_standards = Table(
    "lesson_standards",
    Base.metadata,
    Column("lesson_id", ForeignKey("course_lessons.id", ondelete="CASCADE"), primary_key=True),
    Column("standard_id", ForeignKey("standards.id", ondelete="CASCADE"), primary_key=True),
)


class CourseLesson(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A lesson of a course's draft. Its content is validated JSON (see content.py)."""

    __tablename__ = "course_lessons"
    __table_args__ = (
        UniqueConstraint(
            "unit_id",
            "position",
            name="uq_course_lessons_unit_id_position",
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint("estimated_minutes BETWEEN 1 AND 600", name="estimated_minutes_range"),
    )

    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True
    )
    unit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("course_units.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str | None] = mapped_column(String(1000))
    position: Mapped[int] = mapped_column(Integer)
    # {"schema_version": 1, "blocks": [...]}: validated on every write (content.py).
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, default=empty_content)
    estimated_minutes: Mapped[int | None] = mapped_column(SmallInteger)
    difficulty: Mapped[Difficulty | None] = mapped_column(
        Enum(
            Difficulty,
            name="difficulty",
            native_enum=False,
            length=20,
            values_callable=lambda e: [m.value for m in e],
        )
    )
    # Increases on every change: editors send the revision they started from, so two people
    # editing the same lesson cannot silently overwrite each other.
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")


class CourseVersion(UUIDPrimaryKeyMixin, Base):
    """An immutable published snapshot of a course (BE-CAT-04). A database trigger rejects
    UPDATE and DELETE. Drafts change freely; learners see versions."""

    __tablename__ = "course_versions"
    __table_args__ = (
        UniqueConstraint("course_id", "number", name="uq_course_versions_course_id_number"),
    )

    course_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("courses.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)  # 1, 2, 3, ... per course
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    change_note: Mapped[str | None] = mapped_column(String(500))
    published_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )


class SearchDocument(UUIDPrimaryKeyMixin, Base):
    """Full-text search index of *published* content (BE-CAT-07): one row per course and per
    lesson, rebuilt when a course is published, changed or unpublished."""

    __tablename__ = "search_documents"
    __table_args__ = (Index("ix_search_documents_tsv", "tsv", postgresql_using="gin"),)

    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True
    )
    lesson_id: Mapped[uuid.UUID | None] = mapped_column()  # from the snapshot, no FK
    kind: Mapped[str] = mapped_column(String(10))  # "course" | "lesson"
    config: Mapped[str] = mapped_column(String(20))  # text-search configuration
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    grade_level: Mapped[int] = mapped_column(SmallInteger)
    subject_id: Mapped[uuid.UUID] = mapped_column()
    tsv: Mapped[Any] = mapped_column(TSVECTOR)
