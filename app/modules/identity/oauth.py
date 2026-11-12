"""Social sign-in providers (BE-AUTH-11): OpenID Connect authorization code flow.

This file only talks to the providers: build the authorization URL, exchange the code,
and verify the ID token. Linking identities to users happens in service.py.

Security notes:
- state (CSRF), nonce (token replay) and, where supported, PKCE (code interception) are
  generated per attempt and kept on the server.
- ID tokens are verified with the provider's published keys (JWKS): signature, issuer,
  audience, expiry and nonce.
- An email is only *trusted* (used to find or create an account) when the provider vouches
  for it. Microsoft work/school accounts can carry unverified, admin-editable emails, so
  they are not trusted unless Microsoft marks the domain as verified ("nOAuth").
"""

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

from app.core.config import Settings
from app.modules.identity.domain import SignInMethod

# Microsoft's tenant id for personal accounts (outlook.com, hotmail.com, ...), whose email
# addresses Microsoft has verified.
MICROSOFT_CONSUMER_TENANT = "9188040d-6c67-4c5b-b112-36a304b66dad"
JWKS_CACHE_SECONDS = 3600
CLOCK_LEEWAY_SECONDS = 60


class OAuthError(Exception):
    """The provider exchange or token validation failed. The message is for logs only."""


@dataclass(frozen=True)
class ProviderIdentity:
    subject: str
    email: str | None
    email_trusted: bool
    name: str | None


@dataclass
class _Jwks:
    keys: dict[str, dict[str, Any]] = field(default_factory=dict)
    fetched_at: float = 0.0


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)  # 86 characters (43-128 allowed)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge.decode().rstrip("=")


def _truthy(value: object) -> bool:
    return value is True or (isinstance(value, str) and value.lower() in {"true", "1"})


class Provider:
    name: SignInMethod
    authorize_url: str
    token_url: str
    jwks_url: str
    scopes = "openid email profile"
    supports_pkce = True

    def __init__(self, settings: Settings, http: httpx.AsyncClient) -> None:
        self._settings = settings
        self._http = http
        self._jwks = _Jwks()

    # --- configuration --------------------------------------------------------------------

    @property
    def client_id(self) -> str:
        raise NotImplementedError

    def client_secret(self) -> str:
        raise NotImplementedError

    def redirect_uri(self) -> str:
        base = self._settings.api_public_url.rstrip("/")
        return f"{base}{self._settings.api_v1_prefix}/auth/oauth/{self.name.value}/callback"

    def extra_authorize_params(self) -> dict[str, str]:
        return {}

    # --- the flow -------------------------------------------------------------------------

    def authorization_url(self, *, state: str, nonce: str, code_challenge: str) -> str:
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri(),
            "scope": self.scopes,
            "state": state,
            "nonce": nonce,
            **self.extra_authorize_params(),
        }
        if self.supports_pkce:
            params |= {"code_challenge": code_challenge, "code_challenge_method": "S256"}
        return f"{self.authorize_url}?{urlencode(params)}"

    async def exchange_code(self, code: str, code_verifier: str) -> str:
        """Trade the authorization code for tokens; returns the raw ID token."""
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri(),
            "client_id": self.client_id,
            "client_secret": self.client_secret(),
        }
        if self.supports_pkce:
            data["code_verifier"] = code_verifier
        try:
            response = await self._http.post(self.token_url, data=data, timeout=10)
        except httpx.HTTPError as exc:
            raise OAuthError(f"token endpoint unreachable: {type(exc).__name__}") from exc
        if response.status_code != 200:
            raise OAuthError(f"token endpoint answered {response.status_code}")
        id_token = response.json().get("id_token")
        if not isinstance(id_token, str):
            raise OAuthError("no id_token in the token response")
        return id_token

    async def verify_id_token(
        self, id_token: str, *, nonce: str, extra: dict[str, Any] | None = None
    ) -> ProviderIdentity:
        try:
            header = jwt.get_unverified_header(id_token)
        except jwt.PyJWTError as exc:
            raise OAuthError("malformed id_token") from exc
        key = await self._signing_key(str(header.get("kid", "")))
        try:
            claims: dict[str, Any] = jwt.decode(
                id_token,
                key=key,
                algorithms=["RS256"],
                audience=self.client_id,
                leeway=CLOCK_LEEWAY_SECONDS,
                options={"require": ["iss", "sub", "aud", "exp", "iat"], "verify_iss": False},
            )
        except jwt.PyJWTError as exc:
            raise OAuthError(f"invalid id_token: {type(exc).__name__}") from exc
        self.check_issuer(claims)
        if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
            raise OAuthError("nonce mismatch")
        return self.identity(claims, extra or {})

    async def _signing_key(self, kid: str) -> Any:
        fresh = time.monotonic() - self._jwks.fetched_at < JWKS_CACHE_SECONDS
        if kid not in self._jwks.keys or not fresh:
            # Unknown kid: the provider may have rotated its keys, so fetch them again.
            try:
                response = await self._http.get(self.jwks_url, timeout=10)
                response.raise_for_status()
                keys = response.json()["keys"]
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                raise OAuthError("could not fetch the provider's signing keys") from exc
            self._jwks = _Jwks({k["kid"]: k for k in keys if "kid" in k}, time.monotonic())
        if kid not in self._jwks.keys:
            raise OAuthError("id_token signed with an unknown key")
        return jwt.PyJWK(self._jwks.keys[kid]).key

    def check_issuer(self, claims: dict[str, Any]) -> None:
        raise NotImplementedError

    def identity(self, claims: dict[str, Any], extra: dict[str, Any]) -> ProviderIdentity:
        raise NotImplementedError


