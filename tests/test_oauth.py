"""Sign in with Google, Microsoft and Apple (BE-AUTH-11), against a fake provider."""

import json
import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import AsyncClient
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.db.mixins import utcnow
from app.modules.identity import totp
from app.modules.identity.domain import Role
from app.modules.identity.models import LoginChallenge, OAuthIdentity, Session, User
from tests.conftest import PASSWORD, Account, AccountFactory, RecordingTransport, make_settings
from tests.fake_oidc import MICROSOFT_CONSUMER_TENANT, FakeProviders, provider_settings


@pytest.fixture
def fake() -> FakeProviders:
    return FakeProviders()


@pytest.fixture
def oauth_client(client_factory: Callable[..., AsyncClient], fake: FakeProviders) -> AsyncClient:
    return client_factory(make_settings(**provider_settings()), fake.transport())


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def start(
    client: AsyncClient, provider: str, headers: dict[str, str] | None = None, **params: str
) -> str:
    if headers is not None and params.get("intent") == "link":
        response = await client.post(f"/api/v1/me/oauth/{provider}/link", headers=headers)
    else:
        response = await client.get(f"/api/v1/auth/oauth/{provider}/start", params=params)
    assert response.status_code == 200, response.text
    return str(response.json()["authorization_url"])


async def callback(
    client: AsyncClient, provider: str, code: str, state: str, user: dict[str, Any] | None = None
) -> dict[str, str]:
    """Deliver the provider's answer; returns the query of the web-app URL redirected to."""
    if provider == "apple":  # form_post
        form = {"code": code, "state": state, **({"user": json.dumps(user)} if user else {})}
        response = await client.post(
            f"/api/v1/auth/oauth/{provider}/callback",
            content=urlencode(form),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    else:
        response = await client.get(
            f"/api/v1/auth/oauth/{provider}/callback", params={"code": code, "state": state}
        )
    assert response.status_code == 303, response.text
    location = urlparse(response.headers["location"])
    assert (
        f"{location.scheme}://{location.netloc}{location.path}" == "http://web.test/auth/complete"
    )
    assert response.headers["cache-control"] == "no-store"
    return {k: v[0] for k, v in parse_qs(location.query).items()}


async def social_sign_in(
    client: AsyncClient,
    fake: FakeProviders,
    provider: str,
    *,
    intent: str = "login",
    role: str | None = None,
    headers: dict[str, str] | None = None,
    user: dict[str, Any] | None = None,
    **identity: Any,
) -> dict[str, str]:
    params = {"intent": intent, **({"role": role} if role else {})}
    url = await start(client, provider, headers, **params)
    code, state = fake.approve(url, **identity)
    return await callback(client, provider, code, state, user)


async def exchange(client: AsyncClient, code: str) -> Any:
    return await client.post("/api/v1/auth/oauth/exchange", json={"code": code})


# --- configuration and the authorization URL ----------------------------------------------


async def test_providers_are_listed_only_when_configured(
    client: AsyncClient, oauth_client: AsyncClient
) -> None:
    assert (await client.get("/api/v1/auth/oauth/providers")).json() == {"providers": []}
    assert (await oauth_client.get("/api/v1/auth/oauth/providers")).json() == {
        "providers": ["apple", "google", "microsoft"]
    }
    assert (await client.get("/api/v1/auth/oauth/google/start")).status_code == 404
    assert (await oauth_client.get("/api/v1/auth/oauth/facebook/start")).status_code == 404


async def test_authorization_urls(oauth_client: AsyncClient) -> None:
    google = urlparse(await start(oauth_client, "google"))
    params = {k: v[0] for k, v in parse_qs(google.query).items()}
    assert google.netloc == "accounts.google.com"
    assert params["redirect_uri"] == "http://api.test/api/v1/auth/oauth/google/callback"
    assert params["response_type"] == "code" and params["scope"] == "openid email profile"
    assert params["code_challenge_method"] == "S256" and len(params["code_challenge"]) == 43
    assert len(params["state"]) >= 43 and len(params["nonce"]) >= 43

    apple = {
        k: v[0] for k, v in parse_qs(urlparse(await start(oauth_client, "apple")).query).items()
    }
    assert apple["response_mode"] == "form_post" and apple["scope"] == "name email"
    assert "code_challenge" not in apple  # Apple does not document PKCE

    microsoft = urlparse(await start(oauth_client, "microsoft"))
    assert microsoft.path == "/common/oauth2/v2.0/authorize"

    bad = await oauth_client.get(
        "/api/v1/auth/oauth/google/start", params={"intent": "signup", "role": "student"}
    )
    assert bad.status_code == 422
    no_role = await oauth_client.get("/api/v1/auth/oauth/google/start", params={"intent": "signup"})
    assert no_role.status_code == 422


# --- sign up, sign in, automatic linking -------------------------------------------------


async def test_sign_up_with_google_then_sign_in_again(
    oauth_client: AsyncClient, fake: FakeProviders, session: AsyncSession
) -> None:
    done = await social_sign_in(
        oauth_client, fake, "google", intent="signup", role="teacher",
        sub="g-123", email="Grace@Example.com", name="Grace Hopper",
    )  # fmt: skip
    assert set(done) == {"code"}
    tokens = (await exchange(oauth_client, done["code"])).json()
    me = (await oauth_client.get("/api/v1/me", headers=bearer(tokens["access_token"]))).json()
    assert (me["email"], me["display_name"], me["role"]) == (
        "grace@example.com",
        "Grace Hopper",
        "teacher",
    )
    assert me["email_verified"] is True
    assert me["sign_in_methods"] == ["google"]
    assert me["teacher_profile"]["is_verified"] is False

    # Next time: same Google account (found by its id, even if the email changed there).
    again = await social_sign_in(
        oauth_client, fake, "google", sub="g-123", email="other@example.com"
    )
    tokens2 = (await exchange(oauth_client, again["code"])).json()
    me2 = (await oauth_client.get("/api/v1/me", headers=bearer(tokens2["access_token"]))).json()
    assert me2["id"] == me["id"]
    assert (
        await session.scalar(select(User.email).where(User.role == Role.TEACHER))
        == "grace@example.com"
    )


async def test_unknown_account_without_signup(
    oauth_client: AsyncClient, fake: FakeProviders
) -> None:
    done = await social_sign_in(oauth_client, fake, "google", sub="g-9", email="new@example.com")
    assert done == {"error": "no_account"}


async def test_verified_email_links_the_existing_account(
    oauth_client: AsyncClient,
    fake: FakeProviders,
    make_account: AccountFactory,
    outbox: RecordingTransport,
) -> None:
    parent = await make_account(Role.PARENT, email="pat@example.com")
    done = await social_sign_in(oauth_client, fake, "google", sub="g-pat", email="PAT@example.com")
    tokens = (await exchange(oauth_client, done["code"])).json()
    me = (await oauth_client.get("/api/v1/me", headers=bearer(tokens["access_token"]))).json()
    assert me["id"] == parent.id
    assert me["sign_in_methods"] == ["email", "google"]
    assert outbox.last("identity_linked").to == "pat@example.com"


async def test_unverified_google_email_is_never_matched(
    oauth_client: AsyncClient,
    fake: FakeProviders,
    make_account: AccountFactory,
    session: AsyncSession,
) -> None:
    await make_account(Role.PARENT, email="victim@example.com")
    done = await social_sign_in(
        oauth_client,
        fake,
        "google",
        sub="attacker",
        email="victim@example.com",
        email_verified=False,
    )
    assert done == {"error": "email_not_verified"}
    assert await session.scalar(select(OAuthIdentity.id)) is None


# --- Microsoft: the "nOAuth" protection ---------------------------------------------------


async def test_microsoft_work_account_email_is_not_trusted(
    oauth_client: AsyncClient,
    fake: FakeProviders,
    make_account: AccountFactory,
    session: AsyncSession,
) -> None:
    """Work/school tenants let admins type any email: it must not take over an account."""
    await make_account(Role.TEACHER, email="victim@example.com")
    done = await social_sign_in(
        oauth_client, fake, "microsoft", sub="ms-attacker", email="victim@example.com"
    )
    assert done == {"error": "email_not_verified"}
    assert await session.scalar(select(OAuthIdentity.id)) is None


async def test_microsoft_verified_domain_or_personal_account_is_trusted(
    oauth_client: AsyncClient, fake: FakeProviders, make_account: AccountFactory
) -> None:
    teacher = await make_account(Role.TEACHER, email="t@school.example.com")
    work = await social_sign_in(
        oauth_client, fake, "microsoft", sub="ms-1", email="t@school.example.com", xms_edov=True
    )
    assert "code" in work
    me = (
        await oauth_client.get(
            "/api/v1/me",
            headers=bearer((await exchange(oauth_client, work["code"])).json()["access_token"]),
        )
    ).json()
    assert me["id"] == teacher.id

    parent = await make_account(Role.PARENT, email="p@outlook.example.com")
    personal = await social_sign_in(
        oauth_client, fake, "microsoft", sub="ms-2", email="p@outlook.example.com",
        tid=MICROSOFT_CONSUMER_TENANT,
    )  # fmt: skip
    tokens = (await exchange(oauth_client, personal["code"])).json()
    assert (await oauth_client.get("/api/v1/me", headers=bearer(tokens["access_token"]))).json()[
        "id"
    ] == parent.id


async def test_microsoft_single_tenant_rejects_other_organisations(
    client_factory: Callable[..., AsyncClient], fake: FakeProviders, make_account: AccountFactory
) -> None:
    http = client_factory(
        make_settings(
            **provider_settings(), microsoft_tenant="aaaaaaaa-0000-0000-0000-000000000000"
        ),
        fake.transport(),
    )
    await make_account(Role.TEACHER, email="t@example.com")
    done = await social_sign_in(
        http, fake, "microsoft", sub="x", email="t@example.com", xms_edov=True
    )
    assert done == {"error": "provider_error"}


# --- Apple ---------------------------------------------------------------------------------


async def test_sign_up_with_apple_form_post(oauth_client: AsyncClient, fake: FakeProviders) -> None:
    done = await social_sign_in(
        oauth_client, fake, "apple", intent="signup", role="parent",
        sub="apple-1", email="relay@privaterelay.appleid.com", email_verified="true",
        user={"name": {"firstName": "Ada", "lastName": "Lovelace"}},
    )  # fmt: skip
    tokens = (await exchange(oauth_client, done["code"])).json()
    me = (await oauth_client.get("/api/v1/me", headers=bearer(tokens["access_token"]))).json()
    assert (me["display_name"], me["role"]) == ("Ada Lovelace", "parent")
    # The fake accepted our ES256 client-secret JWT, and no PKCE verifier was sent.
    assert "code_verifier" not in fake.token_requests[-1]


# --- everything that must fail safely -----------------------------------------------------


async def test_state_is_single_use_and_checked(
    oauth_client: AsyncClient, fake: FakeProviders
) -> None:
    url = await start(oauth_client, "google", intent="signup", role="parent")
    code, state = fake.approve(url, sub="g-1", email="a@example.com")
    assert "code" in await callback(oauth_client, "google", code, state)
    code2, _ = fake.approve(url, sub="g-1", email="a@example.com")
    assert await callback(oauth_client, "google", code2, state) == {"error": "invalid_state"}
    assert await callback(oauth_client, "google", code2, "forged-state") == {
        "error": "invalid_state"
    }
    # A state from Google cannot finish a Microsoft sign-in.
    url = await start(oauth_client, "google")
    code3, state3 = fake.approve(url, sub="g-1", email="a@example.com")
    assert await callback(oauth_client, "microsoft", code3, state3) == {"error": "invalid_state"}


async def test_user_cancels_at_the_provider(oauth_client: AsyncClient) -> None:
    url = await start(oauth_client, "google")
    state = parse_qs(urlparse(url).query)["state"][0]
    response = await oauth_client.get(
        "/api/v1/auth/oauth/google/callback", params={"error": "access_denied", "state": state}
    )
    assert "error=cancelled" in response.headers["location"]


@pytest.mark.parametrize(
    "tamper",
    ["foreign_key", "wrong_audience", "wrong_nonce", "expired", "wrong_issuer"],
)
async def test_forged_or_invalid_id_tokens_are_rejected(
    oauth_client: AsyncClient, fake: FakeProviders, make_account: AccountFactory, tamper: str
) -> None:
    await make_account(Role.PARENT, email="a@example.com")
    if tamper == "foreign_key":
        fake.sign_with = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    elif tamper == "wrong_audience":
        fake.claim_overrides = {"aud": "someone-else"}
    elif tamper == "wrong_nonce":
        fake.claim_overrides = {"nonce": "replayed"}
    elif tamper == "expired":
        fake.claim_overrides = {"exp": int(utcnow().timestamp()) - 3600}
    else:
        fake.claim_overrides = {"iss": "https://evil.example.com"}
    done = await social_sign_in(oauth_client, fake, "google", sub="g-a", email="a@example.com")
    assert done == {"error": "provider_error"}


async def test_provider_key_rotation_is_followed(
    oauth_client: AsyncClient, fake: FakeProviders, make_account: AccountFactory
) -> None:
    await make_account(Role.PARENT, email="a@example.com")
    assert "code" in await social_sign_in(
        oauth_client, fake, "google", sub="g-a", email="a@example.com"
    )
    fake.rotate_key()  # new kid: the cached keys no longer contain it
    assert "code" in await social_sign_in(
        oauth_client, fake, "google", sub="g-a", email="a@example.com"
    )


async def test_exchange_code_is_single_use_and_short_lived(
    oauth_client: AsyncClient,
    fake: FakeProviders,
    make_account: AccountFactory,
    engine: AsyncEngine,
) -> None:
    await make_account(Role.PARENT, email="a@example.com")
    done = await social_sign_in(oauth_client, fake, "google", sub="g-a", email="a@example.com")
    assert (await exchange(oauth_client, done["code"])).status_code == 200
    assert (await exchange(oauth_client, done["code"])).status_code == 400

    late = await social_sign_in(oauth_client, fake, "google", sub="g-a", email="a@example.com")
    async with engine.begin() as conn:
        await conn.execute(
            update(LoginChallenge).values(expires_at=utcnow() - timedelta(seconds=1))
        )
    assert (await exchange(oauth_client, late["code"])).status_code == 400


async def test_deactivated_accounts_cannot_sign_in(
    oauth_client: AsyncClient,
    fake: FakeProviders,
    make_account: AccountFactory,
    engine: AsyncEngine,
) -> None:
    await make_account(Role.PARENT, email="a@example.com")
    await social_sign_in(oauth_client, fake, "google", sub="g-a", email="a@example.com")
    async with engine.begin() as conn:
        await conn.execute(update(User).values(is_active=False))
    done = await social_sign_in(oauth_client, fake, "google", sub="g-a", email="a@example.com")
    assert done == {"error": "account_inactive"}


async def test_younger_students_cannot_use_social_sign_in(
    oauth_client: AsyncClient, fake: FakeProviders, engine: AsyncEngine
) -> None:
    """Google/Microsoft/Apple sign-in starts at Grade 9 (teen band)."""
    async with engine.begin() as conn:
        await conn.execute(
            insert(User).values(
                id=uuid.uuid4(),
                email="kid@example.com",
                display_name="Kid",
                role="student",
                email_verified_at=utcnow(),
            )
        )
    done = await social_sign_in(oauth_client, fake, "google", sub="g-kid", email="kid@example.com")
    assert done == {"error": "method_not_allowed"}


async def test_two_factor_still_applies_after_social_sign_in(
    oauth_client: AsyncClient, fake: FakeProviders, make_account: AccountFactory
) -> None:
    teacher = await make_account(Role.TEACHER, email="t@example.com")
    secret = (await oauth_client.post("/api/v1/me/mfa/totp", headers=teacher.headers)).json()[
        "secret"
    ]
    await oauth_client.post(
        "/api/v1/me/mfa/totp/confirm",
        json={"code": totp.code_at(secret, totp.current_step())},
        headers=teacher.headers,
    )
    done = await social_sign_in(oauth_client, fake, "google", sub="g-t", email="t@example.com")
    result = (await exchange(oauth_client, done["code"])).json()
    assert result["mfa_required"] is True
    finished = await oauth_client.post(
        "/api/v1/auth/login/mfa",
        json={
            "mfa_token": result["mfa_token"],
            "code": totp.code_at(secret, totp.current_step() + 1),
        },
    )
    assert finished.status_code == 200


# --- linking from the account settings ----------------------------------------------------


async def test_link_and_unlink_from_settings(
    oauth_client: AsyncClient, fake: FakeProviders, make_account: AccountFactory
) -> None:
    parent = await make_account(Role.PARENT)
    # Linking works even with a work email Microsoft does not vouch for: the user proved
    # both sides (signed in here, signed in there).
    done = await social_sign_in(
        oauth_client, fake, "microsoft", intent="link", headers=parent.headers,
        sub="ms-p", email="anything@example.com",
    )  # fmt: skip
    assert done == {"linked": "microsoft"}
    linked = (await oauth_client.get("/api/v1/me/oauth", headers=parent.headers)).json()
    assert [i["provider"] for i in linked] == ["microsoft"]

    # That Microsoft account now signs in to this parent.
    login = await social_sign_in(oauth_client, fake, "microsoft", sub="ms-p", email=None)
    tokens = (await exchange(oauth_client, login["code"])).json()
    assert (await oauth_client.get("/api/v1/me", headers=bearer(tokens["access_token"]))).json()[
        "id"
    ] == parent.id

    # Another account cannot take the same Microsoft account.
    other = await make_account(Role.PARENT)
    taken = await social_sign_in(
        oauth_client,
        fake,
        "microsoft",
        intent="link",
        headers=other.headers,
        sub="ms-p",
        email=None,
    )
    assert taken == {"error": "identity_in_use"}

    assert (
        await oauth_client.delete("/api/v1/me/oauth/microsoft", headers=parent.headers)
    ).status_code == 204
    assert (
        await oauth_client.delete("/api/v1/me/oauth/microsoft", headers=parent.headers)
    ).status_code == 404
    assert (await oauth_client.post("/api/v1/me/oauth/google/link")).status_code == 401


async def test_cannot_unlink_the_only_way_in(
    oauth_client: AsyncClient, fake: FakeProviders
) -> None:
    done = await social_sign_in(
        oauth_client,
        fake,
        "google",
        intent="signup",
        role="parent",
        sub="g-only",
        email="only@example.com",
    )
    tokens = (await exchange(oauth_client, done["code"])).json()
    response = await oauth_client.delete(
        "/api/v1/me/oauth/google", headers=bearer(tokens["access_token"])
    )
    assert response.status_code == 409


async def test_social_only_accounts_confirm_with_a_recent_sign_in(
    oauth_client: AsyncClient, fake: FakeProviders, engine: AsyncEngine
) -> None:
    """No password to type: deleting the account needs a sign-in in the last 10 minutes."""
    done = await social_sign_in(
        oauth_client,
        fake,
        "google",
        intent="signup",
        role="parent",
        sub="g-del",
        email="del@example.com",
    )
    tokens = (await exchange(oauth_client, done["code"])).json()
    headers = bearer(tokens["access_token"])
    async with engine.begin() as conn:
        await conn.execute(update(Session).values(created_at=utcnow() - timedelta(minutes=11)))
    stale = await oauth_client.request("DELETE", "/api/v1/me", json={}, headers=headers)
    assert stale.status_code == 401
    assert "Sign in again" in stale.json()["error"]["message"]

    fresh = await social_sign_in(oauth_client, fake, "google", sub="g-del", email="del@example.com")
    fresh_tokens = (await exchange(oauth_client, fresh["code"])).json()
    deleted = await oauth_client.request(
        "DELETE", "/api/v1/me", json={}, headers=bearer(fresh_tokens["access_token"])
    )
    assert deleted.status_code == 204


async def test_password_accounts_still_need_the_password(
    oauth_client: AsyncClient, make_account: AccountFactory
) -> None:
    parent = await make_account(Role.PARENT)
    response = await oauth_client.request("DELETE", "/api/v1/me", json={}, headers=parent.headers)
    assert response.status_code == 401
    ok = await oauth_client.request(
        "DELETE", "/api/v1/me", json={"password": PASSWORD}, headers=parent.headers
    )
    assert ok.status_code == 204


async def test_impersonation_cannot_link_providers(
    oauth_client: AsyncClient, admin: Account, make_account: AccountFactory
) -> None:
    parent = await make_account(Role.PARENT)
    token = (
        await oauth_client.post(
            f"/api/v1/admin/users/{parent.id}/impersonate", headers=admin.headers
        )
    ).json()["access_token"]
    response = await oauth_client.post("/api/v1/me/oauth/google/link", headers=bearer(token))
    assert response.status_code == 403
