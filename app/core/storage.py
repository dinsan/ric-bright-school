"""Object storage for uploaded media (BE-MEDIA-01): S3-compatible (MinIO locally).

Files never pass through the API. The API signs short-lived links:
- an upload form (pre-signed POST) the browser sends the file to directly. A POST policy,
  unlike a pre-signed PUT, lets the storage itself enforce the maximum size and the exact
  content type;
- download links (pre-signed GET), or CDN URLs when a CDN fronts the bucket.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import Settings
from app.core.exceptions import ServiceUnavailableError

logger = logging.getLogger("lumina.storage")


@dataclass(frozen=True)
class PresignedUpload:
    url: str
    fields: dict[str, str]  # send them as form fields, with the file last
    expires_in: int


@dataclass(frozen=True)
class StoredObject:
    size: int
    content_type: str | None
    etag: str | None


class ObjectStorage(Protocol):
    def presign_upload(
        self, key: str, *, content_type: str, max_bytes: int, expires_in: int
    ) -> PresignedUpload: ...

    def download_url(self, key: str, *, expires_in: int, filename: str | None = None) -> str: ...

    async def head(self, key: str) -> StoredObject | None: ...


class S3Storage:
    def __init__(self, settings: Settings) -> None:
        self._bucket = settings.storage_bucket
        self._cdn = settings.media_cdn_base_url.rstrip("/") if settings.media_cdn_base_url else None
        common: dict[str, Any] = {
            "region_name": settings.storage_region,
            "aws_access_key_id": settings.storage_access_key,
            "aws_secret_access_key": settings.storage_secret_key.get_secret_value(),
            # Path-style URLs work with MinIO and every S3-compatible service. Short timeouts:
            # fail fast when the storage is down (boto3's default waits 60 s per attempt).
            "config": Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                connect_timeout=5,
                read_timeout=20,
                retries={"max_attempts": 2},
            ),
        }
        # Two clients: the API talks to the storage on its own network address; links are
        # signed for the address browsers use (the host is part of the signature).
        self._internal = boto3.client("s3", endpoint_url=settings.storage_endpoint_url, **common)
        self._public = boto3.client(
            "s3",
            endpoint_url=settings.storage_public_endpoint_url or settings.storage_endpoint_url,
            **common,
        )

    def presign_upload(
        self, key: str, *, content_type: str, max_bytes: int, expires_in: int
    ) -> PresignedUpload:
        post = self._public.generate_presigned_post(
            Bucket=self._bucket,
            Key=key,
            Fields={"Content-Type": content_type},
            Conditions=[
                {"Content-Type": content_type},
                ["content-length-range", 1, max_bytes],
            ],
            ExpiresIn=expires_in,
        )
        return PresignedUpload(
            url=str(post["url"]),
            fields={k: str(v) for k, v in post["fields"].items()},
            expires_in=expires_in,
        )

    def download_url(self, key: str, *, expires_in: int, filename: str | None = None) -> str:
        if self._cdn:
            return f"{self._cdn}/{key}"
        params: dict[str, Any] = {"Bucket": self._bucket, "Key": key}
        if filename:
            safe = filename.replace('"', "")
            params["ResponseContentDisposition"] = f'inline; filename="{safe}"'
        return str(
            self._public.generate_presigned_url("get_object", Params=params, ExpiresIn=expires_in)
        )

    async def head(self, key: str) -> StoredObject | None:
        def _head() -> StoredObject | None:
            try:
                response = self._internal.head_object(Bucket=self._bucket, Key=key)
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                    return None
                logger.exception("storage request failed")
                raise ServiceUnavailableError("The file storage is unavailable.") from exc
            except BotoCoreError as exc:  # unreachable, timed out, bad credentials setup
                logger.exception("storage unreachable")
                raise ServiceUnavailableError("The file storage is unavailable.") from exc
            return StoredObject(
                size=int(response["ContentLength"]),
                content_type=response.get("ContentType"),
                etag=str(response.get("ETag", "")).strip('"') or None,
            )

        return await asyncio.to_thread(_head)
