"""Run the command-line scripts as real subprocesses, the way people use them.

In-process tests import every model through the test setup, which hides a script that
forgot to load all of them (a foreign key to an unknown table only fails at runtime).
"""

import os
import subprocess
import sys

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.modules.catalog.models import Course
from app.modules.identity.models import User
from tests.conftest import ROOT, TEST_DATABASE_URL


def run(*args: str, **env: str) -> subprocess.CompletedProcess[str]:
    environment = {k: v for k, v in os.environ.items() if k != "LUMINA_ADMIN_PASSWORD"}
    environment.update(LUMINA_DATABASE_URL=TEST_DATABASE_URL, **env)
    return subprocess.run(
        [sys.executable, "-m", *args],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=60,
    )


async def test_seed_script_is_idempotent(engine: AsyncEngine) -> None:
    first = run("app.scripts.seed")
    assert first.returncode == 0, first.stderr
    assert "18 new course(s)" in first.stdout
    second = run("app.scripts.seed")
    assert second.returncode == 0, second.stderr
    assert "0 new course(s), 18 already present" in second.stdout

    async with engine.connect() as conn:
        assert await conn.scalar(select(func.count(Course.id))) == 18


async def test_create_admin_script(engine: AsyncEngine) -> None:
    created = run(
        "app.scripts.create_admin",
        "--email",
        "Ops@Example.com",
        LUMINA_ADMIN_PASSWORD="ops-team-passphrase",
    )
    assert created.returncode == 0, created.stderr
    assert "ops-team-passphrase" not in created.stdout + created.stderr

    again = run("app.scripts.create_admin", "--email", "ops@example.com", "--if-missing")
    assert again.returncode == 0 and "already exists" in again.stdout
    duplicate = run("app.scripts.create_admin", "--email", "ops@example.com", "--generate-password")
    assert duplicate.returncode == 1

    generated = run("app.scripts.create_admin", "--email", "x@example.com", "--generate-password")
    assert generated.returncode == 0 and "Generated password" in generated.stdout

    no_terminal = run("app.scripts.create_admin", "--email", "y@example.com")
    assert no_terminal.returncode != 0 and "LUMINA_ADMIN_PASSWORD" in no_terminal.stderr
    weak = run(
        "app.scripts.create_admin", "--email", "z@example.com", LUMINA_ADMIN_PASSWORD="short"
    )
    assert weak.returncode == 1

    async with engine.connect() as conn:
        admins = (
            await conn.execute(select(User.email).where(User.email_verified_at.is_not(None)))
        ).scalars()
        assert sorted(email for email in admins if email) == ["ops@example.com", "x@example.com"]
