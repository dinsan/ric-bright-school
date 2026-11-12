import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    SmallInteger,
    String,
    UniqueConstraint,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.modules.catalog.domain import MAX_GRADE_LEVEL, MIN_GRADE_LEVEL
from app.modules.catalog.models import Subject
from app.modules.classes.domain import EnrollmentStatus
from app.modules.identity.models import User


class SchoolClass(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "classes"
    __table_args__ = (
        CheckConstraint(
            f"grade_level BETWEEN {MIN_GRADE_LEVEL} AND {MAX_GRADE_LEVEL}",
            name="grade_level_range",
        ),
    )

    teacher_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    grade_level: Mapped[int | None] = mapped_column(SmallInteger)
    subject_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("subjects.id", ondelete="SET NULL")
    )
    school_year: Mapped[str | None] = mapped_column(String(9))
    # Shared in the classroom: students join with it, and young children sign in with it.
    join_code: Mapped[str] = mapped_column(String(9), unique=True)
    picture_sign_in: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    teacher: Mapped[User] = relationship(lazy="raise")
    subject: Mapped[Subject | None] = relationship(lazy="raise")

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None


class ClassEnrollment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "class_enrollments"
    __table_args__ = (
        UniqueConstraint("class_id", "student_id", name="uq_class_enrollments_class_id_student_id"),
    )

    class_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("classes.id", ondelete="CASCADE"))
    student_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[EnrollmentStatus] = mapped_column(
        Enum(
            EnrollmentStatus,
            name="enrollment_status",
            native_enum=False,
            length=30,
            values_callable=lambda e: [m.value for m in e],
        ),
        default=EnrollmentStatus.ACTIVE,
        server_default=EnrollmentStatus.ACTIVE.value,
    )

    student: Mapped[User] = relationship(lazy="raise")
    school_class: Mapped[SchoolClass] = relationship(lazy="raise")
