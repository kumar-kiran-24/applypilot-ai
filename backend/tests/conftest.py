"""Shared pytest fixtures.

Tests run against an in-memory SQLite database using ``aiosqlite`` and override
the ``get_db`` dependency, so no PostgreSQL instance and no API keys are needed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import app.models
import pytest
import pytest_asyncio
from app.core.metrics import reset_metrics
from app.core.rate_limit import reset_rate_limits
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

TEST_DATABASE_URL = "sqlite+aiosqlite://"


@pytest.fixture(autouse=True)
def reset_in_process_rate_limits() -> Iterator[None]:
    """Keep per-client auth rate limits from leaking between tests."""
    reset_rate_limits()
    reset_metrics()
    yield
    reset_rate_limits()
    reset_metrics()


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    """Isolated in-memory database per test, sharing one connection."""
    test_engine = create_async_engine(
        TEST_DATABASE_URL,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield test_engine
    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await test_engine.dispose()


@pytest_asyncio.fixture
async def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def client(session_factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncClient]:
    """HTTP client bound to the ASGI app with the database dependency overridden."""

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def candidate_payload() -> dict[str, Any]:
    """A valid candidate creation payload."""
    return {
        "full_name": "Ada Lovelace",
        "email": "Ada@Example.com",
        "phone": "+1-555-0100",
        "location": "London, UK",
        "linkedin_url": "https://www.linkedin.com/in/ada-lovelace",
        "github_url": "https://github.com/ada",
        "portfolio_url": "https://ada.example.com",
        "professional_summary": "Backend engineer focused on distributed systems.",
    }


async def create_candidate(client: AsyncClient, payload: dict[str, Any]) -> dict[str, Any]:
    """Helper creating a candidate and returning the response body."""
    response = await client.post("/api/v1/candidates", json=payload)
    assert response.status_code == 201, response.text
    return response.json()
