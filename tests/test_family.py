"""Parents and children (BE-USERS-02) and the parent ownership policy (BE-AUTHZ-03)."""

import uuid
from datetime import timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.db.mixins import utcnow
from app.modules.identity.domain import LinkStatus, Role
from app.modules.identity.models import ParentChildLink, User
from tests.conftest import PASSWORD, AccountFactory, RecordingTransport, login

CHILD = {"display_name": "Sam", "grade_level": 3, "date_of_birth": "2018-05-01"}


async def add_child(client: AsyncClient, headers: dict[str, str], **overrides: Any) -> Any:
    return await client.post("/api/v1/me/children", json={**CHILD, **overrides}, headers=headers)


async def test_parent_adds_a_managed_child(
    client: AsyncClient, make_account: AccountFactory, outbox: RecordingTransport
) -> None:
    parent = await make_account(Role.PARENT)
    response = await add_child(client, parent.headers, school_name="Hill Primary")
    assert response.status_code == 201
    child = response.json()
    assert child["display_name"] == "Sam"
    assert child["grade_level"] == 3
    assert child["school_name"] == "Hill Primary"
    assert child["email"] is None
    assert child["is_managed"] is True
    assert child["can_sign_in"] is False  # no email: managed by the parent only
    assert outbox.messages == []

    listed = (await client.get("/api/v1/me/children", headers=parent.headers)).json()
    assert [c["id"] for c in listed] == [child["id"]]


async def test_child_with_email_activates_and_signs_in(
    client: AsyncClient, make_account: AccountFactory, outbox: RecordingTransport
) -> None:
    parent = await make_account(Role.PARENT, name="Pat")
    # Grade 4 and up may have an email (Grades 1-3 sign in with pictures or a username).
    child = (await add_child(client, parent.headers, email="Sam@Example.com", grade_level=5)).json()
    assert child["email"] == "sam@example.com"
    assert child["can_sign_in"] is False  # not until a password is set

    invite = outbox.last("activate_account")
    assert invite.to == "sam@example.com"
    assert "Pat created a Lumina account for you" in invite.body
    activated = await client.post(
        "/api/v1/auth/password/reset",
        json={"token": outbox.token("activate_account"), "new_password": "sam-loves-dinosaurs"},
    )
    assert activated.status_code == 204

    tokens = await login(client, "sam@example.com", "sam-loves-dinosaurs")
    me = (
        await client.get(
            "/api/v1/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
        )
    ).json()
    assert me["role"] == "student"
    assert me["email_verified"] is True  # the activation link proved the address
    assert me["student_profile"]["grade_level"] == 5

    listed = (await client.get("/api/v1/me/children", headers=parent.headers)).json()
    assert listed[0]["can_sign_in"] is True


async def test_add_child_validation_and_conflicts(
    client: AsyncClient, make_account: AccountFactory
) -> None:
    parent = await make_account(Role.PARENT)
    await make_account(Role.TEACHER, email="taken@example.com")

    for overrides in (
        {"grade_level": 13},
        {"date_of_birth": (utcnow() + timedelta(days=2)).date().isoformat()},
        {"display_name": ""},
        {"email": "nope"},
    ):
        assert (await add_child(client, parent.headers, **overrides)).status_code == 422, overrides
    taken = await add_child(client, parent.headers, email="taken@example.com", grade_level=6)
    assert taken.status_code == 409


async def test_young_children_cannot_have_an_email_sign_in(
    client: AsyncClient, make_account: AccountFactory
) -> None:
    parent = await make_account(Role.PARENT)
    response = await add_child(client, parent.headers, grade_level=2, email="tiny@example.com")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "domain_validation_failed"


async def test_only_parents_manage_children(
    client: AsyncClient, make_account: AccountFactory
) -> None:
    assert (await client.get("/api/v1/me/children")).status_code == 401
    for role in (Role.TEACHER, Role.STUDENT):
        account = await make_account(role)
        assert (await add_child(client, account.headers)).status_code == 403
        assert (await client.get("/api/v1/me/children", headers=account.headers)).status_code == 403


async def test_parents_only_see_their_own_children(
    client: AsyncClient, make_account: AccountFactory
) -> None:
    alice = await make_account(Role.PARENT)
    bob = await make_account(Role.PARENT)
    alices_child = (await add_child(client, alice.headers)).json()

    assert (await client.get("/api/v1/me/children", headers=bob.headers)).json() == []
    response = await client.put(
        "/api/v1/me/active-child", json={"child_id": alices_child["id"]}, headers=bob.headers
    )
    assert response.status_code == 404  # not "forbidden": the child is invisible to Bob


# --- link an existing student account with a code ------------------------------------------


async def student_code(client: AsyncClient, headers: dict[str, str]) -> str:
    response = await client.post("/api/v1/me/link-code", headers=headers)
    assert response.status_code == 201
    return str(response.json()["code"])


async def test_link_existing_student_with_code(
    client: AsyncClient, make_account: AccountFactory
) -> None:
    student = await make_account(Role.STUDENT)
    parent = await make_account(Role.PARENT)
    code = await student_code(client, student.headers)
    assert len(code) == 9 and code[4] == "-"

    # Typed sloppily: case and separators do not matter.
    response = await client.post(
        "/api/v1/me/children/link",
        json={"code": f" {code.lower().replace('-', ' ')} "},
        headers=parent.headers,
    )
    assert response.status_code == 200, response.text
    linked = response.json()
    assert linked["id"] == student.id
    assert linked["is_managed"] is False
    assert linked["can_sign_in"] is True

    again = await client.post(
        "/api/v1/me/children/link", json={"code": code}, headers=parent.headers
    )
    assert again.status_code == 400  # codes are single-use