class GoogleProvider(Provider):
    name = SignInMethod.GOOGLE
    authorize_url = "https://accounts.google.com/o/oauth2/v2/auth"
    token_url = "https://oauth2.googleapis.com/token"
    jwks_url = "https://www.googleapis.com/oauth2/v3/certs"

    @property
    def client_id(self) -> str:
        assert self._settings.google_client_id
        return self._settings.google_client_id

    def client_secret(self) -> str:
        assert self._settings.google_client_secret
        return self._settings.google_client_secret.get_secret_value()

    def extra_authorize_params(self) -> dict[str, str]:
        return {"prompt": "select_account"}

    def check_issuer(self, claims: dict[str, Any]) -> None:
        if claims["iss"] not in {"https://accounts.google.com", "accounts.google.com"}:
            raise OAuthError("unexpected issuer")

    def identity(self, claims: dict[str, Any], extra: dict[str, Any]) -> ProviderIdentity:
        return ProviderIdentity(
            subject=str(claims["sub"]),
            email=claims.get("email"),
            email_trusted=_truthy(claims.get("email_verified")),
            name=claims.get("name"),
        )


class MicrosoftProvider(Provider):
    name = SignInMethod.MICROSOFT

    def __init__(self, settings: Settings, http: httpx.AsyncClient) -> None:
        super().__init__(settings, http)
        tenant = settings.microsoft_tenant
        base = f"https://login.microsoftonline.com/{tenant}"
        self.authorize_url = f"{base}/oauth2/v2.0/authorize"
        self.token_url = f"{base}/oauth2/v2.0/token"
        self.jwks_url = f"{base}/discovery/v2.0/keys"

    @property
    def client_id(self) -> str:
        assert self._settings.microsoft_client_id
        return self._settings.microsoft_client_id

    def client_secret(self) -> str:
        assert self._settings.microsoft_client_secret
        return self._settings.microsoft_client_secret.get_secret_value()

    def extra_authorize_params(self) -> dict[str, str]:
        return {"prompt": "select_account"}

    def check_issuer(self, claims: dict[str, Any]) -> None:
        # Multi-tenant apps receive a per-tenant issuer: check it matches the token's tid.
        tid = str(claims.get("tid", ""))
        if not tid or claims["iss"] != f"https://login.microsoftonline.com/{tid}/v2.0":
            raise OAuthError("unexpected issuer")
        configured = self._settings.microsoft_tenant
        if configured == "consumers" and tid != MICROSOFT_CONSUMER_TENANT:
            raise OAuthError("only personal Microsoft accounts are allowed")
        if configured == "organizations" and tid == MICROSOFT_CONSUMER_TENANT:
            raise OAuthError("only work or school accounts are allowed")
        if configured not in {"common", "consumers", "organizations"} and tid != configured:
            raise OAuthError("account from another organisation")

    def identity(self, claims: dict[str, Any], extra: dict[str, Any]) -> ProviderIdentity:
        personal = claims.get("tid") == MICROSOFT_CONSUMER_TENANT
        return ProviderIdentity(
            subject=str(claims["sub"]),
            email=claims.get("email"),
            # xms_edov: "email domain owner verified" (optional claim to enable in Entra).
            email_trusted=bool(claims.get("email"))
            and (personal or _truthy(claims.get("xms_edov"))),
            name=claims.get("name"),
        )


