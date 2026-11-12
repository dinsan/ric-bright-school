"""Optional two-factor authentication (TOTP) for teachers and admins (BE-AUTH-12)."""

import uuid
from datetime import timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.db.mixins import utcnow
from app.modules.identity import totp
from app.modules.identity.domain import MFA_CHALLENGE_MAX_ATTEMPTS, RECOVERY_CODE_COUNT, Role
from app.modules.identity.models import LoginChallenge, UserMfa
from tests.conftest import PASSWORD, Account, AccountFactory, RecordingTransport, make_settings


def code(secret: str, steps_ahead: int = 0) -> str:
    return totp.code_at(secret, totp.current_step() + steps_ahead)


async def enable_totp(client: AsyncClient, account: Account) -> tuple[str, list[str]]:
    setup = await client.post("/api/v1/me/mfa/totp", headers=account.headers)
    assert setup.status_code == 200, setup.text
    secret = setup.json()["secret"]
    confirmed = await client.post(
        "/api/v1/me/mfa/totp/confirm", json={"code": code(secret)}, headers=account.headers
    )
    assert confirmed.status_code == 200, confirmed.text
    return secret, confirmed.json()["recovery_codes"]


async def start_login(client: AsyncClient, account: Account) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/auth/login", json={"email": account.email, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


async def finish(client: AsyncClient, mfa_token: str, **second_factor: str) -> Any:
    return await client.post(
        "/api/v1/auth/login/mfa", json={"mfa_token": mfa_token, **second_factor}
    )


async def test_enable_then_sign_in_with_a_code(
    client: AsyncClient, teacher: Account, outbox: RecordingTransport
) -> None:
    setup = (await client.post("/api/v1/me/mfa/totp", headers=teacher.headers)).json()
    assert setup["otpauth_uri"].startswith("otpauth://totp/Lumina:teacher%40example.com?secret=")
    assert (await client.get("/api/v1/me", headers=teacher.headers)).json()["mfa_enabled"] is False

    wrong = await client.post(
        "/api/v1/me/mfa/totp/confirm", json={"code": "000000"}, headers=teacher.headers
    )
    assert wrong.status_code == 401
    first_code = code(setup["secret"])  # kept: the replay below must reuse this exact code
    confirmed = await client.post(
        "/api/v1/me/mfa/totp/confirm", json={"code": first_code}, headers=teacher.headers
    )
    codes = confirmed.json()["recovery_codes"]
    assert len(codes) == RECOVERY_CODE_COUNT and len(set(codes)) == RECOVERY_CODE_COUNT
    assert (await client.get("/api/v1/me", headers=teacher.headers)).json()["mfa_enabled"] is True
    assert "mfa_enabled" in outbox.kinds()

    # The password alone is no longer enough.
    challenge = await start_login(client, teacher)
    assert challenge["mfa_required"] is True
    assert "access_token" not in challenge
    assert challenge["methods"] == ["totp", "recovery_code"]

    # The code used to confirm cannot be replayed: the next one (30 s later) works.
    replay = await finish(client, challenge["mfa_token"], code=first_code)
    assert replay.status_code == 401
    done = await finish(client, challenge["mfa_token"], code=code(setup["secret"], 1))
    assert done.status_code == 200, done.text
    me = await client.get(
        "/api/v1/me", headers={"Authorization": f"Bearer {done.json()['access_token']}"}
    )
    assert me.status_code == 200

    # A finished challenge is used up.
    assert (
        await finish(client, challenge["mfa_token"], code=code(setup["secret"], 1))
    ).status_code == 400


async def test_wrong_codes_use_up_the_challenge(
    client: AsyncClient, teacher: Account, engine: AsyncEngine
) -> None:
    secret, _ = await enable_totp(client, teacher)
    challenge = await start_login(client, teacher)
    for _ in range(MFA_CHALLENGE_MAX_ATTEMPTS):
        await finish(client, challenge["mfa_token"], code="123456")
    # Lift the account-level backoff, so only the per-challenge limit can stop the next try.
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM login_throttles"))
    right = await finish(client, challenge["mfa_token"], code=code(secret, 1))
    assert right.status_code == 400  # this sign-in is used up: start again
    assert right.json()["error"]["code"] == "invalid_token"


async def test_wrong_codes_also_back_off_the_account(client: AsyncClient, teacher: Account) -> None:
    """A fresh challenge does not reset the count: the account itself backs off."""
    await enable_totp(client, teacher)
    statuses = []
    for _ in range(3):
        challenge = await start_login(client, teacher)
        for _ in range(2):
            statuses.append(
                (await finish(client, challenge["mfa_token"], code="123456")).status_code
            )
    assert statuses[:4] == [401, 401, 401, 401]
    assert 429 in statuses


async def test_recovery_codes_work_once(
    client: AsyncClient, teacher: Account, outbox: RecordingTransport
) -> None:
    _, codes = await enable_totp(client, teacher)
    challenge = await start_login(client, teacher)
    used = await finish(client, challenge["mfa_token"], recovery_code=codes[0].lower())
    assert used.status_code == 200
    assert outbox.last("recovery_code_used").to == teacher.email
    assert f"You have {RECOVERY_CODE_COUNT - 1} left" in outbox.last("recovery_code_used").body

    again = await start_login(client, teacher)
    reused = await finish(client, again["mfa_token"], recovery_code=codes[0])
    assert reused.status_code == 401


