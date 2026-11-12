"""Temporary admin API key for write endpoints (BE-FIX-03)."""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import ADMIN_API_KEY_HEADER
from tests.conftest import build_client

WRITE_REQUESTS = [
    ("POST", "/api/v1/subjects", {"name": "Art"}),
    ("DELETE", f"/api/v1/subjects/{uuid.uuid4()}", None),
    (
        "POST",
        "/api/v1/courses",
        {"title": "T", "summary": "S", "grade_level": 1, "subject_id": str(uuid.uuid4())},
    ),
    ("PATCH", f"/api/v1/courses/{uuid.uuid4()}", {"title": "T"}),
    ("DELETE", f"/api/v1/courses/{uuid.uuid4()}", None),
]


@pytest.mark.parametrize(("method", "url", "body"), WRITE_REQUESTS)
async def test_write_without_key_is_unauthorized(
    client: AsyncClient, method: str, url: str, body: dict[str, object] | None
) -> None:
    response = await client.request(method, url, json=body)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


@pytest.mark.parametrize(("method", "url", "body"), WRITE_REQUESTS)
async def test_write_with_wrong_key_is_unauthorized(
    client: AsyncClient, method: str, url: str, body: dict[str, object] | None
) -> None:
    response = await client.request(method, url, json=body, headers={ADMIN_API_KEY_HEADER: "wrong"})
    assert response.status_code == 401


async def test_wrong_key_on_a_public_read_is_rejected(client: AsyncClient) -> None:
    # A bad key is a client bug worth surfacing, not something to silently ignore.
    response = await client.get("/api/v1/courses", headers={ADMIN_API_KEY_HEADER: "wrong"})
    assert response.status_code == 401


async def test_writes_are_disabled_when_no_key_is_configured(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with build_client(session_factory, admin_api_key=None) as client:
        anonymous = await client.post("/api/v1/subjects", json={"name": "Art"})
        assert anonymous.status_code == 403
        assert anonymous.json()["error"]["code"] == "forbidden"

        with_key = await client.post(
            "/api/v1/subjects", json={"name": "Art"}, headers={ADMIN_API_KEY_HEADER: "anything"}
        )
        assert with_key.status_code == 403

        # Public reads still work.
        assert (await client.get("/api/v1/courses")).status_code == 200


async def test_admin_key_is_documented_in_openapi(client: AsyncClient) -> None:
    spec = (await client.get("/openapi.json")).json()
    assert ADMIN_API_KEY_HEADER in str(spec["components"]["securitySchemes"])
    assert spec["paths"]["/api/v1/courses"]["post"]["security"]
