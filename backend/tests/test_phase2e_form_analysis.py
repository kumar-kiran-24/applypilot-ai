"""Phase 2E Stage B: application form detection and field classification.

The extraction and classification tests use synthetic HTML and a read-only page
fixture, so no browser is launched. The API tests replace the Playwright driver
with a fake handle that only implements URL, liveness, close and content, which
is exactly the Stage B capability boundary.
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
from app.models.application_form_analysis import ApplicationFormAnalysis
from app.models.browser_session import BrowserSessionState, BrowserSessionStatus
from app.models.candidate import Candidate
from app.models.resume import Resume
from app.schemas.application_form import FieldCategory, FieldSource, FormFieldType
from app.services import application_url_security
from app.services.application_form_analyzer import (
    ApplicationFormAnalyzer,
    FormPageUnavailableError,
)
from app.services.application_form_classification import FieldClassifierFacade
from app.services.application_form_extraction import (
    RawFormField,
    StaticPageContent,
    extract_form_snapshot,
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

ACCESSIBLE_FORM = """
<html><body>
<form id="application">
  <label for="full">Full name</label>
  <input id="full" name="full_name" type="text" required>

  <label>Email address
    <input name="email" type="email" aria-required="true">
  </label>

  <label for="phone">Phone number</label>
  <input id="phone" name="phone" type="tel">

  <label for="dob">Date of birth</label>
  <input id="dob" name="date_of_birth" type="date">

  <label for="years">Years of experience</label>
  <input id="years" name="years_experience" type="number">

  <label for="portfolio">Portfolio URL</label>
  <input id="portfolio" name="portfolio_url" type="url">

  <label for="summary">Professional experience summary</label>
  <textarea id="summary" name="experience_summary"></textarea>

  <label for="loc">Current location</label>
  <select id="loc" name="location">
    <option value="">Choose...</option>
    <option value="austin">Austin</option>
    <option value="london">London</option>
  </select>

  <label for="resume">Upload resume</label>
  <input id="resume" name="resume" type="file" required>
</form>
</body></html>
"""

GROUP_FORM = """
<html><body>
<form>
  <label for="notice">Notice period</label>
  <select id="notice" name="notice_period">
    <option>Immediate</option>
    <option selected>30 days</option>
  </select>

  <fieldset>
    <legend>Willing to relocate</legend>
    <label><input type="radio" name="relocate" value="yes" checked> Yes</label>
    <label><input type="radio" name="relocate" value="no"> No</label>
  </fieldset>

  <fieldset>
    <legend>Technical skills</legend>
    <label><input type="checkbox" name="skills" value="python"> Python</label>
    <label><input type="checkbox" name="skills" value="go"> Go</label>
  </fieldset>
</form>
</body></html>
"""

SAFETY_FORM = """
<html><body>
<form>
  <label for="password">Password</label>
  <input id="password" name="password" type="password" value="hunter2-secret">

  <input type="hidden" name="csrf_token" value="csrf-secret-value">

  <label for="otp">Verification code</label>
  <input id="otp" name="otp" type="text" value="123456">

  <label for="mfa">Authenticator code</label>
  <input id="mfa" name="authenticator_code" type="text" value="654321">

  <label for="captcha">CAPTCHA answer</label>
  <input id="captcha" name="captcha_answer" type="text" value="captcha-secret">

  <div class="g-recaptcha" data-sitekey="sitekey-secret"></div>

  <label for="agree">I certify that the information is true and complete</label>
  <input id="agree" name="certify" type="checkbox">
</form>
</body></html>
"""

MULTI_FORM = """
<html><body>
<form id="one">
  <label>Address line 1 <input name="address1"></label>
  <label>Address line 1 <input name="address2"></label>
</form>
<form id="two">
  <label>Email address <input name="email" type="email"></label>
</form>
</body></html>
"""

BASIC_API_FORM = """
<html><body>
<form>
  <label for="name">Full name</label>
  <input id="name" name="full_name" type="text" required>
  <label for="email">Email address</label>
  <input id="email" name="email" type="email" required>
  <label for="resume">Resume upload</label>
  <input id="resume" name="resume" type="file" required>
  <label for="clearance">Security clearance level</label>
  <input id="clearance" name="clearance" type="text" required>