async def test_challenge_expires(
    client: AsyncClient, teacher: Account, engine: AsyncEngine
) -> None:
    secret, _ = await enable_totp(client, teacher)
    challenge = await start_login(client, teacher)
    async with engine.begin() as conn:
        await conn.execute(
            update(LoginChallenge).values(expires_at=utcnow() - timedelta(seconds=1))
        )
    assert (await finish(client, challenge["mfa_token"], code=code(secret, 1))).status_code == 400
    assert (await finish(client, "made-up", code="123456")).status_code == 400


async def test_mfa_request_validation(client: AsyncClient) -> None:
    for body in ({"mfa_token": "x"}, {"mfa_token": "x", "code": "1", "recovery_code": "2"}):
        assert (await client.post("/api/v1/auth/login/mfa", json=body)).status_code == 422


async def test_disable_needs_password_and_code(
    client: AsyncClient, teacher: Account, outbox: RecordingTransport
) -> None:
    secret, _ = await enable_totp(client, teacher)
    url = "/api/v1/me/mfa/totp"
    wrong_password = await client.request(
        "DELETE",
        url,
        json={"code": code(secret, 1), "password": "nope-nope-nope"},
        headers=teacher.headers,
    )
    assert wrong_password.status_code == 401
    wrong_code = await client.request(
        "DELETE", url, json={"code": "000000", "password": PASSWORD}, headers=teacher.headers
    )
    assert wrong_code.status_code == 401
    ok = await client.request(
        "DELETE", url, json={"code": code(secret, 1), "password": PASSWORD}, headers=teacher.headers
    )
    assert ok.status_code == 204
    assert "mfa_disabled" in outbox.kinds()
    assert "access_token" in await start_login(client, teacher)  # no second step any more


async def test_regenerate_recovery_codes(client: AsyncClient, teacher: Account) -> None:
    secret, old_codes = await enable_totp(client, teacher)
    response = await client.post(
        "/api/v1/me/mfa/recovery-codes", json={"code": code(secret, 1)}, headers=teacher.headers
    )
    assert response.status_code == 200
    new_codes = response.json()["recovery_codes"]
    assert not set(new_codes) & set(old_codes)

    challenge = await start_login(client, teacher)
    assert (
        await finish(client, challenge["mfa_token"], recovery_code=old_codes[0])
    ).status_code == 401
    assert (
        await finish(client, challenge["mfa_token"], recovery_code=new_codes[0])
    ).status_code == 200


async def test_who_can_use_two_factor(
    client: AsyncClient, admin: Account, teacher: Account, make_account: AccountFactory
) -> None:
    for role in (Role.PARENT, Role.STUDENT):
        account = await make_account(role)
        response = await client.post("/api/v1/me/mfa/totp", headers=account.headers)
        assert response.status_code == 403, role
    assert (await client.post("/api/v1/me/mfa/totp", headers=admin.headers)).status_code == 200
    assert (await client.post("/api/v1/me/mfa/totp")).status_code == 401

    await enable_totp(client, teacher)
    again = await client.post("/api/v1/me/mfa/totp", headers=teacher.headers)
    assert again.status_code == 409  # already on

    # An admin impersonating a teacher cannot touch the teacher's 2FA.
    other = await make_account(Role.TEACHER)
    token = (
        await client.post(f"/api/v1/admin/users/{other.id}/impersonate", headers=admin.headers)
    ).json()["access_token"]
    impersonating = await client.post(
        "/api/v1/me/mfa/totp", headers={"Authorization": f"Bearer {token}"}
    )
    assert impersonating.status_code == 403


async def test_admin_resets_a_lost_second_factor(
    client: AsyncClient, admin: Account, teacher: Account, outbox: RecordingTransport
) -> None:
    await enable_totp(client, teacher)
    url = f"/api/v1/admin/users/{teacher.id}/mfa"
    assert (await client.delete(url, headers=teacher.headers)).status_code == 403
    assert (await client.delete(url, headers=admin.headers)).status_code == 204
    assert (
        await client.get("/api/v1/me", headers=teacher.headers)
    ).status_code == 401  # signed out
    assert "access_token" in await start_login(client, teacher)
    assert "mfa_disabled" in outbox.kinds()
    assert (await client.delete(url, headers=admin.headers)).status_code == 404
    assert (
        await client.delete(f"/api/v1/admin/users/{admin.id}/mfa", headers=admin.headers)
    ).status_code == 403
    assert (
        await client.delete(f"/api/v1/admin/users/{uuid.uuid4()}/mfa", headers=admin.headers)
    ).status_code == 404


async def test_secrets_are_encrypted_at_rest(
    client: AsyncClient, teacher: Account, session: AsyncSession
) -> None:
    secret, codes = await enable_totp(client, teacher)
    stored = await session.scalar(select(UserMfa.secret_encrypted))
    assert stored is not None and secret not in stored
    assert totp.SecretBox(make_settings()).decrypt(stored) == secret
    other_key = make_settings(encryption_key="a-completely-different-encryption-key-123")
    assert totp.SecretBox(other_key).decrypt(stored) is None

    everything = "".join(
        str(v) for row in (await session.execute(select(UserMfa))).all() for v in row
    )
    assert not any(c in everything for c in codes)
