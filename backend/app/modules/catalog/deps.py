from typing import Annotated

from fastapi import Depends

from app.api.deps import SessionDep
from app.modules.catalog.repository import CourseRepository, SubjectRepository
from app.modules.catalog.service import CourseService, SubjectService


def get_subject_service(session: SessionDep) -> SubjectService:
    return SubjectService(session, SubjectRepository(session))


def get_course_service(session: SessionDep) -> CourseService:
    return CourseService(session, CourseRepository(session), SubjectRepository(session))


SubjectServiceDep = Annotated[SubjectService, Depends(get_subject_service)]
CourseServiceDep = Annotated[CourseService, Depends(get_course_service)]
