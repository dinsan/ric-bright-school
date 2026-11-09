import uuid

from httpx import AsyncClient

from tests.conftest import make_course, make_subject


async def test_create_course_derives_slug_and_publishes(client: AsyncClient) -> None:
    subject = await make_subject(client)
    course = await make_course(client, subject["id"])

    assert course["slug"] == "fractions-fundamentals"
    assert course["status"] == "published"
    assert course["published_at"] is not None
    assert course["subject"]["slug"] == "mathematics"


async def test_draft_course_has_no_published_at(client: AsyncClient) -> None:
    subject = await make_subject(client)
    course = await make_course(client, subject["id"], status="draft")
    assert course["published_at"] is None


async def test_create_course_with_unknown_subject_is_404(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/courses",
        json={
            "title": "Ghost course",
            "summary": "Nope",
            "grade_level": 3,
            "subject_id": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_grade_level_outside_1_to_12_is_rejected(client: AsyncClient) -> None:
    subject = await make_subject(client)
    response = await client.post(
        "/api/v1/courses",
        json={
            "title": "Too advanced",
            "summary": "x",
            "grade_level": 13,
            "subject_id": subject["id"],
        },
    )
    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "validation_error"
    assert any(d["field"].endswith("grade_level") for d in body["details"])


async def test_duplicate_course_slug_conflicts(client: AsyncClient) -> None:
    subject = await make_subject(client)
    await make_course(client, subject["id"])
    response = await client.post(
        "/api/v1/courses",
        json={
            "title": "Fractions Fundamentals",
            "summary": "again",
            "grade_level": 4,
            "subject_id": subject["id"],
        },
    )
    assert response.status_code == 409


async def test_list_filters_by_grade_subject_and_text(client: AsyncClient) -> None:
    maths = await make_subject(client, "Mathematics")
    science = await make_subject(client, "Science")
    await make_course(client, maths["id"], title="Fractions", grade_level=4)
    await make_course(client, maths["id"], title="Algebra", grade_level=8)
    await make_course(client, science["id"], title="Matter and Energy", grade_level=4)
    await make_course(client, science["id"], title="Hidden draft", grade_level=4, status="draft")

    everything = (await client.get("/api/v1/courses")).json()
    assert everything["total"] == 3  # drafts are excluded by default

    grade_4 = (await client.get("/api/v1/courses", params={"grade_level": 4})).json()
    assert {c["title"] for c in grade_4["items"]} == {"Fractions", "Matter and Energy"}

    maths_only = (await client.get("/api/v1/courses", params={"subject": "mathematics"})).json()
    assert maths_only["total"] == 2

    searched = (await client.get("/api/v1/courses", params={"q": "alge"})).json()
    assert [c["title"] for c in searched["items"]] == ["Algebra"]

    drafts = (await client.get("/api/v1/courses", params={"status": "draft"})).json()
    assert [c["title"] for c in drafts["items"]] == ["Hidden draft"]


async def test_list_is_paginated_and_ordered_by_grade(client: AsyncClient) -> None:
    subject = await make_subject(client)
    for grade in (9, 2, 5):
        await make_course(client, subject["id"], title=f"Course {grade}", grade_level=grade)

    page = (await client.get("/api/v1/courses", params={"limit": 2, "offset": 0})).json()
    assert page["total"] == 3
    assert [c["grade_level"] for c in page["items"]] == [2, 5]

    rest = (await client.get("/api/v1/courses", params={"limit": 2, "offset": 2})).json()
    assert [c["grade_level"] for c in rest["items"]] == [9]


async def test_get_course_by_id_and_slug(client: AsyncClient) -> None:
    subject = await make_subject(client)
    course = await make_course(client, subject["id"])

    by_id = await client.get(f"/api/v1/courses/{course['id']}")
    by_slug = await client.get(f"/api/v1/courses/slug/{course['slug']}")
    assert by_id.status_code == by_slug.status_code == 200
    assert by_id.json() == by_slug.json()


async def test_missing_course_returns_error_envelope(client: AsyncClient) -> None:
    response = await client.get("/api/v1/courses/slug/does-not-exist")
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "not_found"
    assert error["request_id"]
