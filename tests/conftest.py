"""Test fixtures. The suite runs against a real PostgreSQL database.

Start the disposable test database with ``make test-db`` (Compose service ``test-db`` on
port 5433), or point ``LUMINA_TEST_DATABASE_URL`` at any PostgreSQL database whose name
contains "test". Everything in it is dropped and rebuilt from the Alembic migrations.

Accounts are inserted straight into the database (fast), but tokens always come from the
real ``POST /auth/login``, so every test goes through genuine authentication.
"""

import asyncio
import os
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from app.api.deps import get_session
from app.core.config import Settings
from app.core.email import EmailMessage
from app.core.storage import PresignedUpload, StoredObject
from app.db import registry  # noqa: F401  (registers all models)
from app.db.base import Base
from app.db.mixins import utcnow
from app.db.session import create_session_factory
from app.main import create_app
from app.modules.identity.domain import Role
from app.modules.identity.models import TeacherProfile, User
from app.modules.identity.security import PasswordHasher

ROOT = Path(__file__).resolve().parent.parent
TEST_DATABASE_URL = os.environ.get(
    "LUMINA_TEST_DATABASE_URL",
    "postgresql+asyncpg://lumina:lumina@localhost:5433/lumina_test",
)
PASSWORD = "correct-horse-battery"  # meets the policy; used by every test account


def alembic_config() -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL.replace("%", "%%"))
    return config


async def _reset_schema() -> None:
    engine = create_async_engine(TEST_DATABASE_URL)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def migrated_database() -> str:
    """Rebuild the test database from the migrations once per run (not ``create_all``),
    so the tests exercise the same schema, triggers and indexes as production."""
    database = make_url(TEST_DATABASE_URL).database or ""
    if "test" not in database:
        pytest.exit(
            f"Refusing to run: database {database!r} does not look like a test database "
            "(its name must contain 'test'). All its data would be deleted.",
            returncode=2,
        )
    try:
        asyncio.run(asyncio.wait_for(_reset_schema(), timeout=10))
    except Exception as exc:
        pytest.exit(
            f"Cannot reach the test database at {make_url(TEST_DATABASE_URL)!r} ({exc!r}).\n"
            "Start it with `make test-db`, or set LUMINA_TEST_DATABASE_URL.",
            returncode=2,
        )
    command.upgrade(alembic_config(), "head")
    return TEST_DATABASE_URL


# Empties every table before each test. DELETE is ~20x faster than TRUNCATE on small
# tables. The two append-only tables refuse DELETE by trigger, so their user triggers are
# switched off for this one transaction (needs only table ownership, not superuser).
_APPEND_ONLY = ("audit_events", "course_versions")
RESET_STATEMENTS = (
    [f"ALTER TABLE {t} DISABLE TRIGGER USER" for t in _APPEND_ONLY]
    + ["UPDATE courses SET published_version_id = NULL"]
    + [f"DELETE FROM {t.name}" for t in reversed(Base.metadata.sorted_tables)]
    + [f"ALTER TABLE {t} ENABLE TRIGGER USER" for t in _APPEND_ONLY]
)


@pytest.fixture
async def engine(migrated_database: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(migrated_database)
    async with engine.begin() as conn:
        for statement in RESET_STATEMENTS:
            await conn.execute(text(statement))
    yield engine
    await engine.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with create_session_factory(engine)() as session:
        yield session


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "environment": "test",
        "database_url": TEST_DATABASE_URL,
        "frontend_url": "http://web.test",
        # Cheap Argon2 for speed; production uses the (much higher) defaults.
        "argon2_memory_cost_kib": 64,
        "argon2_time_cost": 1,
        **overrides,
    }
    return Settings(_env_file=None, **values)


HASHER = PasswordHasher(make_settings())


# --- email ------------------------------------------------------------------------------


class RecordingTransport:
    """Keeps every email the app sends, in order, instead of delivering it."""

    def __init__(self) -> None:
        self.messages: list[EmailMessage] = []

    async def deliver(self, message: EmailMessage) -> None:
        self.messages.append(message)

    def last(self, kind: str, to: str | None = None) -> EmailMessage:
        for message in reversed(self.messages):
            if message.kind == kind and (to is None or message.to == to):
                return message
        raise AssertionError(f"no {kind!r} email to {to!r}; sent: {self.kinds()}")

    def kinds(self) -> list[str]:
        return [m.kind for m in self.messages]

    def token(self, kind: str, to: str | None = None) -> str:
        """The single-use token from the link in the latest email of this kind."""
        match = re.search(r"\?token=([\w\-]+)", self.last(kind, to).body)
        assert match, "the email has no token link"
        return match.group(1)


@pytest.fixture
def outbox() -> RecordingTransport:
    return RecordingTransport()


# --- object storage ----------------------------------------------------------------------


class FakeStorage:
    """In-memory stand-in for S3/MinIO. Tests "upload" with ``put``."""

    def __init__(self) -> None:
        self.objects: dict[str, StoredObject] = {}
        self.upload_limits: dict[str, int] = {}

    def presign_upload(
        self, key: str, *, content_type: str, max_bytes: int, expires_in: int
    ) -> PresignedUpload:
        self.upload_limits[key] = max_bytes
        return PresignedUpload(
            url="https://storage.test/lumina-media",
            fields={"key": key, "Content-Type": content_type, "policy": "signed"},
            expires_in=expires_in,
        )

    def download_url(self, key: str, *, expires_in: int, filename: str | None = None) -> str:
        return f"https://storage.test/lumina-media/{key}?expires={expires_in}&signature=test"

    async def head(self, key: str) -> StoredObject | None:
        return self.objects.get(key)

    def put(self, key: str, size: int, content_type: str) -> None:
        self.objects[key] = StoredObject(
            size=size, content_type=content_type, etag="etag-" + key[-6:]
        )


