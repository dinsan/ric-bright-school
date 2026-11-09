from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.deps import get_session
from app.core.config import Settings
from app.db import registry  # noqa: F401
from app.db.base import Base
from app.db.session import create_session_factory
from app.main import create_app


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    # In-memory SQLite keeps the unit/API suite fast and dependency-free.
    # Run `make test-pg` style checks against PostgreSQL in CI for dialect-specific behaviour.
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def client(engine: AsyncEngine) -> AsyncIterator[AsyncClient]:
    factory = create_session_factory(engine)
    app = create_app(Settings(_env_file=None, environment="test"))  # type: ignore[call-arg]

    async def override_session() -> AsyncIterator[Any]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http


async def make_subject(client: AsyncClient, name: str = "Mathematics") -> dict[str, Any]:
    response = await client.post("/api/v1/subjects", json={"name": name})
    assert response.status_code == 201, response.text
    return dict(response.json())


async def make_course(client: AsyncClient, subject_id: str, **overrides: Any) -> dict[str, Any]:
    payload = {
        "title": "Fractions Fundamentals",
        "summary": "Equivalent fractions and comparing.",
        "grade_level": 4,
        "subject_id": subject_id,
        "status": "published",
        **overrides,
    }
    response = await client.post("/api/v1/courses", json=payload)
    assert response.status_code == 201, response.text
    return dict(response.json())
