"""The error codes are a public contract: docs/errors.md and ErrorCode must agree."""

import re
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from app.core.exceptions import AppError, ErrorCode
from app.main import create_app
from tests.conftest import Account, make_settings, make_subject

ERRORS_DOC = Path(__file__).resolve().parent.parent / "docs" / "errors.md"
# Table rows look like: | `not_found` | 404 | ...
ROW = re.compile(r"^\| `(?P<code>[a-z_]+)` \| (?P<status>[^|]+?) \|", re.MULTILINE)


def documented_codes() -> dict[str, str]:
    text = ERRORS_DOC.read_text()
    section = text.split("\n## Codes\n", 1)[1].split("\n## ", 1)[0]
    return {m["code"]: m["status"] for m in ROW.finditer(section)}


def expected_statuses() -> dict[str, str]:
    statuses: dict[str, str] = {
        ErrorCode.VALIDATION_ERROR: "422",
        ErrorCode.METHOD_NOT_ALLOWED: "405",
        ErrorCode.HTTP_ERROR: "varies (4xx/5xx)",
    }

    def walk(cls: type[AppError]) -> None:
        statuses[cls.code] = str(cls.status_code)
        for sub in cls.__subclasses__():
            walk(sub)

    walk(AppError)
    return statuses


def test_every_error_code_is_documented_and_nothing_else() -> None:
    assert set(documented_codes()) == {code.value for code in ErrorCode}


def test_documented_http_statuses_match_the_code() -> None:
    assert documented_codes() == expected_statuses()


def test_error_codes_are_snake_case() -> None:
    for code in ErrorCode:
        assert re.fullmatch(r"[a-z]+(_[a-z]+)*", code.value), code


# --- codes not covered by the endpoint tests ---------------------------------------------


@pytest.fixture
async def bare_client() -> AsyncIterator[tuple[FastAPI, AsyncClient]]:
    """An app without a database, for errors raised before or outside the handlers."""
    app = create_app(make_settings())
    # Return the 500 response instead of re-raising into the test, like a real server.
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield app, http


async def test_domain_validation_failed(client: AsyncClient, admin: Account) -> None:
    subject = await make_subject(client, headers=admin.headers)
    response = await client.post(
        "/api/v1/courses",
        json={"title": "???", "summary": "x", "grade_level": 3, "subject_id": subject["id"]},
        headers=admin.headers,
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == ErrorCode.DOMAIN_VALIDATION_FAILED


async def test_method_not_allowed_keeps_the_allow_header(
    bare_client: tuple[FastAPI, AsyncClient],
) -> None:
    _, http = bare_client
    response = await http.put("/health/live")
    assert response.status_code == 405
    assert response.json()["error"]["code"] == ErrorCode.METHOD_NOT_ALLOWED
    assert response.headers["allow"] == "GET"


async def test_unknown_route_is_not_found(bare_client: tuple[FastAPI, AsyncClient]) -> None:
    _, http = bare_client
    response = await http.get("/api/v1/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == ErrorCode.NOT_FOUND


async def test_unmapped_http_status_falls_back_to_http_error(
    bare_client: tuple[FastAPI, AsyncClient],
) -> None:
    app, http = bare_client

    async def teapot() -> None:
        raise HTTPException(status_code=418, detail="short and stout")

    app.add_api_route("/_teapot", teapot)
    response = await http.get("/_teapot")
    assert response.status_code == 418
    assert response.json()["error"]["code"] == ErrorCode.HTTP_ERROR


async def test_internal_error_hides_the_exception(
    bare_client: tuple[FastAPI, AsyncClient],
) -> None:
    app, http = bare_client

    async def boom() -> None:
        raise RuntimeError("secret internals: password=hunter2")

    app.add_api_route("/_boom", boom)
    response = await http.get("/_boom")
    assert response.status_code == 500
    error = response.json()["error"]
    assert error["code"] == ErrorCode.INTERNAL_ERROR
    assert "hunter2" not in response.text
    assert error["request_id"] == response.headers["x-request-id"]


async def test_validation_error_details_shape(bare_client: tuple[FastAPI, AsyncClient]) -> None:
    _, http = bare_client
    # A body that is not JSON is rejected before authentication or the database run.
    response = await http.post(
        "/api/v1/auth/login",
        content=b"{not json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == ErrorCode.VALIDATION_ERROR
    assert [set(d) for d in error["details"]] == [{"field", "message"}]
    assert error["details"][0]["field"].startswith("body")
