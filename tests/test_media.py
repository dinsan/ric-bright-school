"""Media uploads (BE-MEDIA-01/02): signed upload forms, server-side checks, alt text."""

import uuid
from collections.abc import Callable
from typing import Any

import pytest
from httpx import AsyncClient

from app.modules.identity.domain import Role
from app.modules.media.domain import MB
from tests.conftest import Account, AccountFactory, FakeStorage, make_settings, upload_media


async def start_upload(client: AsyncClient, headers: dict[str, str], **body: Any) -> Any:
    payload = {
        "kind": "image",
        "filename": "cat.png",
        "content_type": "image/png",
        "size_bytes": 2048,
        "alt_text": "A grey cat",
        **body,
    }
    return await client.post("/api/v1/media", json=payload, headers=headers)


async def test_upload_flow(client: AsyncClient, teacher: Account, storage: FakeStorage) -> None:
    response = await start_upload(client, teacher.headers, filename="../../etc/Cat Photo!.PNG")
    assert response.status_code == 201, response.text
    body = response.json()
    media, upload = body["media"], body["upload"]
    assert media["status"] == "pending_upload"
    assert media["url"] is None
    assert upload["url"] == "https://storage.test/lumina-media"
    key = upload["fields"]["key"]
    assert key.startswith(f"media/{teacher.id}/{media['id']}/")
    assert ".." not in key and " " not in key and "!" not in key  # safe file name
    assert storage.upload_limits[key] == 10 * MB  # the storage enforces the image limit
    assert upload["expires_in"] == 900

    not_yet = await client.post(f"/api/v1/media/{media['id']}/complete", headers=teacher.headers)
    assert not_yet.status_code == 409

    storage.put(key, 2048, "image/png")
    done = await client.post(f"/api/v1/media/{media['id']}/complete", headers=teacher.headers)
    assert done.status_code == 200
    assert done.json()["status"] == "ready"
    assert done.json()["url"].startswith("https://storage.test/lumina-media/media/")

    again = await client.post(f"/api/v1/media/{media['id']}/complete", headers=teacher.headers)
    assert again.json()["status"] == "ready"  # idempotent


@pytest.mark.parametrize(
    ("stored_size", "stored_type", "reason"),
    [
        (4096, "image/png", "declared size"),  # not what was declared
        (2048, "text/html", "declared type"),  # a different kind of file arrived
        (11 * MB, "image/png", "larger than allowed"),
    ],
)
async def test_the_api_checks_what_actually_arrived(
    client: AsyncClient,
    teacher: Account,
    storage: FakeStorage,
    stored_size: int,
    stored_type: str,
    reason: str,
) -> None:
    """Never trust the client (or the storage's policy enforcement) alone."""
    body = (await start_upload(client, teacher.headers)).json()
    storage.put(body["upload"]["fields"]["key"], stored_size, stored_type)
    done = await client.post(
        f"/api/v1/media/{body['media']['id']}/complete", headers=teacher.headers
    )
    assert done.json()["status"] == "rejected"
    assert reason in done.json()["rejection_reason"]
    assert done.json()["url"] is None


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"content_type": "image/svg+xml"}, "not accepted"),  # SVG can carry scripts
        ({"content_type": "text/html", "kind": "document"}, "not accepted"),
        ({"size_bytes": 11 * MB}, "too large"),
        ({"alt_text": None}, "alt_text"),
        ({"alt_text": "   "}, "alt_text"),
    ],
)
async def test_upload_rules(
    client: AsyncClient, teacher: Account, body: dict[str, Any], message: str
) -> None:
    response = await start_upload(client, teacher.headers, **body)
    assert response.status_code == 422
    assert message in response.json()["error"]["message"]


async def test_decorative_images_need_no_alt_text(client: AsyncClient, teacher: Account) -> None:
    response = await start_upload(client, teacher.headers, alt_text=None, decorative=True)
    assert response.status_code == 201


async def test_upload_validation(client: AsyncClient, teacher: Account) -> None:
    for body in ({"kind": "spreadsheet"}, {"size_bytes": 0}, {"checksum_sha256": "nothex"}):
        assert (await start_upload(client, teacher.headers, **body)).status_code == 422