async def test_link_code_rules(client: AsyncClient, make_account: AccountFactory) -> None:
    parent = await make_account(Role.PARENT)
    assert (await client.post("/api/v1/me/link-code", headers=parent.headers)).status_code == 403

    bad = await client.post(
        "/api/v1/me/children/link", json={"code": "AAAA-BBBB"}, headers=parent.headers
    )
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "invalid_token"

    student = await make_account(Role.STUDENT)
    old = await student_code(client, student.headers)
    new = await student_code(client, student.headers)  # a new code replaces the old one
    stale = await client.post(
        "/api/v1/me/children/link", json={"code": old}, headers=parent.headers
    )
    assert stale.status_code == 400
    fresh = await client.post(
        "/api/v1/me/children/link", json={"code": new}, headers=parent.headers
    )
    assert fresh.status_code == 200

    teacher = await make_account(Role.TEACHER)
    code = await student_code(client, student.headers)
    assert (
        await client.post("/api/v1/me/children/link", json={"code": code}, headers=teacher.headers)
    ).status_code == 403


async def test_two_parents_can_share_a_child(
    client: AsyncClient, make_account: AccountFactory
) -> None:
    student = await make_account(Role.STUDENT)
    for _ in range(2):
        parent = await make_account(Role.PARENT)
        code = await student_code(client, student.headers)
        response = await client.post(
            "/api/v1/me/children/link", json={"code": code}, headers=parent.headers
        )
        assert response.status_code == 200


# --- switch active child ------------------------------------------------------------------


async def test_switch_active_child_per_session(
    client: AsyncClient, make_account: AccountFactory
) -> None:
    parent = await make_account(Role.PARENT)
    first = (await add_child(client, parent.headers, display_name="Ann")).json()
    second = (await add_child(client, parent.headers, display_name="Ben")).json()

    for child in (first, second):
        response = await client.put(
            "/api/v1/me/active-child", json={"child_id": child["id"]}, headers=parent.headers
        )
        assert response.status_code == 204
        me = (await client.get("/api/v1/me", headers=parent.headers)).json()
        assert me["active_child_id"] == child["id"]

    # Another device keeps its own choice.
    other = await login(client, parent.email)
    other_me = (
        await client.get("/api/v1/me", headers={"Authorization": f"Bearer {other['access_token']}"})
    ).json()
    assert other_me["active_child_id"] is None

    cleared = await client.put(
        "/api/v1/me/active-child", json={"child_id": None}, headers=parent.headers
    )
    assert cleared.status_code == 204
    assert (await client.get("/api/v1/me", headers=parent.headers)).json()[
        "active_child_id"
    ] is None

    unknown = await client.put(
        "/api/v1/me/active-child", json={"child_id": str(uuid.uuid4())}, headers=parent.headers
    )
    assert unknown.status_code == 404


# --- deleting a parent account ------------------------------------------------------------


async def test_deleting_a_parent_removes_children_only_they_manage(
    client: AsyncClient, make_account: AccountFactory, session: AsyncSession
) -> None:
    parent = await make_account(Role.PARENT)
    co_parent = await make_account(Role.PARENT)
    alone = (await add_child(client, parent.headers, display_name="Alone")).json()
    shared_student = await make_account(Role.STUDENT)
    code = (await client.post("/api/v1/me/link-code", headers=shared_student.headers)).json()[
        "code"
    ]
    await client.post("/api/v1/me/children/link", json={"code": code}, headers=parent.headers)
    code = (await client.post("/api/v1/me/link-code", headers=shared_student.headers)).json()[
        "code"
    ]
    await client.post("/api/v1/me/children/link", json={"code": code}, headers=co_parent.headers)

    response = await client.request(
        "DELETE", "/api/v1/me", json={"password": PASSWORD}, headers=parent.headers
    )
    assert response.status_code == 204

    managed = await session.get(User, uuid.UUID(alone["id"]))
    assert managed is not None and managed.deleted_at is not None  # nobody else could reach it
    kept = await session.scalar(select(User).where(User.id == uuid.UUID(shared_student.id)))
    assert kept is not None and kept.deleted_at is None  # has its own login and another parent
    assert [
        c["id"] for c in (await client.get("/api/v1/me/children", headers=co_parent.headers)).json()
    ] == [shared_student.id]


async def test_revoked_link_grants_nothing(
    client: AsyncClient, make_account: AccountFactory, engine: AsyncEngine
) -> None:
    parent = await make_account(Role.PARENT)
    child = (await add_child(client, parent.headers)).json()
    async with engine.begin() as conn:
        await conn.execute(update(ParentChildLink).values(status=LinkStatus.REVOKED))

    assert (await client.get("/api/v1/me/children", headers=parent.headers)).json() == []
    response = await client.put(
        "/api/v1/me/active-child", json={"child_id": child["id"]}, headers=parent.headers
    )
    assert response.status_code == 404
