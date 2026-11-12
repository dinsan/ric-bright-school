import uuid

from sqlalchemy import CheckConstraint, ForeignKey, SmallInteger, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class StandardFramework(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A set of standards, e.g. Common Core Math (US) or the National Curriculum (GB)."""

    __tablename__ = "standard_frameworks"
    __table_args__ = (CheckConstraint("country_code ~ '^[A-Z]{2}$'", name="country_code_iso"),)

    code: Mapped[str] = mapped_column(String(40), unique=True)  # e.g. "CCSS-MATH"
    name: Mapped[str] = mapped_column(String(200))
    country_code: Mapped[str] = mapped_column(String(2), index=True)  # ISO 3166-1, e.g. "US"
    subject_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("subjects.id", ondelete="SET NULL")
    )
    description: Mapped[str | None] = mapped_column(Text)


class Standard(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "standards"
    __table_args__ = (
        UniqueConstraint("framework_id", "code", name="uq_standards_framework_id_code"),
        CheckConstraint("grade_min IS NULL OR grade_min BETWEEN 1 AND 12", name="grade_min_range"),
        CheckConstraint("grade_max IS NULL OR grade_max BETWEEN 1 AND 12", name="grade_max_range"),
        CheckConstraint(
            "grade_min IS NULL OR grade_max IS NULL OR grade_min <= grade_max", name="grade_order"
        ),
    )

    framework_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("standard_frameworks.id", ondelete="CASCADE"), index=True
    )
    code: Mapped[str] = mapped_column(String(60))  # e.g. "4.NF.A.1"
    description: Mapped[str] = mapped_column(Text)
    grade_min: Mapped[int | None] = mapped_column(SmallInteger)
    grade_max: Mapped[int | None] = mapped_column(SmallInteger)

    framework: Mapped[StandardFramework] = relationship(lazy="raise")
