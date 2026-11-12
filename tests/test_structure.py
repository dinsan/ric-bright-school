"""Course structure: units and lessons, ordering, move and reorder (BE-CAT-01)."""

import uuid
from typing import Any

from httpx import AsyncClient

from app.modules.identity.domain import Role
from tests.conftest import Account, AccountFactory, make_course, make_subject


async def new_course(client: AsyncClient, author: Account, **fields: Any) -> dict[str, Any]:
    subject = await make_subject(client, f"Subject {uuid.uuid4().hex[:6]}", headers=author.headers)
    return await make_course(
        client, subject["id"], headers=author.headers, status="draft", **fields
    )


async def add_unit(
    client: AsyncClient, author: Account, course_id: str, title: str, **extra: Any
) -> Any:
    response = await client.post(
        f"/api/v1/courses/{course_id}/units", json={"title": title, **extra}, headers=author.headers
    )
    assert response.status_code == 201, response.text
    return response.json()


async def add_lesson(
    client: AsyncClient, author: Account, course_id: str, unit_id: str, title: str, **extra: Any
) -> dict[str, Any]:
    response = await client.post(
        f"/api/v1/courses/{course_id}/units/{unit_id}/lessons",
        json={"title": title, **extra},
        headers=author.headers,
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


def outline(draft: dict[str, Any]) -> list[tuple[str, list[str]]]:
    """[(unit title, [lesson titles])], and checks positions are 0..n-1 everywhere."""
    assert [u["position"] for u in draft["units"]] == list(range(len(draft["units"])))
    for unit in draft["units"]:
        assert [lesson["position"] for lesson in unit["lessons"]] == list(
            range(len(unit["lessons"]))
        )
    return [(u["title"], [lesson["title"] for lesson in u["lessons"]]) for u in draft["units"]]


async def get_draft(client: AsyncClient, author: Account, course_id: str) -> dict[str, Any]:
    response = await client.get(f"/api/v1/courses/{course_id}/draft", headers=author.headers)
    assert response.status_code == 200, response.text
    return dict(response.json())


async def test_build_a_course_outline(client: AsyncClient, teacher: Account) -> None:
    course = await new_course(client, teacher)
    cid = course["id"]
    draft = await get_draft(client, teacher, cid)
    assert draft["units"] == [] and draft["lesson_count"] == 0
    assert draft["published_version"] is None and draft["has_unpublished_changes"] is True

    await add_unit(client, teacher, cid, "Numbers")
    await add_unit(client, teacher, cid, "Shapes")
    first = await add_unit(client, teacher, cid, "Warm-up", position=0)  # insert at the top
    assert outline(first) == [("Warm-up", []), ("Numbers", []), ("Shapes", [])]

    numbers = first["units"][1]["id"]
    await add_lesson(client, teacher, cid, numbers, "Counting", estimated_minutes=15)
    await add_lesson(client, teacher, cid, numbers, "Adding", estimated_minutes=20)
    await add_lesson(
        client, teacher, cid, numbers, "Place value", position=1, difficulty="beginner"
    )
    draft = await get_draft(client, teacher, cid)
    assert outline(draft)[1] == ("Numbers", ["Counting", "Place value", "Adding"])
    assert draft["lesson_count"] == 3
    assert draft["estimated_minutes"] == 35

    huge = await add_unit(client, teacher, cid, "Last", position=99)  # clamped to the end
    assert outline(huge)[-1] == ("Last", [])


async def test_reorder_units_and_lessons(client: AsyncClient, teacher: Account) -> None:
    course = await new_course(client, teacher)
    cid = course["id"]
    for title in ("A", "B", "C"):
        draft = await add_unit(client, teacher, cid, title)
    ids = {u["title"]: u["id"] for u in draft["units"]}
    response = await client.put(
        f"/api/v1/courses/{cid}/units/order",
        json={"ids": [ids["C"], ids["A"], ids["B"]]},
        headers=teacher.headers,
    )
    assert response.status_code == 200
    assert [t for t, _ in outline(response.json())] == ["C", "A", "B"]

    for bad in (
        [ids["A"], ids["B"]],
        [ids["A"], ids["A"], ids["B"]],
        [ids["A"], ids["B"], str(uuid.uuid4())],
    ):
        wrong = await client.put(
            f"/api/v1/courses/{cid}/units/order", json={"ids": bad}, headers=teacher.headers
        )
        assert wrong.status_code == 422, bad

    for title in ("one", "two", "three"):
        lesson = await add_lesson(client, teacher, cid, ids["A"], title)
    lesson_ids = {
        item["title"]: item["id"]
        for item in (await get_draft(client, teacher, cid))["units"][1]["lessons"]
    }
    reordered = await client.put(
        f"/api/v1/courses/{cid}/units/{ids['A']}/lessons/order",
        json={"ids": [lesson_ids["three"], lesson_ids["one"], lesson_ids["two"]]},
        headers=teacher.headers,
    )
    assert outline(reordered.json())[1] == ("A", ["three", "one", "two"])
    assert lesson["title"] == "three"


async def test_move_lessons_within_and_between_units(client: AsyncClient, teacher: Account) -> None:
    course = await new_course(client, teacher)
    cid = course["id"]
    await add_unit(client, teacher, cid, "U1")
    draft = await add_unit(client, teacher, cid, "U2")
    u1, u2 = (u["id"] for u in draft["units"])
    lessons = {}
    for title in ("a", "b", "c", "d"):
        lessons[title] = (await add_lesson(client, teacher, cid, u1, title))["id"]
    lessons["x"] = (await add_lesson(client, teacher, cid, u2, "x"))["id"]

    async def move(
        title: str, unit: str, position: int | None = None
    ) -> list[tuple[str, list[str]]]:
        body: dict[str, Any] = {"unit_id": unit}
        if position is not None:
            body["position"] = position
        response = await client.post(
            f"/api/v1/courses/{cid}/lessons/{lessons[title]}/move",
            json=body,
            headers=teacher.headers,
        )
        assert response.status_code == 200, response.text
        return outline(response.json())

    assert await move("a", u1, 2) == [("U1", ["b", "c", "a", "d"]), ("U2", ["x"])]  # down
    assert await move("d", u1, 0) == [("U1", ["d", "b", "c", "a"]), ("U2", ["x"])]  # up
    assert await move("b", u2, 0) == [("U1", ["d", "c", "a"]), ("U2", ["b", "x"])]  # across
    assert await move("a", u2) == [("U1", ["d", "c"]), ("U2", ["b", "x", "a"])]  # to the end
    assert await move("x", u1, 99) == [("U1", ["d", "c", "x"]), ("U2", ["b", "a"])]  # clamped

    other_course = await new_course(client, teacher, title="Other")
    elsewhere = (await add_unit(client, teacher, other_course["id"], "Elsewhere"))["units"][0]["id"]
    response = await client.post(
        f"/api/v1/courses/{cid}/lessons/{lessons['c']}/move",
        json={"unit_id": elsewhere},
        headers=teacher.headers,
    )
    assert response.status_code == 404  # units of another course are not reachable


async def test_rename_and_delete_keep_positions_contiguous(
    client: AsyncClient, teacher: Account
) -> None:
    course = await new_course(client, teacher)
    cid = course["id"]
    for title in ("A", "B", "C"):
        draft = await add_unit(client, teacher, cid, title)
    a, b, _ = (u["id"] for u in draft["units"])
    for title in ("1", "2", "3"):
        lesson = await add_lesson(client, teacher, cid, a, title)

    renamed = await client.patch(
        f"/api/v1/courses/{cid}/units/{b}",
        json={"title": "Bee", "summary": "Second"},
        headers=teacher.headers,
    )
    assert renamed.json()["units"][1]["title"] == "Bee"
    assert renamed.json()["units"][1]["summary"] == "Second"
    assert (
        await client.patch(
            f"/api/v1/courses/{cid}/units/{b}", json={"title": None}, headers=teacher.headers
        )
    ).status_code == 422

    middle = (await get_draft(client, teacher, cid))["units"][0]["lessons"][1]["id"]
    assert (
        await client.delete(f"/api/v1/courses/{cid}/lessons/{middle}", headers=teacher.headers)
    ).status_code == 204
    assert outline(await get_draft(client, teacher, cid))[0] == ("A", ["1", "3"])

    assert (
        await client.delete(f"/api/v1/courses/{cid}/units/{b}", headers=teacher.headers)
    ).status_code == 204
    assert [t for t, _ in outline(await get_draft(client, teacher, cid))] == ["A", "C"]
    assert (
        await client.delete(f"/api/v1/courses/{cid}/units/{a}", headers=teacher.headers)
    ).status_code == 204
    assert outline(await get_draft(client, teacher, cid)) == [("C", [])]  # lessons went with it
    gone = await client.patch(
        f"/api/v1/courses/{cid}/lessons/{lesson['id']}",
        json={"title": "x"},
        headers=teacher.headers,
    )
    assert gone.status_code == 404


async def test_lesson_metadata_and_revisions(client: AsyncClient, teacher: Account) -> None:
    course = await new_course(client, teacher)
    cid = course["id"]
    unit = (await add_unit(client, teacher, cid, "U"))["units"][0]["id"]
    lesson = await add_lesson(client, teacher, cid, unit, "Draft title")
    assert lesson["revision"] == 1
    url = f"/api/v1/courses/{cid}/lessons/{lesson['id']}"

    updated = await client.patch(
        url,
        json={
            "title": "Final",
            "estimated_minutes": 25,
            "difficulty": "intermediate",
            "expected_revision": 1,
        },
        headers=teacher.headers,
    )
    assert updated.status_code == 200
    assert (updated.json()["title"], updated.json()["revision"]) == ("Final", 2)
    stale = await client.patch(
        url, json={"title": "Late", "expected_revision": 1}, headers=teacher.headers
    )
    assert stale.status_code == 409  # someone else saved in between
    assert "revision 2" in stale.json()["error"]["message"]
    for bad in (
        {"estimated_minutes": 0},
        {"estimated_minutes": 601},
        {"title": ""},
        {"difficulty": "hard"},
    ):
        assert (await client.patch(url, json=bad, headers=teacher.headers)).status_code == 422, bad


async def test_only_the_author_or_an_admin_edits_structure(
    client: AsyncClient, teacher: Account, admin: Account, make_account: AccountFactory
) -> None:
    course = await new_course(client, teacher)
    cid = course["id"]
    unit = (await add_unit(client, teacher, cid, "U"))["units"][0]["id"]
    other_teacher = await make_account(Role.TEACHER, teacher_verified=True)
    parent = await make_account(Role.PARENT)
    calls = [
        ("GET", f"/api/v1/courses/{cid}/draft", None),
        ("POST", f"/api/v1/courses/{cid}/units", {"title": "Hack"}),
        ("POST", f"/api/v1/courses/{cid}/units/{unit}/lessons", {"title": "Hack"}),
        ("PUT", f"/api/v1/courses/{cid}/units/order", {"ids": [unit]}),
    ]
    for method, path, body in calls:
        assert (await client.request(method, path, json=body)).status_code == 401
        # The draft course is invisible to others: "not found", not "forbidden".
        for outsider in (other_teacher, parent):
            response = await client.request(method, path, json=body, headers=outsider.headers)
            assert response.status_code == 404, (method, path)
        # Admins get past authorization (the reorder itself may then be invalid: 422).
        admin_response = await client.request(method, path, json=body, headers=admin.headers)
        assert admin_response.status_code not in (401, 403, 404), (method, path)

    missing = await client.get(f"/api/v1/courses/{uuid.uuid4()}/draft", headers=teacher.headers)
    assert missing.status_code == 404


async def test_published_course_structure_is_still_author_only(
    client: AsyncClient, teacher: Account, make_account: AccountFactory
) -> None:
    subject = await make_subject(client, headers=teacher.headers)
    course = await make_course(client, subject["id"], headers=teacher.headers)  # published
    other = await make_account(Role.TEACHER, teacher_verified=True)
    response = await client.post(
        f"/api/v1/courses/{course['id']}/units", json={"title": "Mine now"}, headers=other.headers
    )
    assert response.status_code == 403  # visible, but not theirs
