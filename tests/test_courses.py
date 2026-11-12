import uuid
from datetime import datetime
from typing import Any

from httpx import AsyncClient

from app.modules.identity.domain import Role
from tests.conftest import Account, AccountFactory, make_course, make_subject

COURSE_PAYLOAD: dict[str, Any] = {
    "title": "Fractions Fundamentals",
    "summary": "Equivalent fractions and comparing.",
    "grade_level": 4,
}


# --- create -------------------------------------------------------------------------------


async def test_verified_teacher_creates_and_publishes(
    client: AsyncClient, teacher: Account
) -> None:
    subject = await make_subject(client, headers=teacher.headers)
    course = await make_course(client, subject["id"], headers=teacher.headers)

    assert course["slug"] == "fractions-fundamentals"
    assert course["status"] == "published"
    assert course["published_at"] is not None
    assert course["author_id"] == teacher.id
    assert course["subject"]["slug"] == "mathematics"


async def test_draft_course_has_no_published_at(client: AsyncClient, teacher: Account) -> None:
    subject = await make_subject(client, headers=teacher.headers)
    course = await make_course(client, subject["id"], headers=teacher.headers, status="draft")
    assert course["published_at"] is None


async def test_unverified_teacher_can_draft_but_not_publish(
    client: AsyncClient, admin: Account, make_account: AccountFactory
) -> None:
    newcomer = await make_account(Role.TEACHER, teacher_verified=False)
    subject = await make_subject(client, headers=admin.headers)

    draft = await make_course(client, subject["id"], headers=newcomer.headers, status="draft")
    assert draft["author_id"] == newcomer.id

    response = await client.post(
        "/api/v1/courses",
        json={
            **COURSE_PAYLOAD,
            "title": "Straight to print",
            "subject_id": subject["id"],
            "status": "published",
        },
        headers=newcomer.headers,
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"

    publish = await client.patch(
        f"/api/v1/courses/{draft['id']}", json={"status": "published"}, headers=newcomer.headers
    )
    assert publish.status_code == 403


async def test_create_course_with_unknown_subject_is_404(
    client: AsyncClient, admin: Account
) -> None:
    response = await client.post(
        "/api/v1/courses",
        json={**COURSE_PAYLOAD, "subject_id": str(uuid.uuid4())},
        headers=admin.headers,
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_grade_level_outside_1_to_12_is_rejected(client: AsyncClient, admin: Account) -> None:
    subject = await make_subject(client, headers=admin.headers)
    response = await client.post(
        "/api/v1/courses",
        json={**COURSE_PAYLOAD, "grade_level": 13, "subject_id": subject["id"]},
        headers=admin.headers,
    )
    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "validation_error"
    assert any(d["field"].endswith("grade_level") for d in body["details"])


async def test_duplicate_course_slug_conflicts(client: AsyncClient, admin: Account) -> None:
    subject = await make_subject(client, headers=admin.headers)
    await make_course(client, subject["id"], headers=admin.headers)
    response = await client.post(
        "/api/v1/courses",
        json={**COURSE_PAYLOAD, "summary": "again", "subject_id": subject["id"]},
        headers=admin.headers,
    )
    assert response.status_code == 409


async def test_create_course_requires_sign_in(client: AsyncClient, admin: Account) -> None:
    subject = await make_subject(client, headers=admin.headers)
    payload = {**COURSE_PAYLOAD, "subject_id": subject["id"]}

    anonymous = await client.post("/api/v1/courses", json=payload)
    assert anonymous.status_code == 401
    assert anonymous.json()["error"]["code"] == "unauthorized"
    assert anonymous.headers["www-authenticate"] == "Bearer"

    forged = await client.post(
        "/api/v1/courses", json=payload, headers={"Authorization": "Bearer not-a-jwt"}
    )
    assert forged.status_code == 401


async def test_parents_cannot_create_courses(
    client: AsyncClient, admin: Account, make_account: AccountFactory
) -> None:
    parent = await make_account(Role.PARENT)
    subject = await make_subject(client, headers=admin.headers)
    response = await client.post(
        "/api/v1/courses",
        json={**COURSE_PAYLOAD, "subject_id": subject["id"]},
        headers=parent.headers,
    )
    assert response.status_code == 403


# --- list and search ----------------------------------------------------------------------


async def test_list_filters_by_grade_subject_and_text(client: AsyncClient, admin: Account) -> None:
    h = admin.headers
    maths = await make_subject(client, "Mathematics", headers=h)
    science = await make_subject(client, "Science", headers=h)
    await make_course(client, maths["id"], headers=h, title="Fractions", grade_level=4)
    await make_course(client, maths["id"], headers=h, title="Algebra", grade_level=8)
    await make_course(client, science["id"], headers=h, title="Matter and Energy", grade_level=4)
    await make_course(
        client, science["id"], headers=h, title="Hidden draft", grade_level=4, status="draft"
    )

    everything = (await client.get("/api/v1/courses")).json()
    assert everything["total"] == 3  # drafts are never public

    grade_4 = (await client.get("/api/v1/courses", params={"grade_level": 4})).json()
    assert {c["title"] for c in grade_4["items"]} == {"Fractions", "Matter and Energy"}

    maths_only = (await client.get("/api/v1/courses", params={"subject": "mathematics"})).json()
    assert maths_only["total"] == 2

    searched = (await client.get("/api/v1/courses", params={"q": "alge"})).json()
    assert [c["title"] for c in searched["items"]] == ["Algebra"]


async def test_search_treats_like_wildcards_literally(client: AsyncClient, admin: Account) -> None:
    subject = await make_subject(client, headers=admin.headers)
    await make_course(client, subject["id"], headers=admin.headers, title="100% Fractions")
    await make_course(client, subject["id"], headers=admin.headers, title="Decimals")

    percent = (await client.get("/api/v1/courses", params={"q": "%"})).json()
    assert [c["title"] for c in percent["items"]] == ["100% Fractions"]


async def test_public_and_parents_cannot_list_drafts(
    client: AsyncClient, admin: Account, make_account: AccountFactory
) -> None:
    parent = await make_account(Role.PARENT)
    subject = await make_subject(client, headers=admin.headers)
    await make_course(client, subject["id"], headers=admin.headers, title="Draft", status="draft")

    for headers in ({}, parent.headers):
        for status in ("draft", "archived"):
            response = await client.get(
                "/api/v1/courses", params={"status": status}, headers=headers
            )
            assert response.status_code == 403
            assert response.json()["error"]["code"] == "forbidden"


async def test_drafts_list_admin_sees_all_teacher_sees_own(
    client: AsyncClient, admin: Account, teacher: Account, make_account: AccountFactory
) -> None:
    other = await make_account(Role.TEACHER, teacher_verified=True)
    subject = await make_subject(client, headers=admin.headers)
    await make_course(client, subject["id"], headers=teacher.headers, title="Mine", status="draft")
    await make_course(client, subject["id"], headers=other.headers, title="Theirs", status="draft")
    await make_course(client, subject["id"], headers=teacher.headers, title="Published one")

    def titles(response: Any) -> list[str]:
        assert response.status_code == 200, response.text
        return sorted(c["title"] for c in response.json()["items"])

    params = {"status": "draft"}
    assert titles(await client.get("/api/v1/courses", params=params, headers=admin.headers)) == [
        "Mine",
        "Theirs",
    ]
    assert titles(await client.get("/api/v1/courses", params=params, headers=teacher.headers)) == [
        "Mine"
    ]


async def test_list_is_paginated_and_ordered_by_grade(client: AsyncClient, admin: Account) -> None:
    subject = await make_subject(client, headers=admin.headers)
    for grade in (9, 2, 5):
        await make_course(
            client, subject["id"], headers=admin.headers, title=f"Course {grade}", grade_level=grade
        )

    page = (await client.get("/api/v1/courses", params={"limit": 2, "offset": 0})).json()
    assert page["total"] == 3
    assert [c["grade_level"] for c in page["items"]] == [2, 5]

    rest = (await client.get("/api/v1/courses", params={"limit": 2, "offset": 2})).json()
    assert [c["grade_level"] for c in rest["items"]] == [9]


# --- read one -----------------------------------------------------------------------------


async def test_get_course_by_id_and_slug(client: AsyncClient, admin: Account) -> None:
    subject = await make_subject(client, headers=admin.headers)
    course = await make_course(client, subject["id"], headers=admin.headers)

    by_id = await client.get(f"/api/v1/courses/{course['id']}")
    by_slug = await client.get(f"/api/v1/courses/slug/{course['slug']}")
    assert by_id.status_code == by_slug.status_code == 200
    assert by_id.json() == by_slug.json()


async def test_draft_visible_only_to_author_and_admin(
    client: AsyncClient, admin: Account, teacher: Account, make_account: AccountFactory
) -> None:
    other = await make_account(Role.TEACHER, teacher_verified=True)
    subject = await make_subject(client, headers=admin.headers)
    draft = await make_course(client, subject["id"], headers=teacher.headers, status="draft")

    for path in (f"/api/v1/courses/{draft['id']}", f"/api/v1/courses/slug/{draft['slug']}"):
        for outsider in ({}, other.headers):
            hidden = await client.get(path, headers=outsider)
            assert hidden.status_code == 404  # indistinguishable from a missing course
            assert hidden.json()["error"]["code"] == "not_found"
        for insider in (teacher.headers, admin.headers):
            visible = await client.get(path, headers=insider)
            assert visible.status_code == 200
            assert visible.json()["status"] == "draft"


async def test_missing_course_returns_error_envelope(client: AsyncClient) -> None:
    response = await client.get("/api/v1/courses/slug/does-not-exist")
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "not_found"
    assert error["request_id"]


async def test_expired_or_bad_token_on_public_read_is_401(client: AsyncClient) -> None:
    response = await client.get("/api/v1/courses", headers={"Authorization": "Bearer nope"})
    assert response.status_code == 401  # tells the client to refresh, not "anonymous"


# --- update (PATCH) -----------------------------------------------------------------------


async def test_author_patches_only_the_fields_sent(client: AsyncClient, teacher: Account) -> None:
    subject = await make_subject(client, headers=teacher.headers)
    course = await make_course(
        client, subject["id"], headers=teacher.headers, description="Original"
    )

    response = await client.patch(
        f"/api/v1/courses/{course['id']}",
        json={"title": "  Fractions Deep Dive ", "grade_level": 5},
        headers=teacher.headers,
    )
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["title"] == "Fractions Deep Dive"
    assert updated["grade_level"] == 5
    assert updated["slug"] == course["slug"]  # slugs stay stable on rename
    assert updated["summary"] == course["summary"]
    assert updated["description"] == "Original"
    assert datetime.fromisoformat(updated["updated_at"]) > datetime.fromisoformat(
        course["updated_at"]
    )


async def test_patch_can_clear_description_and_move_subject(
    client: AsyncClient, admin: Account
) -> None:
    maths = await make_subject(client, "Mathematics", headers=admin.headers)
    science = await make_subject(client, "Science", headers=admin.headers)
    course = await make_course(client, maths["id"], headers=admin.headers, description="Some text")

    response = await client.patch(
        f"/api/v1/courses/{course['id']}",
        json={"description": None, "subject_id": science["id"]},
        headers=admin.headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["description"] is None
    assert response.json()["subject"]["slug"] == "science"


async def test_patch_status_maintains_published_at(client: AsyncClient, admin: Account) -> None:
    subject = await make_subject(client, headers=admin.headers)
    course = await make_course(client, subject["id"], headers=admin.headers, status="draft")
    url = f"/api/v1/courses/{course['id']}"
    h = admin.headers

    published = (await client.patch(url, json={"status": "published"}, headers=h)).json()
    assert published["published_at"] is not None

    archived = (await client.patch(url, json={"status": "archived"}, headers=h)).json()
    assert archived["published_at"] == published["published_at"]  # history is kept

    draft = (await client.patch(url, json={"status": "draft"}, headers=h)).json()
    assert draft["published_at"] is None


async def test_patch_rejects_invalid_values(client: AsyncClient, admin: Account) -> None:
    subject = await make_subject(client, headers=admin.headers)
    course = await make_course(client, subject["id"], headers=admin.headers)
    url = f"/api/v1/courses/{course['id']}"

    for body in ({"grade_level": 0}, {"title": None}, {"slug": "Not A Slug"}, {"unknown": 1}):
        response = await client.patch(url, json=body, headers=admin.headers)
        assert response.status_code == 422, body
        assert response.json()["error"]["code"] == "validation_error"


async def test_patch_slug_conflict_is_409(client: AsyncClient, admin: Account) -> None:
    subject = await make_subject(client, headers=admin.headers)
    await make_course(client, subject["id"], headers=admin.headers, title="Taken")
    course = await make_course(client, subject["id"], headers=admin.headers, title="Other")

    response = await client.patch(
        f"/api/v1/courses/{course['id']}", json={"slug": "taken"}, headers=admin.headers
    )
    assert response.status_code == 409


async def test_patch_unknown_course_or_subject_is_404(client: AsyncClient, admin: Account) -> None:
    subject = await make_subject(client, headers=admin.headers)
    course = await make_course(client, subject["id"], headers=admin.headers)

    missing_course = await client.patch(
        f"/api/v1/courses/{uuid.uuid4()}", json={"title": "x"}, headers=admin.headers
    )
    assert missing_course.status_code == 404

    missing_subject = await client.patch(
        f"/api/v1/courses/{course['id']}",
        json={"subject_id": str(uuid.uuid4())},
        headers=admin.headers,
    )
    assert missing_subject.status_code == 404


async def test_only_author_or_admin_can_patch(
    client: AsyncClient, admin: Account, teacher: Account, make_account: AccountFactory
) -> None:
    other = await make_account(Role.TEACHER, teacher_verified=True)
    subject = await make_subject(client, headers=admin.headers)
    course = await make_course(client, subject["id"], headers=teacher.headers)
    platform_course = await make_course(
        client, subject["id"], headers=admin.headers, title="Platform"
    )
    url = f"/api/v1/courses/{course['id']}"

    assert (await client.patch(url, json={"title": "Hacked"})).status_code == 401
    assert (
        await client.patch(url, json={"title": "Hacked"}, headers=other.headers)
    ).status_code == 403
    # Platform content (created by an admin) belongs to admins, not to any teacher.
    platform = await client.patch(
        f"/api/v1/courses/{platform_course['id']}",
        json={"title": "Mine now"},
        headers=teacher.headers,
    )
    assert platform.status_code == 403

    by_admin = await client.patch(url, json={"title": "Edited by admin"}, headers=admin.headers)
    assert by_admin.status_code == 200
    unchanged_author = (await client.get(url)).json()["author_id"]
    assert unchanged_author == teacher.id


# --- archive (DELETE) ---------------------------------------------------------------------


async def test_author_archives_the_course(client: AsyncClient, teacher: Account) -> None:
    subject = await make_subject(client, headers=teacher.headers)
    course = await make_course(client, subject["id"], headers=teacher.headers)
    url = f"/api/v1/courses/{course['id']}"

    response = await client.delete(url, headers=teacher.headers)
    assert response.status_code == 204
    assert response.content == b""

    assert (await client.get(url)).status_code == 404
    assert (await client.get("/api/v1/courses")).json()["total"] == 0

    kept = (await client.get(url, headers=teacher.headers)).json()
    assert kept["status"] == "archived"

    again = await client.delete(url, headers=teacher.headers)
    assert again.status_code == 204  # idempotent


async def test_delete_unknown_course_is_404(client: AsyncClient, admin: Account) -> None:
    response = await client.delete(f"/api/v1/courses/{uuid.uuid4()}", headers=admin.headers)
    assert response.status_code == 404


async def test_archive_requires_author_or_admin(
    client: AsyncClient, teacher: Account, make_account: AccountFactory
) -> None:
    other = await make_account(Role.TEACHER, teacher_verified=True)
    subject = await make_subject(client, headers=teacher.headers)
    course = await make_course(client, subject["id"], headers=teacher.headers)
    url = f"/api/v1/courses/{course['id']}"

    assert (await client.delete(url)).status_code == 401
    assert (await client.delete(url, headers=other.headers)).status_code == 403
    assert (await client.get(url)).status_code == 200
