"""Child sign-in methods by age band (BE-AUTH-10): usernames, passwords, picture passwords."""

import uuid
from datetime import datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncEngine

from app.modules.identity.domain import (
    SIGN_IN_POLICIES,
    THROTTLE_ACCOUNT_THRESHOLD,
    AgeBand,
    Role,
    SignInMethod,
    age_band,
)
from app.modules.identity.models import StudentProfile, User
from tests.conftest import HASHER, PASSWORD, AccountFactory

KID_PASSWORD = "jumping-frogs"  # 13 characters: fine for every student band


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def add_child(client: AsyncClient, headers: dict[str, str], **fields: Any) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/me/children", json={"display_name": "Kid", **fields}, headers=headers
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


async def username_login(client: AsyncClient, username: str, password: str) -> Any:
    return await client.post(
        "/api/v1/auth/login/username", json={"username": username, "password": password}
    )


# --- the age-band table -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("role", "grade", "band"),
    [
        (Role.STUDENT, 1, AgeBand.EARLY),
        (Role.STUDENT, 3, AgeBand.EARLY),
        (Role.STUDENT, 4, AgeBand.MIDDLE),
        (Role.STUDENT, 8, AgeBand.MIDDLE),
        (Role.STUDENT, None, AgeBand.MIDDLE),
        (Role.STUDENT, 9, AgeBand.TEEN),
        (Role.STUDENT, 12, AgeBand.TEEN),
        (Role.PARENT, None, AgeBand.ADULT),
        (Role.TEACHER, None, AgeBand.ADULT),
    ],
)
def test_age_bands(role: Role, grade: int | None, band: AgeBand) -> None:
    assert age_band(role, grade) == band


def test_sign_in_policies_by_band() -> None:
    early, middle, teen, adult = (SIGN_IN_POLICIES[b] for b in AgeBand)
    assert early.methods == {SignInMethod.PICTURE, SignInMethod.USERNAME}
    assert SignInMethod.EMAIL not in early.methods
    assert SignInMethod.PICTURE not in middle.methods
    assert SignInMethod.GOOGLE not in middle.methods  # social sign-in from Grade 9
    assert SignInMethod.GOOGLE in teen.methods
    assert SignInMethod.USERNAME not in adult.methods
    assert [p.password_min_length for p in (early, middle, teen, adult)] == [8, 8, 10, 12]
    assert not early.sliding and early.session_lifetime == timedelta(hours=8)
    assert middle.sliding and teen.sliding and adult.sliding


# --- username + password ------------------------------------------------------------------


async def test_parent_sets_username_and_child_signs_in(
    client: AsyncClient, make_account: AccountFactory
) -> None:
    parent = await make_account(Role.PARENT)
    child = await add_child(
        client, parent.headers, grade_level=5, username="Sam.K", password=KID_PASSWORD
    )
    assert child["username"] == "sam.k"  # stored in lower case
    assert child["age_band"] == "middle"
    assert child["sign_in_methods"] == ["username"]
    assert child["can_sign_in"] is True

    response = await username_login(client, "SAM.K", KID_PASSWORD)  # case-insensitive
    assert response.status_code == 200, response.text
    tokens = response.json()
    me = (await client.get("/api/v1/me", headers=bearer(tokens["access_token"]))).json()
    assert (me["role"], me["age_band"], me["username"]) == ("student", "middle", "sam.k")

    # Middle band: a 7-day session.
    [session] = (
        await client.get("/api/v1/me/sessions", headers=bearer(tokens["access_token"]))
    ).json()
    assert session["sign_in_method"] == "username"
    lifetime = datetime.fromisoformat(session["expires_at"]) - datetime.fromisoformat(
        session["created_at"]
    )
    assert timedelta(days=6, hours=23) < lifetime <= timedelta(days=7)


async def test_wrong_username_or_password_look_the_same(
    client: AsyncClient, make_account: AccountFactory
) -> None:
    parent = await make_account(Role.PARENT)
    await add_child(client, parent.headers, grade_level=6, username="ava", password=KID_PASSWORD)
    wrong = await username_login(client, "ava", "not-the-password")
    unknown = await username_login(client, "nobody", KID_PASSWORD)
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["error"]["message"] == unknown.json()["error"]["message"]


