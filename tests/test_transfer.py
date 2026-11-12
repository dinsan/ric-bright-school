"""Duplicate a course (BE-CAT-03), export and import (BE-CAT-10)."""

import uuid
from typing import Any

from httpx import AsyncClient

from app.modules.identity.domain import Role
from tests.conftest import Account, AccountFactory, FakeStorage, make_subject, upload_media
from tests.test_content import save
from tests.test_publishing import course_with_lesson, publish
from tests.test_structure import add_lesson, add_unit, get_draft, new_course


async def import_standard(
    client: AsyncClient, admin: Account, code: str = "4.NF.A.1"
) -> dict[str, Any]:
    await client.post(
        "/api/v1/standards/frameworks",
        json={"code": "CCSS-MATH", "name": "Common Core Math", "country_code": "US"},
        headers=admin.headers,
    )
    await client.put(
        "/api/v1/standards/frameworks/CCSS-MATH/standards",
        json={
            "standards": [
                {
                    "code": code,
                    "description": "Equivalent fractions",
                    "grade_min": 4,
                    "grade_max": 4,
                }
            ]
        },
        headers=admin.headers,
    )
    found = await client.get("/api/v1/standards", params={"q": code})
    return dict(found.json()["items"][0])


async def test_duplicate_own_draft(client: AsyncClient, teacher: Account, admin: Account) -> None:
    course = await new_course(
        client, teacher, title="Fractions", tags=["fractions"], difficulty="beginner"
    )
    cid = course["id"]
    unit = (await add_unit(client, teacher, cid, "Basics"))["units"][0]["id"]
    first = await add_lesson(client, teacher, cid, unit, "Halves")
    second = await add_lesson(client, teacher, cid, unit, "Quarters")
    await save(client, teacher, cid, second, [{"type": "text", "markdown": "Quarter = 1/4"}])
    standard = await import_standard(client, admin)
    await client.put(
        f"/api/v1/courses/{cid}/lessons/{second['id']}/standards",
        json={"standard_ids": [standard["id"]]},
        headers=teacher.headers,
    )
    await client.put(
        f"/api/v1/courses/{cid}/lessons/{second['id']}/prerequisites",
        json={"lesson_ids": [first["id"]]},
        headers=teacher.headers,
    )

    response = await client.post(
        f"/api/v1/courses/{cid}/duplicate", json={}, headers=teacher.headers
    )
    assert response.status_code == 201, response.text
    copy = response.json()
    assert copy["id"] != cid
    assert (copy["title"], copy["slug"], copy["status"]) == (
        "Fractions (copy)",
        "fractions-copy",
        "draft",
    )
    assert (copy["author_id"], copy["tags"], copy["difficulty"]) == (
        teacher.id,
        ["fractions"],
        "beginner",
    )

    copied = await get_draft(client, teacher, copy["id"])
    [copied_unit] = copied["units"]
    halves, quarters = copied_unit["lessons"]
    assert (copied_unit["title"], halves["title"], quarters["title"]) == (
        "Basics",
        "Halves",
        "Quarters",
    )
    assert {halves["id"], quarters["id"]}.isdisjoint({first["id"], second["id"]})  # new rows
    assert quarters["standard_ids"] == [standard["id"]]
    assert quarters["prerequisite_lesson_ids"] == [halves["id"]]  # re-pointed to the copy
    lesson = await client.get(
        f"/api/v1/courses/{copy['id']}/draft/lessons/{quarters['id']}", headers=teacher.headers
    )
    assert lesson.json()["content"]["blocks"][0]["markdown"] == "Quarter = 1/4"

    again = await client.post(
        f"/api/v1/courses/{cid}/duplicate", json={"title": "Fractions B"}, headers=teacher.headers
    )
    assert again.json()["slug"] == "fractions-b"
    third = await client.post(f"/api/v1/courses/{cid}/duplicate", json={}, headers=teacher.headers)
    assert third.json()["slug"] == "fractions-copy-2"  # unique automatically


async def test_other_teachers_copy_the_published_version(
    client: AsyncClient, teacher: Account, storage: FakeStorage, make_account: AccountFactory
) -> None:
    image = await upload_media(client, storage, teacher.headers)
    cid, lesson = await course_with_lesson(
        client, teacher, [{"type": "image", "media_id": image["id"]}]
    )
    await publish(client, teacher, cid)
    await save(client, teacher, cid, lesson, [{"type": "text", "markdown": "secret draft"}])

    other = await make_account(Role.TEACHER, teacher_verified=True)
    response = await client.post(f"/api/v1/courses/{cid}/duplicate", json={}, headers=other.headers)
    assert response.status_code == 201
    copy = response.json()
    assert copy["author_id"] == other.id
    draft = await get_draft(client, other, copy["id"])
    copied_lesson = draft["units"][0]["lessons"][0]
    content = await client.get(
        f"/api/v1/courses/{copy['id']}/draft/lessons/{copied_lesson['id']}", headers=other.headers
    )
    blocks = content.json()["content"]["blocks"]
    assert blocks[0]["type"] == "image"  # the published version, not the author's draft
    # The copy stays editable although the image belongs to the original author.
    kept = await save(
        client,
        other,
        copy["id"],
        content.json(),
        [{k: v for k, v in blocks[0].items() if k != "media"}],
    )
    assert kept.status_code == 200


