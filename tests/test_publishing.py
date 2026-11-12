"""Publishing, versions, reading published content (BE-CAT-03/04), accessibility checks
before publishing (BE-MEDIA-04)."""

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.modules.identity.domain import Role
from tests.conftest import (
    Account,
    AccountFactory,
    FakeStorage,
    make_course,
    make_subject,
    upload_media,
)
from tests.test_content import save
from tests.test_structure import add_lesson, add_unit, get_draft, new_course


async def course_with_lesson(
    client: AsyncClient, author: Account, blocks: list[dict[str, Any]] | None = None
) -> tuple[str, dict[str, Any]]:
    course = await new_course(client, author)
    unit = (await add_unit(client, author, course["id"], "Unit 1"))["units"][0]["id"]
    lesson = await add_lesson(client, author, course["id"], unit, "Lesson 1", estimated_minutes=20)
    if blocks is not None:
        response = await save(client, author, course["id"], lesson, blocks)
        assert response.status_code == 200, response.text
        lesson = response.json()
    return course["id"], lesson


async def publish(
    client: AsyncClient, author: Account, course_id: str, note: str | None = None
) -> Any:
    return await client.post(
        f"/api/v1/courses/{course_id}/publish", json={"change_note": note}, headers=author.headers
    )


async def test_publish_creates_versions(client: AsyncClient, teacher: Account) -> None:
    cid, lesson = await course_with_lesson(
        client, teacher, [{"type": "text", "markdown": "v1 text"}]
    )
    first = await publish(client, teacher, cid, "First release")
    assert first.status_code == 200, first.text
    assert (first.json()["number"], first.json()["is_current"], first.json()["change_note"]) == (
        1,
        True,
        "First release",
    )
    assert first.json()["lesson_count"] == 1

    course = (await client.get(f"/api/v1/courses/{cid}")).json()  # now public
    assert course["status"] == "published"
    assert course["estimated_minutes"] == 20
    draft = await get_draft(client, teacher, cid)
    assert (draft["published_version"], draft["has_unpublished_changes"]) == (1, False)

    # Nothing changed: publishing again does not create a version.
    assert (await publish(client, teacher, cid)).json()["number"] == 1

    lesson = (
        await save(client, teacher, cid, lesson, [{"type": "text", "markdown": "v2 text"}])
    ).json()
    assert (await get_draft(client, teacher, cid))["has_unpublished_changes"] is True
    second = await publish(client, teacher, cid, "Better wording")
    assert second.json()["number"] == 2

    versions = (await client.get(f"/api/v1/courses/{cid}/versions", headers=teacher.headers)).json()
    assert [(v["number"], v["is_current"]) for v in versions] == [(2, True), (1, False)]


async def test_learners_see_versions_not_the_draft(client: AsyncClient, teacher: Account) -> None:
    cid, lesson = await course_with_lesson(
        client, teacher, [{"type": "text", "markdown": "Published words"}]
    )
    await publish(client, teacher, cid)
    # The author keeps editing...
    await save(client, teacher, cid, lesson, [{"type": "text", "markdown": "Draft words"}])
    await client.post(
        f"/api/v1/courses/{cid}/units", json={"title": "Unfinished"}, headers=teacher.headers
    )

    # ...but learners (anonymous here) still see version 1.
    outline = (await client.get(f"/api/v1/courses/{cid}/content")).json()
    assert outline["version"] == 1
    assert [u["title"] for u in outline["units"]] == ["Unit 1"]
    assert outline["estimated_minutes"] == 20
    published = (await client.get(f"/api/v1/courses/{cid}/content/lessons/{lesson['id']}")).json()
    assert published["blocks"][0]["markdown"] == "Published words"
    assert published["version"] == 1

    await publish(client, teacher, cid)
    latest = (await client.get(f"/api/v1/courses/{cid}/content/lessons/{lesson['id']}")).json()
    assert latest["blocks"][0]["markdown"] == "Draft words"
    # Someone who started version 1 can keep reading it.
    old = await client.get(
        f"/api/v1/courses/{cid}/content/lessons/{lesson['id']}", params={"version": 1}
    )
    assert old.json()["blocks"][0]["markdown"] == "Published words"
    assert (
        await client.get(f"/api/v1/courses/{cid}/content", params={"version": 9})
    ).status_code == 404


