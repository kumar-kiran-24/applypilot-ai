"""Stage G security and reliability tests.

Covers the hardening that is not already exercised by the Stage A-F suites:
production CORS rules, response security headers, JWT rejection on owned API
routes, and the idle browser-session sweeper that closes expired or crashed
handles.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from typing import Any

import pytest
from app.core.config import settings
from app.core.rate_limit import SlidingWindowRateLimiter
from app.core.security import create_access_token
from app.services.application_platforms import ApplicationPlatform
from app.services.application_url_security import build_navigation_policy
from app.services.browser_session_manager import (
    BrowserSessionConflictError,
    BrowserSessionManager,
)
from httpx import AsyncClient
from pydantic import ValidationError

from tests.test_config_gmail_security import production_settings

APPLICATIONS = "/api/v1/applications"
GREENHOUSE_URL = "https://boards.greenhouse.io/acme/jobs/123"


class _Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _FakeHandle:
    def __init__(self, url: str) -> None:
        self.url = url
        self.alive = True
        self.closed = False
        self.unsafe_navigation = False

    async def current_url(self) -> str:
        return self.url

    async def is_alive(self) -> bool:
        return self.alive and not self.closed

    async def close(self) -> None:
        self.closed = True
        self.alive = False


class _FakeDriver:
    def __init__(self) -> None:
        self.handles: list[_FakeHandle] = []

    async def open(
        self,
        url: str,
        *,
        policy: Any,
        user_id: str,
        pinned_address: str | None = None,
    ) -> _FakeHandle:
        del policy, user_id
        handle = _FakeHandle(url)
        self.handles.append(handle)
        return handle


class _SlowFakeDriver(_FakeDriver):
    """Yields while opening, so two concurrent starts really race."""

    async def open(
        self,
        url: str,
        *,
        policy: Any,
        user_id: str,
        pinned_address: str | None = None,
    ) -> _FakeHandle:
        handle = await super().open(url, policy=policy, user_id=user_id)
        await asyncio.sleep(0)
        return handle


def _policy():
    return build_navigation_policy(GREENHOUSE_URL, platform=ApplicationPlatform.GREENHOUSE)


async def _acquire(manager: BrowserSessionManager, owner: uuid.UUID) -> Any:
    return await manager.acquire(
        session_id=uuid.uuid4(),
        user_id=owner,
        application_id=uuid.uuid4(),
        url=GREENHOUSE_URL,
        policy=_policy(),
    )


# ---------------------------------------------------------------------------
# HTTP hardening
# ---------------------------------------------------------------------------
async def test_api_responses_carry_security_headers(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["X-Request-ID"]


async def test_readiness_reports_active_browser_sessions(client: AsyncClient) -> None:
    response = await client.get("/api/v1/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["active_browser_sessions"] == 0
    assert body["active_browser_views"] == 0
    assert body["single_process"] is True


async def test_owned_application_route_rejects_bad_and_expired_tokens(
    client: AsyncClient,
) -> None:
    malformed = await client.get(APPLICATIONS, headers={"Authorization": "Bearer not-a-real-token"})
    assert malformed.status_code == 401
    assert malformed.json()["detail"] == "Invalid or expired access token."

    expired_token = create_access_token(uuid.uuid4(), expires_delta=timedelta(seconds=-30))
    expired = await client.get(APPLICATIONS, headers={"Authorization": f"Bearer {expired_token}"})
    assert expired.status_code == 401

    missing = await client.get(APPLICATIONS)
    assert missing.status_code == 401


async def test_cookie_alone_never_authenticates_an_api_call(
    client: AsyncClient,
) -> None:
    """The API is bearer-only, so a browser cookie cannot drive mutations.

    The OAuth session cookie is a one-flow state artifact, not an API credential.
    Replaying a cookie (the classic CSRF vector) must therefore be rejected.
    """
    client.cookies.set("applypilot_oauth_session", "forged-session-value")
    response = await client.get(APPLICATIONS)

    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required."


async def test_login_exchange_is_rate_limited_per_client(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "AUTH_RATE_LIMIT_PER_MINUTE", 2)
    headers = {"Content-Type": "application/json"}
    body = {"code": "a" * 43}

    for _ in range(2):
        response = await client.post("/api/v1/auth/login/exchange", json=body, headers=headers)
        assert response.status_code == 400

    limited = await client.post("/api/v1/auth/login/exchange", json=body, headers=headers)
    assert limited.status_code == 429
    assert limited.json()["code"] == "rate_limited"
    assert int(limited.headers["Retry-After"]) >= 1

    # A different scope has its own budget: the login redirect is not blocked.
    login = await client.get("/api/v1/auth/google/login", follow_redirects=False)
    assert login.status_code != 429


def test_production_cors_rejects_http_and_loopback_origins() -> None:
    with pytest.raises(ValidationError) as insecure:
        production_settings(BACKEND_CORS_ORIGINS=["http://app.example.com"])
    assert "absolute https origins" in str(insecure.value)

    with pytest.raises(ValidationError) as loopback:
        production_settings(BACKEND_CORS_ORIGINS=["https://localhost:3000"])
    assert "loopback host" in str(loopback.value)


# ---------------------------------------------------------------------------
# Browser session sweep
# ---------------------------------------------------------------------------
async def test_sweep_closes_crashed_then_expired_browsers() -> None:
    clock = _Clock()
    driver = _FakeDriver()
    manager = BrowserSessionManager(driver=driver, ttl_seconds=900, clock=clock)
    owner = uuid.uuid4()

    await _acquire(manager, owner)
    await _acquire(manager, owner)
    assert manager.live_count() == 2

    # A crashed page is detected before its TTL expires.
    driver.handles[1].alive = False
    assert await manager.sweep_expired() == 1
    assert manager.live_count() == 1
    assert driver.handles[1].closed is True

    # Nothing is closed while the surviving handle is still healthy.
    assert await manager.sweep_expired() == 0

    # Once its TTL elapses the idle session is closed without any request.
    clock.advance(901)
    assert await manager.sweep_expired() == 1
    assert manager.live_count() == 0
    assert driver.handles[0].closed is True


async def test_concurrent_starts_create_only_one_browser_for_a_session() -> None:
    driver = _SlowFakeDriver()
    manager = BrowserSessionManager(driver=driver, ttl_seconds=900)
    session_id = uuid.uuid4()
    owner = uuid.uuid4()

    async def start() -> Any:
        return await manager.acquire(
            session_id=session_id,
            user_id=owner,
            application_id=uuid.uuid4(),
            url=GREENHOUSE_URL,
            policy=_policy(),
        )

    results = await asyncio.gather(start(), start(), return_exceptions=True)
    successes = [item for item in results if not isinstance(item, Exception)]
    conflicts = [item for item in results if isinstance(item, BrowserSessionConflictError)]

    assert len(successes) == 1
    assert len(conflicts) == 1
    assert manager.live_count() == 1
    # The losing browser was closed instead of being leaked.
    assert len(driver.handles) == 2
    assert sum(1 for handle in driver.handles if handle.closed) == 1


class _LimiterClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


async def test_sliding_window_limiter_expires_old_attempts_per_key() -> None:
    clock = _LimiterClock()
    limiter = SlidingWindowRateLimiter(clock=clock)

    assert await limiter.hit("a", limit=2, window_seconds=60) is None
    assert await limiter.hit("a", limit=2, window_seconds=60) is None
    retry_after = await limiter.hit("a", limit=2, window_seconds=60)
    assert retry_after == 60

    # Another key is unaffected, and the window really rolls over.
    assert await limiter.hit("b", limit=2, window_seconds=60) is None
    clock.now += 61
    assert await limiter.hit("a", limit=2, window_seconds=60) is None


async def test_sweep_is_safe_when_no_manager_exists() -> None:
    from app.services import browser_session_manager as module

    previous = module._manager
    module._manager = None
    try:
        assert await module.sweep_browser_sessions() == 0
        assert module.live_browser_count() == 0
    finally:
        module._manager = previous
