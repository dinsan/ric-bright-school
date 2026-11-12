"""Time-based one-time passwords (RFC 6238) and encryption of the stored secrets.

Compatible with every common authenticator app (Google Authenticator, Microsoft
Authenticator, 1Password, Authy, ...): HMAC-SHA1, 6 digits, 30-second steps.
"""

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote, urlencode

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core.config import Settings

DIGITS = 6
STEP_SECONDS = 30
# Accept the previous and next step too: phone clocks drift and people type slowly.
DRIFT_STEPS = 1
ISSUER = "Lumina"


def new_secret() -> str:
    """160 random bits, base32 (what authenticator apps expect)."""
    return base64.b32encode(secrets.token_bytes(20)).decode()


def code_at(secret: str, step: int, digits: int = DIGITS) -> str:
    key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**digits).zfill(digits)


def current_step(now: float | None = None) -> int:
    return int((time.time() if now is None else now) // STEP_SECONDS)


def matching_step(
    secret: str, code: str, *, last_used_step: int | None, now: float | None = None
) -> int | None:
    """The step the code belongs to, or None. A step at or before ``last_used_step`` never
    matches, so an intercepted code cannot be replayed."""
    code = code.strip().replace(" ", "")
    if len(code) != DIGITS or not code.isdigit():
        return None
    center = current_step(now)
    for step in range(center - DRIFT_STEPS, center + DRIFT_STEPS + 1):
        if last_used_step is not None and step <= last_used_step:
            continue
        if hmac.compare_digest(code_at(secret, step), code):
            return step
    return None


def provisioning_uri(secret: str, account_name: str) -> str:
    """The ``otpauth://`` link shown as a QR code to add the account to an app."""
    # Literal ":" between issuer and account: the form every authenticator app parses.
    label = quote(f"{ISSUER}:{account_name}", safe=":")
    query = urlencode(
        {
            "secret": secret,
            "issuer": ISSUER,
            "algorithm": "SHA1",
            "digits": DIGITS,
            "period": STEP_SECONDS,
        }
    )
    return f"otpauth://totp/{label}?{query}"


def new_recovery_code() -> str:
    """e.g. ``7QKM-D2XP-9RTA`` (60 bits): single-use backup for a lost phone."""
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    raw = "".join(secrets.choice(alphabet) for _ in range(12))
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"


def normalize_recovery_code(code: str) -> str:
    cleaned = "".join(ch for ch in code.upper() if ch.isalnum())
    return f"{cleaned[:4]}-{cleaned[4:8]}-{cleaned[8:]}" if len(cleaned) == 12 else cleaned


class SecretBox:
    """Authenticated encryption (Fernet) for TOTP secrets at rest."""

    def __init__(self, settings: Settings) -> None:
        material = (settings.encryption_key or settings.secret_key).get_secret_value().encode()
        key = HKDF(
            algorithm=hashes.SHA256(), length=32, salt=None, info=b"lumina/totp-secrets"
        ).derive(material)
        self._fernet = Fernet(base64.urlsafe_b64encode(key))

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str | None:
        """None if the data cannot be decrypted (wrong key, tampered)."""
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken:
            return None