async def test_only_authors_upload_and_see_their_own(
    client: AsyncClient,
    teacher: Account,
    admin: Account,
    storage: FakeStorage,
    make_account: AccountFactory,
) -> None:
    assert (await start_upload(client, {})).status_code == 401
    parent = await make_account(Role.PARENT)
    assert (await start_upload(client, parent.headers)).status_code == 403

    mine = await upload_media(client, storage, teacher.headers)
    other = await make_account(Role.TEACHER)
    for method, path in (
        ("GET", f"/api/v1/media/{mine['id']}"),
        ("POST", f"/api/v1/media/{mine['id']}/complete"),
        ("PATCH", f"/api/v1/media/{mine['id']}"),
    ):
        response = await client.request(method, path, json={"alt_text": "x"}, headers=other.headers)
        assert response.status_code == 404, path
    assert (
        await client.get(f"/api/v1/media/{mine['id']}", headers=admin.headers)
    ).status_code == 200
    assert (
        await client.get(f"/api/v1/media/{uuid.uuid4()}", headers=teacher.headers)
    ).status_code == 404

    listed = (await client.get("/api/v1/media", headers=teacher.headers)).json()
    assert [m["id"] for m in listed["items"]] == [mine["id"]]
    assert (await client.get("/api/v1/media", headers=other.headers)).json()["total"] == 0


async def test_change_alt_text(client: AsyncClient, teacher: Account, storage: FakeStorage) -> None:
    media = await upload_media(client, storage, teacher.headers)
    url = f"/api/v1/media/{media['id']}"
    updated = await client.patch(url, json={"alt_text": "A cat on a mat"}, headers=teacher.headers)
    assert updated.json()["alt_text"] == "A cat on a mat"
    cleared = await client.patch(url, json={"alt_text": None}, headers=teacher.headers)
    assert cleared.status_code == 422  # an image needs a description...
    decorative = await client.patch(
        url, json={"alt_text": None, "decorative": True}, headers=teacher.headers
    )
    assert decorative.status_code == 200  # ...unless it is decorative
    assert (await client.patch(url, json={}, headers=teacher.headers)).status_code == 422


async def test_other_kinds(client: AsyncClient, teacher: Account, storage: FakeStorage) -> None:
    for kind, content_type in (
        ("video", "video/mp4"),
        ("captions", "text/vtt"),
        ("document", "application/pdf"),
        ("audio", "audio/mpeg"),
    ):
        media = await upload_media(
            client, storage, teacher.headers, kind=kind, content_type=content_type
        )
        assert (media["kind"], media["status"]) == (kind, "ready")


class RefuseEverything:
    async def is_clean(self, asset: object, storage: object) -> bool:
        return False


async def test_the_virus_scan_hook_can_reject(
    client_factory: Callable[..., AsyncClient],
    teacher: Account,
    storage: FakeStorage,
) -> None:
    strict = client_factory(make_settings(), media_scanner=RefuseEverything())
    body = (await start_upload(strict, teacher.headers)).json()
    storage.put(body["upload"]["fields"]["key"], 2048, "image/png")
    done = await strict.post(
        f"/api/v1/media/{body['media']['id']}/complete", headers=teacher.headers
    )
    assert done.json()["status"] == "rejected"
    assert "virus scan" in done.json()["rejection_reason"]


async def test_storage_outage_is_a_503_not_a_crash(
    client: AsyncClient,
    teacher: Account,
    storage: FakeStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real S3 client against a port nobody listens on: a clear, retryable error, and
    the upload stays pending so the author can confirm it again later."""
    from app.core.storage import S3Storage

    unreachable = S3Storage(make_settings(storage_endpoint_url="http://127.0.0.1:9"))
    body = (await start_upload(client, teacher.headers)).json()
    monkeypatch.setattr(storage, "head", unreachable.head)

    done = await client.post(
        f"/api/v1/media/{body['media']['id']}/complete", headers=teacher.headers
    )
    assert done.status_code == 503
    assert done.json()["error"]["code"] == "service_unavailable"

    monkeypatch.undo()
    media = await client.get(f"/api/v1/media/{body['media']['id']}", headers=teacher.headers)
    assert media.json()["status"] == "pending_upload"
