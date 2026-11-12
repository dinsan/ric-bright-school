"""Lesson content: typed blocks, validation, media references, revisions (BE-CAT-02)."""

import uuid
from typing import Any

import pytest
from httpx import AsyncClient

from app.modules.identity.domain import Role
from tests.conftest import Account, AccountFactory, FakeStorage, upload_media
from tests.test_structure import add_lesson, add_unit, new_course


async def lesson_for(client: AsyncClient, author: Account) -> tuple[str, dict[str, Any]]:
    course = await new_course(client, author)
    unit = (await add_unit(client, author, course["id"], "Unit"))["units"][0]["id"]
    return course["id"], await add_lesson(client, author, course["id"], unit, "Lesson")


async def save(
    client: AsyncClient,
    author: Account,
    course_id: str,
    lesson: dict[str, Any],
    blocks: list[dict[str, Any]],
) -> Any:
    return await client.put(
        f"/api/v1/courses/{course_id}/lessons/{lesson['id']}/content",
        json={
            "content": {"schema_version": 1, "blocks": blocks},
            "expected_revision": lesson["revision"],
        },
        headers=author.headers,
    )


async def test_every_block_type(
    client: AsyncClient, teacher: Account, storage: FakeStorage
) -> None:
    cid, lesson = await lesson_for(client, teacher)
    image = await upload_media(client, storage, teacher.headers, alt_text="Three slices of pizza")
    video = await upload_media(
        client, storage, teacher.headers, kind="video", content_type="video/mp4"
    )
    captions = await upload_media(
        client, storage, teacher.headers, kind="captions", content_type="text/vtt"
    )
    pdf = await upload_media(
        client, storage, teacher.headers, kind="document", content_type="application/pdf"
    )
    blocks = [
        {"type": "text", "markdown": "## Fractions\nA fraction has a *numerator*."},
        {"type": "image", "media_id": image["id"], "caption": "Pizza"},
        {
            "type": "video",
            "title": "Intro",
            "media_id": video["id"],
            "captions_media_id": captions["id"],
        },
        {
            "type": "video",
            "title": "On YouTube",
            "url": "https://www.youtube.com/watch?v=abc",
            "transcript": "Hello",
        },
        {"type": "file", "media_id": pdf["id"], "title": "Worksheet"},
        {"type": "embed", "url": "https://www.geogebra.org/m/abc", "title": "Fraction bars"},
        {
            "type": "interactive",
            "title": "Cards",
            "activity": {"widget": "flashcards", "cards": [{"front": "1/2", "back": "one half"}]},
        },
        {
            "type": "interactive",
            "title": "Sort",
            "activity": {
                "widget": "sorting",
                "categories": [
                    {"name": "Less than 1", "items": ["1/2"]},
                    {"name": "More than 1", "items": ["3/2"]},
                ],
            },
        },
        {
            "type": "interactive",
            "title": "Match",
            "activity": {
                "widget": "matching",
                "pairs": [{"left": "1/2", "right": "0.5"}, {"left": "1/4", "right": "0.25"}],
            },
        },
        {
            "type": "interactive",
            "title": "Simulation",
            "activity": {"widget": "simulation", "url": "https://phet.colorado.edu/sims/fractions"},
        },
        {"type": "quiz", "quiz_id": str(uuid.uuid4()), "title": "Check your understanding"},
    ]
    response = await save(client, teacher, cid, lesson, blocks)
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["revision"] == 2
    assert saved["block_count"] == len(blocks)
    got = saved["content"]["blocks"]
    assert [b["type"] for b in got] == [b["type"] for b in blocks]
    assert all(uuid.UUID(b["id"]) for b in got)  # every block got a stable id
    # Previews for the author: signed links and the image description.
    assert got[1]["media"]["url"].startswith("https://storage.test/")
    assert got[1]["media"]["alt_text"] == "Three slices of pizza"
    assert got[2]["captions"]["kind"] == "captions"

    # Ids survive the next save (e.g. a reorder), so progress can point at blocks.
    reordered = list(reversed(got))
    again = await save(
        client,
        teacher,
        cid,
        saved,
        [{k: v for k, v in b.items() if k not in ("media", "captions")} for b in reordered],
    )
    assert again.status_code == 200
    assert [b["id"] for b in again.json()["content"]["blocks"]] == [b["id"] for b in reordered]


