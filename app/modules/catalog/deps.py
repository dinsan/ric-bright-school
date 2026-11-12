from typing import Annotated

from fastapi import Depends

from app.api.deps import SessionDep
from app.modules.catalog.repository import CourseRepository, SubjectRepository
from app.modules.catalog.service import CourseService, LearningPathService, SubjectService
from app.modules.curriculum.deps import PublishingDep
from app.modules.identity.deps import AuditLogDep


def get_subject_service(session: SessionDep, audit: AuditLogDep) -> SubjectService:
    return SubjectService(session, SubjectRepository(session), audit)


def get_course_service(
    session: SessionDep, audit: AuditLogDep, publishing: PublishingDep
) -> CourseService:
    # Publishing through the catalog (status changes) goes through the curriculum module, so
    # every published course gets an immutable version and a search entry.
    return CourseService(
        session, CourseRepository(session), SubjectRepository(session), audit, publishing
    )


def get_path_service(session: SessionDep) -> LearningPathService:
    return LearningPathService(session, CourseRepository(session))


SubjectServiceDep = Annotated[SubjectService, Depends(get_subject_service)]
CourseServiceDep = Annotated[CourseService, Depends(get_course_service)]
PathServiceDep = Annotated[LearningPathService, Depends(get_path_service)]
