"""Classes: create, list, roster, join codes, and class-code picture sign-in.

Every method checks ownership through classes/policies.py before touching data. Student
accounts are created and set up through identity's StudentAccountService, which shares this
request's database session (so one commit covers both).
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from app.core.exceptions import ConflictError, NotFoundError
from app.db.mixins import utcnow
from app.modules.audit.domain import AuditAction
from app.modules.audit.service import AuditLog
from app.modules.classes import policies
from app.modules.classes.domain import (
    CLASS_CODE_IP_THRESHOLD,
    PICTURE_SIGN_IN_MAX_GRADE,
    EnrollmentStatus,
)
from app.modules.classes.models import ClassEnrollment, SchoolClass
from app.modules.classes.repository import ClassRepository
from app.modules.classes.schemas import ClassCreate, ClassStudentCreate, ClassUpdate
from app.modules.identity.domain import Actor, AgeBand, RequestInfo
from app.modules.identity.models import User
from app.modules.identity.policies import band_of
from app.modules.identity.schemas import StudentSignInUpdate
from app.modules.identity.security import new_link_code, normalize_link_code
from app.modules.identity.service import AuthService, LoginResult, StudentAccountService


@dataclass(frozen=True)
class ClassView:
    school_class: SchoolClass
    student_count: int
    show_join_code: bool


class ClassService:
    def __init__(
        self,
        classes: ClassRepository,
        students: StudentAccountService,
        auth: AuthService,
        audit: AuditLog,
    ) -> None:
        self._classes = classes
        self._students = students
        self._auth = auth
        self._audit = audit

    # --- classes --------------------------------------------------------------------------

    async def _new_join_code(self) -> str:
        for _ in range(10):
            code = new_link_code()
            if not await self._classes.code_exists(code):
                return code
        raise ConflictError("Could not create a unique class code; try again.")

    async def _view(self, actor: Actor, school_class: SchoolClass) -> ClassView:
        counts = await self._classes.student_counts([school_class.id])
        return ClassView(
            school_class,
            counts.get(school_class.id, 0),
            show_join_code=policies.is_class_manager(actor)
            or policies.is_teacher_of(actor, school_class),
        )

    async def _check_subject(self, subject_id: uuid.UUID | None) -> None:
        if subject_id is not None and not await self._classes.subject_exists(subject_id):
            raise NotFoundError("Subject not found.")

    async def create_class(
        self, actor: Actor, data: ClassCreate, request: RequestInfo
    ) -> ClassView:
        policies.ensure_can_create_class(actor)
        await self._check_subject(data.subject_id)
        picture = data.picture_sign_in
        if picture is None:
            picture = data.grade_level is not None and data.grade_level <= PICTURE_SIGN_IN_MAX_GRADE
        school_class = SchoolClass(
            teacher_id=actor.user_id,
            name=data.name.strip(),
            grade_level=data.grade_level,
            subject_id=data.subject_id,
            school_year=data.school_year,
            join_code=await self._new_join_code(),
            picture_sign_in=picture,
        )
        await self._classes.add(school_class)
        await self._audit.record(
            AuditAction.CLASS_CREATED,
            actor=actor,
            entity_type="class",
            entity_id=school_class.id,
            request=request,
        )
        await self._students.commit()
        return await self.get_class(actor, school_class.id)

    async def list_classes(self, actor: Actor) -> list[ClassView]:
        policies.ensure_can_list_classes(actor)
        if policies.is_class_manager(actor):
            found = await self._classes.list_all()
        elif policies.is_teacher_role(actor):
            found = await self._classes.list_for_teacher(actor.user_id)
        else:
            found = await self._classes.list_for_student(actor.user_id)
        counts = await self._classes.student_counts([c.id for c in found])
        return [
            ClassView(
                c,
                counts.get(c.id, 0),
                show_join_code=policies.is_class_manager(actor) or policies.is_teacher_of(actor, c),
            )
            for c in found
        ]

    async def get_class(self, actor: Actor, class_id: uuid.UUID) -> ClassView:
        school_class = await self._classes.get(class_id)
        enrollment = await self._classes.get_enrollment(class_id, actor.user_id)
        return await self._view(actor, policies.ensure_can_view(actor, school_class, enrollment))

    async def update_class(self, actor: Actor, class_id: uuid.UUID, data: ClassUpdate) -> ClassView:
        school_class = policies.ensure_can_manage(actor, await self._classes.get(class_id))
        changes = data.model_dump(exclude_unset=True)
        if "subject_id" in changes:
            await self._check_subject(changes["subject_id"])
        for field, value in changes.items():
            setattr(school_class, field, value.strip() if field == "name" else value)
        await self._students.commit()
        return await self.get_class(actor, class_id)

    async def archive_class(self, actor: Actor, class_id: uuid.UUID, request: RequestInfo) -> None:
        """The class leaves the lists; its code stops working (joining and picture sign-in)."""
        school_class = await self._classes.get(class_id)
        if school_class is not None and school_class.is_archived:
            policies.ensure_can_view(actor, school_class, None)
            return  # idempotent
        school_class = policies.ensure_can_manage(actor, school_class)
        school_class.archived_at = utcnow()
        await self._audit.record(
            AuditAction.CLASS_ARCHIVED,
            actor=actor,
            entity_type="class",
            entity_id=school_class.id,
            request=request,
        )
        await self._students.commit()

    async def rotate_join_code(
        self, actor: Actor, class_id: uuid.UUID, request: RequestInfo
    ) -> ClassView:
        """If the code leaked: the old one stops working immediately."""
        school_class = policies.ensure_can_manage(actor, await self._classes.get(class_id))
        school_class.join_code = await self._new_join_code()
        await self._audit.record(
            AuditAction.CLASS_JOIN_CODE_ROTATED,
            actor=actor,
            entity_type="class",
            entity_id=school_class.id,
            request=request,
        )
        await self._students.commit()
        return await self.get_class(actor, class_id)

    # --- roster ---------------------------------------------------------------------------

    async def roster(self, actor: Actor, class_id: uuid.UUID) -> Sequence[ClassEnrollment]:
        school_class = await self._classes.get(class_id)
        if school_class is None or not (
            policies.is_class_manager(actor) or policies.is_teacher_of(actor, school_class)
        ):
            # Students of the class do not see the roster either.
            raise NotFoundError("Class not found.")
        return await self._classes.roster(class_id)

    async def add_new_student(
        self,
        actor: Actor,
        class_id: uuid.UUID,
        data: ClassStudentCreate,
        request: RequestInfo,
    ) -> ClassEnrollment:
        """A teacher creates a school-managed student account, already in the class."""
        school_class = policies.ensure_can_manage(actor, await self._classes.get(class_id))
        sign_in_fields = (data.username, data.password, data.picture_password)
        sign_in = (
            StudentSignInUpdate.model_construct(
                username=data.username,
                password=data.password,
                picture_password=data.picture_password,
            )
            if any(v is not None for v in sign_in_fields)
            else None
        )
        student, _ = await self._students.create_student(
            actor,
            display_name=data.display_name,
            grade_level=data.grade_level or school_class.grade_level,
            email=data.email,
            sign_in=sign_in,
            request=request,
        )
        enrollment = ClassEnrollment(class_id=class_id, student_id=student.id)
        await self._classes.add(enrollment)
        await self._audit.record(
            AuditAction.STUDENT_ENROLLED,
            actor=actor,
            entity_type="class",
            entity_id=class_id,
            request=request,
            details={"student_id": str(student.id), "new_account": True},
        )
        await self._students.commit()
        return await self._enrollment_with_student(class_id, student.id)

    async def join_with_code(self, actor: Actor, code: str, request: RequestInfo) -> ClassView:
        """A student joins a class with the code the teacher shared."""
        policies.ensure_student_can_join(actor)
        school_class = await self._classes.get_by_code(normalize_link_code(code))
        if school_class is None or school_class.is_archived:
            raise NotFoundError("No class has this code. Check it with your teacher.")
        enrollment = await self._classes.get_enrollment(school_class.id, actor.user_id)
        if enrollment is None:
            await self._classes.add(
                ClassEnrollment(class_id=school_class.id, student_id=actor.user_id)
            )
        elif enrollment.status != EnrollmentStatus.ACTIVE:
            enrollment.status = EnrollmentStatus.ACTIVE
        await self._audit.record(
            AuditAction.STUDENT_ENROLLED,
            actor=actor,
            entity_type="class",
            entity_id=school_class.id,
            request=request,
            details={"student_id": str(actor.user_id), "with_code": True},
        )
        await self._students.commit()
        return await self.get_class(actor, school_class.id)

    async def remove_student(
        self, actor: Actor, class_id: uuid.UUID, student_id: uuid.UUID, request: RequestInfo
    ) -> None:
        policies.ensure_can_manage(actor, await self._classes.get(class_id))
        enrollment = policies.ensure_enrolled(
            await self._classes.get_enrollment(class_id, student_id)
        )
        enrollment.status = EnrollmentStatus.REMOVED
        await self._audit.record(
            AuditAction.STUDENT_REMOVED,
            actor=actor,
            entity_type="class",
            entity_id=class_id,
            request=request,
            details={"student_id": str(student_id)},
        )
        await self._students.commit()

    async def update_student_sign_in(
        self,
        actor: Actor,
        class_id: uuid.UUID,
        student_id: uuid.UUID,
        data: StudentSignInUpdate,
        request: RequestInfo,
    ) -> ClassEnrollment:
        """The teacher sets up sign-in for a child in their class (e.g. picture passwords for
        the whole Grade 1 class)."""
        policies.ensure_can_manage(actor, await self._classes.get(class_id))
        policies.ensure_enrolled(await self._classes.get_enrollment(class_id, student_id))
        student = await self._students.get_student(student_id)
        assert student is not None
        await self._students.update_sign_in(actor, student, data, request)
        return await self._enrollment_with_student(class_id, student_id)

    async def _enrollment_with_student(
        self, class_id: uuid.UUID, student_id: uuid.UUID
    ) -> ClassEnrollment:
        for enrollment in await self._classes.roster(class_id):
            if enrollment.student_id == student_id:
                return enrollment
        raise NotFoundError("Student not found in this class.")

    # --- picture sign-in (Grades 1-3) -----------------------------------------------------

    async def _picture_class(self, code: str, request: RequestInfo) -> SchoolClass:
        await self._auth.ensure_code_lookup_allowed(request)
        school_class = await self._classes.get_by_code(normalize_link_code(code))
        if school_class is None or school_class.is_archived or not school_class.picture_sign_in:
            await self._auth.record_code_lookup_failure(request, CLASS_CODE_IP_THRESHOLD)
            raise NotFoundError("No class has this code. Check it with your teacher.")
        return school_class

    @staticmethod
    def _can_use_pictures(student: User) -> bool:
        profile = student.student_profile
        return (
            student.is_active
            and student.deleted_at is None
            and band_of(student) == AgeBand.EARLY
            and profile is not None
            and profile.picture_password_hash is not None
        )

    async def picture_sign_in_page(
        self, code: str, request: RequestInfo
    ) -> tuple[SchoolClass, list[User]]:
        """What the classroom device shows: the children who can sign in with pictures."""
        school_class = await self._picture_class(code, request)
        students = [e.student for e in await self._classes.roster(school_class.id)]
        return school_class, [s for s in students if self._can_use_pictures(s)]

    async def picture_login(
        self,
        code: str,
        student_id: uuid.UUID,
        pictures: Sequence[str],
        request: RequestInfo,
    ) -> LoginResult:
        school_class = await self._picture_class(code, request)
        enrollment = await self._classes.get_enrollment(school_class.id, student_id)
        if enrollment is None or enrollment.status != EnrollmentStatus.ACTIVE:
            await self._auth.record_code_lookup_failure(request, CLASS_CODE_IP_THRESHOLD)
            raise NotFoundError("This child is not in this class.")
        student = await self._students.get_student(student_id)
        if student is None or not self._can_use_pictures(student):
            raise NotFoundError("This child is not in this class.")
        return await self._auth.login_with_picture(student, pictures, request)
