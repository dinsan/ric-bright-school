"""The signed-in user's own account (BE-USERS-01) and sessions (BE-AUTH-09)."""

import uuid

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.domain import Role
from app.modules.identity.models import User
from tests.conftest import (
    PASSWORD,
    Account,
    AccountFactory,
    RecordingTransport,
    login,
    make_subject,
)

NEW_PASSWORD = "another-long-passphrase"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- profile ------------------------------------------------------------------------------


async def test_get_me(client: AsyncClient, make_account: AccountFactory) -> None:
    account = await make_account(Role.PARENT, name="Grace")
    me = (await client.get("/api/v1/me", headers=account.headers)).json()
    assert me["id"] == account.id
    assert me["display_name"] == "Grace"
    assert me["permissions"] == ["children:manage"]
    assert me["locale"] == "en" and me["timezone"] == "UTC"
    assert me["impersonated_by"] is None


async def test_me_requires_sign_in(client: AsyncClient) -> None:
    response = await client.get("/api/v1/me")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_update_me(client: AsyncClient, make_account: AccountFactory) -> None:
    account = await make_account(Role.PARENT)
    response = await client.patch(
        "/api/v1/me",
        json={"display_name": " Grace H. ", "locale": "fr-CA", "timezone": "America/Toronto"},
        headers=account.headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert (body["display_name"], body["locale"], body["timezone"]) == (
        "Grace H.",
        "fr-CA",
        "America/Toronto",
    )


async def test_update_me_validation(client: AsyncClient, make_account: AccountFactory) -> None:
    account = await make_account(Role.PARENT)
    for body in (
        {"timezone": "Nowhere/Land"},
        {"display_name": None},
        {"role": "platform_admin"},  # cannot promote yourself
        {"email": "new@example.com"},  # has its own verified flow
    ):
        response = await client.patch("/api/v1/me", json=body, headers=account.headers)
        assert response.status_code == 422, body


# --- password -----------------------------------------------------------------------------


async def test_change_password_signs_out_other_devices(
    client: AsyncClient, make_account: AccountFactory, outbox: RecordingTransport
) -> None:
    account = await make_account(Role.PARENT)
    other = await login(client, account.email)

    response = await client.post(
        "/api/v1/me/password",
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
        headers=account.headers,
    )
    assert response.status_code == 204
    assert (await client.get("/api/v1/me", headers=account.headers)).status_code == 200
    assert (
        await client.get("/api/v1/me", headers=bearer(other["access_token"]))
    ).status_code == 401
    await login(client, account.email, NEW_PASSWORD)
    assert outbox.last("password_changed").to == account.email


async def test_change_password_needs_the_current_one(
    client: AsyncClient, make_account: AccountFactory
) -> None:
    account = await make_account(Role.PARENT)
    wrong = await client.post(
        "/api/v1/me/password",
        json={"current_password": "not-my-password", "new_password": NEW_PASSWORD},
        headers=account.headers,
    )
    assert wrong.status_code == 401
    assert wrong.json()["error"]["code"] == "invalid_credentials"

    weak = await client.post(
        "/api/v1/me/password",
        json={"current_password": PASSWORD, "new_password": "short"},
        headers=account.headers,
    )
    assert weak.status_code == 422


# --- email change -------------------------------------------------------------------------


async def test_change_email_requires_confirming_the_new_address(
    client: AsyncClient, make_account: AccountFactory, outbox: RecordingTransport
) -> None:
    account = await make_account(Role.PARENT)
    response = await client.post(
        "/api/v1/me/email",
        json={"new_email": "New.Address@Example.com", "password": PASSWORD},
        headers=account.headers,
    )
    assert response.status_code == 202
    # Nothing changes until the link is used.
    assert (await client.get("/api/v1/me", headers=account.headers)).json()[
        "email"
    ] == account.email

    confirm = outbox.last("change_email")
    assert confirm.to == "new.address@example.com"
    done = await client.post(
        "/api/v1/auth/email/confirm", json={"token": outbox.token("change_email")}
    )
    assert done.status_code == 204

    me = (await client.get("/api/v1/me", headers=account.headers)).json()
    assert me["email"] == "new.address@example.com"
    assert outbox.last("email_changed").to == account.email  # old address is told
    await login(client, "new.address@example.com")


async def test_change_email_to_a_taken_address_reveals_nothing(
    client: AsyncClient, make_account: AccountFactory, outbox: RecordingTransport
) -> None:
    account = await make_account(Role.PARENT)
    await make_account(Role.TEACHER, email="taken@example.com")
    response = await client.post(
        "/api/v1/me/email",
        json={"new_email": "taken@example.com", "password": PASSWORD},
        headers=account.headers,
    )
    assert response.status_code == 202
    assert "change_email" not in outbox.kinds()


async def test_change_email_race_is_a_conflict(
    client: AsyncClient, make_account: AccountFactory, outbox: RecordingTransport
) -> None:
    account = await make_account(Role.PARENT)
    await client.post(
        "/api/v1/me/email",
        json={"new_email": "wanted@example.com", "password": PASSWORD},
        headers=account.headers,
    )
    await make_account(Role.TEACHER, email="wanted@example.com")  # someone else got it first
    response = await client.post(
        "/api/v1/auth/email/confirm", json={"token": outbox.token("change_email")}
    )
    assert response.status_code == 409


async def test_change_email_checks(client: AsyncClient, make_account: AccountFactory) -> None:
    account = await make_account(Role.PARENT)
    wrong_password = await client.post(
        "/api/v1/me/email",
        json={"new_email": "x@example.com", "password": "not-my-password"},
        headers=account.headers,
    )
    assert wrong_password.status_code == 401
    same = await client.post(
        "/api/v1/me/email",
        json={"new_email": account.email.upper(), "password": PASSWORD},
        headers=account.headers,
    )
    assert same.status_code == 422


# --- sessions -----------------------------------------------------------------------------


async def test_list_and_revoke_sessions(client: AsyncClient, make_account: AccountFactory) -> None:
    account = await make_account(Role.PARENT)
    tablet = await login(client, account.email)

    sessions = (await client.get("/api/v1/me/sessions", headers=account.headers)).json()
    assert len(sessions) == 2
    current = [s for s in sessions if s["current"]]
    assert len(current) == 1
    other_id = next(s["id"] for s in sessions if not s["current"])

    response = await client.delete(f"/api/v1/me/sessions/{other_id}", headers=account.headers)
    assert response.status_code == 204
    assert (
        await client.get("/api/v1/me", headers=bearer(tablet["access_token"]))
    ).status_code == 401
    remaining = (await client.get("/api/v1/me/sessions", headers=account.headers)).json()
    assert [s["current"] for s in remaining] == [True]


async def test_cannot_revoke_someone_elses_session(
    client: AsyncClient, make_account: AccountFactory
) -> None:
    alice = await make_account(Role.PARENT)
    bob = await make_account(Role.PARENT)
    bobs_session = (await client.get("/api/v1/me/sessions", headers=bob.headers)).json()[0]["id"]

    response = await client.delete(f"/api/v1/me/sessions/{bobs_session}", headers=alice.headers)
    assert response.status_code == 404
    assert (await client.get("/api/v1/me", headers=bob.headers)).status_code == 200

    missing = await client.delete(f"/api/v1/me/sessions/{uuid.uuid4()}", headers=alice.headers)
    assert missing.status_code == 404


async def test_sign_out_everywhere_else(client: AsyncClient, make_account: AccountFactory) -> None:
    account = await make_account(Role.PARENT)
    devices = [await login(client, account.email) for _ in range(2)]

    response = await client.post("/api/v1/me/sessions/revoke-others", headers=account.headers)
    assert response.json() == {"revoked": 2}
    for device in devices:
        assert (
            await client.get("/api/v1/me", headers=bearer(device["access_token"]))
        ).status_code == 401
    assert (await client.get("/api/v1/me", headers=account.headers)).status_code == 200


# --- delete account -----------------------------------------------------------------------


async def test_delete_account_anonymises_and_signs_out(
    client: AsyncClient, make_account: AccountFactory, session: AsyncSession
) -> None:
    account = await make_account(Role.PARENT)
    wrong = await client.request(
        "DELETE", "/api/v1/me", json={"password": "not-my-password"}, headers=account.headers
    )
    assert wrong.status_code == 401

    response = await client.request(
        "DELETE", "/api/v1/me", json={"password": PASSWORD}, headers=account.headers
    )
    assert response.status_code == 204
    assert (await client.get("/api/v1/me", headers=account.headers)).status_code == 401
    gone = await client.post(
        "/api/v1/auth/login", json={"email": account.email, "password": PASSWORD}
    )
    assert gone.status_code == 401

    user = await session.scalar(select(User).where(User.id == uuid.UUID(account.id)))
    assert user is not None  # kept for audit references...
    assert user.email is None and user.password_hash is None  # ...but no personal data
    assert user.display_name == "Deleted user"
    assert user.deleted_at is not None and not user.is_active


async def test_deleted_email_can_register_again(
    client: AsyncClient, make_account: AccountFactory, outbox: RecordingTransport
) -> None:
    account = await make_account(Role.PARENT)
    await client.request(
        "DELETE", "/api/v1/me", json={"password": PASSWORD}, headers=account.headers
    )
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": account.email,
            "password": PASSWORD,
            "display_name": "New",
            "role": "parent",
        },
    )
    assert outbox.last("verify_email").to == account.email


