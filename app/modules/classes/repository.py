import uuid
from collections.abc import Sequence

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.modules.catalog.models import Subject
from app.modules.classes.domain import EnrollmentStatus
from app.modules.classes.models import ClassEnrollment, SchoolClass
from app.modules.identity.models import User


class ClassRepository:
    """Data access only: no business rules, no commits."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, obj: object) -> None:
        self._session.add(obj)
        await self._session.flush()

    def _with_relations(self) -> Select[SchoolClass]:
        return select(SchoolClass).options(
            joinedload(SchoolClass.teacher), joinedload(SchoolClass.subject)
        )

    async def get(self, class_id: uuid.UUID) -> SchoolClass | None:
        stmt = self._with_relations().where(SchoolClass.id == class_id)
        return await self._session.scalar(stmt.execution_options(populate_existing=True))

    async def get_by_code(self, code: str) -> SchoolClass | None:
        stmt = self._with_relations().where(SchoolClass.join_code == code)
        return await self._session.scalar(stmt)

    async def subject_exists(self, subject_id: uuid.UUID) -> bool:
        return await self._session.get(Subject, subject_id) is not None

    async def code_exists(self, code: str) -> bool:
        stmt = select(select(SchoolClass.id).where(SchoolClass.join_code == code).exists())
        return bool(await self._session.scalar(stmt))

    async def list_for_teacher(self, teacher_id: uuid.UUID) -> Sequence[SchoolClass]:
        stmt = (
            self._with_relations()
            .where(SchoolClass.teacher_id == teacher_id)
            .order_by(SchoolClass.archived_at.is_not(None), SchoolClass.name)
        )
        return (await self._session.scalars(stmt)).unique().all()

    async def list_all(self) -> Sequence[SchoolClass]:
        stmt = self._with_relations().order_by(SchoolClass.name)
        return (await self._session.scalars(stmt)).unique().all()

    async def list_for_student(self, student_id: uuid.UUID) -> Sequence[SchoolClass]:
        stmt = (
            self._with_relations()
            .join(ClassEnrollment, ClassEnrollment.class_id == SchoolClass.id)
            .where(
                ClassEnrollment.student_id == student_id,
                ClassEnrollment.status == EnrollmentStatus.ACTIVE,
                SchoolClass.archived_at.is_(None),
            )
            .order_by(SchoolClass.name)
        )
        return (await self._session.scalars(stmt)).unique().all()

    async def get_enrollment(
        self, class_id: uuid.UUID, student_id: uuid.UUID
    ) -> ClassEnrollment | None:
        stmt = select(ClassEnrollment).where(
            ClassEnrollment.class_id == class_id, ClassEnrollment.student_id == student_id
        )
        return await self._session.scalar(stmt)

    async def roster(self, class_id: uuid.UUID) -> Sequence[ClassEnrollment]:
        stmt = (
            select(ClassEnrollment)
            .options(joinedload(ClassEnrollment.student).joinedload(User.student_profile))
            .join(User, User.id == ClassEnrollment.student_id)
            .where(
                ClassEnrollment.class_id == class_id,
                ClassEnrollment.status == EnrollmentStatus.ACTIVE,
                User.deleted_at.is_(None),
            )
            .order_by(User.display_name)
            .execution_options(populate_existing=True)
        )
        return (await self._session.scalars(stmt)).unique().all()

    async def student_counts(self, class_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, int]:
        if not class_ids:
            return {}
        stmt = (
            select(ClassEnrollment.class_id, func.count(ClassEnrollment.id))
            .where(
                ClassEnrollment.class_id.in_(class_ids),
                ClassEnrollment.status == EnrollmentStatus.ACTIVE,
            )
            .group_by(ClassEnrollment.class_id)
        )
        return {class_id: count for class_id, count in (await self._session.execute(stmt)).all()}
