"""Uploading media (BE-MEDIA-01/02).

1. ``create``: check kind, type, size and alt text; return a signed upload form.
2. The browser uploads the file straight to the storage.
3. ``complete``: the API checks what actually arrived (it never trusts the client or the
   storage's policy enforcement alone): size within the limit and equal to the declared
   size, exact content type. Then the virus-scan hook runs. Only then is it ``ready``.
"""

import re
import uuid
from collections.abc import Iterable, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import ConflictError, DomainValidationError
from app.core.storage import ObjectStorage, PresignedUpload
from app.db.mixins import utcnow
from app.modules.identity.domain import Actor
from app.modules.media import policies
from app.modules.media.domain import ALLOWED_TYPES, MAX_BYTES, MB, MediaKind, MediaStatus
from app.modules.media.models import MediaAsset
from app.modules.media.repository import MediaRepository
from app.modules.media.scanner import MediaScanner
from app.modules.media.schemas import MediaCreate, MediaUpdate

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_filename(name: str) -> str:
    """Keep a readable, harmless file name for the storage key."""
    base = _UNSAFE.sub("-", name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]).strip(".-")
    return (base or "file")[:100]


class MediaService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        storage: ObjectStorage,
        scanner: MediaScanner,
    ) -> None:
        self._session = session
        self._settings = settings
        self._storage = storage
        self._scanner = scanner
        self._media = MediaRepository(session)

    def url_for(self, asset: MediaAsset) -> str | None:
        if asset.status != MediaStatus.READY:
            return None
        return self._storage.download_url(
            asset.storage_key,
            expires_in=self._settings.media_download_url_ttl_seconds,
            filename=asset.filename,
        )

    async def create(self, actor: Actor, data: MediaCreate) -> tuple[MediaAsset, PresignedUpload]:
        policies.ensure_can_upload(actor)
        content_type = data.content_type.lower().split(";")[0].strip()
        if content_type not in ALLOWED_TYPES[data.kind]:
            allowed = ", ".join(sorted(ALLOWED_TYPES[data.kind]))
            raise DomainValidationError(
                f"{content_type} is not accepted for {data.kind.value}. Accepted: {allowed}."
            )
        limit = MAX_BYTES[data.kind]
        if data.size_bytes > limit:
            raise DomainValidationError(
                f"The file is too large: {data.kind.value} files can be up to {limit // MB} MB."
            )
        alt_text = data.alt_text.strip() if data.alt_text else None
        if data.kind == MediaKind.IMAGE and not alt_text and not data.decorative:
            raise DomainValidationError(
                "Describe the image in `alt_text` (read aloud to blind students), or mark it "
                "`decorative` if it adds no information."
            )

        asset_id = uuid.uuid4()
        asset = MediaAsset(
            id=asset_id,
            owner_id=actor.user_id,
            kind=data.kind,
            filename=data.filename.strip(),
            content_type=content_type,
            size_bytes=data.size_bytes,
            checksum_sha256=data.checksum_sha256,
            storage_key=f"media/{actor.user_id}/{asset_id}/{safe_filename(data.filename)}",
            alt_text=alt_text,
            decorative=data.decorative,
        )
        await self._media.add(asset)
        upload = self._storage.presign_upload(
            asset.storage_key,
            content_type=content_type,
            max_bytes=limit,
            expires_in=self._settings.media_upload_url_ttl_seconds,
        )
        await self._session.commit()
        return asset, upload

    async def complete(self, actor: Actor, asset_id: uuid.UUID) -> MediaAsset:
        asset = policies.ensure_owner(actor, await self._media.get_for_update(asset_id))
        if asset.status != MediaStatus.PENDING_UPLOAD:
            return asset  # idempotent
        stored = await self._storage.head(asset.storage_key)
        if stored is None:
            raise ConflictError("The file has not been uploaded yet.")

        problem = None
        if stored.size > MAX_BYTES[asset.kind]:
            problem = "The file is larger than allowed."
        elif stored.size != asset.size_bytes:
            problem = "The uploaded file does not have the declared size."
        elif (stored.content_type or "").lower().split(";")[0].strip() != asset.content_type:
            problem = "The uploaded file does not have the declared type."
        elif not await self._scanner.is_clean(asset, self._storage):
            problem = "The file did not pass the virus scan."

        asset.etag = stored.etag
        asset.uploaded_at = utcnow()
        if problem:
            asset.status = MediaStatus.REJECTED
            asset.rejection_reason = problem
        else:
            asset.status = MediaStatus.READY
        await self._session.commit()
        return asset

    async def get(self, actor: Actor, asset_id: uuid.UUID) -> MediaAsset:
        return policies.ensure_owner(actor, await self._media.get(asset_id))

    async def list_mine(
        self,
        actor: Actor,
        *,
        kind: MediaKind | None,
        status: MediaStatus | None,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[MediaAsset], int]:
        policies.ensure_can_upload(actor)
        return await self._media.list_for_owner(
            actor.user_id, kind=kind, status=status, limit=limit, offset=offset
        )

    async def update(self, actor: Actor, asset_id: uuid.UUID, data: MediaUpdate) -> MediaAsset:
        asset = policies.ensure_owner(actor, await self._media.get_for_update(asset_id))
        alt_text = asset.alt_text
        if "alt_text" in data.model_fields_set:
            alt_text = data.alt_text.strip() if data.alt_text else None
        decorative = data.decorative if data.decorative is not None else asset.decorative
        if asset.kind == MediaKind.IMAGE and not alt_text and not decorative:
            raise DomainValidationError("An image needs `alt_text` unless it is decorative.")
        asset.alt_text, asset.decorative = alt_text, decorative
        await self._session.commit()
        return asset

    # --- used by the curriculum module ----------------------------------------------------

    async def get_many(self, ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, MediaAsset]:
        return await self._media.get_many(ids)
