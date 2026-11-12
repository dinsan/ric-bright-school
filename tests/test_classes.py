"""Classes (teachers see only their own, BE-AUTHZ-03) and class-code picture sign-in
(Grades 1-3, BE-AUTH-10)."""

import uuid
from datetime import datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.modules.identity.domain import PICTURE_HARD_LOCK_FAILURES, PICTURE_THROTTLE_THRESHOLD, Role
from tests.conftest import Account, AccountFactory, make_subject

PICTURES = ["cat", "sun", "apple"]


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def new_class(client: AsyncClient, teacher: Account, **fields: Any) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/classes", json={"name": "Class 1B", **fields}, headers=teacher.headers
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


async def new_student(
    client: AsyncClient, teacher: Account, class_id: str, **fields: Any
) -> dict[str, Any]:
    response = await client.post(
        f"/api/v1/classes/{class_id}/students",
        json={"display_name": "Ada", **fields},
        headers=teacher.headers,
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


async def picture_login(
    client: AsyncClient, code: str, student_id: str, pictures: list[str]
) -> Any:
    return await client.post(
        "/api/v1/auth/login/picture",
        json={"class_code": code, "student_id": student_id, "pictures": pictures},
    )


# --- teachers see only their own classes ---------------------------------------------------


async def test_teacher_creates_and_lists_own_classes(
    client: AsyncClient, teacher: Account, admin: Account, make_account: AccountFactory
) -> None:
    subject = await make_subject(client, "Mathematics", headers=admin.headers)
    created = await new_class(
        client, teacher, grade_level=2, subject_id=subject["id"], school_year="2026-2027"
    )
    assert created["picture_sign_in"] is True  # on by default for Grades 1-3
    assert len(created["join_code"]) == 9 and created["join_code"][4] == "-"
    assert created["subject"]["slug"] == "mathematics"
    assert created["teacher_id"] == teacher.id
    assert (await new_class(client, teacher, name="Grade 6", grade_level=6))[
        "picture_sign_in"
    ] is False

    other = await make_account(Role.TEACHER)
    await new_class(client, other, name="Not yours")

    mine = (await client.get("/api/v1/classes", headers=teacher.headers)).json()
    assert sorted(c["name"] for c in mine) == ["Class 1B", "Grade 6"]
    everything = (await client.get("/api/v1/classes", headers=admin.headers)).json()
    assert len(everything) == 3


async def test_other_teachers_classes_are_invisible(
    client: AsyncClient, teacher: Account, make_account: AccountFactory
) -> None:
    cls = await new_class(client, teacher)
    other = await make_account(Role.TEACHER)
    for method, path, body in (
        ("GET", f"/api/v1/classes/{cls['id']}", None),
        ("PATCH", f"/api/v1/classes/{cls['id']}", {"name": "Mine now"}),
        ("DELETE", f"/api/v1/classes/{cls['id']}", None),
        ("GET", f"/api/v1/classes/{cls['id']}/students", None),
        ("POST", f"/api/v1/classes/{cls['id']}/students", {"display_name": "X"}),
        ("POST", f"/api/v1/classes/{cls['id']}/join-code", None),
    ):
        response = await client.request(method, path, json=body, headers=other.headers)
        assert response.status_code == 404, (method, path)  # not even "forbidden"


async def test_class_access_by_role(
    client: AsyncClient, teacher: Account, make_account: AccountFactory
) -> None:
    assert (await client.get("/api/v1/classes")).status_code == 401
    parent = await make_account(Role.PARENT)
    assert (await client.get("/api/v1/classes", headers=parent.headers)).status_code == 403
    response = await client.post("/api/v1/classes", json={"name": "X"}, headers=parent.headers)
    assert response.status_code == 403
    missing = await client.get(f"/api/v1/classes/{uuid.uuid4()}", headers=teacher.headers)
    assert missing.status_code == 404


async def test_class_validation(client: AsyncClient, teacher: Account) -> None:
    for body in (
        {"name": ""},
        {"name": "A", "grade_level": 13},
        {"name": "A", "school_year": "2026"},
        {"name": "A", "subject_id": "nope"},
    ):
        response = await client.post("/api/v1/classes", json=body, headers=teacher.headers)
        assert response.status_code == 422, body
    unknown_subject = await client.post(
        "/api/v1/classes",
        json={"name": "A", "subject_id": str(uuid.uuid4())},
        headers=teacher.headers,
    )
    assert unknown_subject.status_code == 404

    cls = await new_class(client, teacher)
    url = f"/api/v1/classes/{cls['id']}"
    assert (
        await client.patch(url, json={"name": None}, headers=teacher.headers)
    ).status_code == 422
    updated = await client.patch(
        url, json={"name": "1B Owls", "grade_level": None}, headers=teacher.headers
    )
    assert updated.status_code == 200
    assert (updated.json()["name"], updated.json()["grade_level"]) == ("1B Owls", None)


# --- students: join with the code, roster --------------------------------------------------


async def test_student_joins_with_code_and_sees_the_class(
    client: AsyncClient, teacher: Account, make_account: AccountFactory
) -> None:
    cls = await new_class(client, teacher, grade_level=7)
    student = await make_account(Role.STUDENT)

    joined = await client.post(
        "/api/v1/classes/join",
        json={"code": cls["join_code"].lower().replace("-", "")},  # typed sloppily
        headers=student.headers,
    )
    assert joined.status_code == 200, joined.text
    assert joined.json()["join_code"] is None  # students do not get the code back

    listed = (await client.get("/api/v1/classes", headers=student.headers)).json()
    assert [c["id"] for c in listed] == [cls["id"]]
    # Students see the class, but not the roster.
    assert (
        await client.get(f"/api/v1/classes/{cls['id']}", headers=student.headers)
    ).status_code == 200
    roster = await client.get(f"/api/v1/classes/{cls['id']}/students", headers=student.headers)
    assert roster.status_code == 404

    students = (
        await client.get(f"/api/v1/classes/{cls['id']}/students", headers=teacher.headers)
    ).json()
    assert [s["id"] for s in students] == [student.id]

    bad = await client.post(
        "/api/v1/classes/join", json={"code": "AAAA-BBBB"}, headers=student.headers
    )
    assert bad.status_code == 404
    parent = await make_account(Role.PARENT)
    not_student = await client.post(
        "/api/v1/classes/join", json={"code": cls["join_code"]}, headers=parent.headers
    )
    assert not_student.status_code == 403


async def test_rotating_or_archiving_stops_the_old_code(
    client: AsyncClient, teacher: Account, make_account: AccountFactory
) -> None:
    cls = await new_class(client, teacher, grade_level=7)
    student = await make_account(Role.STUDENT)
    old_code = cls["join_code"]

    rotated = await client.post(f"/api/v1/classes/{cls['id']}/join-code", headers=teacher.headers)
    new_code = rotated.json()["join_code"]
    assert new_code != old_code
    assert (
        await client.post("/api/v1/classes/join", json={"code": old_code}, headers=student.headers)
    ).status_code == 404

    assert (
        await client.delete(f"/api/v1/classes/{cls['id']}", headers=teacher.headers)
    ).status_code == 204
    assert (
        await client.delete(f"/api/v1/classes/{cls['id']}", headers=teacher.headers)
    ).status_code == 204
    assert (
        await client.post("/api/v1/classes/join", json={"code": new_code}, headers=student.headers)
    ).status_code == 404
    # An archived class can no longer be changed.
    assert (
        await client.patch(
            f"/api/v1/classes/{cls['id']}", json={"name": "x"}, headers=teacher.headers
        )
    ).status_code == 403


async def test_teacher_creates_and_removes_students(client: AsyncClient, teacher: Account) -> None:
    cls = await new_class(client, teacher, grade_level=5)
    student = await new_student(
        client, teacher, cls["id"], username="ada.l", password="ada-password"
    )
    assert student["grade_level"] == 5  # from the class
    assert student["age_band"] == "middle"
    assert student["sign_in_methods"] == ["username"]
    assert student["managed_by_guardians"] is True

    login = await client.post(
        "/api/v1/auth/login/username", json={"username": "ada.l", "password": "ada-password"}
    )
    assert login.status_code == 200

    url = f"/api/v1/classes/{cls['id']}/students/{student['id']}"
    assert (await client.delete(url, headers=teacher.headers)).status_code == 204
    assert (await client.delete(url, headers=teacher.headers)).status_code == 404
    roster = (
        await client.get(f"/api/v1/classes/{cls['id']}/students", headers=teacher.headers)
    ).json()
    assert roster == []


async def test_teacher_sets_sign_in_only_for_own_students(
    client: AsyncClient, teacher: Account, make_account: AccountFactory
) -> None:
    cls = await new_class(client, teacher, grade_level=1)
    student = await new_student(client, teacher, cls["id"])
    assert student["sign_in_methods"] == []

    url = f"/api/v1/classes/{cls['id']}/students/{student['id']}/sign-in"
    response = await client.put(url, json={"picture_password": PICTURES}, headers=teacher.headers)
    assert response.status_code == 200
    assert response.json()["sign_in_methods"] == ["picture"]

    # Another teacher, or this teacher through a class the child is not in: not found.
    other = await make_account(Role.TEACHER)
    assert (
        await client.put(url, json={"picture_password": PICTURES}, headers=other.headers)
    ).status_code == 404
    other_class = await new_class(client, teacher, name="Other", grade_level=1)
    wrong_class = f"/api/v1/classes/{other_class['id']}/students/{student['id']}/sign-in"
    assert (
        await client.put(wrong_class, json={"picture_password": PICTURES}, headers=teacher.headers)
    ).status_code == 404

    # A parent's child who joined the class: the teacher may set up pictures too, because
    # the child has no email of their own and is in the teacher's class.
    parent = await make_account(Role.PARENT)
    kid = (
        await client.post(
            "/api/v1/me/children",
            json={
                "display_name": "Pat",
                "grade_level": 1,
                "username": "pat",
                "password": "pat-password",
            },
            headers=parent.headers,
        )
    ).json()
    kid_tokens = (
        await client.post(
            "/api/v1/auth/login/username", json={"username": "pat", "password": "pat-password"}
        )
    ).json()
    joined = await client.post(
        "/api/v1/classes/join",
        json={"code": cls["join_code"]},
        headers=bearer(kid_tokens["access_token"]),
    )
    assert joined.status_code == 200
    kid_url = f"/api/v1/classes/{cls['id']}/students/{kid['id']}/sign-in"
    response = await client.put(
        kid_url, json={"picture_password": PICTURES}, headers=teacher.headers
    )
    assert response.status_code == 200
    assert response.json()["sign_in_methods"] == ["username", "picture"]


# --- picture sign-in ----------------------------------------------------------------------


@pytest.fixture
async def picture_class(client: AsyncClient, teacher: Account) -> dict[str, Any]:
    cls = await new_class(client, teacher, grade_level=1)
    ada = await new_student(
        client, teacher, cls["id"], display_name="Ada", picture_password=PICTURES
    )
    await new_student(client, teacher, cls["id"], display_name="Ben")  # no pictures yet
    return {"class": cls, "ada": ada}


async def test_classroom_page_lists_children_with_pictures(
    client: AsyncClient, picture_class: dict[str, Any]
) -> None:
    code = picture_class["class"]["join_code"]
    page = await client.get(f"/api/v1/auth/class-sign-in/{code}")
    assert page.status_code == 200
    body = page.json()
    assert body["class_name"] == "Class 1B"
    assert [s["display_name"] for s in body["students"]] == ["Ada"]  # Ben has no pictures
    assert "cat" in body["pictures"] and len(body["pictures"]) == 16
    assert body["picture_password_length"] == 3
    assert (await client.get("/api/v1/auth/class-sign-in/ZZZZ-ZZZZ")).status_code == 404


async def test_picture_sign_in_gives_a_school_day_session(
    client: AsyncClient, picture_class: dict[str, Any]
) -> None:
    code, ada = picture_class["class"]["join_code"], picture_class["ada"]
    response = await picture_login(client, code, ada["id"], PICTURES)
    assert response.status_code == 200, response.text
    tokens = response.json()

    me = (await client.get("/api/v1/me", headers=bearer(tokens["access_token"]))).json()
    assert (me["display_name"], me["age_band"], me["sign_in_methods"]) == (
        "Ada",
        "early",
        ["picture"],
    )
    [session] = (
        await client.get("/api/v1/me/sessions", headers=bearer(tokens["access_token"]))
    ).json()
    assert session["sign_in_method"] == "picture"
    expires = datetime.fromisoformat(session["expires_at"])
    assert expires - datetime.fromisoformat(session["created_at"]) <= timedelta(hours=8)

    # Refreshing never extends a Grade 1-3 session.
    refreshed = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert refreshed.status_code == 200
    [again] = (
        await client.get("/api/v1/me/sessions", headers=bearer(refreshed.json()["access_token"]))
    ).json()
    assert datetime.fromisoformat(again["expires_at"]) == expires


async def expire_backoff_locks(engine: AsyncEngine) -> None:
    """Pretend the backoff waits are over (but not a hard lock, which is ~100 years)."""
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE login_throttles SET locked_until = now() - interval '1 second' "
                "WHERE locked_until < now() + interval '1 day'"
            )
        )


