from typing import Annotated

from fastapi import Depends, Request

from app.api.deps import SessionDep, SettingsDep
from app.core.storage import ObjectStorage
from app.modules.media.scanner import MediaScanner
from app.modules.media.service import MediaService


def get_storage(request: Request) -> ObjectStorage:
    storage: ObjectStorage = request.app.state.storage
    return storage


def get_scanner(request: Request) -> MediaScanner:
    scanner: MediaScanner = request.app.state.media_scanner
    return scanner


def get_media_service(
    session: SessionDep,
    settings: SettingsDep,
    storage: Annotated[ObjectStorage, Depends(get_storage)],
    scanner: Annotated[MediaScanner, Depends(get_scanner)],
) -> MediaService:
    return MediaService(session, settings, storage, scanner)


MediaServiceDep = Annotated[MediaService, Depends(get_media_service)]