async def test_who_can_duplicate(
    client: AsyncClient, teacher: Account, make_account: AccountFactory
) -> None:
    course = await new_course(client, teacher)
    other = await make_account(Role.TEACHER, teacher_verified=True)
    parent = await make_account(Role.PARENT)
    url = f"/api/v1/courses/{course['id']}/duplicate"
    assert (await client.post(url, json={})).status_code == 401
    assert (
        await client.post(url, json={}, headers=other.headers)
    ).status_code == 404  # a private draft
    assert (await client.post(url, json={}, headers=parent.headers)).status_code == 403
    assert (
        await client.post(
            f"/api/v1/courses/{uuid.uuid4()}/duplicate", json={}, headers=teacher.headers
        )
    ).status_code == 404


async def test_export_then_import(client: AsyncClient, teacher: Account, admin: Account) -> None:
    cid, lesson = await course_with_lesson(
        client, teacher, [{"type": "text", "markdown": "Exported"}]
    )
    standard = await import_standard(client, admin)
    await client.put(
        f"/api/v1/courses/{cid}/lessons/{lesson['id']}/standards",
        json={"standard_ids": [standard["id"]]},
        headers=teacher.headers,
    )
    await publish(client, teacher, cid)

    exported = await client.get(f"/api/v1/courses/{cid}/export")  # published: public
    assert exported.status_code == 200
    document = exported.json()
    assert (document["format"], document["format_version"]) == ("lumina-course", 1)
    assert document["units"][0]["lessons"][0]["standards"][0]["code"] == "4.NF.A.1"
    draft_export = await client.get(f"/api/v1/courses/{cid}/export", params={"source": "draft"})
    assert draft_export.status_code == 404  # the draft is for authors only

    imported = await client.post(
        "/api/v1/courses/import",
        json={"document": document, "slug": "imported-copy"},
        headers=teacher.headers,
    )
    assert imported.status_code == 201, imported.text
    body = imported.json()
    assert body["warnings"] == []
    assert (body["course"]["slug"], body["course"]["status"]) == ("imported-copy", "draft")
    draft = await get_draft(client, teacher, body["course"]["id"])
    assert draft["units"][0]["lessons"][0]["standard_ids"] == [standard["id"]]


async def test_import_reports_what_does_not_fit(
    client: AsyncClient, teacher: Account, storage: FakeStorage, make_account: AccountFactory
) -> None:
    subject = await make_subject(client, "Science", headers=teacher.headers)
    document: dict[str, Any] = {
        "course": {
            "title": "Imported",
            "summary": "From elsewhere",
            "grade_level": 5,
            "subject": {"slug": "science"},
        },
        "units": [
            {
                "title": "U",
                "lessons": [
                    {"title": "L", "standards": [{"framework": "NGSS", "code": "5-PS1-1"}]}
                ],
            }
        ],
    }
    ok = await client.post(
        "/api/v1/courses/import", json={"document": document}, headers=teacher.headers
    )
    assert ok.status_code == 201
    assert ok.json()["warnings"] == ["Standard NGSS 5-PS1-1 does not exist here; link skipped."]
    assert ok.json()["course"]["subject"]["id"] == subject["id"]

    unknown_subject = {
        **document,
        "course": {**document["course"], "subject": {"slug": "astrology"}},
    }
    response = await client.post(
        "/api/v1/courses/import", json={"document": unknown_subject}, headers=teacher.headers
    )
    assert response.status_code == 422

    someone = await make_account(Role.TEACHER, teacher_verified=True)
    foreign = await upload_media(client, storage, someone.headers)
    with_media = {
        **document,
        "units": [
            {
                "title": "U",
                "lessons": [
                    {
                        "title": "L",
                        "content": {"blocks": [{"type": "image", "media_id": foreign["id"]}]},
                    }
                ],
            }
        ],
    }
    response = await client.post(
        "/api/v1/courses/import", json={"document": with_media}, headers=teacher.headers
    )
    assert response.status_code == 422  # media must be usable here

    bad = await client.post(
        "/api/v1/courses/import",
        json={"document": {"format": "scorm", "course": {}}},
        headers=teacher.headers,
    )
    assert bad.status_code == 422
    parent = await make_account(Role.PARENT)
    assert (
        await client.post(
            "/api/v1/courses/import", json={"document": document}, headers=parent.headers
        )
    ).status_code == 403