async def test_wrong_pictures_then_backoff_then_hard_lock(
    client: AsyncClient, teacher: Account, picture_class: dict[str, Any], engine: AsyncEngine
) -> None:
    code, ada = picture_class["class"]["join_code"], picture_class["ada"]
    wrong = ["dog", "dog", "dog"]
    first = await picture_login(client, code, ada["id"], wrong)
    assert first.status_code == 401
    assert first.json()["error"]["code"] == "invalid_credentials"
    for _ in range(PICTURE_THROTTLE_THRESHOLD - 1):
        await picture_login(client, code, ada["id"], wrong)
    assert (await picture_login(client, code, ada["id"], PICTURES)).status_code == 429

    # Keep failing, waiting out each backoff, until the hard lock.
    for _ in range(PICTURE_HARD_LOCK_FAILURES - PICTURE_THROTTLE_THRESHOLD):
        await expire_backoff_locks(engine)
        assert (await picture_login(client, code, ada["id"], wrong)).status_code == 401
    await expire_backoff_locks(engine)  # does not lift the hard lock
    locked = await picture_login(client, code, ada["id"], PICTURES)  # right pictures, still no
    assert locked.status_code == 429
    assert int(locked.headers["retry-after"]) > 60 * 60 * 24  # only an adult can unlock

    # The teacher sets new pictures: unlocked.
    await client.put(
        f"/api/v1/classes/{picture_class['class']['id']}/students/{ada['id']}/sign-in",
        json={"picture_password": ["moon", "star", "sun"]},
        headers=teacher.headers,
    )
    assert (
        await picture_login(client, code, ada["id"], ["moon", "star", "sun"])
    ).status_code == 200


