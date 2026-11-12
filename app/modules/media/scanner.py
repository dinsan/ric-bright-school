"""Virus-scan hook (BE-MEDIA-01). Plug a real scanner (ClamAV, a cloud service) in here."""

import logging
from typing import Protocol

from app.core.storage import ObjectStorage
from app.modules.media.models import MediaAsset

logger = logging.getLogger("lumina.media")


class MediaScanner(Protocol):
    async def is_clean(self, asset: MediaAsset, storage: ObjectStorage) -> bool: ...


class NoScanner:
    """Accepts everything. The default until a real scanner is configured; logged so it is
    visible in production logs."""

    async def is_clean(self, asset: MediaAsset, storage: ObjectStorage) -> bool:
        logger.info("no virus scanner configured: accepting upload (kind=%s)", asset.kind.value)
        return True