@pytest.fixture
def storage() -> FakeStorage:
    return FakeStorage()


# --- HTTP clients ------------------------------------------------------------------------


# Apps are reused between tests that use the same settings. FastAPI prepares each route
# (its dependencies, validation models) on the first request of every new app, which costs
# about half a second for all routes: more than most tests take. The per-test parts (the
# email outbox, the storage, the database session) are swapped in by `build` below.
_APPS: list[tuple[Settings, FastAPI]] = []


@pytest.fixture
async def client_factory(
    engine: AsyncEngine, outbox: RecordingTransport, storage: FakeStorage
) -> AsyncIterator[Callable[..., AsyncClient]]:
    """Build extra clients with custom settings (and optionally a fake Google/Microsoft/
    Apple behind ``oauth_transport``)."""
    factory = create_session_factory(engine)
    opened: list[AsyncClient] = []

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    def build(
        settings: Settings,
        oauth_transport: httpx.AsyncBaseTransport | None = None,
        media_scanner: Any = None,
    ) -> AsyncClient:
        reusable = oauth_transport is None and media_scanner is None
        app = next((a for s, a in _APPS if s == settings), None) if reusable else None
        if app is None:
            app = create_app(
                settings,
                email_transport=outbox,
                oauth_transport=oauth_transport,
                storage=storage,
                media_scanner=media_scanner,
            )
            if reusable:
                _APPS.append((settings, app))
        else:
            # Same app as an earlier test: give it this test's outbox and storage.
            app.state.email_transport = outbox
            app.state.storage = storage
        app.dependency_overrides[get_session] = override_session
        http = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        opened.append(http)
        return http

    yield build
    for http in opened:
        await http.aclose()


@pytest.fixture
def client(client_factory: Callable[..., AsyncClient]) -> Iterator[AsyncClient]:
    """Anonymous client. Pass ``headers=account.headers`` to act as a user."""
    yield client_factory(make_settings())


# --- accounts ----------------------------------------------------------------------------


@dataclass
class Account:
    id: str
    email: str
    role: Role
    access_token: str = ""
    refresh_token: str = ""
    password: str = PASSWORD
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}


AccountFactory = Callable[..., Awaitable[Account]]


async def login(client: AsyncClient, email: str, password: str = PASSWORD) -> dict[str, Any]:
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return dict(response.json())


@pytest.fixture
def make_account(engine: AsyncEngine, client: AsyncClient) -> AccountFactory:
    """``await make_account(Role.TEACHER, teacher_verified=True)`` -> a signed-in account."""
    factory = create_session_factory(engine)
    counter = 0

    async def make(
        role: Role = Role.PARENT,
        *,
        email: str | None = None,
        verified: bool = True,
        teacher_verified: bool = False,
        sign_in: bool = True,
        name: str | None = None,
    ) -> Account:
        nonlocal counter
        counter += 1
        email = email or f"{role.value}{counter}@example.com"
        async with factory() as session:
            user = User(
                email=email,
                password_hash=HASHER.hash(PASSWORD),
                display_name=name or f"{role.value.title()} {counter}",
                role=role,
                email_verified_at=utcnow() if verified else None,
            )
            session.add(user)
            await session.flush()
            if role == Role.TEACHER:
                session.add(
                    TeacherProfile(
                        user_id=user.id, verified_at=utcnow() if teacher_verified else None
                    )
                )
            await session.commit()
            account = Account(id=str(user.id), email=email, role=role)
        if sign_in:
            tokens = await login(client, email)
            account.access_token = tokens["access_token"]
            account.refresh_token = tokens["refresh_token"]
        return account

    return make


@pytest.fixture
async def admin(make_account: AccountFactory) -> Account:
    return await make_account(Role.PLATFORM_ADMIN, email="admin@example.com")


@pytest.fixture
async def teacher(make_account: AccountFactory) -> Account:
    """A verified teacher: may publish."""
    return await make_account(Role.TEACHER, email="teacher@example.com", teacher_verified=True)


# --- catalog helpers ---------------------------------------------------------------------


async def make_subject(
    client: AsyncClient, name: str = "Mathematics", *, headers: dict[str, str]
) -> dict[str, Any]:
    response = await client.post("/api/v1/subjects", json={"name": name}, headers=headers)
    assert response.status_code == 201, response.text
    return dict(response.json())


async def make_course(
    client: AsyncClient, subject_id: str, *, headers: dict[str, str], **overrides: Any
) -> dict[str, Any]:
    payload = {
        "title": "Fractions Fundamentals",
        "summary": "Equivalent fractions and comparing.",
        "grade_level": 4,
        "subject_id": subject_id,
        "status": "published",
        **overrides,
    }
    response = await client.post("/api/v1/courses", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return dict(response.json())


async def upload_media(
    client: AsyncClient,
    storage: FakeStorage,
    headers: dict[str, str],
    *,
    kind: str = "image",
    content_type: str = "image/png",
    size: int = 1234,
    **fields: Any,
) -> dict[str, Any]:
    """Create, "upload" and confirm a media file; returns the ready media."""
    if kind == "image" and "alt_text" not in fields and not fields.get("decorative"):
        fields["alt_text"] = "A test picture"
    response = await client.post(
        "/api/v1/media",
        json={
            "kind": kind,
            "filename": f"file.{content_type.split('/')[1]}",
            "content_type": content_type,
            "size_bytes": size,
            **fields,
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    created = response.json()
    storage.put(created["upload"]["fields"]["key"], size, content_type)
    done = await client.post(f"/api/v1/media/{created['media']['id']}/complete", headers=headers)
    assert done.status_code == 200, done.text
    return dict(done.json())
