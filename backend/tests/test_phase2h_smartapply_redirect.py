"""Phase 2H: Glassdoor Easy Apply -> Indeed SmartApply hand-off.

No real browser or network call is made. A fake persistent browser exposes the
live page URL, a controlled DNS resolver keeps every destination public, and
the tests assert both the normal path and the security refusals.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from app.core.config import settings
from app.models.application import Application, RedirectStatus
from app.models.candidate import Candidate
from app.schemas.application_fill import FillFieldStatus
from app.schemas.application_form import (
    DetectedFormField,
    FieldCategory,
    FieldSource,
    FieldSourceMatch,
    FormFieldType,
)
from app.services import application_url_security
from app.services.application_adapters import (
    IndeedSmartApplyAdapter,
    get_adapter,
)
from app.services.application_field_planner import FieldExecutionPlanner
from app.services.application_form_analyzer import ApplicationFormAnalyzer
from app.services.application_platforms import (
    ApplicationPlatform,
    ApplicationUrlError,
    detect_platform,
    is_job_board_url,
    listing_source_for_url,
)
from app.services.application_url_security import (
    build_listing_redirect_policy,
    build_navigation_policy,
)
from app.services.browser_session_manager import (
    BrowserSessionManager,
    set_browser_session_manager,
)
from app.services.indeed_smartapply import (
    SmartApplyStep,
    application_step_for_url,
    inspect_page,
    is_authentication_url,
    is_smartapply_url,
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

APPLICATIONS = "/api/v1/applications"
BROWSER = "/api/v1/browser-sessions"
GLASSDOOR = "https://www.glassdoor.com/job-listing/associate-ai-engineer-JV_1.htm"
GREENHOUSE = "https://boards.greenhouse.io/acme/jobs/123"
SMARTAPPLY_RESUME = (
    "https://smartapply.indeed.com/beta/indeedapply/form/"
    "resume-selection-module/resume-selection"
)
PUBLIC_ADDRESS = "93.184.216.34"

RESUME_SELECTION_HTML = """
<!doctype html>
<html><body>
<main>
  <h1>Associate AI Engineer</h1>
  <p>Digital Green - Bengaluru, Karnataka</p>
  <section aria-label="Resume selection">
    <fieldset>
      <legend>Resume selection</legend>
      <label for="resume-file">Upload resume</label>
      <input id="resume-file" name="resume" type="file" accept="application/pdf" required>
      <label for="applicant-email">Email address</label>
      <input id="applicant-email" name="email" type="email" required>
      <label for="applicant-phone">Phone number</label>
      <input id="applicant-phone" name="phone" type="tel">
      <label for="country">Country</label>
      <select id="country" name="country">
        <option value="">Select a country</option>
        <option>India</option>
      </select>
      <label>
        <input type="radio" name="authorized" value="yes">
        I am authorized to work
      </label>
      <label>
        <input type="checkbox" name="consent">
        I certify the information is true and complete
      </label>
    </fieldset>
  </section>