@pytest.mark.parametrize(
    "block",
    [
        {"type": "html", "html": "<script>alert(1)</script>"},
        {"type": "text", "markdown": ""},
        {"type": "text", "markdown": "x", "style": "evil"},
        {"type": "embed", "url": "http://www.geogebra.org/m/a", "title": "t"},
        {"type": "embed", "url": "javascript:alert(1)", "title": "t"},
        {"type": "embed", "url": "https://www.geogebra.org/m/a"},  # no title (screen readers)
        {"type": "video", "title": "t", "url": "https://example.com/movie.mp4"},
        {"type": "video", "title": "t"},  # no source
        {"type": "interactive", "title": "t", "activity": {"widget": "matching", "pairs": []}},
        {"type": "interactive", "title": "t", "activity": {"widget": "unknown"}},
        {"type": "quiz", "title": "t", "quiz_id": "not-a-uuid"},
    ],
)
async def test_invalid_blocks_are_rejected(
    client: AsyncClient, teacher: Account, block: dict[str, Any]
) -> None:
    cid, lesson = await lesson_for(client, teacher)
    response = await save(client, teacher, cid, lesson, [block])
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_duplicate_block_ids_and_too_many_blocks(
    client: AsyncClient, teacher: Account
) -> None:
    cid, lesson = await lesson_for(client, teacher)
    block_id = str(uuid.uuid4())
    dup = [
        {"id": block_id, "type": "text", "markdown": "a"},
        {"id": block_id, "type": "text", "markdown": "b"},
    ]
    assert (await save(client, teacher, cid, lesson, dup)).status_code == 422
    many = [{"type": "text", "markdown": "x"}] * 201
    assert (await save(client, teacher, cid, lesson, many)).status_code == 422


async def test_embeds_only_from_allowed_sites(client: AsyncClient, teacher: Account) -> None:
    cid, lesson = await lesson_for(client, teacher)
    response = await save(
        client,
        teacher,
        cid,
        lesson,
        [{"type": "embed", "url": "https://evil.example.com/page", "title": "t"}],
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "domain_validation_failed"
    assert "evil.example.com" in response.json()["error"]["details"][0]["message"]


async def test_media_rules(
    client: AsyncClient, teacher: Account, storage: FakeStorage, make_account: AccountFactory
) -> None:
    cid, lesson = await lesson_for(client, teacher)
    other = await make_account(Role.TEACHER, teacher_verified=True)
    theirs = await upload_media(client, storage, other.headers)
    not_yours = await save(
        client, teacher, cid, lesson, [{"type": "image", "media_id": theirs["id"]}]
    )
    assert not_yours.status_code == 422
    assert "not found" in not_yours.json()["error"]["details"][0]["message"]

    pdf = await upload_media(
        client, storage, teacher.headers, kind="document", content_type="application/pdf"
    )
    wrong_kind = await save(
        client, teacher, cid, lesson, [{"type": "image", "media_id": pdf["id"]}]
    )
    assert wrong_kind.status_code == 422
    assert "Expected image" in wrong_kind.json()["error"]["details"][0]["message"]

    missing = await save(
        client, teacher, cid, lesson, [{"type": "image", "media_id": str(uuid.uuid4())}]
    )
    assert missing.status_code == 422

    # Uploads still in progress may be used in a draft (checked again at publish).
    pending = await client.post(
        "/api/v1/media",
        json={
            "kind": "image",
            "filename": "a.png",
            "content_type": "image/png",
            "size_bytes": 10,
            "alt_text": "A",
        },
        headers=teacher.headers,
    )
    ok = await save(
        client, teacher, cid, lesson, [{"type": "image", "media_id": pending.json()["media"]["id"]}]
    )
    assert ok.status_code == 200
    assert ok.json()["content"]["blocks"][0]["media"]["status"] == "pending_upload"
    assert ok.json()["content"]["blocks"][0]["media"]["url"] is None


async def test_concurrent_edits_are_detected(client: AsyncClient, teacher: Account) -> None:
    cid, lesson = await lesson_for(client, teacher)
    first = await save(client, teacher, cid, lesson, [{"type": "text", "markdown": "Mine"}])
    assert first.status_code == 200
    second = await save(client, teacher, cid, lesson, [{"type": "text", "markdown": "Theirs"}])
    assert second.status_code == 409  # based on revision 1, but it is now 2
    current = await client.get(
        f"/api/v1/courses/{cid}/draft/lessons/{lesson['id']}", headers=teacher.headers
    )
    assert current.json()["content"]["blocks"][0]["markdown"] == "Mine"


async def test_new_lesson_with_content(client: AsyncClient, teacher: Account) -> None:
    course = await new_course(client, teacher)
    unit = (await add_unit(client, teacher, course["id"], "U"))["units"][0]["id"]
    lesson = await add_lesson(
        client,
        teacher,
        course["id"],
        unit,
        "Ready-made",
        content={"blocks": [{"type": "text", "markdown": "Hello"}]},
    )
    assert lesson["content"]["schema_version"] == 1
    assert lesson["content"]["blocks"][0]["markdown"] == "Hello"


async def test_content_needs_authoring_rights(
    client: AsyncClient, teacher: Account, make_account: AccountFactory
) -> None:
    cid, lesson = await lesson_for(client, teacher)
    other = await make_account(Role.TEACHER, teacher_verified=True)
    body = {"content": {"blocks": []}, "expected_revision": 1}
    url = f"/api/v1/courses/{cid}/lessons/{lesson['id']}/content"
    assert (await client.put(url, json=body)).status_code == 401
    assert (await client.put(url, json=body, headers=other.headers)).status_code == 404
    missing = f"/api/v1/courses/{cid}/lessons/{uuid.uuid4()}/content"
    assert (await client.put(missing, json=body, headers=teacher.headers)).status_code == 404
    no_revision = await client.put(url, json={"content": {"blocks": []}}, headers=teacher.headers)
    assert no_revision.status_code == 422
