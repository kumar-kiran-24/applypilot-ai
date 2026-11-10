"""Stage H security and deployment-hardening tests.

Covers token revocation/logout, production docs lockdown, the new response and
header policy, the global API rate limiter, the metrics endpoint, the
process-wide browser ceiling, DNS pinning helpers and secret auditing.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from app.core.config import settings
from app.core.error_reporting import build_error_payload
from app.core.metrics import get_metrics
from app.core.secrets_audit import secret_fingerprint
from app.core.security import create_access_token
from app.integrations.browser.launch_policy import (
    chromium_launch_args,
    host_resolver_rule,
    preferred_address,
)
from app.main import docs_urls
from app.models.revoked_token import RevokedToken
from app.services.application_platforms import ApplicationPlatform
from app.services.application_url_security import (
    UnsafeNavigationError,
    build_navigation_policy,
    validate_application_destination,
)
from app.services.browser_session_manager import (
    BrowserCapacityError,
    BrowserSessionManager,
)
from app.services.token_revocation import purge_expired_revocations
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.test_config_gmail_security import development_settings, production_settings

SessionFactory = async_sessionmaker[AsyncSession]

ME_PATH = "/api/v1/auth/me"
LOGOUT_PATH = "/api/v1/auth/logout"
GREENHOUSE_URL = "https://boards.greenhouse.io/acme/jobs/123"


async def _create_user(session_factory: SessionFactory) -> uuid.UUID:
    from app.models.user import User

    async with session_factory() as session:
        user = User(
            google_sub=f"stage-h-{uuid.uuid4().hex}",
            email=f"stage-h-{uuid.uuid4().hex[:8]}@example.com",
            email_verified=True,
            full_name="Stage H User",
            is_active=True,
        )
        session.add(user)
        await session.commit()
        return user.id


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Logout / JWT revocation
# ---------------------------------------------------------------------------
async def test_logout_revokes_the_presented_token(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user_id = await _create_user(session_factory)
    token = create_access_token(user_id)
    headers = _bearer(token)

    assert (await client.get(ME_PATH, headers=headers)).status_code == 200

    signed_out = await client.post(LOGOUT_PATH, headers=headers)
    assert signed_out.status_code == 200, signed_out.text
    assert signed_out.json()["revoked"] is True

    # The stateless token is now rejected everywhere, not just on /me.
    assert (await client.get(ME_PATH, headers=headers)).status_code == 401
    assert (
        await client.get("/api/v1/applications", headers=headers)
    ).status_code == 401

    # Repeating logout is harmless but the token stays revoked.
    repeated = await client.post(LOGOUT_PATH, headers=headers)
    assert repeated.status_code == 401


async def test_logout_does_not_revoke_other_sessions(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user_id = await _create_user(session_factory)
    first = create_access_token(user_id)
    second = create_access_token(user_id)

    assert (await client.post(LOGOUT_PATH, headers=_bearer(first))).status_code == 200
    assert (await client.get(ME_PATH, headers=_bearer(first))).status_code == 401
    assert (await client.get(ME_PATH, headers=_bearer(second))).status_code == 200


async def test_expired_revocation_rows_are_purged(
    session_factory: SessionFactory,
) -> None:
    user_id = await _create_user(session_factory)
    async with session_factory() as session:
        session.add(
            RevokedToken(
                jti=uuid.uuid4().hex,
                user_id=user_id,
                expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
                reason="logout",
            )
        )
        session.add(
            RevokedToken(
                jti=uuid.uuid4().hex,
                user_id=user_id,
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
                reason="logout",
            )
        )
        await session.commit()

        assert await purge_expired_revocations(session) == 1


# ---------------------------------------------------------------------------
# Documentation lockdown and headers
# ---------------------------------------------------------------------------
def test_docs_are_disabled_in_production_only() -> None:
    production = docs_urls(production_settings())
    assert production == {
        "docs_url": None,
        "redoc_url": None,
        "openapi_url": None,
    }

    development = docs_urls(development_settings())
    assert development["docs_url"] == "/docs"
    assert development["openapi_url"] == "/openapi.json"


async def test_api_responses_carry_full_security_header_set(
    client: AsyncClient,
) -> None:
    response = await client.get("/api/v1/health")

    assert response.headers["Content-Security-Policy"].startswith(
        "default-src 'none'"
    )
    assert "camera=()" in response.headers["Permissions-Policy"]
    assert response.headers["Cross-Origin-Opener-Policy"] == "same-origin"
    assert response.headers["Cross-Origin-Resource-Policy"] == "same-site"
    assert response.headers["X-Permitted-Cross-Domain-Policies"] == "none"
    assert response.headers["Cache-Control"] == "no-store"
    assert "server" not in {key.lower() for key in response.headers}


def test_production_configuration_rejects_unsafe_toggles() -> None:
    cases = [
        ({"API_DOCS_ENABLED": True}, "API_DOCS_ENABLED"),
        ({"METRICS_ENABLED": True, "METRICS_TOKEN": None}, "METRICS_TOKEN"),
        ({"APPLICATION_BROWSER_PIN_DNS": False}, "APPLICATION_BROWSER_PIN_DNS"),
        ({"API_RATE_LIMIT_ENABLED": False}, "API_RATE_LIMIT_ENABLED"),
        (
            {"ERROR_WEBHOOK_URL": "http://alerts.internal/errors"},
            "ERROR_WEBHOOK_URL",
        ),
    ]
    for overrides, expected in cases:
        with pytest.raises(ValidationError) as exc:
            production_settings(**overrides)
        assert expected in str(exc.value)

    with pytest.raises(ValidationError) as duplicate:
        production_settings(OAUTH_SESSION_SECRET="j" * 48, JWT_SECRET_KEY="j" * 48)
    assert "must differ" in str(duplicate.value)


# ---------------------------------------------------------------------------
# Global API rate limiting
# ---------------------------------------------------------------------------
async def test_api_rate_limit_is_per_user_and_health_is_exempt(
    client: AsyncClient, session_factory: SessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "API_RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(settings, "API_RATE_LIMIT_PER_MINUTE", 2)

    first_user = await _create_user(session_factory)
    second_user = await _create_user(session_factory)
    first_headers = _bearer(create_access_token(first_user))
    second_headers = _bearer(create_access_token(second_user))

    for _ in range(2):
        response = await client.get("/api/v1/applications", headers=first_headers)
        assert response.status_code == 200

    limited = await client.get("/api/v1/applications", headers=first_headers)
    assert limited.status_code == 429
    assert limited.json()["code"] == "rate_limited"
    assert int(limited.headers["Retry-After"]) >= 1

    # A different authenticated principal has its own budget.
    assert (
        await client.get("/api/v1/applications", headers=second_headers)
    ).status_code == 200

    # Probes never consume or exhaust a budget.
    for _ in range(5):
        assert (await client.get("/api/v1/health")).status_code == 200


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
async def test_metrics_are_public_in_development_and_token_protected_when_configured(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "METRICS_ENABLED", True)
    monkeypatch.setattr(settings, "METRICS_TOKEN", None)

    await client.get("/api/v1/health")
    open_response = await client.get("/api/v1/metrics")
    assert open_response.status_code == 200
    assert "applypilot_http_requests_total" in open_response.text
    assert "applypilot_build_info" in open_response.text

    token = "metrics-" + "x" * 32
    monkeypatch.setattr(settings, "METRICS_TOKEN", token)
    assert (await client.get("/api/v1/metrics")).status_code == 401
    authorized = await client.get(
        "/api/v1/metrics", headers={"Authorization": f"Bearer {token}"}
    )
    assert authorized.status_code == 200
    assert "applypilot_http_requests_total" in authorized.text


def test_metrics_registry_does_not_record_request_bodies_or_queries() -> None:
    registry = get_metrics()
    registry.reset()
    registry.record_request(
        method="GET", route="/api/v1/jobs", status_code=200, duration_seconds=0.012
    )
    rendered = registry.render(version="0.1.0", environment="testing")
    assert 'route="/api/v1/jobs"' in rendered
    assert "token" not in rendered
    assert "?" not in rendered


# ---------------------------------------------------------------------------
# Browser capacity and DNS pinning
# ---------------------------------------------------------------------------
class _FakeHandle:
    def __init__(self) -> None:
        self.closed = False
        self.unsafe_navigation = False

    async def is_alive(self) -> bool:
        return not self.closed

    async def close(self) -> None:
        self.closed = True


class _FakeDriver:
    def __init__(self) -> None:
        self.handles: list[_FakeHandle] = []

    async def open(
        self, url: str, *, policy: Any, user_id: str, pinned_address: str | None = None
    ) -> _FakeHandle:
        del url, policy, user_id, pinned_address
        handle = _FakeHandle()
        self.handles.append(handle)
        return handle


def _policy():
    return build_navigation_policy(GREENHOUSE_URL, platform=ApplicationPlatform.GREENHOUSE)


async def test_process_wide_browser_ceiling_refuses_new_sessions() -> None:
    driver = _FakeDriver()
    manager = BrowserSessionManager(driver=driver, ttl_seconds=900, max_sessions=1)

    async def acquire() -> Any:
        return await manager.acquire(
            session_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            application_id=uuid.uuid4(),
            url=GREENHOUSE_URL,
            policy=_policy(),
        )

    await acquire()
    with pytest.raises(BrowserCapacityError):
        await acquire()
    assert manager.live_count() == 1
    assert len(driver.handles) == 1


def test_dns_pinning_builds_a_host_map_and_skips_ip_literals() -> None:
    assert (
        host_resolver_rule(GREENHOUSE_URL, "93.184.216.34")
        == "MAP boards.greenhouse.io 93.184.216.34"
    )
    assert host_resolver_rule(GREENHOUSE_URL, "2606:2800:220:1::1") == (
        "MAP boards.greenhouse.io [2606:2800:220:1::1]"
    )
    # IP-literal URLs connect directly and need no mapping.
    assert host_resolver_rule("http://93.184.216.34/apply", "93.184.216.34") is None
    assert host_resolver_rule(GREENHOUSE_URL, "not-an-ip") is None
    assert preferred_address(("2606:2800:220:1::1", "93.184.216.34")) == "93.184.216.34"


def test_chromium_launch_args_enforce_heap_and_pin_but_keep_site_isolation() -> None:
    args = chromium_launch_args(
        settings,
        url=GREENHOUSE_URL,
        pinned_address="93.184.216.34",
    )

    assert f"--js-flags=--max-old-space-size={settings.APPLICATION_BROWSER_JS_HEAP_MB}" in args
    assert "--host-resolver-rules=MAP boards.greenhouse.io 93.184.216.34" in args
    assert "--disable-dev-shm-usage" in args
    # Collapsing renderer processes would weaken Chromium's site isolation.
    assert not any(argument.startswith("--renderer-process-limit") for argument in args)
    assert not any(argument.startswith("--process-per-site") for argument in args)


async def test_destination_validation_returns_verified_addresses() -> None:
    async def public_resolver(host: str, port: int) -> list[str]:
        del host, port
        return ["93.184.216.34", "93.184.216.35"]

    url, addresses = await validate_application_destination(
        GREENHOUSE_URL, resolver=public_resolver
    )
    assert url == GREENHOUSE_URL
    assert addresses == ("93.184.216.34", "93.184.216.35")

    async def private_resolver(host: str, port: int) -> list[str]:
        del host, port
        return ["10.0.0.5"]

    with pytest.raises(UnsafeNavigationError):
        await validate_application_destination(
            GREENHOUSE_URL, resolver=private_resolver
        )


# ---------------------------------------------------------------------------
# Secret handling
# ---------------------------------------------------------------------------
def test_secret_fingerprints_are_stable_and_non_reversible() -> None:
    fingerprint = secret_fingerprint("s" * 48)
    assert fingerprint == secret_fingerprint("s" * 48)
    assert fingerprint != secret_fingerprint("s" * 47)
    assert len(fingerprint) == 12
    assert "s" * 12 not in fingerprint
    assert secret_fingerprint(None) == "unset"


def test_error_webhook_payload_excludes_messages_and_secrets() -> None:
    payload = build_error_payload(
        error=RuntimeError("password=hunter2 token=abc"),
        method="POST",
        route="/api/v1/applications",
        request_id="req-12345678",
    )
    assert payload["error_class"] == "RuntimeError"
    assert payload["route"] == "/api/v1/applications"
    assert "password" not in str(payload)
    assert "hunter2" not in str(payload)
    assert "message" not in payload
