"""Administration (BE-USERS-03), authorization helpers (BE-AUTHZ-01), audit log (BE-AUTHZ-04)."""

import uuid
from collections.abc import Callable
from typing import Annotated, Any

import pytest
from fastapi import Depends
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.main import create_app
from app.modules.identity.deps import require_permission, require_role
from app.modules.identity.domain import Actor, Permission, Role
from tests.conftest import (
    PASSWORD,
    Account,
    AccountFactory,
    make_course,
    make_settings,
    make_subject,
)


async def audit_events(client: AsyncClient, admin: Account, **params: Any) -> list[dict[str, Any]]:
    response = await client.get("/api/v1/admin/audit-events", params=params, headers=admin.headers)
    assert response.status_code == 200, response.text
    return list(response.json()["items"])


# --- access to /admin ---------------------------------------------------------------------


async def test_admin_endpoints_need_a_platform_admin(
    client: AsyncClient, make_account: AccountFactory
) -> None:
    target = str(uuid.uuid4())
    calls = [
        ("GET", "/api/v1/admin/users", None),
        ("GET", f"/api/v1/admin/users/{target}", None),
        ("PATCH", f"/api/v1/admin/users/{target}", {"is_active": False}),
        ("PUT", f"/api/v1/admin/users/{target}/teacher-verification", {"verified": True}),
        ("POST", f"/api/v1/admin/users/{target}/impersonate", None),
        ("DELETE", f"/api/v1/admin/users/{target}/mfa", None),
        ("GET", "/api/v1/admin/audit-events", None),
    ]
    for method, url, body in calls:
        assert (await client.request(method, url, json=body)).status_code == 401, url
    for role in (Role.PARENT, Role.TEACHER, Role.STUDENT, Role.SCHOOL_ADMIN):
        account = await make_account(role)
        for method, url, body in calls:
            response = await client.request(method, url, json=body, headers=account.headers)
            assert response.status_code == 403, (role, url)
            assert response.json()["error"]["code"] == "forbidden"


# --- search and read ----------------------------------------------------------------------


async def test_search_users(
    client: AsyncClient, admin: Account, make_account: AccountFactory
) -> None:
    await make_account(Role.TEACHER, email="ada@example.com", name="Ada Lovelace")
    await make_account(Role.PARENT, email="grace@example.com", name="Grace Hopper")

    def emails(response: Any) -> list[str]:
        assert response.status_code == 200, response.text
        return sorted(u["email"] for u in response.json()["items"])

    url = "/api/v1/admin/users"
    assert emails(await client.get(url, params={"q": "lovelace"}, headers=admin.headers)) == [
        "ada@example.com"
    ]
    assert emails(await client.get(url, params={"q": "GRACE@"}, headers=admin.headers)) == [
        "grace@example.com"
    ]
    assert emails(await client.get(url, params={"role": "teacher"}, headers=admin.headers)) == [
        "ada@example.com"
    ]
    page = (await client.get(url, params={"limit": 2}, headers=admin.headers)).json()
    assert page["total"] == 3 and len(page["items"]) == 2
    assert (
        await client.get(url, params={"role": "wizard"}, headers=admin.headers)
    ).status_code == 422


async def test_get_user(client: AsyncClient, admin: Account, teacher: Account) -> None:
    user = (await client.get(f"/api/v1/admin/users/{teacher.id}", headers=admin.headers)).json()
    assert user["role"] == "teacher"
    assert user["teacher_verified"] is True
    assert user["deleted"] is False
    missing = await client.get(f"/api/v1/admin/users/{uuid.uuid4()}", headers=admin.headers)
    assert missing.status_code == 404


# --- role changes -------------------------------------------------------------------------