# --- teacher profile ----------------------------------------------------------------------


async def test_teacher_updates_own_profile(
    client: AsyncClient, teacher: Account, admin: Account
) -> None:
    maths = await make_subject(client, "Mathematics", headers=admin.headers)
    science = await make_subject(client, "Science", headers=admin.headers)
    response = await client.patch(
        "/api/v1/me/teacher-profile",
        json={"bio": "Maths teacher, 12 years.", "subject_ids": [science["id"], maths["id"]]},
        headers=teacher.headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["bio"] == "Maths teacher, 12 years."
    assert body["is_verified"] is True
    assert [s["name"] for s in body["subjects"]] == ["Mathematics", "Science"]

    me = (await client.get("/api/v1/me", headers=teacher.headers)).json()
    assert me["teacher_profile"]["bio"] == "Maths teacher, 12 years."


async def test_teacher_profile_errors(
    client: AsyncClient, teacher: Account, make_account: AccountFactory
) -> None:
    unknown = await client.patch(
        "/api/v1/me/teacher-profile",
        json={"subject_ids": [str(uuid.uuid4())]},
        headers=teacher.headers,
    )
    assert unknown.status_code == 404

    parent = await make_account(Role.PARENT)
    forbidden = await client.patch(
        "/api/v1/me/teacher-profile", json={"bio": "hi"}, headers=parent.headers
    )
    assert forbidden.status_code == 403

    invalid = await client.patch(
        "/api/v1/me/teacher-profile", json={"bio": "x" * 2001}, headers=teacher.headers
    )
    assert invalid.status_code == 422