async def test_username_sign_in_is_throttled(
    client: AsyncClient, make_account: AccountFactory
) -> None:
    parent = await make_account(Role.PARENT)
    await add_child(client, parent.headers, grade_level=6, username="leo", password=KID_PASSWORD)
    for _ in range(THROTTLE_ACCOUNT_THRESHOLD):
        await username_login(client, "leo", "wrong-password")
    locked = await username_login(client, "leo", KID_PASSWORD)
    assert locked.status_code == 429
    assert int(locked.headers["retry-after"]) > 0


async def test_password_length_depends_on_the_age_band(
    client: AsyncClient, make_account: AccountFactory
) -> None:
    parent = await make_account(Role.PARENT)
    nine = "ninechars"  # 9 characters
    ok = await client.post(
        "/api/v1/me/children",
        json={"display_name": "Mia", "grade_level": 7, "username": "mia", "password": nine},
        headers=parent.headers,
    )
    assert ok.status_code == 201  # Grades 4-8: 8 characters are enough

    teen = await client.post(
        "/api/v1/me/children",
        json={"display_name": "Tom", "grade_level": 10, "username": "tom", "password": nine},
        headers=parent.headers,
    )
    assert teen.status_code == 422  # Grades 9-12: at least 10
    assert "10 characters" in teen.json()["error"]["message"]

    too_short = await client.post(
        "/api/v1/me/children",
        json={"display_name": "Z", "grade_level": 2, "username": "zed", "password": "short"},
        headers=parent.headers,
    )
    assert too_short.status_code == 422


async def test_password_needs_a_username(client: AsyncClient, make_account: AccountFactory) -> None:
    parent = await make_account(Role.PARENT)
    response = await client.post(
        "/api/v1/me/children",
        json={"display_name": "Nia", "grade_level": 5, "password": KID_PASSWORD},
        headers=parent.headers,
    )
    assert response.status_code == 422
    assert "username" in response.json()["error"]["message"]


async def test_usernames_are_unique(client: AsyncClient, make_account: AccountFactory) -> None:
    parent = await make_account(Role.PARENT)
    await add_child(client, parent.headers, grade_level=5, username="max", password=KID_PASSWORD)
    response = await client.post(
        "/api/v1/me/children",
        json={
            "display_name": "Max 2",
            "grade_level": 5,
            "username": "MAX",
            "password": KID_PASSWORD,
        },
        headers=parent.headers,
    )
    assert response.status_code == 409
    for bad in ("a@b", "ab", "-max", "max!"):
        response = await client.post(
            "/api/v1/me/children",
            json={"display_name": "X", "grade_level": 5, "username": bad, "password": KID_PASSWORD},
            headers=parent.headers,
        )
        assert response.status_code == 422, bad


async def test_new_password_from_a_parent_signs_the_child_out(
    client: AsyncClient, make_account: AccountFactory
) -> None:
    parent = await make_account(Role.PARENT)
    child = await add_child(
        client, parent.headers, grade_level=5, username="ivy", password=KID_PASSWORD
    )
    tokens = (await username_login(client, "ivy", KID_PASSWORD)).json()

    response = await client.put(
        f"/api/v1/me/children/{child['id']}/sign-in",
        json={"password": "brand-new-secret"},
        headers=parent.headers,
    )
    assert response.status_code == 200
    assert (
        await client.get("/api/v1/me", headers=bearer(tokens["access_token"]))
    ).status_code == 401
    assert (await username_login(client, "ivy", KID_PASSWORD)).status_code == 401
    assert (await username_login(client, "ivy", "brand-new-secret")).status_code == 200


async def test_a_student_changes_their_own_password(
    client: AsyncClient, make_account: AccountFactory
) -> None:
    parent = await make_account(Role.PARENT)
    await add_child(client, parent.headers, grade_level=8, username="zoe", password=KID_PASSWORD)
    tokens = (await username_login(client, "zoe", KID_PASSWORD)).json()
    response = await client.post(
        "/api/v1/me/password",
        json={"current_password": KID_PASSWORD, "new_password": "zoe-picks-this"},
        headers=bearer(tokens["access_token"]),
    )
    assert response.status_code == 204
    assert (await username_login(client, "zoe", "zoe-picks-this")).status_code == 200