class AppleProvider(Provider):
    name = SignInMethod.APPLE
    authorize_url = "https://appleid.apple.com/auth/authorize"
    token_url = "https://appleid.apple.com/auth/token"
    jwks_url = "https://appleid.apple.com/auth/keys"
    scopes = "name email"
    # Apple does not document PKCE; the confidential client secret, state and nonce protect
    # the flow instead.
    supports_pkce = False

    @property
    def client_id(self) -> str:
        assert self._settings.apple_client_id
        return self._settings.apple_client_id

    def client_secret(self) -> str:
        """Apple wants a short-lived ES256 JWT signed with the team's private key."""
        s = self._settings
        assert s.apple_team_id and s.apple_key_id and s.apple_private_key
        pem = s.apple_private_key.get_secret_value().replace("\\n", "\n")
        now = int(time.time())
        return jwt.encode(
            {
                "iss": s.apple_team_id,
                "iat": now,
                "exp": now + 300,
                "aud": "https://appleid.apple.com",
                "sub": self.client_id,
            },
            pem,
            algorithm="ES256",
            headers={"kid": s.apple_key_id},
        )

    def extra_authorize_params(self) -> dict[str, str]:
        # Required by Apple when asking for name/email: the result is POSTed to the callback.
        return {"response_mode": "form_post"}

    def check_issuer(self, claims: dict[str, Any]) -> None:
        if claims["iss"] != "https://appleid.apple.com":
            raise OAuthError("unexpected issuer")

    def identity(self, claims: dict[str, Any], extra: dict[str, Any]) -> ProviderIdentity:
        # Apple sends the name only once, on the very first sign-in, outside the token.
        name_parts = (extra.get("name") or {}) if isinstance(extra, dict) else {}
        name = " ".join(p for p in (name_parts.get("firstName"), name_parts.get("lastName")) if p)
        return ProviderIdentity(
            subject=str(claims["sub"]),
            email=claims.get("email"),
            email_trusted=_truthy(claims.get("email_verified")),
            name=name or None,
        )


class OAuthProviders:
    """The configured providers. One instance per app (keeps the JWKS caches)."""

    def __init__(self, settings: Settings, http: httpx.AsyncClient) -> None:
        self._providers: dict[str, Provider] = {}
        if settings.google_client_id and settings.google_client_secret:
            self._providers["google"] = GoogleProvider(settings, http)
        if settings.microsoft_client_id and settings.microsoft_client_secret:
            self._providers["microsoft"] = MicrosoftProvider(settings, http)
        if (
            settings.apple_client_id
            and settings.apple_team_id
            and settings.apple_key_id
            and settings.apple_private_key
        ):
            self._providers["apple"] = AppleProvider(settings, http)

    def enabled(self) -> list[str]:
        return sorted(self._providers)

    def get(self, name: str) -> Provider | None:
        return self._providers.get(name)
