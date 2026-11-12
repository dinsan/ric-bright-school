from typing import Annotated

from fastapi import Depends

from app.api.deps import SessionDep
from app.modules.classes.repository import ClassRepository
from app.modules.classes.service import ClassService
from app.modules.identity.deps import AuditLogDep, AuthServiceDep, StudentAccountServiceDep


def get_class_service(
    session: SessionDep,
    students: StudentAccountServiceDep,
    auth: AuthServiceDep,
    audit: AuditLogDep,
) -> ClassService:
    return ClassService(ClassRepository(session), students, auth, audit)


ClassServiceDep = Annotated[ClassService, Depends(get_class_service)]
