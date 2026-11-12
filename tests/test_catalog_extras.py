"""Tags, difficulty, language, prerequisites (BE-CAT-06); learning paths and recommendations
(BE-CAT-08)."""

import uuid
from typing import Any

from httpx import AsyncClient

from app.modules.identity.domain import Role
from tests.conftest import Account, AccountFactory, make_course, make_subject
from tests.test_structure import add_lesson, add_unit, new_course


async def test_tags_difficulty_and_language(client: AsyncClient, admin: Account) -> None:
    subject = await make_subject(client, headers=admin.headers)
    sid = subject["id"]
    await make_course(
        client,
        sid,
        headers=admin.headers,
        title="Fractions",
        tags=["Fractions", " grade 4 ", "fractions"],
        difficulty="beginner",
    )
    await make_course(
        client,
        sid,
        headers=admin.headers,
        title="Algebra",
        tags=["equations"],
        difficulty="advanced",
        language="es",
    )

    async def titles(**params: Any) -> list[str]:
        return sorted(
            c["title"] for c in (await client.get("/api/v1/courses", params=params)).json()["items"]
        )

    assert await titles(tag="FRACTIONS") == ["Fractions"]
    assert await titles(tag="grade 4") == ["Fractions"]
    assert await titles(difficulty="advanced") == ["Algebra"]
    assert await titles(language="es") == ["Algebra"]
    course = (await client.get("/api/v1/courses", params={"tag": "fractions"})).json()["items"][0]
    assert course["tags"] == ["fractions", "grade 4"]  # normalised, no duplicates

    too_many = await client.post(
        "/api/v1/courses",
        json={
            "title": "T",
            "summary": "S",
            "grade_level": 3,
            "subject_id": sid,
            "tags": [f"t{i}" for i in range(11)],
        },
        headers=admin.headers,
    )
    assert too_many.status_code == 422
    bad_tag = await client.post(
        "/api/v1/courses",
        json={"title": "T", "summary": "S", "grade_level": 3, "subject_id": sid, "tags": ["<b>"]},
        headers=admin.headers,
    )
    assert bad_tag.status_code == 422


async def test_course_prerequisites(
    client: AsyncClient, teacher: Account, make_account: AccountFactory
) -> None:
    subject = await make_subject(client, headers=teacher.headers)
    a, b, c = [
        await make_course(client, subject["id"], headers=teacher.headers, title=t) for t in "ABC"
    ]
    url = f"/api/v1/courses/{c['id']}/prerequisites"
    response = await client.put(
        url, json={"course_ids": [a["id"], b["id"]]}, headers=teacher.headers
    )
    assert response.status_code == 200
    assert [p["title"] for p in response.json()] == ["A", "B"]
    assert [p["title"] for p in (await client.get(url)).json()] == ["A", "B"]  # public read

    # A needs C would close a circle (C needs A).
    circle = await client.put(
        f"/api/v1/courses/{a['id']}/prerequisites",
        json={"course_ids": [c["id"]]},
        headers=teacher.headers,
    )
    assert circle.status_code == 422
    assert "circle" in circle.json()["error"]["message"]
    itself = await client.put(url, json={"course_ids": [c["id"]]}, headers=teacher.headers)
    assert itself.status_code == 422
    unknown = await client.put(
        url, json={"course_ids": [str(uuid.uuid4())]}, headers=teacher.headers
    )
    assert unknown.status_code == 404
    other = await make_account(Role.TEACHER, teacher_verified=True)
    assert (
        await client.put(url, json={"course_ids": []}, headers=other.headers)
    ).status_code == 403
    assert (await client.put(url, json={"course_ids": []})).status_code == 401
    cleared = await client.put(url, json={"course_ids": []}, headers=teacher.headers)
    assert cleared.json() == []


async def test_lesson_prerequisites_stay_inside_the_course(
    client: AsyncClient, teacher: Account
) -> None:
    course = await new_course(client, teacher)
    cid = course["id"]
    unit = (await add_unit(client, teacher, cid, "U"))["units"][0]["id"]
    l1, l2, l3 = [await add_lesson(client, teacher, cid, unit, f"L{i}") for i in (1, 2, 3)]

    async def set_pre(lesson: dict[str, Any], ids: list[str]) -> Any:
        return await client.put(
            f"/api/v1/courses/{cid}/lessons/{lesson['id']}/prerequisites",
            json={"lesson_ids": ids},
            headers=teacher.headers,
        )

    assert (await set_pre(l3, [l1["id"], l2["id"]])).json()["prerequisite_lesson_ids"] == sorted(
        [l1["id"], l2["id"]]
    )
    assert (await set_pre(l2, [l1["id"]])).status_code == 200
    assert (await set_pre(l1, [l3["id"]])).status_code == 422  # L1 -> L3 -> L1
    assert (await set_pre(l1, [l1["id"]])).status_code == 422
    other = await new_course(client, teacher, title="Other")
    other_unit = (await add_unit(client, teacher, other["id"], "U"))["units"][0]["id"]
    foreign = await add_lesson(client, teacher, other["id"], other_unit, "Elsewhere")
    assert (await set_pre(l1, [foreign["id"]])).status_code == 404


