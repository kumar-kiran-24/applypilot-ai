"""Phase 2J.5: real-Chromium evidence for the checkbox overwrite race.

A single checkbox can legitimately be planned from a confirmed questionnaire
answer (for example "Do you have a bachelor degree?" -> "No"). Because the plan
can say "this box stays unchecked", the engine must never uncheck a box the user
checked after approval.

These tests drive the actual service and the actual DOM. They assert the live
checkbox state before and after every step, so a passing result means the page
was not changed, not merely that a method returned False.

Run explicitly:

    APPLYPILOT_RUN_BROWSER_TESTS=1 python -m pytest \
        tests/test_phase2j5_checkbox_browser.py -m browser -q
"""

from __future__ import annotations

import os
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
import pytest_asyncio

if os.environ.get("APPLYPILOT_RUN_BROWSER_TESTS") != "1":
    pytest.skip(
        "Set APPLYPILOT_RUN_BROWSER_TESTS=1 to run the real Chromium test.",
        allow_module_level=True,
    )

from app.core.config import settings
from app.integrations.browser.field_actions import (
    FieldAction,
    FieldActionKind,
    FieldActionResult,
)
from app.integrations.browser.persistent import PlaywrightPersistentBrowserDriver
from app.models.application import (
    AnswerSource,
    Application,
    ApplicationQuestion,
    ApplicationStatus,
    QuestionStatus,
    QuestionType,
)
from app.models.candidate import Candidate
from app.models.job import Job
from app.models.user import User
from app.services.browser_session_manager import (
    BrowserSessionManager,
    get_browser_session_manager,
    set_browser_session_manager,
)
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.test_applications import bearer

pytestmark = pytest.mark.browser

SessionFactory = async_sessionmaker[AsyncSession]

BROWSER = "/api/v1/browser-sessions"

NAME_ID = "form0-name"
EMAIL_ID = "form0-email"
DEGREE_ID = "form0-degree"

FORM_HTML = """
<!doctype html>
<html><body>
<h1>Controlled employer application</h1>
<form id="application" onsubmit="event.preventDefault();">
  <label for="name">Full name</label>
  <input id="name" name="full_name" type="text" required>

  <label for="email">Email address</label>
  <input id="email" name="email" type="email">

  <label for="degree">Do you have a bachelor degree?</label>
  <input id="degree" name="degree" type="checkbox">

  <label for="otp">Verification code</label>
  <input id="otp" name="otp_code" type="text">
</form>
</body></html>
"""


class _Policy:
    """Test-only navigation policy pinned to the loopback origin."""

    def __init__(self, origin: str) -> None:
        self.origin = origin
        self.redirect_flow = False

    def allows(self, url: str) -> bool:
        return url.startswith(self.origin)

    @property
    def allowed_origins(self) -> tuple[str, ...]:
        return (self.origin,)


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # BaseHTTPRequestHandler API
        body = FORM_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture()
def origin() -> Any:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


@pytest_asyncio.fixture
async def manager(monkeypatch: pytest.MonkeyPatch) -> Any:
    """A real Chromium manager with the test-only loopback policy bypass."""
    monkeypatch.setattr(settings, "APPLICATION_AUTOMATION_ENABLED", True)
    monkeypatch.setattr(settings, "APPLICATION_BROWSER_HEADLESS", True)

    async def _allow_local(url: str, *, resolver: object = None) -> tuple[str, tuple[str, ...]]:
        del resolver
        return url, ("127.0.0.1",)

    monkeypatch.setattr(
        "app.services.browser_session_service.validate_application_destination",
        _allow_local,
    )
    for target in (
        "app.services.application_platforms.normalize_application_url",
        "app.services.application_adapters.normalize_application_url",
        "app.services.application_url_security.normalize_application_url",
    ):
        monkeypatch.setattr(target, lambda url: url)

    live_manager = BrowserSessionManager(
        driver=PlaywrightPersistentBrowserDriver(settings), ttl_seconds=900
    )
    set_browser_session_manager(live_manager)
    try:
        yield live_manager
    finally:
        await live_manager.close_all()
        set_browser_session_manager(None)