async def test_unpublish_and_republish(client: AsyncClient, teacher: Account) -> None:
    cid, _ = await course_with_lesson(client, teacher, [])
    await publish(client, teacher, cid)
    assert (
        await client.post(f"/api/v1/courses/{cid}/unpublish", headers=teacher.headers)
    ).status_code == 204
    assert (await client.get(f"/api/v1/courses/{cid}/content")).status_code == 404
    assert (await client.get(f"/api/v1/courses/{cid}")).status_code == 404
    # The author can still preview the last version.
    preview = await client.get(f"/api/v1/courses/{cid}/content", headers=teacher.headers)
    assert preview.status_code == 200
    assert (await publish(client, teacher, cid)).json()["number"] == 1  # same content, same version
    assert (await client.get(f"/api/v1/courses/{cid}/content")).status_code == 200


async def test_patch_status_published_also_creates_a_version(
    client: AsyncClient, teacher: Account
) -> None:
    cid, _ = await course_with_lesson(client, teacher, [{"type": "text", "markdown": "Hi"}])
    response = await client.patch(
        f"/api/v1/courses/{cid}", json={"status": "published"}, headers=teacher.headers
    )
    assert response.status_code == 200
    assert response.json()["published_version_id"] is not None
    assert (await client.get(f"/api/v1/courses/{cid}/content")).json()["version"] == 1


async def test_publishing_needs_a_verified_teacher_or_admin(
    client: AsyncClient, admin: Account, make_account: AccountFactory
) -> None:
    newcomer = await make_account(Role.TEACHER)
    cid, _ = await course_with_lesson(client, newcomer, [])
    assert (await publish(client, newcomer, cid)).status_code == 403
    assert (await publish(client, admin, cid)).status_code == 200
    other = await make_account(Role.TEACHER, teacher_verified=True)
    assert (await publish(client, other, cid)).status_code == 403  # published, not theirs
    assert (await client.post(f"/api/v1/courses/{cid}/publish", json={})).status_code == 401


async def test_accessibility_and_readiness_are_checked_before_publishing(
    client: AsyncClient, teacher: Account, storage: FakeStorage
) -> None:
    ready_video = await upload_media(
        client, storage, teacher.headers, kind="video", content_type="video/mp4"
    )
    pending = await client.post(
        "/api/v1/media",
        json={
            "kind": "image",
            "filename": "a.png",
            "content_type": "image/png",
            "size_bytes": 10,
            "alt_text": "Diagram",
        },
        headers=teacher.headers,
    )
    blocks = [
        {"type": "image", "media_id": pending.json()["media"]["id"]},  # upload not finished
        {"type": "video", "title": "No captions", "media_id": ready_video["id"]},
        {"type": "video", "title": "YouTube", "url": "https://youtu.be/abc"},  # no transcript
    ]
    cid, lesson = await course_with_lesson(client, teacher, blocks)
    response = await publish(client, teacher, cid)
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "domain_validation_failed"
    messages = [d["message"] for d in error["details"]]
    assert len(messages) == 3
    assert "not ready" in messages[0]
    assert all("captions or a transcript" in m for m in messages[1:])
    assert all(d["field"].startswith(f"lessons.{lesson['id']}.blocks.") for d in error["details"])
    unchanged = await client.get(f"/api/v1/courses/{cid}", headers=teacher.headers)
    assert unchanged.json()["status"] == "draft"  # nothing was published

    captions = await upload_media(
        client, storage, teacher.headers, kind="captions", content_type="text/vtt"
    )
    storage.put(pending.json()["upload"]["fields"]["key"], 10, "image/png")
    await client.post(
        f"/api/v1/media/{pending.json()['media']['id']}/complete", headers=teacher.headers
    )
    fixed = [
        blocks[0],
        {**blocks[1], "captions_media_id": captions["id"]},
        {**blocks[2], "transcript": "Today we learn fractions."},
    ]
    await save(client, teacher, cid, lesson, fixed)
    assert (await publish(client, teacher, cid)).status_code == 200


