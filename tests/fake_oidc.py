"""A fake Google / Microsoft / Apple for tests: JWKS, token endpoint, signed ID tokens.

It is strict on purpose, like the real providers: it checks the client secret (for Apple:
the ES256 client-secret JWT), the exact redirect URI, and the PKCE verifier.
"""

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

MICROSOFT_CONSUMER_TENANT = "9188040d-6c67-4c5b-b112-36a304b66dad"
WORK_TENANT = "11111111-2222-3333-4444-555555555555"

GOOGLE_CLIENT_ID, GOOGLE_SECRET = "google-client-id", "google-client-secret"
MICROSOFT_CLIENT_ID, MICROSOFT_SECRET = "microsoft-client-id", "microsoft-client-secret"
APPLE_CLIENT_ID, APPLE_TEAM_ID, APPLE_KEY_ID = "com.lumina.web", "TEAM123456", "KEY1234567"

_APPLE_KEY = ec.generate_private_key(ec.SECP256R1())
APPLE_PRIVATE_KEY_PEM = _APPLE_KEY.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
).decode()

ISSUERS = {
    "google": "https://accounts.google.com",
    "apple": "https://appleid.apple.com",
}
TOKEN_URLS = {
    "oauth2.googleapis.com": "google",
    "login.microsoftonline.com": "microsoft",
    "appleid.apple.com": "apple",
}


def provider_settings() -> dict[str, Any]:
    return {
        "google_client_id": GOOGLE_CLIENT_ID,
        "google_client_secret": GOOGLE_SECRET,
        "microsoft_client_id": MICROSOFT_CLIENT_ID,
        "microsoft_client_secret": MICROSOFT_SECRET,
        "apple_client_id": APPLE_CLIENT_ID,
        "apple_team_id": APPLE_TEAM_ID,
        "apple_key_id": APPLE_KEY_ID,
        "apple_private_key": APPLE_PRIVATE_KEY_PEM,
        "api_public_url": "http://api.test",
    }


@dataclass
class _Pending:
    provider: str
    claims: dict[str, Any]
    nonce: str
    code_challenge: str | None
    redirect_uri: str


@dataclass
class FakeProviders:
    kid: str = "key-1"
    key: rsa.RSAPrivateKey = field(
        default_factory=lambda: rsa.generate_private_key(public_exponent=65537, key_size=2048)
    )
    pending: dict[str, _Pending] = field(default_factory=dict)
    token_requests: list[dict[str, str]] = field(default_factory=list)
    # Test knobs: tamper with what the provider returns.
    sign_with: rsa.RSAPrivateKey | None = None
    claim_overrides: dict[str, Any] = field(default_factory=dict)

    def rotate_key(self) -> None:
        self.kid = f"key-{secrets.token_hex(4)}"
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    # --- the user approves at the provider ------------------------------------------------

    def approve(
        self,
        authorization_url: str,
        *,
        sub: str,
        email: str | None,
        email_verified: Any = True,
        name: str | None = None,
        **extra_claims: Any,
    ) -> tuple[str, str]:
        """Returns (code, state) as the provider would send them to the callback."""
        url = urlparse(authorization_url)
        params = {k: v[0] for k, v in parse_qs(url.query).items()}
        provider = {"accounts.google.com": "google", "login.microsoftonline.com": "microsoft",
                    "appleid.apple.com": "apple"}[url.netloc]  # fmt: skip
        claims: dict[str, Any] = {"sub": sub, "aud": params["client_id"]}
        if email is not None:
            claims["email"] = email
            if provider != "microsoft":
                claims["email_verified"] = email_verified
        if name and provider != "apple":
            claims["name"] = name
        claims.update(extra_claims)
        code = secrets.token_urlsafe(16)
        self.pending[code] = _Pending(
            provider=provider,
            claims=claims,
            nonce=params["nonce"],
            code_challenge=params.get("code_challenge"),
            redirect_uri=params["redirect_uri"],
        )
        return code, params["state"]

    # --- HTTP -----------------------------------------------------------------------------

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _jwk(self) -> dict[str, Any]:
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        return {**jwk, "kid": self.kid, "use": "sig", "alg": "RS256"}

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/keys") or path.endswith("/certs"):
            return httpx.Response(200, json={"keys": [self._jwk()]})
        if request.method == "POST" and request.url.host in TOKEN_URLS:
            return self._token(TOKEN_URLS[request.url.host], request)
        return httpx.Response(404)

    def _token(self, provider: str, request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        self.token_requests.append(form)
        pending = self.pending.pop(form.get("code", ""), None)
        if pending is None or pending.provider != provider:
            return httpx.Response(400, json={"error": "invalid_grant"})
        if form.get("redirect_uri") != pending.redirect_uri:
            return httpx.Response(400, json={"error": "redirect_uri_mismatch"})
        if not self._client_ok(provider, form):
            return httpx.Response(401, json={"error": "invalid_client"})
        if pending.code_challenge is not None:
            digest = hashlib.sha256(form.get("code_verifier", "").encode()).digest()
            if base64.urlsafe_b64encode(digest).decode().rstrip("=") != pending.code_challenge:
                return httpx.Response(400, json={"error": "invalid_grant", "detail": "pkce"})

        now = int(time.time())
        claims = {**pending.claims, "nonce": pending.nonce, "iat": now, "exp": now + 600}
        if provider == "microsoft":
            tid = claims.setdefault("tid", WORK_TENANT)
            claims["iss"] = f"https://login.microsoftonline.com/{tid}/v2.0"
        else:
            claims["iss"] = ISSUERS[provider]
        claims.update(self.claim_overrides)
        id_token = jwt.encode(
            claims, self.sign_with or self.key, algorithm="RS256", headers={"kid": self.kid}
        )
        return httpx.Response(200, json={"id_token": id_token, "access_token": "unused"})

    def _client_ok(self, provider: str, form: dict[str, str]) -> bool:
        if provider == "google":
            return (
                form.get("client_id") == GOOGLE_CLIENT_ID
                and form.get("client_secret") == GOOGLE_SECRET
            )
        if provider == "microsoft":
            return (
                form.get("client_id") == MICROSOFT_CLIENT_ID
                and form.get("client_secret") == MICROSOFT_SECRET
            )
        try:  # Apple: the client secret is a JWT signed with the team's key
            secret = jwt.decode(
                form.get("client_secret", ""),
                _APPLE_KEY.public_key(),
                algorithms=["ES256"],
                audience="https://appleid.apple.com",
            )
        except jwt.PyJWTError:
            return False
        header = jwt.get_unverified_header(form["client_secret"])
        return (
            header.get("kid") == APPLE_KEY_ID
            and secret.get("iss") == APPLE_TEAM_ID
            and secret.get("sub") == APPLE_CLIENT_ID
            and form.get("client_id") == APPLE_CLIENT_ID
        )