async def test_picture_sign_in_needs_the_right_class(
    client: AsyncClient, teacher: Account, picture_class: dict[str, Any]
) -> None:
    ada = picture_class["ada"]
    other = await new_class(client, teacher, name="Other 1st grade", grade_level=1)
    assert (await picture_login(client, other["join_code"], ada["id"], PICTURES)).status_code == 404
    assert (await picture_login(client, "AAAA-BBBB", ada["id"], PICTURES)).status_code == 404

    # Turning picture sign-in off for the class closes the door.
    cls = picture_class["class"]
    await client.patch(
        f"/api/v1/classes/{cls['id']}", json={"picture_sign_in": False}, headers=teacher.headers
    )
    assert (await picture_login(client, cls["join_code"], ada["id"], PICTURES)).status_code == 404


async def test_guessing_class_codes_is_blocked(client: AsyncClient) -> None:
    for n in range(20):
        response = await client.get(f"/api/v1/auth/class-sign-in/AAAA-{n:04d}")
        assert response.status_code == 404
    assert (await client.get("/api/v1/auth/class-sign-in/AAAA-9999")).status_code == 429


async def test_picture_password_validation(
    client: AsyncClient, picture_class: dict[str, Any]
) -> None:
    code, ada = picture_class["class"]["join_code"], picture_class["ada"]
    for pictures in (["cat", "sun"], ["cat", "sun", "apple", "dog"], ["cat", "sun", "dragon"]):
        assert (await picture_login(client, code, ada["id"], pictures)).status_code == 422