</form>
</body></html>
"""

DYNAMIC_API_FORM = """
<html><body>
<form>
  <label for="name">Full name</label>
  <input id="name" name="full_name" type="text" required>
  <label for="email">Email address</label>
  <input id="email" name="email" type="email" required>
  <label for="resume">Resume upload</label>
  <input id="resume" name="resume" type="file">
  <label for="clearance">Security clearance level</label>
  <input id="clearance" name="clearance" type="text" required>
  <fieldset>
    <legend>Work authorization</legend>
    <label><input type="radio" name="authorization" value="yes"> Yes</label>
    <label><input type="radio" name="authorization" value="no"> No</label>
  </fieldset>
</form>
</body></html>
"""


# ---------------------------------------------------------------------------
# Read-only fakes (no real browser)
# ---------------------------------------------------------------------------
class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeFormHandle:
    def __init__(self, url: str, html: str) -> None:
        self.url = url
        self.html = html
        self.alive = True
        self.closed = False
        self.unsafe_navigation = False

    async def current_url(self) -> str:
        return self.url

    async def content(self) -> str:
        return self.html

    async def is_alive(self) -> bool:
        return self.alive and not self.closed

    async def close(self) -> None:
        self.closed = True
        self.alive = False


class FakeFormDriver:
    def __init__(self) -> None:
        self.html = BASIC_API_FORM
        self.handles: list[FakeFormHandle] = []
        self.fail = False
        self.unsafe = False

    async def open(
        self,
        url: str,
        *,
        policy: Any,
        user_id: str,
        pinned_address: str | None = None,
    ) -> FakeFormHandle:
        del policy, user_id
        if self.fail:
            raise BrowserUnavailableError("Playwright is not installed.")
        handle = FakeFormHandle(url, self.html)
        handle.unsafe_navigation = self.unsafe
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
    driver = FakeFormDriver()
    manager = BrowserSessionManager(driver=driver, ttl_seconds=900, clock=clock)
    set_browser_session_manager(manager)
    yield SimpleNamespace(driver=driver, manager=manager, clock=clock)
    await manager.close_all()
    set_browser_session_manager(None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def field_by_label(fields: list[Any], label: str) -> Any:
    return next(
        item
        for item in fields
        if (item["label"] if isinstance(item, dict) else item.label) == label
    )


def ids(fields: list[Any]) -> list[str]:
    return [item.field_id for item in fields]


async def prepare_application(
    client: AsyncClient,
    session_factory: SessionFactory,
    *,
    email: str = "form@example.com",
    google_sub: str = "google-sub-form",
) -> tuple[Any, dict[str, Any]]:
    user = await create_user(session_factory, email=email, google_sub=google_sub)
    job = await create_job(session_factory, user, job_url=GREENHOUSE_URL)
    await create_resume(session_factory, user)
    await prepare_profile(client, user)
    response = await client.post(
        f"{APPLICATIONS}/analyze",
        json={"job_id": str(job.id)},
        headers=bearer(user),
    )
    assert response.status_code == 200, response.text
    return user, response.json()


async def start_session(client: AsyncClient, user: Any, application_id: str):
    return await client.post(
        BROWSER,
        json={"application_id": application_id},
        headers=bearer(user),
    )


def form_url(application_id: str, suffix: str = "") -> str:
    return f"{BROWSER}/by-application/{application_id}/form-analysis{suffix}"


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------
def test_extracts_labels_types_required_flags_and_options() -> None:
    result = ApplicationFormAnalyzer().analyze(ACCESSIBLE_FORM)
    fields = list(result.fields)

    assert result.form_count == 1
    assert len(fields) == 9

    full_name = field_by_label(fields, "Full name")
    assert full_name.field_type is FormFieldType.TEXT
    assert full_name.classification is FieldCategory.PERSONAL_NAME
    assert full_name.required is True
    assert full_name.confidence >= 0.75

    email = field_by_label(fields, "Email address")
    assert email.field_type is FormFieldType.EMAIL
    assert email.classification is FieldCategory.EMAIL
    assert email.required is True
    assert email.name == "email"

    phone = field_by_label(fields, "Phone number")
    assert phone.field_type is FormFieldType.PHONE
    assert phone.classification is FieldCategory.PHONE

    date_of_birth = field_by_label(fields, "Date of birth")
    assert date_of_birth.field_type is FormFieldType.DATE
    assert date_of_birth.classification is FieldCategory.UNKNOWN

    years = field_by_label(fields, "Years of experience")
    assert years.field_type is FormFieldType.NUMBER
    assert years.classification is FieldCategory.EXPERIENCE

    portfolio = field_by_label(fields, "Portfolio URL")
    assert portfolio.field_type is FormFieldType.URL
    assert portfolio.classification is FieldCategory.PORTFOLIO

    summary = field_by_label(fields, "Professional experience summary")
    assert summary.field_type is FormFieldType.TEXTAREA

    location = field_by_label(fields, "Current location")
    assert location.field_type is FormFieldType.SELECT
    assert location.classification is FieldCategory.LOCATION
    assert location.options == ["Choose...", "Austin", "London"]

    resume = field_by_label(fields, "Upload resume")
    assert resume.field_type is FormFieldType.FILE
    assert resume.classification is FieldCategory.RESUME_UPLOAD
    assert resume.required is True
    # File contents and paths are never inspected.
    assert resume.current_value is None

    assert len(set(ids(fields))) == len(fields)


def test_accessible_names_win_over_positional_text() -> None:
    html = """
    <html><body>
      <span id="email-label">Email address</span>
      <input id="e" name="email" type="email" aria-labelledby="email-label">
      <input id="p" name="phone" type="tel" aria-label="Mobile number">
    </body></html>
    """
    fields = list(ApplicationFormAnalyzer().analyze(html).fields)
    assert field_by_label(fields, "Email address").classification is FieldCategory.EMAIL
    assert field_by_label(fields, "Mobile number").classification is FieldCategory.PHONE


def test_missing_labels_and_ambiguous_labels_stay_unknown() -> None:
    html = """
    <html><body><form>
      <input name="mystery_field" type="text">
      <label>Company name <input name="company_name"></label>
    </form></body></html>
    """
    fields = list(ApplicationFormAnalyzer().analyze(html).fields)

    mystery = field_by_label(fields, "mystery field")
    assert mystery.classification is FieldCategory.UNKNOWN
    assert mystery.confidence < 0.75
    assert mystery.requires_human_input is True
    assert mystery.automation_eligible is False

    # "Company name" superficially contains "name" but is not a person's name.
    company = field_by_label(fields, "Company name")
    assert company.classification is FieldCategory.UNKNOWN
    assert company.requires_human_input is True


def test_dropdown_radio_and_checkbox_groups_are_structured() -> None:
    fields = list(ApplicationFormAnalyzer().analyze(GROUP_FORM).fields)

    notice = field_by_label(fields, "Notice period")
    assert notice.field_type is FormFieldType.SELECT
    assert notice.classification is FieldCategory.NOTICE_PERIOD
    assert notice.options == ["Immediate", "30 days"]
    assert notice.is_sensitive is True
    assert notice.requires_confirmation is True
    # Sensitive current values are not echoed back.
    assert notice.current_value is None

    relocate = field_by_label(fields, "Willing to relocate")
    assert relocate.field_type is FormFieldType.RADIO_GROUP
    assert relocate.classification is FieldCategory.RELOCATION
    assert relocate.options == ["Yes", "No"]
    assert relocate.is_sensitive is True
    assert relocate.requires_confirmation is True
    assert relocate.current_value is None

    skills = field_by_label(fields, "Technical skills")
    assert skills.field_type is FormFieldType.CHECKBOX_GROUP
    assert skills.classification is FieldCategory.SKILLS
    assert skills.options == ["Python", "Go"]
    assert skills.requires_human_input is False


def test_duplicate_labels_have_unique_stable_ids_and_multiple_forms_are_grouped() -> None:
    analyzer = ApplicationFormAnalyzer()
    first = analyzer.analyze(MULTI_FORM)
    second = analyzer.analyze(MULTI_FORM)
    fields = list(first.fields)

    assert first.form_count == 2
    assert len(fields) == 3
    assert len(set(ids(fields))) == 3
    assert ids(fields) == ids(list(second.fields))

    address_fields = [item for item in fields if item.label == "Address line 1"]
    assert len(address_fields) == 2
    assert {item.form_index for item in address_fields} == {0}
    assert {item.form_index for item in fields} == {0, 1}


# ---------------------------------------------------------------------------
# Safety and classification
# ---------------------------------------------------------------------------
def test_legal_captcha_otp_mfa_and_password_fields_are_human_only() -> None:
    result = ApplicationFormAnalyzer().analyze(SAFETY_FORM)
    fields = list(result.fields)

    assert result.captcha_detected is True
    assert result.otp_detected is True
    assert result.mfa_detected is True
    assert result.legal_declaration_count == 1

    password = field_by_label(fields, "Password")
    assert password.field_type is FormFieldType.PASSWORD
    assert password.current_value is None
    assert password.requires_human_input is True
    assert password.requires_confirmation is True

    otp = field_by_label(fields, "Verification code")
    assert otp.classification is FieldCategory.OTP_VERIFICATION
    assert otp.current_value is None
    assert otp.requires_human_input is True

    mfa = field_by_label(fields, "Authenticator code")
    assert mfa.classification is FieldCategory.MFA_VERIFICATION
    assert mfa.current_value is None
    assert mfa.requires_human_input is True

    captcha = field_by_label(fields, "CAPTCHA challenge")
    assert captcha.classification is FieldCategory.CAPTCHA
    assert captcha.current_value is None
    assert captcha.requires_human_input is True

    legal = field_by_label(
        fields, "I certify that the information is true and complete"
    )
    assert legal.classification is FieldCategory.LEGAL_DECLARATION
    assert legal.is_legal is True
    assert legal.requires_human_input is True
    assert legal.requires_confirmation is True
    assert legal.automation_eligible is False

    # Hidden CSRF bookkeeping is not a question and is not returned at all.
    assert all(item.name != "csrf_token" for item in fields)

    serialized = json.dumps([item.model_dump(mode="json") for item in fields])
    for secret in (
        "hunter2-secret",
        "csrf-secret-value",
        "123456",
        "654321",
        "captcha-secret",
        "sitekey-secret",
    ):
        assert secret not in serialized


def test_unknown_fields_are_never_forced_into_a_category() -> None:
    decision = FieldClassifierFacade().classify(
        RawFormField(
            form_index=0,
            field_type="text",
            label="Mystery",
            name="mystery",
        )
    )
    assert decision.category is FieldCategory.UNKNOWN
    assert decision.confidence < 0.75
    assert decision.requires_human_input is True

    # The analyzer applies the same safety rule end to end.
    result = ApplicationFormAnalyzer().analyze(
        '<form><input name="mystery" type="text"></form>'
    )
    field = result.fields[0]
    assert field.classification is FieldCategory.UNKNOWN
    assert field.automation_eligible is False
    assert field.requires_human_input is True


def test_sources_reuse_profile_and_resume_provenance_without_filling() -> None:
    candidate = Candidate(
        full_name="Ada Lovelace",
        email="ada@example.com",
        phone="+1 555 0100",
        phone_country_code="+1",
        github_url="https://github.com/ada",
    )
    resume = Resume(
        original_filename="ada.pdf",
        skills=["COBOL"],
        parsed_data={
            "full_name": "A. Lovelace",
            "email": "resume@example.com",
            "links": ["https://github.com/ada-resume"],
        },
    )
    html = """
    <html><body><form>
      <label for="email">Email address</label>
      <input id="email" name="email" type="email" required>
      <label for="github">GitHub profile</label>
      <input id="github" name="github" type="url">
      <label for="clearance">Security clearance level</label>
      <input id="clearance" name="clearance" type="text" required>
    </form></body></html>
    """
    result = ApplicationFormAnalyzer().analyze(html, candidate=candidate)
    fields = list(result.fields)

    email = field_by_label(fields, "Email address")
    assert email.source.source is FieldSource.VERIFIED_PROFILE
    assert email.source.suggested_value == "ada@example.com"
    assert email.source.confidence > 0.9
    assert email.requires_human_input is False
    assert email.automation_eligible is True

    github = field_by_label(fields, "GitHub profile")
    assert github.source.source is FieldSource.VERIFIED_PROFILE
    assert github.source.suggested_value == "https://github.com/ada"

    clearance = field_by_label(fields, "Security clearance level")
    assert clearance.source.source is FieldSource.NONE
    assert clearance.source.suggested_value is None
    assert clearance.requires_human_input is True

    # A resume value is only a suggestion and always needs confirmation.
    resume_result = ApplicationFormAnalyzer().analyze(html, resume=resume)
    resume_email = field_by_label(list(resume_result.fields), "Email address")
    assert resume_email.source.source is FieldSource.RESUME_SUGGESTION
    assert resume_email.source.suggested_value == "resume@example.com"
    assert resume_email.requires_confirmation is True
    assert resume_email.automation_eligible is False


async def test_analyzer_accepts_a_controlled_page_interface() -> None:
    analyzer = ApplicationFormAnalyzer()
    result = await analyzer.analyze_page(StaticPageContent(html=ACCESSIBLE_FORM))
    assert len(result.fields) == 9

    with pytest.raises(FormPageUnavailableError):
        await analyzer.analyze_page(StaticPageContent(html="   "))


def test_snapshot_extraction_is_separate_from_classification() -> None:
    snapshot = extract_form_snapshot(ACCESSIBLE_FORM)
    assert len(snapshot.fields) == 9
    # Raw extraction has no classification fields at all.
    assert not hasattr(snapshot.fields[0], "classification")
    assert snapshot.fields[0].label == "Full name"


# ---------------------------------------------------------------------------
# API integration
# ---------------------------------------------------------------------------
async def test_form_analysis_requires_authentication(client: AsyncClient) -> None:
    application_id = str(uuid.uuid4())
    assert (await client.post(form_url(application_id))).status_code == 401
    assert (await client.get(form_url(application_id))).status_code == 401
    assert (await client.get(form_url(application_id, "/fields"))).status_code == 401
    assert (
        await client.post(form_url(application_id, "/reanalyze"))
    ).status_code == 401


async def test_analysis_requires_a_live_browser_session(
    client: AsyncClient, session_factory: SessionFactory, browser_runtime: Any
) -> None:
    del browser_runtime
    user, preview = await prepare_application(client, session_factory)
    response = await client.post(
        form_url(preview["application_id"]), headers=bearer(user)
    )
    assert response.status_code == 404
    assert response.json()["code"] == "browser_session_not_found"


async def test_analyze_persists_fields_and_returns_latest(
    client: AsyncClient, session_factory: SessionFactory, browser_runtime: Any
) -> None:
    user, preview = await prepare_application(client, session_factory)
    headers = bearer(user)
    started = await start_session(client, user, preview["application_id"])
    assert started.status_code == 200, started.text

    analyzed = await client.post(form_url(preview["application_id"]), headers=headers)
    assert analyzed.status_code == 200, analyzed.text
    body = analyzed.json()

    assert body["application_id"] == preview["application_id"]
    assert body["version"] == 1
    assert body["form_count"] == 1
    assert body["field_count"] == 4
    assert body["requires_human_input_count"] >= 1
    assert body["warnings"] == [] or isinstance(body["warnings"], list)
    assert body["page_url"] == GREENHOUSE_URL

    fields = body["fields"]
    assert len(fields) == 4
    full_name = field_by_label(fields, "Full name")
    assert full_name["classification"] == "personal_name"
    assert full_name["source"]["source"] == "verified_profile"
    assert full_name["source"]["suggested_value"] == "Ada Lovelace"
    assert full_name["automation_eligible"] is True

    resume = field_by_label(fields, "Resume upload")
    assert resume["classification"] == "resume_upload"
    assert resume["field_type"] == "file"
    assert resume["current_value"] is None

    latest = await client.get(form_url(preview["application_id"]), headers=headers)
    assert latest.status_code == 200, latest.text
    assert latest.json()["analysis_id"] == body["analysis_id"]
    assert latest.json()["version"] == 1

    fields_only = await client.get(
        form_url(preview["application_id"], "/fields"), headers=headers
    )
    assert fields_only.status_code == 200, fields_only.text
    assert fields_only.json()["analysis_id"] == body["analysis_id"]
    assert fields_only.json()["total"] == 4
    assert len(fields_only.json()["fields"]) == 4

    async with session_factory() as session:
        rows = list(
            (
                await session.scalars(
                    select(ApplicationFormAnalysis).where(
                        ApplicationFormAnalysis.application_id
                        == uuid.UUID(preview["application_id"])
                    )
                )
            ).all()
        )
    assert len(rows) == 1
    assert rows[0].user_id == user.id
    assert rows[0].browser_session_id == uuid.UUID(body["browser_session_id"])
    assert rows[0].field_count == 4


async def test_reanalysis_picks_up_dynamically_revealed_fields_and_keeps_versions(
    client: AsyncClient, session_factory: SessionFactory, browser_runtime: Any
) -> None:
    user, preview = await prepare_application(client, session_factory)
    headers = bearer(user)
    await start_session(client, user, preview["application_id"])

    initial = await client.post(form_url(preview["application_id"]), headers=headers)
    assert initial.status_code == 200, initial.text
    assert initial.json()["field_count"] == 4

    browser_runtime.driver.handles[0].html = DYNAMIC_API_FORM

    reanalyzed = await client.post(
        form_url(preview["application_id"], "/reanalyze"), headers=headers
    )
    assert reanalyzed.status_code == 200, reanalyzed.text
    body = reanalyzed.json()
    assert body["version"] == 2
    assert body["field_count"] == 5
    authority = field_by_label(body["fields"], "Work authorization")
    assert authority["field_type"] == "radio_group"
    assert authority["classification"] == "work_authorization"
    assert authority["requires_human_input"] is True
    assert authority["is_sensitive"] is True

    latest = await client.get(form_url(preview["application_id"]), headers=headers)
    assert latest.json()["version"] == 2
    assert latest.json()["field_count"] == 5

    async with session_factory() as session:
        versions = list(
            (
                await session.scalars(
                    select(ApplicationFormAnalysis.version).where(
                        ApplicationFormAnalysis.application_id
                        == uuid.UUID(preview["application_id"])
                    )
                )
            ).all()
        )
    assert sorted(versions) == [1, 2]


async def test_form_analysis_is_owner_scoped(
    client: AsyncClient, session_factory: SessionFactory, browser_runtime: Any
) -> None:
    del browser_runtime
    owner, preview = await prepare_application(client, session_factory)
    intruder = await create_user(
        session_factory,
        email="intruder-form@example.com",
        google_sub="google-sub-intruder-form",
    )
    await start_session(client, owner, preview["application_id"])
    assert (
        await client.post(form_url(preview["application_id"]), headers=bearer(owner))
    ).status_code == 200

    for response in (
        await client.post(form_url(preview["application_id"]), headers=bearer(intruder)),
        await client.get(form_url(preview["application_id"]), headers=bearer(intruder)),
        await client.get(
            form_url(preview["application_id"], "/fields"), headers=bearer(intruder)
        ),
        await client.post(
            form_url(preview["application_id"], "/reanalyze"), headers=bearer(intruder)
        ),
    ):
        assert response.status_code == 404
        assert response.json()["code"] == "application_not_found"


async def test_expired_session_is_reported_honestly(
    client: AsyncClient, session_factory: SessionFactory, browser_runtime: Any
) -> None:
    user, preview = await prepare_application(client, session_factory)
    headers = bearer(user)
    started = await start_session(client, user, preview["application_id"])
    assert started.json()["state"] == BrowserSessionState.BROWSER_READY.value
    assert started.json()["browser_status"] == BrowserSessionStatus.OPEN.value

    await browser_runtime.driver.handles[0].close()

    response = await client.post(form_url(preview["application_id"]), headers=headers)
    assert response.status_code == 400
    assert response.json()["code"] == "browser_session_expired"


async def test_secret_values_never_appear_in_responses_or_logs(
    client: AsyncClient,
    session_factory: SessionFactory,
    browser_runtime: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    user, preview = await prepare_application(client, session_factory)
    headers = bearer(user)
    browser_runtime.driver.html = SAFETY_FORM
    await start_session(client, user, preview["application_id"])

    caplog.clear()
    response = await client.post(form_url(preview["application_id"]), headers=headers)
    assert response.status_code == 200, response.text
    payload = response.text

    for secret in (
        "hunter2-secret",
        "csrf-secret-value",
        "123456",
        "654321",
        "captcha-secret",
        "sitekey-secret",
    ):
        assert secret not in payload
        assert secret not in caplog.text

    async with session_factory() as session:
        row = await session.scalar(
            select(ApplicationFormAnalysis).where(
                ApplicationFormAnalysis.application_id
                == uuid.UUID(preview["application_id"])
            )
        )
    assert row is not None
    stored = json.dumps({"summary": row.summary, "fields": row.fields})
    for secret in (
        "hunter2-secret",
        "csrf-secret-value",
        "123456",
        "654321",
        "captcha-secret",
        "sitekey-secret",
    ):
        assert secret not in stored