async def _seed(
    session_factory: SessionFactory,
    origin: str,
    *,
    degree_answer: str,
) -> tuple[User, Application]:
    """One owned application with a user-confirmed degree answer."""
    async with session_factory() as session:
        user = User(
            google_sub=f"google-{uuid.uuid4().hex}",
            email=f"checkbox-{uuid.uuid4().hex[:8]}@example.com",
            email_verified=True,
            full_name="Ada Lovelace",
            is_active=True,
        )
        session.add(user)
        await session.flush()
        candidate = Candidate(
            user_id=user.id,
            full_name="Ada Lovelace",
            email=user.email,
            phone="+1 555 0100",
            city="Austin",
            country="United States",
        )
        session.add(candidate)
        await session.flush()
        job = Job(
            user_id=user.id,
            dedupe_key=f"checkbox:{uuid.uuid4().int}",
            source="glassdoor",
            job_title="Senior Backend Engineer",
            company="Acme",
            location="Remote",
            job_url="https://www.glassdoor.com/job-listing/backend-JV_1.htm",
            job_description="Python and FastAPI.",
            email_subject="Jobs for you",
            message_id="msg-checkbox",
        )
        session.add(job)
        await session.flush()
        application = Application(
            id=uuid.uuid4(),
            user_id=user.id,
            job_id=job.id,
            candidate_id=candidate.id,
            platform="company_hosted",
            platform_name="Company career site",
            application_url=f"{origin}/apply",
            status=ApplicationStatus.READY_FOR_REVIEW,
            notes=[],
            missing_fields=[],
            automation_details={},
        )
        session.add(application)
        await session.flush()
        session.add(
            ApplicationQuestion(
                application_id=application.id,
                question_key="education.degree",
                field_identifier="degree",
                section="education",
                question_text="Do you have a bachelor degree?",
                field_type=QuestionType.BOOLEAN,
                required=False,
                options=[],
                answer=degree_answer,
                answer_source=AnswerSource.USER_INPUT,
                confidence=1.0,
                requires_confirmation=False,
                status=QuestionStatus.CONFIRMED,
                order_index=0,
            )
        )
        await session.commit()
        return user, application


