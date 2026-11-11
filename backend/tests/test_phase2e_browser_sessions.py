"""Phase 2E Stage A: browser session infrastructure and security.

No real browser is launched: a fake driver replaces Playwright, so these tests
cover ownership, navigation policy, duplicate prevention, pause/resume/cancel,
expiry honesty and the promise that no credentials are persisted.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from app.core.config import settings
from app.integrations.browser.session import BrowserUnavailableError
from app.models.application import ApplicationStatus
from app.models.browser_session import BrowserSession, BrowserSessionState, BrowserSessionStatus
from app.services import application_url_security
from app.services.application_platforms import (
    ApplicationPlatform,
    ApplicationUrlError,
    normalize_application_url,
)
from app.services.application_url_security import (
    UnsafeNavigationError,
    build_navigation_policy,
    ensure_public_destination,
    sanitize_url_for_display,
)
from app.services.browser_session_manager import (
    BrowserSessionManager,
    set_browser_session_manager,
)
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.test_applications import (
    bearer,
    create_job,
    create_resume,
    create_user,
    prepare_profile,
)

SessionFactory = async_sessionmaker[AsyncSession]

BROWSER = "/api/v1/browser-sessions"
APPLICATIONS = "/api/v1/applications"
GREENHOUSE_URL = "https://boards.greenhouse.io/acme/jobs/123"
PUBLIC_ADDRESS = "93.184.216.34"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeHandle:
    def __init__(self, url: str, *, unsafe: bool = False) -> None:
        self.url = url
        self.alive = True
        self.closed = False
        self.unsafe_navigation = unsafe

    async def current_url(self) -> str:
        return self.url

    async def is_alive(self) -> bool:
        return self.alive and not self.closed

    async def close(self) -> None:
        self.closed = True
        self.alive = False


class FakeDriver:
    def __init__(self) -> None:
        self.opened: list[str] = []
        self.handles: list[FakeHandle] = []
        self.fail = False
        self.unsafe = False

    async def open(
        self,
        url: str,
        *,
        policy: Any,
        user_id: str,
        pinned_address: str | None = None,
    ) -> FakeHandle:
        del policy, user_id
        if self.fail:
            raise BrowserUnavailableError("Playwright is not installed.")
        handle = FakeHandle(url, unsafe=self.unsafe)
        self.opened.append(url)
        self.handles.append(handle)
        return handle


@pytest_asyncio.fixture
async def browser_runtime(monkeypatch: pytest.MonkeyPatch):
    """Install a fake browser manager and a deterministic DNS resolver."""
    monkeypatch.setattr(settings, "APPLICATION_AUTOMATION_ENABLED", False)

    async def resolve(host: str, port: int) -> list[str]:
        del host, port
        return [PUBLIC_ADDRESS]

    monkeypatch.setattr(application_url_security, "_resolve_host", resolve)

    clock = FakeClock()
    driver = FakeDriver()
    manager = BrowserSessionManager(driver=driver, ttl_seconds=900, clock=clock)
    set_browser_session_manager(manager)
    yield SimpleNamespace(driver=driver, manager=manager, clock=clock)
    await manager.close_all()
    set_browser_session_manager(None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def prepare_application(
    client: AsyncClient,
    session_factory: SessionFactory,
    *,
    job_url: str = GREENHOUSE_URL,
    application_url: str | None = None,
    email: str = "browser@example.com",
    google_sub: str = "google-sub-browser",
) -> tuple[Any, dict[str, Any]]:
    user = await create_user(session_factory, email=email, google_sub=google_sub)
    job = await create_job(session_factory, user, job_url=job_url)
    await create_resume(session_factory, user)
    await prepare_profile(client, user)
    payload: dict[str, Any] = {"job_id": str(job.id)}
    if application_url:
        payload["application_url"] = application_url
    response = await client.post(
        f"{APPLICATIONS}/analyze",
        json=payload,
        headers=bearer(user),
    )
    assert response.status_code == 200, response.text
    return user, response.json()


async def start_session(
    client: AsyncClient, user: Any, application_id: str, *, restart: bool = False
):
    return await client.post(
        BROWSER,
        json={"application_id": application_id, "restart": restart},
        headers=bearer(user),
    )


# ---------------------------------------------------------------------------
# URL policy: static
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/apply",
        "http://127.0.0.1/apply",
        "http://10.0.0.5/apply",
        "http://192.168.1.10/apply",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/apply",
        "http://metadata.google.internal/apply",
        "http://service.internal/apply",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "https://user:secret@boards.greenhouse.io/acme/jobs/1",
        "https://boards.greenhouse.io:8443/acme/jobs/1",
    ],
)
def test_unsafe_urls_are_rejected(url: str) -> None:
    with pytest.raises(ApplicationUrlError):
        normalize_application_url(url)


def test_public_application_url_is_normalised() -> None:
    assert (
        normalize_application_url(f"{GREENHOUSE_URL}#fragment")
        == GREENHOUSE_URL
    )


def test_navigation_policy_pins_the_employer_and_platform_domains() -> None:
    policy = build_navigation_policy(
        GREENHOUSE_URL, platform=ApplicationPlatform.GREENHOUSE
    )
    assert policy.allows(GREENHOUSE_URL)
    assert policy.allows("https://boards.greenhouse.io/acme/jobs/123/apply")
    assert policy.allows("https://id.greenhouse.io/users/sign_in")
    # Other origins, other schemes and other ports are refused.
    assert not policy.allows("https://evil.example/apply")
    assert not policy.allows("http://boards.greenhouse.io/acme/jobs/123")
    assert not policy.allows("https://boards.greenhouse.io:8443/acme/jobs/123")
    assert not policy.allows("file:///etc/passwd")
    with pytest.raises(UnsafeNavigationError):
        policy.check("https://evil.example/apply")


def test_navigation_policy_allows_explicit_operator_origins() -> None:
    policy = build_navigation_policy(
        GREENHOUSE_URL,
        platform=ApplicationPlatform.GREENHOUSE,
        extra_origins=("https://login.acme.com",),
    )
    assert policy.allows("https://login.acme.com/sso")
    assert not policy.allows("https://login.acme.com.evil.example/sso")


@pytest.mark.asyncio
async def test_private_dns_resolution_is_rejected() -> None:
    async def private(host: str, port: int) -> list[str]:
        del host, port
        return ["10.0.0.7"]

    with pytest.raises(UnsafeNavigationError) as error:
        await ensure_public_destination(GREENHOUSE_URL, resolver=private)
    assert error.value.code == "unsafe_application_host"


def test_display_url_never_persists_query_or_fragment() -> None:
    sanitized = sanitize_url_for_display(
        "https://boards.greenhouse.io/apply?token=secret#otp"
    )
    assert sanitized == "https://boards.greenhouse.io/apply"
    assert "secret" not in sanitized


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------
async def test_session_requires_authentication(client: AsyncClient) -> None:
    response = await client.post(
        BROWSER, json={"application_id": str(uuid.uuid4())}
    )
    assert response.status_code == 401
    assert (await client.get(f"{BROWSER}/active")).status_code == 401


async def test_start_returns_a_ready_owned_session(
    client: AsyncClient, session_factory: SessionFactory, browser_runtime: Any
) -> None:
    user, preview = await prepare_application(client, session_factory)
    response = await start_session(client, user, preview["application_id"])
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["state"] == BrowserSessionState.BROWSER_READY.value
    assert body["browser_status"] == BrowserSessionStatus.OPEN.value
    assert body["active"] is True
    assert body["run_number"] == 1
    assert body["application_id"] == preview["application_id"]
    assert body["current_url"] == GREENHOUSE_URL
    assert body["view"]["available"] is False
    assert body["view"]["user_can_interact"] is False
    assert body["view"]["required_component"]
    assert browser_runtime.driver.opened == [GREENHOUSE_URL]

    # The session is readable by id and by application.
    by_id = await client.get(f"{BROWSER}/{body['session_id']}", headers=bearer(user))
    assert by_id.status_code == 200
    by_app = await client.get(
        f"{BROWSER}/by-application/{preview['application_id']}", headers=bearer(user)
    )
    assert by_app.status_code == 200
    active = await client.get(f"{BROWSER}/active", headers=bearer(user))
    assert active.status_code == 200
    assert active.json()["total"] == 1


async def test_start_is_idempotent_while_live(
    client: AsyncClient, session_factory: SessionFactory, browser_runtime: Any
) -> None:
    user, preview = await prepare_application(client, session_factory)
    first = await start_session(client, user, preview["application_id"])
    second = await start_session(client, user, preview["application_id"])
    assert first.status_code == 200 and second.status_code == 200
    assert second.json()["session_id"] == first.json()["session_id"]
    assert second.json()["run_number"] == 1
    assert len(browser_runtime.driver.opened) == 1

    # An explicit restart cannot silently replace a live browser.
    conflict = await start_session(
        client, user, preview["application_id"], restart=True
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "browser_session_already_open"
    assert len(browser_runtime.driver.opened) == 1


async def test_session_is_user_scoped(
    client: AsyncClient, session_factory: SessionFactory, browser_runtime: Any
) -> None:
    owner, preview = await prepare_application(client, session_factory)
    other = await create_user(
        session_factory, email="intruder@example.com", google_sub="google-intruder"
    )
    started = await start_session(client, owner, preview["application_id"])
    assert started.status_code == 200, started.text
    session_id = started.json()["session_id"]

    assert (
        await client.get(f"{BROWSER}/{session_id}", headers=bearer(other))
    ).status_code == 404
    assert (
        await client.get(
            f"{BROWSER}/by-application/{preview['application_id']}", headers=bearer(other)
        )
    ).status_code == 404
    assert (
        await client.post(
            f"{BROWSER}/by-application/{preview['application_id']}/pause",
            headers=bearer(other),
        )
    ).status_code == 404
    assert (
        await start_session(client, other, preview["application_id"])
    ).status_code == 404


async def test_job_board_links_are_never_opened(
    client: AsyncClient, session_factory: SessionFactory, browser_runtime: Any
) -> None:
    user, preview = await prepare_application(
        client,
        session_factory,
        email="board@example.com",
        google_sub="google-board",
        job_url="https://www.glassdoor.com/job-listing/backend-JV_1.htm",
    )
    body = await start_session(client, user, preview["application_id"])
    assert body.status_code == 400
    assert body.json()["code"] == "job_board_url_not_supported"
    assert browser_runtime.driver.opened == []


async def test_private_destination_is_rejected_before_launch(
    client: AsyncClient,
    session_factory: SessionFactory,
    browser_runtime: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, preview = await prepare_application(client, session_factory)

    async def private(host: str, port: int) -> list[str]:
        del host, port
        return ["192.168.0.9"]

    monkeypatch.setattr(application_url_security, "_resolve_host", private)
    response = await start_session(client, user, preview["application_id"])
    assert response.status_code == 400
    assert response.json()["code"] == "unsafe_application_host"
    assert browser_runtime.driver.opened == []


async def test_pause_resume_cancel_controls(
    client: AsyncClient, session_factory: SessionFactory, browser_runtime: Any
) -> None:
    user, preview = await prepare_application(client, session_factory)
    application_id = preview["application_id"]
    started = await start_session(client, user, application_id)
    assert started.status_code == 200, started.text

    paused = await client.post(
        f"{BROWSER}/by-application/{application_id}/pause",
        json={"reason": "Checking a detail"},
        headers=bearer(user),
    )
    assert paused.status_code == 200, paused.text
    assert paused.json()["state"] == BrowserSessionState.PAUSED.value
    assert paused.json()["pause_reason"] == "Checking a detail"
    assert paused.json()["can_resume"] is True
    assert paused.json()["can_pause"] is False
    assert browser_runtime.driver.handles[0].closed is False

    resumed = await client.post(
        f"{BROWSER}/by-application/{application_id}/resume", headers=bearer(user)
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["state"] == BrowserSessionState.BROWSER_READY.value
    assert resumed.json()["pause_reason"] is None

    cancelled = await client.post(
        f"{BROWSER}/by-application/{application_id}/cancel", headers=bearer(user)
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["state"] == BrowserSessionState.CANCELLED.value
    assert cancelled.json()["browser_status"] == BrowserSessionStatus.CLOSED.value
    assert cancelled.json()["can_cancel"] is False
    assert browser_runtime.driver.handles[0].closed is True

    # Nothing completed a submission; the application is untouched.
    application = await client.get(
        f"{APPLICATIONS}/{application_id}", headers=bearer(user)
    )
    assert application.status_code == 200
    assert application.json()["status"] != ApplicationStatus.SUBMITTED.value
    assert application.json()["submitted_at"] is None

    # A cancelled session cannot be resumed or paused.
    assert (
        await client.post(
            f"{BROWSER}/by-application/{application_id}/resume", headers=bearer(user)
        )
    ).status_code == 400
    assert (
        await client.post(
            f"{BROWSER}/by-application/{application_id}/pause", headers=bearer(user)
        )
    ).status_code == 400

    # Starting again is an explicit new run, not a restored browser.
    restarted = await start_session(client, user, application_id)
    assert restarted.status_code == 200, restarted.text
    assert restarted.json()["run_number"] == 2
    assert len(browser_runtime.driver.opened) == 2


async def test_expired_session_is_reported_and_never_pretended(
    client: AsyncClient, session_factory: SessionFactory, browser_runtime: Any
) -> None:
    user, preview = await prepare_application(client, session_factory)
    application_id = preview["application_id"]
    started = await start_session(client, user, application_id)
    assert started.status_code == 200, started.text
    assert browser_runtime.driver.handles[0].closed is False

    browser_runtime.clock.advance(901)

    fetched = await client.get(
        f"{BROWSER}/by-application/{application_id}", headers=bearer(user)
    )
    assert fetched.status_code == 200, fetched.text
    body = fetched.json()
    assert body["browser_status"] == BrowserSessionStatus.EXPIRED.value
    assert body["state"] == BrowserSessionState.PAUSED.value
    assert body["active"] is False
    assert body["can_resume"] is False
    assert "did not preserve" in body["pause_reason"]
    assert browser_runtime.driver.handles[0].closed is True

    resume = await client.post(
        f"{BROWSER}/by-application/{application_id}/resume", headers=bearer(user)
    )
    assert resume.status_code == 400
    assert resume.json()["code"] == "browser_session_expired"

    reopened = await start_session(client, user, application_id)
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["run_number"] == 2
    assert reopened.json()["browser_status"] == BrowserSessionStatus.OPEN.value


async def test_browser_unavailable_is_reported_honestly(
    client: AsyncClient, session_factory: SessionFactory, browser_runtime: Any
) -> None:
    user, preview = await prepare_application(client, session_factory)
    browser_runtime.driver.fail = True
    response = await start_session(client, user, preview["application_id"])
    assert response.status_code == 503
    assert response.json()["code"] == "browser_automation_unavailable"

    fetched = await client.get(
        f"{BROWSER}/by-application/{preview['application_id']}", headers=bearer(user)
    )
    assert fetched.status_code == 200
    assert fetched.json()["state"] == BrowserSessionState.FAILED.value
    assert (
        fetched.json()["browser_status"] == BrowserSessionStatus.UNAVAILABLE.value
    )
    assert fetched.json()["failure_code"] == "browser_automation_unavailable"


async def test_unsafe_navigation_stops_the_session(
    client: AsyncClient, session_factory: SessionFactory, browser_runtime: Any
) -> None:
    user, preview = await prepare_application(client, session_factory)
    browser_runtime.driver.unsafe = True
    response = await start_session(client, user, preview["application_id"])
    assert response.status_code == 400
    assert response.json()["code"] == "navigation_blocked"

    fetched = await client.get(
        f"{BROWSER}/by-application/{preview['application_id']}", headers=bearer(user)
    )
    assert fetched.status_code == 200
    assert fetched.json()["state"] == BrowserSessionState.FAILED.value


async def test_one_live_browser_per_user(
    client: AsyncClient, session_factory: SessionFactory, browser_runtime: Any
) -> None:
    user = await create_user(
        session_factory, email="limit@example.com", google_sub="google-limit"
    )
    await create_resume(session_factory, user)
    await prepare_profile(client, user)
    applications = []
    for _index in range(2):
        job = await create_job(session_factory, user)
        response = await client.post(
            f"{APPLICATIONS}/analyze",
            json={"job_id": str(job.id), "application_url": GREENHOUSE_URL},
            headers=bearer(user),
        )
        assert response.status_code == 200, response.text
        applications.append(response.json()["application_id"])

    first = await start_session(client, user, applications[0])
    assert first.status_code == 200, first.text
    second = await start_session(client, user, applications[1])
    assert second.status_code == 409
    assert second.json()["code"] == "browser_session_limit_reached"
    assert len(browser_runtime.driver.opened) == 1


async def test_persisted_session_contains_no_credentials(
    client: AsyncClient, session_factory: SessionFactory, browser_runtime: Any
) -> None:
    user, preview = await prepare_application(client, session_factory)
    response = await start_session(client, user, preview["application_id"])
    assert response.status_code == 200, response.text
    body = response.json()

    forbidden = {"cookie", "cookies", "storage_state", "password", "token", "otp", "secret"}
    assert not forbidden.intersection(body.keys())
    assert body["checkpoint"] == {}

    async with session_factory() as session:
        row = await session.scalar(select(BrowserSession))
        assert row is not None
        assert row.state is BrowserSessionState.BROWSER_READY
        columns = {column.name for column in row.__table__.columns}
        assert not {
            "cookies",
            "storage_state",
            "password",
            "otp_secret",
            "auth_token",
            "cdp_url",
        }.intersection(columns)
        assert row.allowed_origins == ["https://boards.greenhouse.io:443"]
        serialized = json.dumps(row.checkpoint).lower()
        assert "cookie" not in serialized and "password" not in serialized


async def test_capabilities_report_the_missing_streaming_component(
    client: AsyncClient,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Pin the deployment state under test instead of depending on the ambient
    # .env, which enables streaming for local end-to-end runs.
    monkeypatch.setattr(settings, "APPLICATION_AUTOMATION_ENABLED", True)
    monkeypatch.setattr(settings, "APPLICATION_BROWSER_STREAMING_ENABLED", False)
    user = await create_user(
        session_factory, email="caps@example.com", google_sub="google-caps"
    )
    response = await client.get(f"{BROWSER}/capabilities", headers=bearer(user))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["view"]["available"] is False
    assert body["view"]["mode"] == "none"
    assert "streaming" in body["view"]["required_component"]
    assert "never clicks the final" in body["final_submission_note"]