async def test_images_need_a_description_to_publish(
    client: AsyncClient, teacher: Account, storage: FakeStorage, engine: AsyncEngine
) -> None:
    """The API never lets an image lose its description, so this is defence in depth: rows
    changed by hand, or created before the rule existed, are caught at publish."""
    image = await upload_media(client, storage, teacher.headers, alt_text="Pie chart")
    cid, _ = await course_with_lesson(client, teacher, [{"type": "image", "media_id": image["id"]}])
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE media_assets SET alt_text = NULL, decorative = false"))
    response = await publish(client, teacher, cid)
    assert response.status_code == 422
    assert "alt text" in response.json()["error"]["details"][0]["message"]

    async with engine.begin() as conn:
        await conn.execute(text("UPDATE media_assets SET decorative = true"))
    assert (await publish(client, teacher, cid)).status_code == 200  # decorative: fine


async def test_published_media_carry_signed_links(
    client: AsyncClient, teacher: Account, storage: FakeStorage
) -> None:
    image = await upload_media(client, storage, teacher.headers, alt_text="Number line")
    cid, lesson = await course_with_lesson(
        client,
        teacher,
        [{"type": "image", "media_id": image["id"], "alt": "A number line from 0 to 1"}],
    )
    await publish(client, teacher, cid)
    block = (await client.get(f"/api/v1/courses/{cid}/content/lessons/{lesson['id']}")).json()[
        "blocks"
    ][0]
    assert block["media"]["url"].startswith("https://storage.test/")
    assert block["media"]["alt_text"] == "A number line from 0 to 1"  # the block's own text wins


async def test_versions_are_immutable(
    client: AsyncClient, teacher: Account, engine: AsyncEngine
) -> None:
    cid, _ = await course_with_lesson(client, teacher, [])
    await publish(client, teacher, cid)
    for statement in (
        "UPDATE course_versions SET change_note = 'rewritten'",
        "DELETE FROM course_versions",
    ):
        with pytest.raises(DBAPIError, match="append-only"):
            async with engine.begin() as conn:
                await conn.execute(text(statement))


async def test_reading_rules(
    client: AsyncClient, teacher: Account, make_account: AccountFactory
) -> None:
    cid, _ = await course_with_lesson(client, teacher, [])
    # A draft course has no public content, and the draft itself is private.
    assert (await client.get(f"/api/v1/courses/{cid}/content")).status_code == 404
    parent = await make_account(Role.PARENT)
    assert (
        await client.get(f"/api/v1/courses/{cid}/versions", headers=parent.headers)
    ).status_code == 404
    await publish(client, teacher, cid)
    assert (
        await client.get(f"/api/v1/courses/{cid}/versions", headers=parent.headers)
    ).status_code == 403
    missing = await client.get(f"/api/v1/courses/{cid}/content/lessons/{uuid.uuid4()}")
    assert missing.status_code == 404
    assert (await client.get(f"/api/v1/courses/{uuid.uuid4()}/content")).status_code == 404


async def test_seeded_style_courses_without_structure_still_work(
    client: AsyncClient, admin: Account
) -> None:
    subject = await make_subject(client, headers=admin.headers)
    course = await make_course(
        client, subject["id"], headers=admin.headers
    )  # published at creation
    outline = (await client.get(f"/api/v1/courses/{course['id']}/content")).json()
    assert (outline["version"], outline["units"]) == (1, [])