async def test_change_role_takes_effect_immediately(
    client: AsyncClient, admin: Account, make_account: AccountFactory
) -> None:
    parent = await make_account(Role.PARENT)
    subject = await make_subject(client, headers=admin.headers)
    draft = {"title": "T", "summary": "S", "grade_level": 2, "subject_id": subject["id"]}
    assert (
        await client.post("/api/v1/courses", json=draft, headers=parent.headers)
    ).status_code == 403

    response = await client.patch(
        f"/api/v1/admin/users/{parent.id}", json={"role": "teacher"}, headers=admin.headers
    )
    assert response.status_code == 200
    assert response.json()["role"] == "teacher"
    assert response.json()["teacher_verified"] is False  # a teacher profile was created

    # Same access token, new rights: the role is read from the database on every request.
    assert (
        await client.post("/api/v1/courses", json=draft, headers=parent.headers)
    ).status_code == 201

    [event] = await audit_events(client, admin, action="admin.role_changed")
    assert event["details"] == {"from": "parent", "to": "teacher"}
    assert event["actor_id"] == admin.id and event["entity_id"] == parent.id


async def test_role_change_rules(
    client: AsyncClient, admin: Account, make_account: AccountFactory
) -> None:
    student = await make_account(Role.STUDENT)
    parent = await make_account(Role.PARENT)
    url = "/api/v1/admin/users/{}"

    own = await client.patch(url.format(admin.id), json={"role": "parent"}, headers=admin.headers)
    assert own.status_code == 403  # cannot demote yourself (and lock the platform out)
    to_student = await client.patch(
        url.format(parent.id), json={"role": "student"}, headers=admin.headers
    )
    assert to_student.status_code == 403
    from_student = await client.patch(
        url.format(student.id), json={"role": "teacher"}, headers=admin.headers
    )
    assert from_student.status_code == 403
    invalid = await client.patch(
        url.format(parent.id), json={"role": "wizard"}, headers=admin.headers
    )
    assert invalid.status_code == 422
    missing = await client.patch(
        url.format(uuid.uuid4()), json={"role": "teacher"}, headers=admin.headers
    )
    assert missing.status_code == 404


# --- deactivation -------------------------------------------------------------------------


async def test_deactivate_signs_out_immediately_and_blocks_sign_in(
    client: AsyncClient, admin: Account, make_account: AccountFactory
) -> None:
    parent = await make_account(Role.PARENT)
    url = f"/api/v1/admin/users/{parent.id}"

    response = await client.patch(url, json={"is_active": False}, headers=admin.headers)
    assert response.status_code == 200 and response.json()["is_active"] is False
    assert (await client.get("/api/v1/me", headers=parent.headers)).status_code == 401
    refresh = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": parent.refresh_token}
    )
    assert refresh.status_code == 401
    login = await client.post(
        "/api/v1/auth/login", json={"email": parent.email, "password": PASSWORD}
    )
    assert login.status_code == 403

    await client.patch(url, json={"is_active": True}, headers=admin.headers)
    login = await client.post(
        "/api/v1/auth/login", json={"email": parent.email, "password": PASSWORD}
    )
    assert login.status_code == 200

    actions = [e["action"] for e in await audit_events(client, admin, entity_id=parent.id)]
    assert "admin.user_deactivated" in actions and "admin.user_reactivated" in actions

    self_lockout = await client.patch(
        f"/api/v1/admin/users/{admin.id}", json={"is_active": False}, headers=admin.headers
    )
    assert self_lockout.status_code == 403


# --- teacher verification -----------------------------------------------------------------


async def test_verifying_a_teacher_allows_publishing(
    client: AsyncClient, admin: Account, make_account: AccountFactory
) -> None:
    newcomer = await make_account(Role.TEACHER)
    subject = await make_subject(client, headers=admin.headers)
    course = await make_course(client, subject["id"], headers=newcomer.headers, status="draft")
    publish = {"status": "published"}
    course_url = f"/api/v1/courses/{course['id']}"
    assert (
        await client.patch(course_url, json=publish, headers=newcomer.headers)
    ).status_code == 403

    verify_url = f"/api/v1/admin/users/{newcomer.id}/teacher-verification"
    verified = await client.put(verify_url, json={"verified": True}, headers=admin.headers)
    assert verified.status_code == 200 and verified.json()["teacher_verified"] is True
    assert (
        await client.patch(course_url, json=publish, headers=newcomer.headers)
    ).status_code == 200

    unverified = await client.put(verify_url, json={"verified": False}, headers=admin.headers)
    assert unverified.json()["teacher_verified"] is False
    actions = [e["action"] for e in await audit_events(client, admin, entity_id=newcomer.id)]
    assert {"admin.teacher_verified", "admin.teacher_unverified"} <= set(actions)


