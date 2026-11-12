"""Registration, verification, sign-in, tokens and sessions (BE-AUTH-05..08)."""

import uuid
from datetime import timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.db.mixins import utcnow
from app.modules.identity.domain import THROTTLE_ACCOUNT_THRESHOLD, Role
from app.modules.identity.models import RefreshToken, User, UserToken
from app.modules.identity.security import create_access_token
from tests.conftest import (
    PASSWORD,
    Account,
    AccountFactory,
    RecordingTransport,
    login,
    make_settings,
)

REGISTER = {
    "email": "Ada.Lovelace@Example.com",
    "password": PASSWORD,
    "display_name": "Ada Lovelace",
    "role": "parent",
}


async def register(client: AsyncClient, **overrides: Any) -> Any:
    return await client.post("/api/v1/auth/register", json={**REGISTER, **overrides})


# --- registration and email verification --------------------------------------------------


async def test_register_verify_then_login(client: AsyncClient, outbox: RecordingTransport) -> None:
    response = await register(client)
    assert response.status_code == 202

    # Cannot sign in before the address is confirmed.
    early = await client.post(
        "/api/v1/auth/login", json={"email": "ada.lovelace@example.com", "password": PASSWORD}
    )
    assert early.status_code == 403
    assert early.json()["error"]["code"] == "email_not_verified"

    email = outbox.last("verify_email")
    assert email.to == "ada.lovelace@example.com"  # stored and used in lower case
    assert "http://web.test/verify-email?token=" in email.body
    verified = await client.post(
        "/api/v1/auth/verify-email", json={"token": outbox.token("verify_email")}
    )
    assert verified.status_code == 204

    # Email is case-insensitive at sign-in too.
    tokens = await login(client, "ADA.LOVELACE@example.COM")
    me = await client.get(
        "/api/v1/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert me.status_code == 200
    assert me.json()["email"] == "ada.lovelace@example.com"
    assert me.json()["role"] == "parent"
    assert me.json()["email_verified"] is True


async def test_register_does_not_reveal_existing_accounts(
    client: AsyncClient, outbox: RecordingTransport, make_account: AccountFactory
) -> None:
    await make_account(Role.TEACHER, email="taken@example.com")

    new = await register(client, email="fresh@example.com")
    taken = await register(client, email="TAKEN@example.com")
    assert new.status_code == taken.status_code == 202
    assert new.json() == taken.json()  # identical answers

    # The owner of the existing account is told instead, by email.
    assert outbox.last("account_exists").to == "taken@example.com"
    assert outbox.last("verify_email").to == "fresh@example.com"


async def test_teacher_registration_creates_an_unverified_teacher_profile(
    client: AsyncClient, outbox: RecordingTransport
) -> None:
    await register(client, email="t@example.com", role="teacher")
    await client.post("/api/v1/auth/verify-email", json={"token": outbox.token("verify_email")})
    tokens = await login(client, "t@example.com")
    me = (
        await client.get(
            "/api/v1/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
        )
    ).json()
    assert me["teacher_profile"] == {"bio": None, "is_verified": False, "subjects": []}
    assert "courses:author" in me["permissions"]


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"email": "not-an-email"}, "email"),
        ({"password": "short"}, "password"),
        ({"password": "x" * 129}, "password"),
        ({"role": "student"}, "role"),  # students are added by parents
        ({"role": "platform_admin"}, "role"),
        ({"timezone": "Mars/Olympus"}, "timezone"),
        ({"locale": "english"}, "locale"),
        ({"display_name": ""}, "display_name"),
    ],
)
async def test_register_validation(
    client: AsyncClient, overrides: dict[str, Any], field: str
) -> None:
    response = await register(client, **overrides)
    assert response.status_code == 422
    assert any(d["field"] == f"body.{field}" for d in response.json()["error"]["details"])


async def test_password_must_not_contain_the_email(client: AsyncClient) -> None:
    response = await register(client, email="rosalind@example.com", password="rosalind-2026!")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "domain_validation_failed"