# --- who may set up a child's sign-in -----------------------------------------------------


async def test_only_the_childs_parent_sets_up_sign_in(
    client: AsyncClient, make_account: AccountFactory
) -> None:
    parent = await make_account(Role.PARENT)
    stranger = await make_account(Role.PARENT)
    child = await add_child(client, parent.headers, grade_level=5)
    url = f"/api/v1/me/children/{child['id']}/sign-in"
    body = {"username": "taken-over", "password": KID_PASSWORD}

    assert (await client.put(url, json=body)).status_code == 401
    assert (await client.put(url, json=body, headers=stranger.headers)).status_code == 404
    teacher = await make_account(Role.TEACHER)
    assert (await client.put(url, json=body, headers=teacher.headers)).status_code == 403
    missing = f"/api/v1/me/children/{uuid.uuid4()}/sign-in"
    assert (await client.put(missing, json=body, headers=parent.headers)).status_code == 404
    assert (await client.put(url, json={"pictures": []}, headers=parent.headers)).status_code == 422


async def test_students_with_their_own_email_manage_their_own_password(
    client: AsyncClient, make_account: AccountFactory
) -> None:
    parent = await make_account(Role.PARENT)
    child = await add_child(client, parent.headers, grade_level=9, email="teen@example.com")
    response = await client.put(
        f"/api/v1/me/children/{child['id']}/sign-in",
        json={"username": "teen", "password": "parent-chosen-1"},
        headers=parent.headers,
    )
    assert response.status_code == 403


async def test_email_child_cannot_also_get_a_password_from_the_parent(
    client: AsyncClient, make_account: AccountFactory
) -> None:
    parent = await make_account(Role.PARENT)
    response = await client.post(
        "/api/v1/me/children",
        json={
            "display_name": "Eve",
            "grade_level": 9,
            "email": "eve@example.com",
            "username": "eve",
            "password": KID_PASSWORD,
        },
        headers=parent.headers,
    )
    assert response.status_code == 422


# --- methods refused by the age band -------------------------------------------------------


async def test_picture_passwords_are_only_for_grades_1_to_3(
    client: AsyncClient, make_account: AccountFactory
) -> None:
    parent = await make_account(Role.PARENT)
    response = await client.post(
        "/api/v1/me/children",
        json={"display_name": "Big", "grade_level": 5, "picture_password": ["cat", "sun", "hat"]},
        headers=parent.headers,
    )
    assert response.status_code == 422
    assert "Grades 1 to 3" in response.json()["error"]["message"]


async def test_young_child_with_an_email_cannot_use_it(
    client: AsyncClient, make_account: AccountFactory, engine: AsyncEngine
) -> None:
    """If a student moves down to Grade 2 (or data was imported), email sign-in stops."""
    parent = await make_account(Role.PARENT)
    child = await add_child(client, parent.headers, grade_level=5, email="kid@example.com")
    async with engine.begin() as conn:
        await conn.execute(
            update(User)
            .where(User.id == uuid.UUID(child["id"]))
            .values(
                password_hash=HASHER.hash(KID_PASSWORD),
                email_verified_at=datetime.now().astimezone(),
            )
        )
        await conn.execute(
            update(StudentProfile)
            .where(StudentProfile.user_id == uuid.UUID(child["id"]))
            .values(grade_level=2)
        )
    response = await client.post(
        "/api/v1/auth/login", json={"email": "kid@example.com", "password": KID_PASSWORD}
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


async def test_adults_do_not_sign_in_with_a_username(
    client: AsyncClient, make_account: AccountFactory, engine: AsyncEngine
) -> None:
    parent = await make_account(Role.PARENT)
    async with engine.begin() as conn:
        await conn.execute(
            update(User).where(User.id == uuid.UUID(parent.id)).values(username="grownup")
        )
    assert (await username_login(client, "grownup", PASSWORD)).status_code == 403
