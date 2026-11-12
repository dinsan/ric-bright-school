"""Unit tests for the cryptographic building blocks and the password policy."""

import uuid
from datetime import timedelta

import pytest

from app.core.exceptions import DomainValidationError
from app.modules.identity.domain import ROLE_PERMISSIONS, Actor, Permission, Role
from app.modules.identity.security import (
    InvalidAccessToken,
    PasswordHasher,
    create_access_token,
    decode_access_token,
    hash_token,
    new_link_code,
    normalize_link_code,
)
from app.modules.identity.service import check_password_policy, normalize_email
from tests.conftest import make_settings

SETTINGS = make_settings()


def test_password_hash_roundtrip() -> None:
    hasher = PasswordHasher(SETTINGS)
    stored = hasher.hash("correct-horse-battery")
    assert stored.startswith("$argon2id$")
    assert hasher.verify(stored, "correct-horse-battery")
    assert not hasher.verify(stored, "wrong")
    assert not hasher.verify(None, "anything")  # unknown user: still does the work, says no
    assert not hasher.verify("not-a-hash", "anything")


def test_hashes_are_upgraded_when_cost_settings_change() -> None:
    cheap = PasswordHasher(SETTINGS).hash("correct-horse-battery")
    stronger = PasswordHasher(make_settings(argon2_time_cost=2))
    assert stronger.verify(cheap, "correct-horse-battery")
    assert stronger.needs_rehash(cheap)


@pytest.mark.parametrize(
    ("password", "email", "ok"),
    [
        ("correct-horse-battery", "grace@example.com", True),
        ("grace-hopper-1906", "grace@example.com", False),  # contains the email name
        ("GRACE-hopper-1906", "grace@example.com", False),  # case-insensitive
        ("sam-loves-dinosaurs", "sam@example.com", True),  # short names are not checked
        ("aaaaaaaaaaaaaaa", None, False),  # too repetitive
        ("abababababababab", None, False),
        ("abcdabcdabcdabcd", None, True),
    ],
)
def test_password_policy(password: str, email: str | None, ok: bool) -> None:
    if ok:
        check_password_policy(password, email)
    else:
        with pytest.raises(DomainValidationError):
            check_password_policy(password, email)


def test_email_normalisation() -> None:
    assert normalize_email("  Ada.Lovelace@Example.COM ") == "ada.lovelace@example.com"


def test_access_token_roundtrip_and_claims() -> None:
    user_id, session_id, admin_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    token = create_access_token(
        SETTINGS,
        user_id=user_id,
        session_id=session_id,
        role=Role.TEACHER,
        ttl=timedelta(minutes=15),
        impersonator_id=admin_id,
    )
    claims = decode_access_token(SETTINGS, token)
    assert (claims.user_id, claims.session_id, claims.role) == (user_id, session_id, Role.TEACHER)
    assert claims.impersonator_id == admin_id


def test_access_token_rejects_alg_none_and_wrong_audience() -> None:
    import jwt

    unsigned = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "sid": str(uuid.uuid4()),
            "role": "parent",
            "exp": 9e9,
            "iat": 0,
            "typ": "access",
            "iss": "lumina-api",
            "aud": "lumina",
        },
        key="",
        algorithm="none",
    )
    with pytest.raises(InvalidAccessToken):
        decode_access_token(SETTINGS, unsigned)

    wrong_audience = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "sid": str(uuid.uuid4()),
            "role": "parent",
            "exp": 9e9,
            "iat": 0,
            "typ": "access",
            "iss": "lumina-api",
            "aud": "someone-else",
        },
        SETTINGS.secret_key.get_secret_value(),
        algorithm="HS256",
    )
    with pytest.raises(InvalidAccessToken):
        decode_access_token(SETTINGS, wrong_audience)


def test_opaque_tokens_and_link_codes() -> None:
    assert hash_token("abc") == hash_token("abc") != hash_token("abd")
    code = new_link_code()
    assert len(code) == 9 and code[4] == "-"
    assert not set(code) & set("01ILO")  # no look-alike characters
    assert normalize_link_code(code.lower().replace("-", " ")) == code


def test_permissions_are_derived_from_roles() -> None:
    assert ROLE_PERMISSIONS[Role.PLATFORM_ADMIN] == frozenset(Permission)
    assert ROLE_PERMISSIONS[Role.STUDENT] == frozenset()
    actor = Actor(user_id=uuid.uuid4(), role=Role.TEACHER, session_id=uuid.uuid4())
    assert actor.can(Permission.COURSES_AUTHOR)
    assert not actor.can(Permission.USERS_MANAGE)