async def test_verification_token_is_single_use_and_expires(
    client: AsyncClient, outbox: RecordingTransport, engine: AsyncEngine
) -> None:
    await register(client)
    token = outbox.token("verify_email")
    assert (
        await client.post("/api/v1/auth/verify-email", json={"token": token})
    ).status_code == 204
    reused = await client.post("/api/v1/auth/verify-email", json={"token": token})
    assert reused.status_code == 400
    assert reused.json()["error"]["code"] == "invalid_token"

    await register(client, email="late@example.com")
    late = outbox.token("verify_email", "late@example.com")
    async with engine.begin() as conn:
        await conn.execute(update(UserToken).values(expires_at=utcnow() - timedelta(seconds=1)))
    expired = await client.post("/api/v1/auth/verify-email", json={"token": late})
    assert expired.status_code == 400

    garbage = await client.post("/api/v1/auth/verify-email", json={"token": "nope"})
    assert garbage.status_code == 400


async def test_resend_verification_has_a_cooldown_and_no_enumeration(
    client: AsyncClient, outbox: RecordingTransport, engine: AsyncEngine
) -> None:
    await register(client)
    assert outbox.kinds().count("verify_email") == 1

    unknown = await client.post(
        "/api/v1/auth/verify-email/resend", json={"email": "nobody@example.com"}
    )
    soon = await client.post("/api/v1/auth/verify-email/resend", json={"email": REGISTER["email"]})
    assert unknown.status_code == soon.status_code == 202
    assert unknown.json() == soon.json()
    assert outbox.kinds().count("verify_email") == 1  # within the 60 s cooldown

    async with engine.begin() as conn:
        await conn.execute(update(UserToken).values(created_at=utcnow() - timedelta(minutes=2)))
    await client.post("/api/v1/auth/verify-email/resend", json={"email": REGISTER["email"]})
    assert outbox.kinds().count("verify_email") == 2

    # Only the newest link works.
    first, _latest = [m for m in outbox.messages if m.kind == "verify_email"]
    old_token = first.body.split("token=")[1].split()[0]
    assert (
        await client.post("/api/v1/auth/verify-email", json={"token": old_token})
    ).status_code == 400
    assert (
        await client.post("/api/v1/auth/verify-email", json={"token": outbox.token("verify_email")})
    ).status_code == 204


# --- sign in ------------------------------------------------------------------------------


