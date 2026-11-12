"""Create the first platform admin (there is no other way to get one).

    python -m app.scripts.create_admin --email admin@example.com

The password comes from, in order: the LUMINA_ADMIN_PASSWORD environment variable,
--generate-password (printed once), or an interactive prompt. It is never accepted as a
command-line argument, which would end up in shell history and `ps` output.

With --if-missing, an existing account is left untouched (for idempotent setup scripts).
"""

import argparse
import asyncio
import getpass
import os
import secrets
import sys

from pydantic import EmailStr, TypeAdapter, ValidationError

from app.core.config import get_settings
from app.core.exceptions import DomainValidationError
from app.db import registry  # noqa: F401  (registers all models)
from app.db.mixins import utcnow
from app.db.session import create_engine, create_session_factory
from app.modules.audit.domain import AuditAction
from app.modules.audit.repository import AuditRepository
from app.modules.audit.service import AuditLog
from app.modules.identity.domain import PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH, Role
from app.modules.identity.models import User
from app.modules.identity.repository import UserRepository
from app.modules.identity.security import PasswordHasher
from app.modules.identity.service import check_password_policy, normalize_email

PASSWORD_ENV = "LUMINA_ADMIN_PASSWORD"


def _read_password(generate: bool, email: str) -> tuple[str, bool]:
    """Return (password, was_generated)."""
    if os.environ.get(PASSWORD_ENV):
        return os.environ[PASSWORD_ENV], False
    if generate:
        return secrets.token_urlsafe(18), True
    if not sys.stdin.isatty():
        sys.exit(f"No terminal to prompt on: set {PASSWORD_ENV} or use --generate-password.")
    first = getpass.getpass(f"Password for {email} ({PASSWORD_MIN_LENGTH}+ characters): ")
    if getpass.getpass("Repeat the password: ") != first:
        sys.exit("The passwords do not match.")
    return first, False


async def create_admin(email: str, name: str, *, generate: bool, if_missing: bool) -> int:
    settings = get_settings()
    engine = create_engine(settings)
    try:
        async with create_session_factory(engine)() as session:
            users = UserRepository(session)
            if await users.get_by_email(email) is not None:
                if if_missing:
                    print(f"An account for {email} already exists; nothing to do.")
                    return 0
                print(f"An account for {email} already exists.", file=sys.stderr)
                return 1

            password, generated = _read_password(generate, email)
            if not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
                print(
                    f"The password must be {PASSWORD_MIN_LENGTH} to {PASSWORD_MAX_LENGTH} "
                    "characters long.",
                    file=sys.stderr,
                )
                return 1
            try:
                check_password_policy(password, email)
            except DomainValidationError as exc:
                print(exc.message, file=sys.stderr)
                return 1

            user = User(
                email=email,
                password_hash=PasswordHasher(settings).hash(password),
                display_name=name,
                role=Role.PLATFORM_ADMIN,
                email_verified_at=utcnow(),
            )
            await users.add(user)
            await AuditLog(AuditRepository(session)).record(
                AuditAction.ADMIN_CREATED_VIA_CLI,
                actor_id=user.id,
                entity_type="user",
                entity_id=user.id,
            )
            await session.commit()
    finally:
        await engine.dispose()

    print(f"Created platform admin {email}.")
    if generated:
        # Shown once, on the operator's terminal only. Not logged anywhere.
        print(f"Generated password (store it now, it is not shown again): {password}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a platform admin account.")
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", default="Administrator")
    parser.add_argument("--generate-password", action="store_true")
    parser.add_argument("--if-missing", action="store_true")
    args = parser.parse_args()
    try:
        email = normalize_email(TypeAdapter(EmailStr).validate_python(args.email))
    except ValidationError:
        sys.exit(f"Not a valid email address: {args.email}")
    code = asyncio.run(
        create_admin(email, args.name, generate=args.generate_password, if_missing=args.if_missing)
    )
    sys.exit(code)


if __name__ == "__main__":
    main()