async def test_only_teachers_can_be_verified(
    client: AsyncClient, admin: Account, make_account: AccountFactory
) -> None:
    parent = await make_account(Role.PARENT)
    response = await client.put(
        f"/api/v1/admin/users/{parent.id}/teacher-verification",
        json={"verified": True},
        headers=admin.headers,
    )
    assert response.status_code == 422
    bad_body = await client.put(
        f"/api/v1/admin/users/{parent.id}/teacher-verification", json={}, headers=admin.headers
    )
    assert bad_body.status_code == 422


# --- impersonation ------------------------------------------------------------------------


async def impersonate(client: AsyncClient, admin: Account, user_id: str) -> Any:
    return await client.post(f"/api/v1/admin/users/{user_id}/impersonate", headers=admin.headers)


async def test_impersonation_is_time_limited_and_attributed(
    client: AsyncClient, admin: Account, make_account: AccountFactory
) -> None:
    parent = await make_account(Role.PARENT)
    response = await impersonate(client, admin, parent.id)
    assert response.status_code == 200
    body = response.json()
    assert body["expires_in"] == 15 * 60
    assert "refresh_token" not in body  # cannot be extended
    as_parent = {"Authorization": f"Bearer {body['access_token']}"}

    me = (await client.get("/api/v1/me", headers=as_parent)).json()
    assert me["id"] == parent.id
    assert me["impersonated_by"] == admin.id

    # Acting as the parent works, and is recorded as done by the admin on their behalf.
    added = await client.post(
        "/api/v1/me/children", json={"display_name": "Kid"}, headers=as_parent
    )
    assert added.status_code == 201
    [child_event] = await audit_events(client, admin, action="family.child_added")
    assert child_event["actor_id"] == parent.id
    assert child_event["impersonator_id"] == admin.id

    [start] = await audit_events(client, admin, action="admin.impersonation_started")
    assert start["actor_id"] == admin.id and start["entity_id"] == parent.id


async def test_impersonation_cannot_touch_account_security(
    client: AsyncClient, admin: Account, make_account: AccountFactory
) -> None:
    parent = await make_account(Role.PARENT)
    token = (await impersonate(client, admin, parent.id)).json()["access_token"]
    as_parent = {"Authorization": f"Bearer {token}"}
    blocked = [
        ("POST", "/api/v1/me/password", {"current_password": PASSWORD, "new_password": "x" * 16}),
        ("POST", "/api/v1/me/email", {"new_email": "evil@example.com", "password": PASSWORD}),
        ("DELETE", "/api/v1/me", {"password": PASSWORD}),
        ("POST", "/api/v1/me/sessions/revoke-others", None),
    ]
    for method, url, body in blocked:
        response = await client.request(method, url, json=body, headers=as_parent)
        assert response.status_code == 403, url
    # No escalation: the impersonation token has the target's rights, not the admin's.
    assert (await client.get("/api/v1/admin/users", headers=as_parent)).status_code == 403


async def test_impersonation_rules(
    client: AsyncClient, admin: Account, make_account: AccountFactory
) -> None:
    other_admin = await make_account(Role.PLATFORM_ADMIN)
    assert (await impersonate(client, admin, other_admin.id)).status_code == 403
    assert (await impersonate(client, admin, admin.id)).status_code == 403
    assert (await impersonate(client, admin, str(uuid.uuid4()))).status_code == 404

    parent = await make_account(Role.PARENT)
    await client.patch(
        f"/api/v1/admin/users/{parent.id}", json={"is_active": False}, headers=admin.headers
    )
    assert (await impersonate(client, admin, parent.id)).status_code == 403


