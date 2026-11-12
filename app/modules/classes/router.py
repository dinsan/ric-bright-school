import uuid

from fastapi import APIRouter, Response, status

from app.modules.catalog.schemas import SubjectRead
from app.modules.classes.deps import ClassServiceDep
from app.modules.classes.models import ClassEnrollment
from app.modules.classes.schemas import (
    ClassCreate,
    ClassRead,
    ClassStudentCreate,
    ClassUpdate,
    JoinClassRequest,
    RosterStudentRead,
)
from app.modules.classes.service import ClassView
from app.modules.identity.deps import CurrentActor, RequestInfoDep
from app.modules.identity.domain import PICTURES
from app.modules.identity.policies import band_of
from app.modules.identity.schemas import (
    ClassSignInRead,
    ClassSignInStudent,
    LoginResponse,
    PictureLoginRequest,
    StudentSignInUpdate,
)
from app.modules.identity.service import sign_in_methods

classes_router = APIRouter(prefix="/classes", tags=["classes"])
class_sign_in_router = APIRouter(prefix="/auth", tags=["auth"])

NO_CONTENT = status.HTTP_204_NO_CONTENT


def _class(view: ClassView) -> ClassRead:
    c = view.school_class
    return ClassRead(
        id=c.id,
        name=c.name,
        grade_level=c.grade_level,
        subject=SubjectRead.model_validate(c.subject) if c.subject else None,
        school_year=c.school_year,
        teacher_id=c.teacher_id,
        teacher_name=c.teacher.display_name,
        picture_sign_in=c.picture_sign_in,
        archived=c.is_archived,
        student_count=view.student_count,
        join_code=c.join_code if view.show_join_code else None,
        created_at=c.created_at,
    )


def _student(enrollment: ClassEnrollment) -> RosterStudentRead:
    student = enrollment.student
    profile = student.student_profile
    return RosterStudentRead(
        id=student.id,
        display_name=student.display_name,
        username=student.username,
        email=student.email,
        grade_level=profile.grade_level if profile else None,
        age_band=band_of(student),
        sign_in_methods=sign_in_methods(student),
        managed_by_guardians=student.email is None,
        enrolled_at=enrollment.created_at,
    )


# --- classes ------------------------------------------------------------------------------


@classes_router.post(
    "",
    response_model=ClassRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a class (teachers)",
    description="Gets a join code, shared with the students. Picture sign-in is on by "
    "default for Grades 1 to 3.",
)
async def create_class(
    payload: ClassCreate, actor: CurrentActor, service: ClassServiceDep, request: RequestInfoDep
) -> ClassRead:
    return _class(await service.create_class(actor, payload, request))


@classes_router.get(
    "",
    response_model=list[ClassRead],
    summary="My classes",
    description="Teachers: the classes they teach. Students: the classes they are in. "
    "Platform admins: every class.",
)
async def list_classes(actor: CurrentActor, service: ClassServiceDep) -> list[ClassRead]:
    return [_class(v) for v in await service.list_classes(actor)]


@classes_router.post(
    "/join",
    response_model=ClassRead,
    summary="Join a class with its code (students)",
)
async def join_class(
    payload: JoinClassRequest,
    actor: CurrentActor,
    service: ClassServiceDep,
    request: RequestInfoDep,
) -> ClassRead:
    return _class(await service.join_with_code(actor, payload.code, request))


@classes_router.get("/{class_id}", response_model=ClassRead, summary="One class")
async def get_class(
    class_id: uuid.UUID, actor: CurrentActor, service: ClassServiceDep
) -> ClassRead:
    return _class(await service.get_class(actor, class_id))


@classes_router.patch("/{class_id}", response_model=ClassRead, summary="Change a class")
async def update_class(
    class_id: uuid.UUID, payload: ClassUpdate, actor: CurrentActor, service: ClassServiceDep
) -> ClassRead:
    return _class(await service.update_class(actor, class_id, payload))