async def test_login_wrong_password_and_unknown_email_look_the_same(
    client: AsyncClient, make_account: AccountFactory
) -> None:
    account = await make_account(Role.PARENT, sign_in=False)
    wrong = await client.post(
        "/api/v1/auth/login", json={"email": account.email, "password": "wrong-password-123"}
    )
    unknown = await client.post(
        "/api/v1/auth/login", json={"email": "ghost@example.com", "password": PASSWORD}
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["error"]["code"] == unknown.json()["error"]["code"] == "invalid_credentials"
    assert wrong.json()["error"]["message"] == unknown.json()["error"]["message"]


async def test_deactivated_account_cannot_sign_in(
    client: AsyncClient, make_account: AccountFactory, engine: AsyncEngine
) -> None:
    account = await make_account(Role.PARENT, sign_in=False)
    async with engine.begin() as conn:
        await conn.execute(update(User).values(is_active=False))
    response = await client.post(
        "/api/v1/auth/login", json={"email": account.email, "password": PASSWORD}
    )
    assert response.status_code == 403


async def test_login_response_shape(client: AsyncClient, make_account: AccountFactory) -> None:
    account = await make_account(Role.PARENT, sign_in=False)
    tokens = await login(client, account.email)
    assert tokens["token_type"] == "bearer"
    assert tokens["expires_in"] == 15 * 60
    assert tokens["access_token"] and tokens["refresh_token"]


async def test_argon2id_hash_is_stored_never_the_password(
    client: AsyncClient, session: AsyncSession
) -> None:
    await register(client)
    stored = await session.scalar(select(User.password_hash))
    assert stored is not None
    assert stored.startswith("$argon2id$")
    assert PASSWORD not in stored


# --- throttling (BE-AUTH-08) --------------------------------------------------------------


async def test_repeated_failures_lock_the_account_with_backoff(
    client: AsyncClient, make_account: AccountFactory, engine: AsyncEngine
) -> None:
    account = await make_account(Role.PARENT, sign_in=False)
    bad = {"email": account.email, "password": "wrong-password-123"}
    for _ in range(THROTTLE_ACCOUNT_THRESHOLD):
        assert (await client.post("/api/v1/auth/login", json=bad)).status_code == 401

    locked = await client.post(
        "/api/v1/auth/login", json={"email": account.email, "password": PASSWORD}
    )
    assert locked.status_code == 429  # even the right password is refused while locked
    assert locked.json()["error"]["code"] == "rate_limited"
    assert 1 <= int(locked.headers["retry-after"]) <= 30

    # Once the lock expires, one more failure locks for twice as long.
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE login_throttles SET locked_until = now() - interval '1 s'"))
    assert (await client.post("/api/v1/auth/login", json=bad)).status_code == 401
    again = await client.post("/api/v1/auth/login", json=bad)
    assert again.status_code == 429
    assert 30 < int(again.headers["retry-after"]) <= 60


async def test_lockout_is_per_account_and_cleared_by_success(
    client: AsyncClient, make_account: AccountFactory
) -> None:
    victim = await make_account(Role.PARENT, sign_in=False)
    other = await make_account(Role.PARENT, sign_in=False)
    bad = {"email": victim.email, "password": "wrong-password-123"}
    for _ in range(THROTTLE_ACCOUNT_THRESHOLD - 1):
        await client.post("/api/v1/auth/login", json=bad)
    await login(client, victim.email)  # success resets the account's counter
    for _ in range(THROTTLE_ACCOUNT_THRESHOLD - 1):
        assert (await client.post("/api/v1/auth/login", json=bad)).status_code == 401

    await login(client, other.email)  # other accounts are unaffected


async def test_unknown_emails_are_throttled_too(client: AsyncClient) -> None:
    # Otherwise the lockout itself would reveal which addresses have accounts.
    bad = {"email": "ghost@example.com", "password": "wrong-password-123"}
    for _ in range(THROTTLE_ACCOUNT_THRESHOLD):
        await client.post("/api/v1/auth/login", json=bad)
    assert (await client.post("/api/v1/auth/login", json=bad)).status_code == 429


async def test_per_ip_limit(
    client: AsyncClient, make_account: AccountFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.modules.identity.service.THROTTLE_IP_THRESHOLD", 3)
    account = await make_account(Role.PARENT, sign_in=False)
    for n in range(3):  # three different accounts from the same IP
        await client.post(
            "/api/v1/auth/login", json={"email": f"x{n}@example.com", "password": "wrong-pw-1234"}
        )
    blocked = await client.post(
        "/api/v1/auth/login", json={"email": account.email, "password": PASSWORD}
    )
    assert blocked.status_code == 429


async def test_throttle_table_stores_no_emails(client: AsyncClient, session: AsyncSession) -> None:
    await client.post(
        "/api/v1/auth/login", json={"email": "secret@example.com", "password": "wrong-pw-1234"}
    )
    keys = (await session.execute(text("SELECT key FROM login_throttles"))).scalars().all()
    assert keys
    assert not any("secret" in key or "127.0.0.1" in key for key in keys)


# --- access tokens ------------------------------------------------------------------------


async def test_expired_access_token_is_rejected(
    client: AsyncClient, make_account: AccountFactory
) -> None:
    account = await make_account(Role.PARENT)
    me = await client.get("/api/v1/me", headers=account.headers)
    session_id = uuid.UUID(
        (await client.get("/api/v1/me/sessions", headers=account.headers)).json()[0]["id"]
    )
    assert me.status_code == 200

    expired = create_access_token(
        make_settings(),
        user_id=uuid.UUID(account.id),
        session_id=session_id,
        role=Role.PARENT,
        ttl=timedelta(minutes=15),
        now=utcnow() - timedelta(minutes=16),
    )
    response = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {expired}"})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


async def test_tampered_or_foreign_tokens_are_rejected(
    client: AsyncClient, make_account: AccountFactory
) -> None:
    account = await make_account(Role.PARENT)
    head, payload, signature = account.access_token.split(".")
    tampered = f"{head}.{payload}.{signature[:-4]}AAAA"
    foreign = create_access_token(
        make_settings(secret_key="another-deployment-secret-key-0123456789"),
        user_id=uuid.UUID(account.id),
        session_id=uuid.uuid4(),
        role=Role.PLATFORM_ADMIN,
        ttl=timedelta(minutes=5),
    )
    for token in (tampered, foreign, "", "a.b.c"):
        response = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401, token


async def test_role_in_token_is_not_trusted(
    client: AsyncClient, make_account: AccountFactory
) -> None:
    """The database decides the role: a (validly signed) token claiming more is ignored."""
    parent = await make_account(Role.PARENT)
    session_id = uuid.UUID(
        (await client.get("/api/v1/me/sessions", headers=parent.headers)).json()[0]["id"]
    )
    claims_admin = create_access_token(
        make_settings(),
        user_id=uuid.UUID(parent.id),
        session_id=session_id,
        role=Role.PLATFORM_ADMIN,
        ttl=timedelta(minutes=5),
    )
    response = await client.get(
        "/api/v1/admin/users", headers={"Authorization": f"Bearer {claims_admin}"}
    )
    assert response.status_code == 403


# --- refresh: rotation, reuse detection, expiry -------------------------------------------


async def refresh(client: AsyncClient, token: str) -> Any:
    return await client.post("/api/v1/auth/refresh", json={"refresh_token": token})


async def test_refresh_rotates_both_tokens(
    client: AsyncClient, make_account: AccountFactory
) -> None:
    account = await make_account(Role.PARENT)
    response = await refresh(client, account.refresh_token)
    assert response.status_code == 200
    rotated = response.json()
    assert rotated["refresh_token"] != account.refresh_token
    assert rotated["access_token"] != account.access_token

    me = await client.get(
        "/api/v1/me", headers={"Authorization": f"Bearer {rotated['access_token']}"}
    )
    assert me.status_code == 200
    # Still the same session (device), not a new one.
    sessions = await client.get(
        "/api/v1/me/sessions", headers={"Authorization": f"Bearer {rotated['access_token']}"}
    )
    assert len(sessions.json()) == 1


async def test_reusing_an_old_refresh_token_revokes_the_whole_family(
    client: AsyncClient, make_account: AccountFactory, admin: Account
) -> None:
    account = await make_account(Role.PARENT)
    stolen = account.refresh_token
    legit = (await refresh(client, stolen)).json()  # the real user refreshes first

    replay = await refresh(client, stolen)  # the attacker replays the old token
    assert replay.status_code == 401

    # Everything from that sign-in is now dead: the newest refresh and access tokens too.
    assert (await refresh(client, legit["refresh_token"])).status_code == 401
    dead = await client.get(
        "/api/v1/me", headers={"Authorization": f"Bearer {legit['access_token']}"}
    )
    assert dead.status_code == 401

    audit = await client.get(
        "/api/v1/admin/audit-events",
        params={"action": "auth.refresh_token_reused"},
        headers=admin.headers,
    )
    assert audit.json()["total"] == 1


async def test_reuse_does_not_affect_other_sessions(
    client: AsyncClient, make_account: AccountFactory
) -> None:
    account = await make_account(Role.PARENT)
    laptop = await login(client, account.email)
    await refresh(client, account.refresh_token)
    await refresh(client, account.refresh_token)  # reuse on the phone session
    assert (await refresh(client, laptop["refresh_token"])).status_code == 200


async def test_expired_refresh_token_is_rejected(
    client: AsyncClient, make_account: AccountFactory, engine: AsyncEngine
) -> None:
    account = await make_account(Role.PARENT)
    async with engine.begin() as conn:
        await conn.execute(update(RefreshToken).values(expires_at=utcnow() - timedelta(seconds=1)))
    response = await refresh(client, account.refresh_token)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_unknown_refresh_token_is_rejected(client: AsyncClient) -> None:
    assert (await refresh(client, "made-up-token")).status_code == 401
    assert (await client.post("/api/v1/auth/refresh", json={})).status_code == 422


async def test_refresh_tokens_are_stored_hashed(
    client: AsyncClient, make_account: AccountFactory, session: AsyncSession
) -> None:
    account = await make_account(Role.PARENT)
    stored = (await session.scalars(select(RefreshToken.token_hash))).all()
    assert account.refresh_token not in stored
    assert all(len(h) == 64 for h in stored)


# --- logout -------------------------------------------------------------------------------


async def test_logout_invalidates_access_and_refresh_tokens(
    client: AsyncClient, make_account: AccountFactory
) -> None:
    account = await make_account(Role.PARENT)
    other_device = await login(client, account.email)

    assert (await client.post("/api/v1/auth/logout", headers=account.headers)).status_code == 204

    # The access token stops working immediately, not after 15 minutes.
    assert (await client.get("/api/v1/me", headers=account.headers)).status_code == 401
    assert (await refresh(client, account.refresh_token)).status_code == 401
    # Only this session ended.
    assert (await refresh(client, other_device["refresh_token"])).status_code == 200


async def test_logout_requires_sign_in(client: AsyncClient) -> None:
    assert (await client.post("/api/v1/auth/logout")).status_code == 401


# --- forgot / reset password --------------------------------------------------------------


async def test_password_reset_flow(
    client: AsyncClient, make_account: AccountFactory, outbox: RecordingTransport
) -> None:
    account = await make_account(Role.PARENT)
    forgot = await client.post("/api/v1/auth/password/forgot", json={"email": account.email})
    unknown = await client.post("/api/v1/auth/password/forgot", json={"email": "x@example.com"})
    assert forgot.status_code == unknown.status_code == 202
    assert forgot.json() == unknown.json()
    assert outbox.kinds() == ["reset_password"]  # nothing sent for the unknown address

    token = outbox.token("reset_password")
    new_password = "a-brand-new-passphrase"
    reset = await client.post(
        "/api/v1/auth/password/reset", json={"token": token, "new_password": new_password}
    )
    assert reset.status_code == 204

    # Signed out everywhere, old password dead, new one works, owner notified.
    assert (await client.get("/api/v1/me", headers=account.headers)).status_code == 401
    old = await client.post(
        "/api/v1/auth/login", json={"email": account.email, "password": PASSWORD}
    )
    assert old.status_code == 401
    await login(client, account.email, new_password)
    assert outbox.last("password_changed").to == account.email

    again = await client.post(
        "/api/v1/auth/password/reset", json={"token": token, "new_password": "yet-another-pass-1"}
    )
    assert again.status_code == 400  # single use


async def test_reset_token_expires_after_an_hour(
    client: AsyncClient,
    make_account: AccountFactory,
    outbox: RecordingTransport,
    engine: AsyncEngine,
) -> None:
    account = await make_account(Role.PARENT, sign_in=False)
    await client.post("/api/v1/auth/password/forgot", json={"email": account.email})
    async with engine.begin() as conn:
        await conn.execute(update(UserToken).values(expires_at=utcnow() - timedelta(seconds=1)))
    response = await client.post(
        "/api/v1/auth/password/reset",
        json={"token": outbox.token("reset_password"), "new_password": "a-brand-new-passphrase"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_token"


async def test_reset_validates_the_new_password(
    client: AsyncClient, make_account: AccountFactory, outbox: RecordingTransport
) -> None:
    account = await make_account(Role.PARENT, sign_in=False)
    await client.post("/api/v1/auth/password/forgot", json={"email": account.email})
    response = await client.post(
        "/api/v1/auth/password/reset",
        json={"token": outbox.token("reset_password"), "new_password": "short"},
    )
    assert response.status_code == 422


async def test_reset_also_lifts_a_lockout(
    client: AsyncClient, make_account: AccountFactory, outbox: RecordingTransport
) -> None:
    account = await make_account(Role.PARENT, sign_in=False)
    for _ in range(THROTTLE_ACCOUNT_THRESHOLD):
        await client.post(
            "/api/v1/auth/login", json={"email": account.email, "password": "wrong-pw-1234"}
        )
    await client.post("/api/v1/auth/password/forgot", json={"email": account.email})
    await client.post(
        "/api/v1/auth/password/reset",
        json={"token": outbox.token("reset_password"), "new_password": "a-brand-new-passphrase"},
    )
    await login(client, account.email, "a-brand-new-passphrase")