async def test_impersonation_session_can_be_ended(
    client: AsyncClient, admin: Account, make_account: AccountFactory
) -> None:
    parent = await make_account(Role.PARENT)
    token = (await impersonate(client, admin, parent.id)).json()["access_token"]
    as_parent = {"Authorization": f"Bearer {token}"}
    assert (await client.post("/api/v1/auth/logout", headers=as_parent)).status_code == 204
    assert (await client.get("/api/v1/me", headers=as_parent)).status_code == 401


# --- audit log ----------------------------------------------------------------------------


async def test_logins_are_audited_without_secrets(
    client: AsyncClient, admin: Account, make_account: AccountFactory
) -> None:
    parent = await make_account(Role.PARENT)
    await client.post(
        "/api/v1/auth/login", json={"email": parent.email, "password": "wrong-pw-1234"}
    )
    await client.post("/api/v1/auth/login", json={"email": "ghost@example.com", "password": "x"})

    [ok] = await audit_events(client, admin, action="auth.login", actor_id=parent.id)
    assert ok["entity_type"] == "session" and ok["ip"] == "127.0.0.1"
    failures = await audit_events(client, admin, action="auth.login_failed")
    assert {f["actor_id"] for f in failures} == {parent.id, None}  # unknown email: no actor

    everything = (await client.get("/api/v1/admin/audit-events", headers=admin.headers)).text
    for secret in (
        PASSWORD,
        "wrong-pw-1234",
        parent.email,
        "ghost@example.com",
        parent.access_token,
    ):
        assert secret not in everything


async def test_audit_log_is_append_only(
    engine: AsyncEngine, client: AsyncClient, admin: Account
) -> None:
    async with engine.connect() as conn:
        count = await conn.scalar(text("SELECT count(*) FROM audit_events"))
    assert count and count > 0
    for statement in ("UPDATE audit_events SET action = 'tampered'", "DELETE FROM audit_events"):
        with pytest.raises(DBAPIError, match="append-only"):
            async with engine.begin() as conn:
                await conn.execute(text(statement))


async def test_audit_search_validation(client: AsyncClient, admin: Account) -> None:
    response = await client.get(
        "/api/v1/admin/audit-events", params={"actor_id": "not-a-uuid"}, headers=admin.headers
    )
    assert response.status_code == 422


# --- the dependency helpers (BE-AUTHZ-01) -------------------------------------------------


@pytest.fixture
def helper_app_client(
    client_factory: Callable[[Settings], AsyncClient],
) -> Callable[..., AsyncClient]:
    """A test app with throwaway routes guarded by require_role / require_permission."""

    def build() -> AsyncClient:
        http = client_factory(make_settings())
        app = http._transport.app  # type: ignore[attr-defined]

        async def teachers_only(
            actor: Annotated[Actor, Depends(require_role(Role.TEACHER, Role.PLATFORM_ADMIN))],
        ) -> dict[str, str]:
            return {"role": actor.role.value}

        async def user_managers_only(
            actor: Annotated[Actor, Depends(require_permission(Permission.USERS_MANAGE))],
        ) -> dict[str, str]:
            return {"role": actor.role.value}

        app.add_api_route("/_role", teachers_only)
        app.add_api_route("/_permission", user_managers_only)
        return http

    return build


async def test_require_role_and_permission(
    helper_app_client: Callable[..., AsyncClient],
    make_account: AccountFactory,
    admin: Account,
    teacher: Account,
) -> None:
    http = helper_app_client()
    parent = await make_account(Role.PARENT)

    assert (await http.get("/_role")).status_code == 401
    assert (await http.get("/_role", headers=parent.headers)).status_code == 403
    assert (await http.get("/_role", headers=teacher.headers)).json() == {"role": "teacher"}
    assert (await http.get("/_permission", headers=teacher.headers)).status_code == 403
    assert (await http.get("/_permission", headers=admin.headers)).json() == {
        "role": "platform_admin"
    }


def test_create_app_accepts_default_transport() -> None:
    # Smoke check that production wiring (SMTP transport) builds without a test transport.
    app = create_app(make_settings())
    assert app.state.email_transport.__class__.__name__ == "SmtpTransport"
    assert isinstance(ASGITransport(app=app), ASGITransport)
