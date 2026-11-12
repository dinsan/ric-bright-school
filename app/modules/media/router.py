import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.core.pagination import Page
from app.modules.identity.deps import CurrentActor
from app.modules.media.deps import MediaServiceDep
from app.modules.media.models import MediaAsset
from app.modules.media.schemas import (
    MediaCreate,
    MediaListQuery,
    MediaRead,
    MediaUpdate,
    MediaUploadRead,
    UploadForm,
)
from app.modules.media.service import MediaService

router = APIRouter(prefix="/media", tags=["media"])


def _read(asset: MediaAsset, service: MediaService) -> MediaRead:
    return MediaRead(
        id=asset.id,
        kind=asset.kind,
        status=asset.status,
        filename=asset.filename,
        content_type=asset.content_type,
        size_bytes=asset.size_bytes,
        alt_text=asset.alt_text,
        decorative=asset.decorative,
        url=service.url_for(asset),
        rejection_reason=asset.rejection_reason,
        created_at=asset.created_at,
    )


@router.post(
    "",
    response_model=MediaUploadRead,
    status_code=status.HTTP_201_CREATED,
    summary="Start an upload (teachers, admins)",
    description="Returns a signed form: POST the file to `upload.url` as multipart/form-data "
    "with every `upload.fields` entry, then the `file`. Then call `POST /media/{id}/complete`. "
    "Images need `alt_text` unless `decorative`.",
)
async def create_media(
    payload: MediaCreate, actor: CurrentActor, service: MediaServiceDep
) -> MediaUploadRead:
    asset, upload = await service.create(actor, payload)
    return MediaUploadRead(
        media=_read(asset, service),
        upload=UploadForm(url=upload.url, fields=upload.fields, expires_in=upload.expires_in),
    )


@router.post(
    "/{media_id}/complete",
    response_model=MediaRead,
    summary="Confirm the upload",
    description="Checks the stored file (size, type) and scans it. `status` becomes `ready`, "
    "or `rejected` with a `rejection_reason`. 409 if the file has not arrived yet.",
)
async def complete_media(
    media_id: uuid.UUID, actor: CurrentActor, service: MediaServiceDep
) -> MediaRead:
    return _read(await service.complete(actor, media_id), service)


@router.get("", response_model=Page[MediaRead], summary="My uploads")
async def list_media(
    query: Annotated[MediaListQuery, Query()], actor: CurrentActor, service: MediaServiceDep
) -> Page[MediaRead]:
    items, total = await service.list_mine(
        actor, kind=query.kind, status=query.status, limit=query.limit, offset=query.offset
    )
    return Page[MediaRead](
        items=[_read(a, service) for a in items],
        total=total,
        limit=query.limit,
        offset=query.offset,
    )


@router.get("/{media_id}", response_model=MediaRead, summary="One of my uploads")
async def get_media(
    media_id: uuid.UUID, actor: CurrentActor, service: MediaServiceDep
) -> MediaRead:
    return _read(await service.get(actor, media_id), service)


@router.patch("/{media_id}", response_model=MediaRead, summary="Change alt text")
async def update_media(
    media_id: uuid.UUID, payload: MediaUpdate, actor: CurrentActor, service: MediaServiceDep
) -> MediaRead:
    return _read(await service.update(actor, media_id, payload), service)