@classes_router.delete(
    "/{class_id}",
    status_code=NO_CONTENT,
    summary="Archive a class",
    description="Its code stops working for joining and for picture sign-in.",
)
async def archive_class(
    class_id: uuid.UUID, actor: CurrentActor, service: ClassServiceDep, request: RequestInfoDep
) -> Response:
    await service.archive_class(actor, class_id, request)
    return Response(status_code=NO_CONTENT)


@classes_router.post(
    "/{class_id}/join-code",
    response_model=ClassRead,
    summary="Replace the class code",
    description="The old code stops working immediately.",
)
async def rotate_join_code(
    class_id: uuid.UUID, actor: CurrentActor, service: ClassServiceDep, request: RequestInfoDep
) -> ClassRead:
    return _class(await service.rotate_join_code(actor, class_id, request))


# --- roster -------------------------------------------------------------------------------


@classes_router.get(
    "/{class_id}/students", response_model=list[RosterStudentRead], summary="The class roster"
)
async def roster(
    class_id: uuid.UUID, actor: CurrentActor, service: ClassServiceDep
) -> list[RosterStudentRead]:
    return [_student(e) for e in await service.roster(actor, class_id)]


@classes_router.post(
    "/{class_id}/students",
    response_model=RosterStudentRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a student account in this class",
    description="A school-managed account: a username and password, or (Grades 1-3) a "
    "picture password for class-code sign-in. Grade 4 and up may get an email instead.",
)
async def add_student(
    class_id: uuid.UUID,
    payload: ClassStudentCreate,
    actor: CurrentActor,
    service: ClassServiceDep,
    request: RequestInfoDep,
) -> RosterStudentRead:
    return _student(await service.add_new_student(actor, class_id, payload, request))


@classes_router.delete(
    "/{class_id}/students/{student_id}",
    status_code=NO_CONTENT,
    summary="Remove a student from the class",
)
async def remove_student(
    class_id: uuid.UUID,
    student_id: uuid.UUID,
    actor: CurrentActor,
    service: ClassServiceDep,
    request: RequestInfoDep,
) -> Response:
    await service.remove_student(actor, class_id, student_id, request)
    return Response(status_code=NO_CONTENT)


@classes_router.put(
    "/{class_id}/students/{student_id}/sign-in",
    response_model=RosterStudentRead,
    summary="Set up how a student in this class signs in",
    description="Username and password, and/or a picture password (Grades 1-3). Only for "
    "students without their own email address. A new password or picture password also "
    "unlocks a locked-out child.",
)
async def update_student_sign_in(
    class_id: uuid.UUID,
    student_id: uuid.UUID,
    payload: StudentSignInUpdate,
    actor: CurrentActor,
    service: ClassServiceDep,
    request: RequestInfoDep,
) -> RosterStudentRead:
    return _student(
        await service.update_student_sign_in(actor, class_id, student_id, payload, request)
    )


# --- class-code picture sign-in (public) --------------------------------------------------


@class_sign_in_router.get(
    "/class-sign-in/{code}",
    response_model=ClassSignInRead,
    summary="The picture sign-in page of a class (Grades 1-3)",
    description="For the classroom device: the children who can sign in with pictures, "
    "and the pictures to show. Repeated wrong codes from one address are blocked (429).",
)
async def class_sign_in_page(
    code: str, service: ClassServiceDep, request: RequestInfoDep
) -> ClassSignInRead:
    school_class, students = await service.picture_sign_in_page(code, request)
    return ClassSignInRead(
        class_name=school_class.name,
        students=[ClassSignInStudent(id=s.id, display_name=s.display_name) for s in students],
        pictures=list(PICTURES),
    )


@class_sign_in_router.post(
    "/login/picture",
    response_model=LoginResponse,
    summary="Sign in with the class code and a picture password (Grades 1-3)",
    description="The session lasts one school day and is never extended. After 3 wrong "
    "tries the child waits, after 10 a teacher or parent must set new pictures.",
)
async def picture_login(
    payload: PictureLoginRequest, service: ClassServiceDep, request: RequestInfoDep
) -> LoginResponse:
    return await service.picture_login(
        payload.class_code, payload.student_id, payload.pictures, request
    )