async def _open_session(
    client: AsyncClient, user: User, application: Application
) -> dict[str, Any]:
    response = await client.post(
        BROWSER,
        json={"application_id": str(application.id)},
        headers=bearer(user),
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _analyzed_plan(
    client: AsyncClient, user: User, application: Application
) -> dict[str, Any]:
    analyzed = await client.post(
        f"{BROWSER}/by-application/{application.id}/form-analysis",
        headers=bearer(user),
    )
    assert analyzed.status_code == 200, analyzed.text
    planned = await client.get(
        f"{BROWSER}/by-application/{application.id}/fill-plan",
        headers=bearer(user),
    )
    assert planned.status_code == 200, planned.text
    return planned.json()


async def _prepare(
    client: AsyncClient,
    session_factory: SessionFactory,
    origin: str,
    manager: BrowserSessionManager,
    *,
    degree_answer: str,
) -> tuple[User, Application, dict[str, Any], Any]:
    user, application = await _seed(
        session_factory, origin, degree_answer=degree_answer
    )
    session_row = await _open_session(client, user, application)
    plan = await _analyzed_plan(client, user, application)
    live = await manager.get(uuid.UUID(session_row["session_id"]), user_id=user.id)
    assert live is not None
    return user, application, plan, live.handle


def _rows(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["field_id"]: item for item in body["results"]}


def _fill_url(application: Application, suffix: str = "") -> str:
    return f"{BROWSER}/by-application/{application.id}/fill{suffix}"


# ---------------------------------------------------------------------------
# Between execution and resume
# ---------------------------------------------------------------------------
async def test_a_box_checked_before_resume_is_left_untouched(
    client: AsyncClient,
    session_factory: SessionFactory,
    origin: Any,
    manager: BrowserSessionManager,
) -> None:
    """Approved "no"; the user ticks it while the run is paused."""
    _server, url = origin
    user, application, plan, handle = await _prepare(
        client, session_factory, url, manager, degree_answer="No"
    )
    degree = {item["field_id"]: item for item in plan["fields"]}[DEGREE_ID]
    assert degree["status"] == "ready"
    assert degree["proposed_value"] == "no"
    page = handle._page  # type: ignore[attr-defined]
    assert await page.is_checked("#degree") is False

    # The user is interacting with the page, so the run pauses before typing.
    handle.user_input_active = lambda seconds=2.0: True  # type: ignore[assignment]
    paused = await client.post(
        _fill_url(application),
        json={"approval_fingerprint": plan["approval_fingerprint"]},
        headers=bearer(user),
    )
    assert paused.status_code == 200, paused.text
    assert paused.json()["status"] == "needs_user_input"
    assert paused.json()["succeeded_count"] == 0
    handle.user_input_active = lambda seconds=2.0: False  # type: ignore[assignment]

    # The user ticks the box themselves.
    await page.check("#degree")
    assert await page.is_checked("#degree") is True

    resumed = await client.post(
        _fill_url(application, "/resume"),
        json={"approval_fingerprint": plan["approval_fingerprint"]},
        headers=bearer(user),
    )
    assert resumed.status_code == 200, resumed.text
    body = resumed.json()
    rows = _rows(body)

    # The tick survives, and the field comes back for a fresh look.
    assert await page.is_checked("#degree") is True
    assert rows[DEGREE_ID]["status"] == "pending_human_input"
    assert "left your value untouched" in rows[DEGREE_ID]["reason"]
    assert "Do you have a bachelor degree?" in (body["pause_reason"] or "")

    # The rest of the form is still filled.
    assert rows[NAME_ID]["status"] == "succeeded"
    assert rows[EMAIL_ID]["status"] == "succeeded"
    assert await page.input_value("#name") == "Ada Lovelace"
    assert await page.input_value("#email") == user.email


async def test_a_box_unchecked_after_execution_is_not_rechecked(
    client: AsyncClient,
    session_factory: SessionFactory,
    origin: Any,
    manager: BrowserSessionManager,
) -> None:
    """Approved "yes"; automation checked it; the user then unchecks it."""
    _server, url = origin
    user, application, plan, handle = await _prepare(
        client, session_factory, url, manager, degree_answer="Yes"
    )
    degree = {item["field_id"]: item for item in plan["fields"]}[DEGREE_ID]
    assert degree["status"] == "ready"
    assert degree["proposed_value"] == "yes"
    page = handle._page  # type: ignore[attr-defined]

    filled = await client.post(
        _fill_url(application),
        json={"approval_fingerprint": plan["approval_fingerprint"]},
        headers=bearer(user),
    )
    assert filled.status_code == 200, filled.text
    assert _rows(filled.json())[DEGREE_ID]["status"] == "succeeded"
    assert await page.is_checked("#degree") is True

    # The user reverses the automation's change.
    await page.uncheck("#degree")
    assert await page.is_checked("#degree") is False

    resumed = await client.post(
        _fill_url(application, "/resume"),
        json={"approval_fingerprint": plan["approval_fingerprint"]},
        headers=bearer(user),
    )
    assert resumed.status_code == 200, resumed.text
    # Nothing re-applied the approved value over the user's decision.
    assert await page.is_checked("#degree") is False
    assert _rows(resumed.json())[DEGREE_ID]["status"] == "succeeded"
    assert await page.input_value("#name") == "Ada Lovelace"


# ---------------------------------------------------------------------------
# Unchanged checkbox
# ---------------------------------------------------------------------------
async def test_an_unchanged_negative_checkbox_is_a_verified_no_op(
    client: AsyncClient,
    session_factory: SessionFactory,
    origin: Any,
    manager: BrowserSessionManager,
) -> None:
    """The approved "no" already holds: verified, not typed, not uncheckable."""
    _server, url = origin
    user, application, plan, handle = await _prepare(
        client, session_factory, url, manager, degree_answer="No"
    )
    page = handle._page  # type: ignore[attr-defined]

    filled = await client.post(
        _fill_url(application),
        json={"approval_fingerprint": plan["approval_fingerprint"]},
        headers=bearer(user),
    )
    assert filled.status_code == 200, filled.text
    body = filled.json()
    rows = _rows(body)

    assert await page.is_checked("#degree") is False
    assert rows[DEGREE_ID]["status"] == "succeeded"
    assert rows[DEGREE_ID]["verified"] is True
    assert rows[NAME_ID]["status"] == "succeeded"
    assert rows[EMAIL_ID]["status"] == "succeeded"
    assert body["failed_count"] == 0
    assert rows["form0-otp"]["status"] == "pending_human_input"


# ---------------------------------------------------------------------------
# During a multi-field execution (driver contract, real DOM)
# ---------------------------------------------------------------------------
def _check_no() -> FieldAction:
    return FieldAction(
        kind=FieldActionKind.CHECK,
        field_id=DEGREE_ID,
        label="Do you have a bachelor degree?",
        html_id="degree",
        name="degree",
        text="no",
    )


def _fill_name() -> FieldAction:
    return FieldAction(
        kind=FieldActionKind.FILL,
        field_id=NAME_ID,
        label="Full name",
        html_id="name",
        name="full_name",
        text="Ada Lovelace",
    )


def _fill_email(email: str) -> FieldAction:
    return FieldAction(
        kind=FieldActionKind.FILL,
        field_id=EMAIL_ID,
        label="Email address",
        html_id="email",
        name="email",
        text=email,
    )


async def test_a_box_changed_during_execution_is_refused(
    origin: Any, manager: BrowserSessionManager
) -> None:
    """A tick that lands after the live snapshot is never undone."""
    _server, url = origin
    handle = await get_browser_session_manager().driver.open(
        f"{url}/apply",
        policy=_Policy(url),  # type: ignore[arg-type]
        user_id="checkbox-mid-run",
    )
    try:
        page = handle._page  # type: ignore[attr-defined]
        assert (await handle.apply_field_action(_fill_name())).ok
        assert await page.input_value("#name") == "Ada Lovelace"

        # The user ticks the box mid-execution, after the run's snapshot.
        await page.check("#degree")
        assert await page.is_checked("#degree") is True

        refused: FieldActionResult = await handle.apply_field_action(_check_no())
        assert refused.ok is False
        assert refused.requires_review is True
        assert "changed after you approved" in refused.reason
        # The DOM is untouched and the earlier field kept its value.
        assert await page.is_checked("#degree") is True
        assert await page.input_value("#name") == "Ada Lovelace"
    finally:
        await handle.close()


async def test_a_refused_checkbox_does_not_block_unrelated_fields(
    origin: Any, manager: BrowserSessionManager
) -> None:
    """After a refusal the next field is still filled, and the box stays put."""
    _server, url = origin
    handle = await get_browser_session_manager().driver.open(
        f"{url}/apply",
        policy=_Policy(url),  # type: ignore[arg-type]
        user_id="checkbox-unblocked",
    )
    try:
        page = handle._page  # type: ignore[attr-defined]
        await page.check("#degree")
        refused = await handle.apply_field_action(_check_no())
        assert refused.ok is False
        assert refused.requires_review is True

        email = "ada@example.com"
        accepted = await handle.apply_field_action(_fill_email(email))
        assert accepted.ok is True
        assert await page.input_value("#email") == email
        assert await page.is_checked("#degree") is True
    finally:
        await handle.close()