async def test_learning_paths(client: AsyncClient, admin: Account, teacher: Account) -> None:
    subject = await make_subject(client, "Mathematics", headers=admin.headers)
    intro = await make_course(
        client, subject["id"], headers=admin.headers, title="Intro", grade_level=4
    )
    next_ = await make_course(
        client, subject["id"], headers=admin.headers, title="Next", grade_level=4
    )
    hidden = await make_course(
        client, subject["id"], headers=admin.headers, title="Hidden", grade_level=4, status="draft"
    )
    body = {
        "title": "Grade 4 Maths",
        "grade_level": 4,
        "subject_id": subject["id"],
        "course_ids": [next_["id"], intro["id"], hidden["id"]],
    }
    created = await client.post("/api/v1/learning-paths", json=body, headers=admin.headers)
    assert created.status_code == 201, created.text
    path = created.json()
    assert path["slug"] == "grade-4-maths" and path["is_published"] is False
    assert [c["title"] for c in path["courses"]] == ["Next", "Intro", "Hidden"]  # admins see all

    # Not published yet: invisible to everyone else.
    assert (await client.get("/api/v1/learning-paths/grade-4-maths")).status_code == 404
    assert (await client.get("/api/v1/learning-paths")).json()["total"] == 0

    published = await client.patch(
        "/api/v1/learning-paths/grade-4-maths",
        json={"is_published": True, "course_ids": [intro["id"], next_["id"], hidden["id"]]},
        headers=admin.headers,
    )
    assert published.status_code == 200
    public = (await client.get("/api/v1/learning-paths/grade-4-maths")).json()
    assert [c["title"] for c in public["courses"]] == ["Intro", "Next"]  # drafts left out
    listed = (
        await client.get(
            "/api/v1/learning-paths", params={"grade_level": 4, "subject": "mathematics"}
        )
    ).json()
    assert [p["slug"] for p in listed["items"]] == ["grade-4-maths"]
    assert (await client.get("/api/v1/learning-paths", params={"grade_level": 5})).json()[
        "total"
    ] == 0

    assert (
        await client.post("/api/v1/learning-paths", json=body, headers=teacher.headers)
    ).status_code == 403
    assert (
        await client.post("/api/v1/learning-paths", json=body, headers=admin.headers)
    ).status_code == 409
    bad = {**body, "title": "Other", "course_ids": [str(uuid.uuid4())]}
    assert (
        await client.post("/api/v1/learning-paths", json=bad, headers=admin.headers)
    ).status_code == 404
    assert (
        await client.delete("/api/v1/learning-paths/grade-4-maths", headers=teacher.headers)
    ).status_code == 403
    assert (
        await client.delete("/api/v1/learning-paths/grade-4-maths", headers=admin.headers)
    ).status_code == 204
    assert (
        await client.get("/api/v1/learning-paths/grade-4-maths", headers=admin.headers)
    ).status_code == 404


async def test_recommended_for_grade(
    client: AsyncClient, admin: Account, make_account: AccountFactory, session: Any
) -> None:
    subject = await make_subject(client, "Mathematics", headers=admin.headers)
    science = await make_subject(client, "Science", headers=admin.headers)
    sid = subject["id"]
    a = await make_course(client, sid, headers=admin.headers, title="Apples", grade_level=4)
    await make_course(client, sid, headers=admin.headers, title="Bananas", grade_level=4)
    z = await make_course(client, sid, headers=admin.headers, title="Zebras", grade_level=4)
    await make_course(client, science["id"], headers=admin.headers, title="Cells", grade_level=4)
    await make_course(client, sid, headers=admin.headers, title="Other grade", grade_level=5)
    await make_course(
        client, sid, headers=admin.headers, title="Draft", grade_level=4, status="draft"
    )
    await client.post(
        "/api/v1/learning-paths",
        json={
            "title": "Path",
            "grade_level": 4,
            "is_published": True,
            "course_ids": [z["id"], a["id"]],
        },
        headers=admin.headers,
    )

    titles = [
        c["title"]
        for c in (await client.get("/api/v1/courses/recommended", params={"grade_level": 4})).json()
    ]
    assert titles == ["Zebras", "Apples", "Bananas", "Cells"]  # path order first, then A-Z
    maths = await client.get(
        "/api/v1/courses/recommended", params={"grade_level": 4, "subject": "mathematics"}
    )
    assert [c["title"] for c in maths.json()] == ["Zebras", "Apples", "Bananas"]
    assert (await client.get("/api/v1/courses/recommended")).status_code == 422  # which grade?

    # A signed-in student gets their own grade.
    parent = await make_account(Role.PARENT)
    await client.post(
        "/api/v1/me/children",
        json={
            "display_name": "Kid",
            "grade_level": 5,
            "username": "kid5",
            "password": "kid-password",
        },
        headers=parent.headers,
    )
    tokens = (
        await client.post(
            "/api/v1/auth/login/username", json={"username": "kid5", "password": "kid-password"}
        )
    ).json()
    mine = await client.get(
        "/api/v1/courses/recommended", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert [c["title"] for c in mine.json()] == ["Other grade"]
