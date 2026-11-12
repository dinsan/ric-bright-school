"""Cryptographic building blocks: password hashing, access tokens, opaque tokens."""

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher as Argon2Hasher
from argon2 import Type
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import Settings
from app.modules.identity.domain import Role

JWT_ALGORITHM = "HS256"
JWT_ISSUER = "lumina-api"
JWT_AUDIENCE = "lumina"
ACCESS_TOKEN_TYPE = "access"

# Unambiguous characters for codes people type (no 0/O, 1/I/L).
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


class PasswordHasher:
    """Argon2id (BE-AUTH-05). Cost comes from settings so tests can make it cheap."""

    def __init__(self, settings: Settings) -> None:
        self._hasher = Argon2Hasher(
            time_cost=settings.argon2_time_cost,
            memory_cost=settings.argon2_memory_cost_kib,
            parallelism=1,
            type=Type.ID,
        )
        # Verified against when the email is unknown, so a login takes the same time
        # whether or not the account exists (no user enumeration through timing).
        self._dummy_hash = self._hasher.hash(secrets.token_urlsafe(16))

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)

    def verify(self, password_hash: str | None, password: str) -> bool:
        try:
            return self._hasher.verify(password_hash or self._dummy_hash, password) and (
                password_hash is not None
            )
        except (VerificationError, InvalidHashError):
            return False

    def needs_rehash(self, password_hash: str) -> bool:
        """True after the cost settings changed; the hash is upgraded on the next login."""
        return self._hasher.check_needs_rehash(password_hash)


@dataclass(frozen=True)
class AccessTokenClaims:
    user_id: uuid.UUID
    session_id: uuid.UUID
    role: Role
    impersonator_id: uuid.UUID | None


class InvalidAccessToken(Exception):
    """The access token is missing, malformed, forged, or expired."""


def create_access_token(
    settings: Settings,
    *,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    role: Role,
    ttl: timedelta,
    impersonator_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> str:
    issued = now or datetime.now(UTC)
    claims: dict[str, object] = {
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "typ": ACCESS_TOKEN_TYPE,
        "sub": str(user_id),
        "sid": str(session_id),
        "role": role.value,
        "iat": issued,
        "exp": issued + ttl,
        # Unique per token: two tokens issued in the same second still differ.
        "jti": uuid.uuid4().hex,
    }
    if impersonator_id is not None:
        claims["act"] = {"sub": str(impersonator_id)}  # RFC 8693 "actor" claim
    return jwt.encode(claims, settings.secret_key.get_secret_value(), algorithm=JWT_ALGORITHM)


def decode_access_token(settings: Settings, token: str) -> AccessTokenClaims:
    try:
        claims = jwt.decode(
            token,
            settings.secret_key.get_secret_value(),
            algorithms=[JWT_ALGORITHM],  # never trust the token's own "alg" header
            audience=JWT_AUDIENCE,
            issuer=JWT_ISSUER,
            options={"require": ["exp", "iat", "sub", "sid"]},
        )
        if claims.get("typ") != ACCESS_TOKEN_TYPE:
            raise InvalidAccessToken("wrong token type")
        actor = claims.get("act")
        return AccessTokenClaims(
            user_id=uuid.UUID(claims["sub"]),
            session_id=uuid.UUID(claims["sid"]),
            role=Role(claims["role"]),
            impersonator_id=uuid.UUID(actor["sub"]) if isinstance(actor, dict) else None,
        )
    except (jwt.PyJWTError, KeyError, ValueError, TypeError) as exc:
        raise InvalidAccessToken(str(exc)) from exc


def new_opaque_token() -> str:
    """A high-entropy random token for refresh tokens and emailed links (256 bits)."""
    return secrets.token_urlsafe(32)


def new_link_code() -> str:
    """A short code a person can read out and type, e.g. ``K7QM-9XDA`` (~40 bits)."""
    raw = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def normalize_link_code(code: str) -> str:
    cleaned = "".join(ch for ch in code.upper() if ch.isalnum())
    return f"{cleaned[:4]}-{cleaned[4:]}" if len(cleaned) == 8 else cleaned


def hash_token(token: str) -> str:
    """SHA-256 is enough for high-entropy random tokens (unlike passwords)."""
    return hashlib.sha256(token.encode()).hexdigest()


def hash_throttle_key(kind: str, value: str) -> str:
    return f"{kind}:{hashlib.sha256(value.encode()).hexdigest()[:64]}"