</main>
</body></html>
"""


class FakeHandle:
    """Minimal live-page contract used by the session and redirect services."""

    def __init__(self, url: str, html: str = RESUME_SELECTION_HTML) -> None:
        self.url = url
        self.html = html
        self.alive = True
        self.closed = False
        self.unsafe_navigation = False
        self.blocked_navigation = False

    async def current_url(self) -> str:
        return self.url

    async def content(self) -> str:
        return self.html

    async def is_alive(self) -> bool:
        return self.alive and not self.closed

    async def close(self) -> None:
        self.closed = True
        self.alive = False


class FakeDriver:
    def __init__(self) -> None:
        self.opened: list[str] = []
        self.handles: list[FakeHandle] = []

    async def open(
        self,
        url: str,
        *,
        policy: Any,
        user_id: str,
        pinned_address: str | None = None,
    ) -> FakeHandle:
        del policy, user_id, pinned_address
        handle = FakeHandle(url)
        self.opened.append(url)
        self.handles.append(handle)
        return handle


async def _public_resolver(host: str, port: int) -> list[str]:
    del host, port
    return [PUBLIC_ADDRESS]


async def _private_resolver(host: str, port: int) -> list[str]:
    del host, port
    return ["10.0.0.7"]


@pytest_asyncio.fixture
async def browser_runtime(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "APPLICATION_AUTOMATION_ENABLED", False)
    monkeypatch.setattr(application_url_security, "_resolve_host", _public_resolver)
    driver = FakeDriver()
    manager = BrowserSessionManager(driver=driver, ttl_seconds=900)
    set_browser_session_manager(manager)
    yield SimpleNamespace(driver=driver, manager=manager)
    await manager.close_all()
    set_browser_session_manager(None)


async def prepare_listing_application(
    client: AsyncClient,
    session_factory: SessionFactory,
    *,
    email: str = "smartapply@example.com",
    google_sub: str = "google-smartapply",
) -> tuple[Any, dict[str, Any]]:
    user = await create_user(session_factory, email=email, google_sub=google_sub)
    job = await create_job(session_factory, user, job_url=GLASSDOOR)
    await create_resume(session_factory, user)
    await prepare_profile(client, user)
    response = await client.post(
        f"{APPLICATIONS}/analyze",
        json={"job_id": str(job.id)},
        headers=bearer(user),
    )
    assert response.status_code == 200, response.text
    return user, response.json()


async def start_listing_session(
    client: AsyncClient, user: Any, application_id: str
):
    return await client.post(
        BROWSER,
        json={"application_id": application_id},
        headers=bearer(user),
    )


async def detect(
    client: AsyncClient, user: Any, application_id: str, *, headers=None
):
    return await client.post(
        f"{BROWSER}/by-application/{application_id}/detect-destination",
        headers=headers or bearer(user),
    )


# ---------------------------------------------------------------------------
# 1-2. Listing acceptance and preservation
# ---------------------------------------------------------------------------
async def test_glassdoor_listing_is_accepted_and_the_original_url_is_preserved(
    client: AsyncClient, session_factory: SessionFactory, browser_runtime: Any
) -> None:
    user, preview = await prepare_listing_application(client, session_factory)
    application_id = preview["application_id"]

    assert preview["application_url"] == GLASSDOOR
    assert preview["job_listing_url"] == GLASSDOOR
    assert preview["application_url_is_job_board"] is True
    assert preview["redirect_flow"] is True
    assert preview["destination_validated"] is False
    assert preview["redirect_status"] == RedirectStatus.NOT_STARTED.value
    assert any("Easy Apply" in note for note in preview["notes"])

    started = await start_listing_session(client, user, application_id)
    assert started.status_code == 200, started.text
    body = started.json()
    assert body["browser_status"] == "open"
    assert body["current_url"] == GLASSDOOR
    assert body["redirect_flow"] is True
    assert body["redirect_status"] == RedirectStatus.AWAITING_USER_ACTION.value
    assert browser_runtime.driver.opened == [GLASSDOOR]


# ---------------------------------------------------------------------------
# 3-5. Redirect recognition, validation and persistence
# ---------------------------------------------------------------------------
async def test_user_started_easy_apply_redirect_is_detected_and_stored(
    client: AsyncClient, session_factory: SessionFactory, browser_runtime: Any
) -> None:
    user, preview = await prepare_listing_application(client, session_factory)
    application_id = preview["application_id"]
    started = await start_listing_session(client, user, application_id)
    assert started.status_code == 200, started.text

    # The user clicked Easy Apply; Playwright navigated inside the approved
    # boundary. Nothing in ApplyPilot clicked it.
    browser_runtime.driver.handles[0].url = SMARTAPPLY_RESUME

    detected = await detect(client, user, application_id)
    assert detected.status_code == 200, detected.text
    body = detected.json()
    assert body["status"] == RedirectStatus.VALIDATED.value
    assert body["application_platform"] == "indeed_smartapply"
    assert body["application_step"] == SmartApplyStep.RESUME_SELECTION.value
    assert body["step_supported"] is True
    assert body["destination_validated"] is True
    assert body["can_analyze"] is True
    assert body["destination_url"] == SMARTAPPLY_RESUME
    assert body["listing_url"] == GLASSDOOR

    fetched = await client.get(
        f"{APPLICATIONS}/{application_id}", headers=bearer(user)
    )
    assert fetched.status_code == 200, fetched.text
    saved = fetched.json()
    assert saved["application_url"] == GLASSDOOR
    assert saved["job_listing_url"] == GLASSDOOR
    assert saved["application_destination_url"] == SMARTAPPLY_RESUME
    assert saved["destination_platform"] == "indeed_smartapply"
    assert saved["destination_validated"] is True
    assert saved["redirect_status"] == RedirectStatus.VALIDATED.value

    async with session_factory() as session:
        row = await session.scalar(select(Application))
        assert row is not None
        assert row.job_listing_url == GLASSDOOR
        assert row.application_url == GLASSDOOR
        assert row.application_destination_url == SMARTAPPLY_RESUME
        assert row.redirect_status is RedirectStatus.VALIDATED


async def test_validated_non_smartapply_destination_can_be_analysed(
    client: AsyncClient, session_factory: SessionFactory, browser_runtime: Any
) -> None:
    """A Greenhouse form is analysable; only SmartApply steps gate analysis."""
    user, preview = await prepare_listing_application(
        client, session_factory, email="greenhouse@example.com", google_sub="g-gh"
    )
    application_id = preview["application_id"]
    started = await start_listing_session(client, user, application_id)
    assert started.status_code == 200, started.text

    browser_runtime.driver.handles[0].url = GREENHOUSE
    detected = await detect(client, user, application_id)
    assert detected.status_code == 200, detected.text
    body = detected.json()
    assert body["status"] == RedirectStatus.VALIDATED.value
    assert body["application_platform"] == "greenhouse"
    assert body["step_supported"] is False  # SmartApply-only concept
    assert body["can_analyze"] is True
    assert body["destination_url"] == GREENHOUSE
    assert body["listing_url"] == GLASSDOOR

    fetched = await client.get(
        f"{APPLICATIONS}/{application_id}", headers=bearer(user)
    )
    assert fetched.status_code == 200, fetched.text
    saved = fetched.json()
    assert saved["application_url"] == GLASSDOOR
    assert saved["job_listing_url"] == GLASSDOOR
    assert saved["application_destination_url"] == GREENHOUSE
    assert saved["destination_platform"] == "greenhouse"


async def test_sign_in_step_is_reported_without_handling_credentials(
    client: AsyncClient, session_factory: SessionFactory, browser_runtime: Any
) -> None:
    user, preview = await prepare_listing_application(
        client, session_factory, email="signin@example.com", google_sub="g-signin"
    )
    application_id = preview["application_id"]
    await start_listing_session(client, user, application_id)
    handle = browser_runtime.driver.handles[0]
    handle.url = SMARTAPPLY_RESUME.replace("resume-selection", "questions")
    handle.html = "<main><h2>Sign in to apply</h2><input type=password></main>"

    response = await detect(client, user, application_id)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == RedirectStatus.VALIDATED.value
    assert "sign in" in body["message"].lower()
    assert any("sign-in" in warning.lower() for warning in body["warnings"])
    assert "password" not in (body["destination_url"] or "").lower()


async def test_sign_in_redirect_keeps_the_session_alive_for_manual_auth(
    client: AsyncClient, session_factory: SessionFactory, browser_runtime: Any
) -> None:
    user, preview = await prepare_listing_application(
        client, session_factory, email="auth@example.com", google_sub="g-auth"
    )
    application_id = preview["application_id"]
    await start_listing_session(client, user, application_id)
    handle = browser_runtime.driver.handles[0]
    handle.url = (
        "https://secure.indeed.com/account/login?continue="
        "https%3A%2F%2Fsmartapply.indeed.com"
    )

    detected = await detect(client, user, application_id)
    assert detected.status_code == 200, detected.text
    body = detected.json()
    assert body["status"] == RedirectStatus.AWAITING_USER_ACTION.value
    assert body["authentication_required"] is True
    assert body["awaiting_user_action"] is True
    assert body["destination_validated"] is False
    assert body["destination_url"] is None
    assert "sign in" in body["message"].lower()
    assert any("sign-in" in warning.lower() for warning in body["warnings"])

    # The session is still usable and owned; completing sign-in manually then
    # returning to SmartApply validates the application destination.
    session = await client.get(
        f"{BROWSER}/by-application/{application_id}", headers=bearer(user)
    )
    assert session.status_code == 200
    assert session.json()["browser_status"] == "open"

    handle.url = SMARTAPPLY_RESUME
    validated = await detect(client, user, application_id)
    assert validated.status_code == 200, validated.text
    assert validated.json()["status"] == RedirectStatus.VALIDATED.value
    assert validated.json()["authentication_required"] is False


# ---------------------------------------------------------------------------
# 6. Unexpected destinations are refused, not stored or followed
# ---------------------------------------------------------------------------
async def test_unexpected_redirect_destination_is_rejected_as_unsupported(
    client: AsyncClient, session_factory: SessionFactory, browser_runtime: Any
) -> None:
    user, preview = await prepare_listing_application(
        client, session_factory, email="unexpected@example.com", google_sub="g-unexpected"
    )
    application_id = preview["application_id"]
    await start_listing_session(client, user, application_id)
    browser_runtime.driver.handles[0].url = "https://careers.acme.example/apply"

    response = await detect(client, user, application_id)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == RedirectStatus.UNSUPPORTED.value
    assert body["destination_validated"] is False
    assert body["destination_url"] is None
    assert "not a supported application platform" in body["message"].lower()

    fetched = await client.get(
        f"{APPLICATIONS}/{application_id}", headers=bearer(user)
    )
    saved = fetched.json()
    assert saved["application_destination_url"] is None
    assert saved["destination_validated"] is False
    assert saved["application_url"] == GLASSDOOR


async def test_repeated_detection_is_idempotent_and_recovers_from_unsupported(
    client: AsyncClient, session_factory: SessionFactory, browser_runtime: Any
) -> None:
    """Re-checking the same page changes nothing, and a retry can recover."""
    user, preview = await prepare_listing_application(
        client, session_factory, email="idempotent@example.com", google_sub="g-idem"
    )
    application_id = preview["application_id"]
    await start_listing_session(client, user, application_id)
    handle = browser_runtime.driver.handles[0]

    # A first hand-off lands somewhere unsupported: nothing is recorded.
    handle.url = "https://careers.acme.example/apply"
    unsupported = await detect(client, user, application_id)
    assert unsupported.status_code == 200, unsupported.text
    assert unsupported.json()["status"] == RedirectStatus.UNSUPPORTED.value

    fetched = await client.get(
        f"{APPLICATIONS}/{application_id}", headers=bearer(user)
    )
    saved = fetched.json()
    assert saved["application_destination_url"] is None
    assert saved["destination_validated"] is False
    assert saved["application_url"] == GLASSDOOR

    # The user clicks Easy Apply again on the same session; it now recovers.
    handle.url = SMARTAPPLY_RESUME
    first = await detect(client, user, application_id)
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first_body["status"] == RedirectStatus.VALIDATED.value

    # Re-detecting the same page is idempotent.
    second = await detect(client, user, application_id)
    assert second.status_code == 200, second.text
    second_body = second.json()
    for key in (
        "status",
        "listing_url",
        "destination_url",
        "application_platform",
        "application_step",
        "destination_validated",
        "can_analyze",
    ):
        assert second_body[key] == first_body[key]

    async with session_factory() as session:
        rows = (await session.scalars(select(Application))).all()
        assert len(rows) == 1
        assert rows[0].application_url == GLASSDOOR
        assert rows[0].job_listing_url == GLASSDOOR
        assert rows[0].application_destination_url == SMARTAPPLY_RESUME
        assert rows[0].redirect_status is RedirectStatus.VALIDATED


async def test_destination_detection_refuses_a_closed_session(
    client: AsyncClient, session_factory: SessionFactory, browser_runtime: Any
) -> None:
    """An expired or crashed browser is never silently reused."""
    user, preview = await prepare_listing_application(
        client, session_factory, email="closed@example.com", google_sub="g-closed"
    )
    application_id = preview["application_id"]
    started = await start_listing_session(client, user, application_id)
    assert started.status_code == 200, started.text

    await browser_runtime.driver.handles[0].close()

    response = await detect(client, user, application_id)
    assert response.status_code == 400, response.text
    assert response.json()["code"] == "browser_session_expired"


async def test_blocked_navigation_is_reported_as_unsupported_not_a_browser_failure(
    client: AsyncClient, session_factory: SessionFactory, browser_runtime: Any
) -> None:
    user, preview = await prepare_listing_application(
        client, session_factory, email="blocked@example.com", google_sub="g-blocked"
    )
    application_id = preview["application_id"]
    started = await start_listing_session(client, user, application_id)
    assert started.status_code == 200, started.text
    handle = browser_runtime.driver.handles[0]
    handle.url = GLASSDOOR
    handle.blocked_navigation = True

    response = await detect(client, user, application_id)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == RedirectStatus.UNSUPPORTED.value
    assert "blocked" in body["message"].lower()
    assert body["destination_validated"] is False

    # The session stays alive: this is a site-support issue, not a crash.
    session = await client.get(
        f"{BROWSER}/by-application/{application_id}", headers=bearer(user)
    )
    assert session.status_code == 200
    assert session.json()["browser_status"] == "open"


# ---------------------------------------------------------------------------
# 7. Private / loopback / look-alike destinations
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "url",
    [
        "http://smartapply.indeed.com/beta/indeedapply/form/resume-selection-module/resume-selection",
        "https://smartapply.indeed.com:8443/beta/indeedapply/form/resume-selection-module/resume-selection",
        "https://smartapply.indeed.com.evil.example/beta/indeedapply/form/resume-selection-module/resume-selection",
        "https://notsmartapply.indeed.com/beta/indeedapply/form/resume-selection-module/resume-selection",
        "https://www.indeed.com/viewjob?jk=abc",
        "http://127.0.0.1/resume-selection",
        "http://localhost/resume-selection",
    ],
)
def test_strict_hostname_validation_rejects_look_alikes_and_private_hosts(
    url: str,
) -> None:
    assert is_smartapply_url(url) is False
    try:
        detection = detect_platform(url)
    except ApplicationUrlError:
        # An unsafe scheme/port/host is refused before classification.
        return
    assert detection.platform is not ApplicationPlatform.INDEED_SMARTAPPLY


def test_authentication_detection_is_exact_host_and_path_scoped() -> None:
    assert is_authentication_url("https://secure.indeed.com/account/login") is True
    assert (
        is_authentication_url(
            "https://www.indeed.com/account/login?continue=https%3A%2F%2Fsmartapply.indeed.com"
        )
        is True
    )
    assert is_authentication_url("https://www.indeed.com/viewjob?jk=abc") is False
    assert is_authentication_url("https://secure.indeed.com.evil.example/login") is False
    assert is_authentication_url("http://secure.indeed.com/account/login") is False


def test_smartapply_host_and_step_are_recognised() -> None:
    assert is_smartapply_url(SMARTAPPLY_RESUME) is True
    assert (
        application_step_for_url(SMARTAPPLY_RESUME)
        is SmartApplyStep.RESUME_SELECTION
    )
    assert listing_source_for_url(GLASSDOOR) == "glassdoor"
    assert is_job_board_url(GLASSDOOR) is True


def test_redirect_policy_allows_only_known_platforms_and_listing_host() -> None:
    policy = build_listing_redirect_policy(GLASSDOOR)
    assert policy.redirect_flow is True
    assert policy.allows(GLASSDOOR)
    assert policy.allows(SMARTAPPLY_RESUME)
    assert policy.allows("https://secure.indeed.com/account/login")
    assert policy.allows("https://www.indeed.com/account/login")
    assert not policy.allows("https://careers.acme.example/apply")
    assert not policy.allows("https://secure.indeed.com.evil.example/login")
    assert not policy.allows("http://www.glassdoor.com/job-listing/x.htm")

    direct = build_navigation_policy(
        SMARTAPPLY_RESUME, platform=ApplicationPlatform.INDEED_SMARTAPPLY
    )
    assert direct.allows(SMARTAPPLY_RESUME)
    assert direct.allows("https://secure.indeed.com/account/login")


async def test_private_resolved_smartapply_destination_is_rejected(
    client: AsyncClient,
    session_factory: SessionFactory,
    browser_runtime: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, preview = await prepare_listing_application(
        client, session_factory, email="private@example.com", google_sub="g-private"
    )
    application_id = preview["application_id"]
    await start_listing_session(client, user, application_id)
    browser_runtime.driver.handles[0].url = SMARTAPPLY_RESUME
    monkeypatch.setattr(
        application_url_security, "_resolve_host", _private_resolver
    )

    response = await detect(client, user, application_id)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == RedirectStatus.REJECTED.value
    assert body["destination_validated"] is False
    assert body["destination_url"] is None
    assert "private" in body["message"].lower()

    fetched = await client.get(
        f"{APPLICATIONS}/{application_id}", headers=bearer(user)
    )
    assert fetched.json()["application_destination_url"] is None


# ---------------------------------------------------------------------------
# 8-9. Resume-selection detection and form analysis
# ---------------------------------------------------------------------------
def test_resume_selection_page_is_recognised_from_url_and_markup() -> None:
    page = inspect_page(SMARTAPPLY_RESUME, RESUME_SELECTION_HTML)
    assert page is not None
    assert page.step is SmartApplyStep.RESUME_SELECTION
    assert page.supported is True
    assert page.resume_selection_detected is True
    assert page.resume_upload_detected is True
    assert page.authentication_detected is False
    assert inspect_page("https://www.glassdoor.com/job-listing/x.htm", "Resume") is None


def test_smartapply_form_fields_are_analysed_with_human_only_controls() -> None:
    result = ApplicationFormAnalyzer().analyze(
        RESUME_SELECTION_HTML, page_url=SMARTAPPLY_RESUME
    )
    assert result.application_step == SmartApplyStep.RESUME_SELECTION.value
    assert result.step_supported is True
    categories = {field.classification for field in result.fields}
    assert FieldCategory.RESUME_UPLOAD in categories
    assert FieldCategory.EMAIL in categories
    assert FieldCategory.PHONE in categories
    assert FieldCategory.LEGAL_DECLARATION in categories
    file_fields = [
        field for field in result.fields if field.field_type is FormFieldType.FILE
    ]
    assert file_fields and file_fields[0].required is True
    legal = [
        field
        for field in result.fields
        if field.classification is FieldCategory.LEGAL_DECLARATION
    ]
    assert all(field.requires_human_input for field in legal)
    assert all(not field.automation_eligible for field in legal)
    assert result.warnings


# ---------------------------------------------------------------------------
# 10-14. Safe filling, preserved values, confirmations, no submission
# ---------------------------------------------------------------------------
def _field(
    *,
    field_id: str,
    label: str,
    category: FieldCategory,
    field_type: FormFieldType,
    source: FieldSource,
    value: str | None = None,
    current_value: str | None = None,
    required: bool = True,
    is_legal: bool = False,
) -> DetectedFormField:
    return DetectedFormField(
        field_id=field_id,
        form_index=0,
        label=label,
        field_type=field_type,
        classification=category,
        required=required,
        current_value=current_value,
        confidence=0.95,
        requires_human_input=False,
        requires_confirmation=False,
        is_sensitive=False,
        is_legal=is_legal,
        automation_eligible=True,
        classification_reason="Test fixture.",
        source=FieldSourceMatch(
            source=source,
            suggested_value=value,
            confidence=0.97 if source is FieldSource.VERIFIED_PROFILE else 0.6,
        ),
    )


def test_verified_information_is_planned_and_user_values_are_preserved() -> None:
    candidate = Candidate(
        full_name="Ada Lovelace",
        email="ada@example.com",
        phone="+1 555 0100",
    )
    fields = [
        _field(
            field_id="email",
            label="Email address",
            category=FieldCategory.EMAIL,
            field_type=FormFieldType.EMAIL,
            source=FieldSource.VERIFIED_PROFILE,
            value="ada@example.com",
        ),
        _field(
            field_id="phone",
            label="Phone number",
            category=FieldCategory.PHONE,
            field_type=FormFieldType.PHONE,
            source=FieldSource.VERIFIED_PROFILE,
            value="+1 555 0100",
            current_value="+1 555 9999",
        ),
        _field(
            field_id="salary",
            label="Expected salary",
            category=FieldCategory.EXPECTED_SALARY,
            field_type=FormFieldType.NUMBER,
            source=FieldSource.NONE,
        ),
        _field(
            field_id="consent",
            label="I certify the information is true and complete",
            category=FieldCategory.LEGAL_DECLARATION,
            field_type=FormFieldType.CHECKBOX,
            source=FieldSource.NONE,
            is_legal=True,
        ),
    ]
    plan = FieldExecutionPlanner().build(
        fields,
        candidate=candidate,
        resume=None,
        reusable_answers=[],
        user_answers={},
    )
    by_id = {item.field_id: item for item in plan.fields}
    assert by_id["email"].status is FillFieldStatus.READY
    assert by_id["email"].proposed_value == "ada@example.com"
    assert by_id["phone"].status is FillFieldStatus.ALREADY_FILLED
    assert by_id["salary"].status is FillFieldStatus.NEEDS_HUMAN_INPUT
    assert by_id["consent"].status is FillFieldStatus.NEEDS_HUMAN_INPUT
    assert plan.eligible_count == 1


@pytest.mark.parametrize(
    "category, field_type",
    [
        (FieldCategory.CAPTCHA, FormFieldType.CAPTCHA),
        (FieldCategory.OTP_VERIFICATION, FormFieldType.TEXT),
        (FieldCategory.MFA_VERIFICATION, FormFieldType.TEXT),
    ],
)
def test_captcha_and_otp_are_never_planned_for_execution(
    category: FieldCategory, field_type: FormFieldType
) -> None:
    plan = FieldExecutionPlanner().build(
        [
            _field(
                field_id="verification",
                label="Verification code",
                category=category,
                field_type=field_type,
                source=FieldSource.VERIFIED_PROFILE,
                value="123456",
            )
        ],
        candidate=None,
        resume=None,
        reusable_answers=[],
        user_answers={},
    )
    assert plan.fields[0].status is FillFieldStatus.NEEDS_HUMAN_INPUT
    assert plan.fields[0].proposed_value is None
    assert plan.executable is False


def test_no_submission_primitive_exists_on_the_adapter_or_handle() -> None:
    adapter = get_adapter(ApplicationPlatform.INDEED_SMARTAPPLY)
    assert isinstance(adapter, IndeedSmartApplyAdapter)
    assert not hasattr(adapter, "submit_application")
    assert not hasattr(FakeHandle, "click")
    assert not hasattr(FakeHandle, "submit")


# ---------------------------------------------------------------------------
# 15. Ownership and authentication
# ---------------------------------------------------------------------------
async def test_destination_detection_requires_authentication_and_ownership(
    client: AsyncClient, session_factory: SessionFactory, browser_runtime: Any
) -> None:
    owner, preview = await prepare_listing_application(
        client, session_factory, email="owner@example.com", google_sub="g-owner"
    )
    application_id = preview["application_id"]
    await start_listing_session(client, owner, application_id)

    unauthenticated = await client.post(
        f"{BROWSER}/by-application/{application_id}/detect-destination"
    )
    assert unauthenticated.status_code == 401

    intruder = await create_user(
        session_factory, email="intruder@example.com", google_sub="g-intruder"
    )
    forbidden = await detect(
        client, intruder, application_id, headers=bearer(intruder)
    )
    assert forbidden.status_code == 404

    # The owner can still use the session.
    allowed = await detect(client, owner, application_id)
    assert allowed.status_code == 200
