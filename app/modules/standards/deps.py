from typing import Annotated

from fastapi import Depends

from app.api.deps import SessionDep
from app.modules.standards.service import StandardsService


def get_standards_service(session: SessionDep) -> StandardsService:
    return StandardsService(session)


StandardsServiceDep = Annotated[StandardsService, Depends(get_standards_service)]
