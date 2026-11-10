"""Stage H reliability checks: degraded dependencies and counters."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from app.core.config import settings
from app.core.security import create_access_token
from app.db.session import get_db
from app.main import app
from app.services.browser_session_service import BrowserSessionCapacityError
from app.services.token_revocation import revoke_token
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.test_phase2e_stage_h_hardening import _create_user

SessionFactory = async_sessionmaker[AsyncSession]


class _BrokenSession:
    """A session whose every statement fails, simulating a DB outage."""

    async def execute(self, *args: object, **kwargs: object) -> object:
        del args, kwargs
        raise ConnectionError("database unavailable")

    async def rollback(self) -> None:
        return


async def test_readiness_reports_503_when_the_database_is_down() -> None:
    async def broken_db() -> AsyncIterator[object]:
        yield _BrokenSession()

    app.dependency_overrides[get_db] = broken_db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.get("/api/v1/ready")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    database = next(check for check in body["checks"] if check["name"] == "database")
    assert database["ok"] is False
    # Liveness must stay independent of the database.
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        assert (await client.get("/api/v1/health")).status_code == 200


def test_capacity_error_has_a_retryable_envelope() -> None:
    error = BrowserSessionCapacityError()
    assert error.status_code == 503
    assert error.code == "browser_capacity_reached"
    assert "capacity" in error.message


async def test_rate_limit_rejections_are_counted(
    client: AsyncClient, session_factory: SessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "API_RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(settings, "API_RATE_LIMIT_PER_MINUTE", 2)
    monkeypatch.setattr(settings, "METRICS_TOKEN", None)

    user_id = await _create_user(session_factory)
    headers = {"Authorization": f"Bearer {create_access_token(user_id)}"}
    for _ in range(2):
        assert (await client.get("/api/v1/applications", headers=headers)).status_code == 200
    assert (await client.get("/api/v1/applications", headers=headers)).status_code == 429

    metrics = await client.get("/api/v1/metrics")
    assert metrics.status_code == 200
    assert 'applypilot_rate_limited_total{scope="api"}' in metrics.text
    assert 'applypilot_http_requests_total{method="GET"' in metrics.text
    # A request rejected before routing is labelled "unmatched" on purpose, so
    # arbitrary attacker paths cannot explode metric cardinality.
    assert 'route="unmatched",status="429"' in metrics.text
    # The histogram count must match the two routed requests, not double-count
    # cumulative buckets.
    assert (
        'applypilot_http_request_duration_seconds_count'
        '{method="GET",route="/api/v1/applications"} 2' in metrics.text
    )


async def test_double_revocation_is_idempotent(
    session_factory: SessionFactory,
) -> None:
    user_id = await _create_user(session_factory)
    token = create_access_token(user_id)
    from app.core.security import decode_access_token

    claims = decode_access_token(token)

    async with session_factory() as session:
        assert await revoke_token(
            session, jti=str(claims["jti"]), user_id=user_id, claims=claims
        )
        # A repeated logout (or a retried request) is not an error.
        assert not await revoke_token(
            session, jti=str(claims["jti"]), user_id=user_id, claims=claims
        )


async def test_token_without_jti_is_rejected(client: AsyncClient) -> None:
    from datetime import datetime, timedelta, timezone

    import jwt

    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "type": "access",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
        },
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )
    response = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 401
