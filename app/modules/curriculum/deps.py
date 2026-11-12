from typing import Annotated

from fastapi import Depends

from app.api.deps import SessionDep, SettingsDep
from app.modules.curriculum.service import (
    AuthoringService,
    PublishingService,
    SearchService,
    TransferService,
)
from app.modules.identity.deps import AuditLogDep
from app.modules.media.deps import MediaServiceDep
from app.modules.standards.deps import StandardsServiceDep


def get_authoring(
    session: SessionDep,
    settings: SettingsDep,
    audit: AuditLogDep,
    media: MediaServiceDep,
    standards: StandardsServiceDep,
) -> AuthoringService:
    return AuthoringService(session, settings, audit, media, standards)


def get_publishing(
    session: SessionDep,
    settings: SettingsDep,
    audit: AuditLogDep,
    media: MediaServiceDep,
    standards: StandardsServiceDep,
) -> PublishingService:
    return PublishingService(session, settings, audit, media, standards)


def get_transfer(
    session: SessionDep,
    settings: SettingsDep,
    audit: AuditLogDep,
    media: MediaServiceDep,
    standards: StandardsServiceDep,
) -> TransferService:
    return TransferService(session, settings, audit, media, standards)


def get_search(
    session: SessionDep,
    settings: SettingsDep,
    audit: AuditLogDep,
    media: MediaServiceDep,
    standards: StandardsServiceDep,
) -> SearchService:
    return SearchService(session, settings, audit, media, standards)


AuthoringDep = Annotated[AuthoringService, Depends(get_authoring)]
PublishingDep = Annotated[PublishingService, Depends(get_publishing)]
TransferDep = Annotated[TransferService, Depends(get_transfer)]
SearchDep = Annotated[SearchService, Depends(get_search)]
