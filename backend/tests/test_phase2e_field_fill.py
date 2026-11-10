"""Phase 2E Stage C: mocked planner, verifier and API coverage.

No real browser is launched here. The fake handle implements the narrow Stage C
field-action contract and records what the engine asks it to do, so duplicate
execution, ownership, pause/resume/cancel and secret-free persistence can be
tested deterministically. Real Chromium coverage lives in
``test_phase2e_field_fill_browser.py`` and is run separately.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from app.core.config import settings
from app.integrations.browser.field_actions import FieldAction, FieldActionKind
from app.integrations.browser.session import BrowserUnavailableError
from app.models.application_field_execution import (
    ApplicationFieldExecution,
    FieldExecutionStatus,
)
from app.models.application_form_analysis import ApplicationFormAnalysis
from app.models.candidate import Candidate
from app.schemas.application_fill import FillAction, FillFieldStatus
from app.services import application_url_security
from app.services.application_field_planner import FieldExecutionPlanner, PlannedField
from app.services.application_form_analyzer import ApplicationFormAnalyzer
from app.services.browser_session_manager import (
    BrowserSessionManager,
    set_browser_session_manager,
)
from app.services.field_value_verifier import FieldValueVerifier
from app.services.playwright_field_interactor import PlaywrightFieldInteractor
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

FILL_FORM = """
<html><body>
<form>
  <label for="name">Full name</label>
  <input id="name" name="full_name" type="text" required>

  <label for="email">Email address</label>
  <input id="email" name="email" type="email" required>

  <label for="phone">Phone number</label>
  <input id="phone" name="phone" type="tel">

  <label for="city">Current location</label>
  <select id="city" name="city">
    <option value="">Choose...</option>
    <option value="austin">Austin</option>
    <option value="london">London</option>
  </select>

  <label for="pref">Preferred name</label>
  <input id="pref" name="preferred_name" type="text" value="Ada">

  <label for="resume">Upload resume</label>
  <input id="resume" name="resume" type="file">

  <label for="agree">I certify that the information is true and complete</label>
  <input id="agree" name="certify" type="checkbox">

  <div class="g-recaptcha" data-sitekey="test-site-key"></div>

  <label for="otp">Verification code</label>
  <input id="otp" name="otp" type="text">

  <button type="submit">Submit application</button>
</form>
</body></html>
"""


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


class FakeFillHandle:
    def __init__(self, url: str, html: str) -> None:
        self.url = url
        self.html = html
        self.alive = True
        self.closed = False
        self.unsafe_navigation = False
        self.actions: list[FieldAction] = []

    async def current_url(self) -> str:
        return self.url

    async def content(self) -> str:
        return self.html

    async def is_alive(self) -> bool:
        return self.alive and not self.closed

    async def close(self) -> None:
        self.closed = True
        self.alive = False

    async def apply_field_action(self, action: FieldAction) -> Any:
        from app.integrations.browser.field_actions import FieldActionResult

        self.actions.append(action)
        if action.kind is FieldActionKind.UPLOAD:
            return FieldActionResult(True, action.field_id, observed_value="strong.pdf")
        if action.kind is FieldActionKind.FILL:
            return FieldActionResult(True, action.field_id, observed_value=action.text)
        if action.kind is FieldActionKind.SELECT:
            return FieldActionResult(
                True,
                action.field_id,
                observed_value=action.option_labels[0] if action.option_labels else None,
            )
        observed = ", ".join(action.option_labels) or "yes"
        return FieldActionResult(True, action.field_id, observed_value=observed)


class FakeFillDriver:
    def __init__(self) -> None:
        self.html = FILL_FORM
        self.handles: list[FakeFillHandle] = []
        self.fail = False
        self.unsafe = False

    async def open(
        self,
        url: str,
        *,
        policy: Any,
        user_id: str,
        pinned_address: str | None = None,
    ) -> FakeFillHandle:
        del policy, user_id
        if self.fail:
            raise BrowserUnavailableError("Playwright is not installed.")
        handle = FakeFillHandle(url, self.html)
        handle.unsafe_navigation = self.unsafe
        self.handles.append(handle)
        return handle


@pytest_asyncio.fixture
async def browser_runtime(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "APPLICATION_AUTOMATION_ENABLED", False)

    async def resolve(host: str, port: int) -> list[str]:
        del host, port
        return [PUBLIC_ADDRESS]

    monkeypatch.setattr(application_url_security, "_resolve_host", resolve)
    clock = FakeClock()
    driver = FakeFillDriver()
    manager = BrowserSessionManager(driver=driver, ttl_seconds=900, clock=clock)
    set_browser_session_manager(manager)
    yield SimpleNamespace(driver=driver, manager=manager, clock=clock)
    await manager.close_all()
    set_browser_session_manager(None)


# ---------------------------------------------------------------------------
# Planner and verifier (no API, no browser)
# ---------------------------------------------------------------------------
def analyzed_fields(html: str = FILL_FORM) -> list[Any]:
    return list(ApplicationFormAnalyzer().analyze(html).fields)


def by_label(fields: list[Any], label: str) -> Any:
    return next(item for item in fields if item.label == label)


def test_planner_fills_only_verified_safe_fields_and_preserves_existing_values() -> None:
    candidate = Candidate(
        full_name="Ada Lovelace",
        email="ada@example.com",
        phone="+1 555 0100",
        phone_country_code="+1",
        city="Austin",
    )
    plan = FieldExecutionPlanner().build(
        analyzed_fields(),
        candidate=candidate,
        resume=None,
        resume_owned=True,
    )
    fields = {item.label: item for item in plan.fields}

    assert fields["Full name"].status is FillFieldStatus.READY
    assert fields["Full name"].action is FillAction.FILL
    assert fields["Full name"].proposed_value == "Ada Lovelace"
    assert fields["Email address"].status is FillFieldStatus.READY
    assert fields["Current location"].status is FillFieldStatus.READY
    assert fields["Current location"].action is FillAction.SELECT
    assert fields["Current location"].proposed_value == "Austin"

    assert fields["Preferred name"].status is FillFieldStatus.ALREADY_FILLED
    assert fields["Preferred name"].proposed_value is None

    assert fields["I certify that the information is true and complete"].status is (
        FillFieldStatus.NEEDS_HUMAN_INPUT
    )
    assert fields["CAPTCHA challenge"].status is FillFieldStatus.NEEDS_HUMAN_INPUT
    assert fields["Verification code"].status is FillFieldStatus.NEEDS_HUMAN_INPUT
    assert fields["Upload resume"].status is FillFieldStatus.NEEDS_HUMAN_INPUT

    # Nothing legal or verification-related may leak a value.
    for label in (
        "I certify that the information is true and complete",
        "CAPTCHA challenge",
        "Verification code",
    ):
        assert fields[label].proposed_value is None
        assert fields[label].action is FillAction.NONE or label == "Upload resume"

    assert plan.eligible_count == 4
    assert plan.human_input_count >= 3
    assert any("CAPTCHA" in warning for warning in plan.warnings)


def test_planner_requires_confirmation_for_sensitive_and_resume_suggestions() -> None:
    html = """
    <form>
      <label for="salary">Expected salary</label>
      <input id="salary" name="expected_salary" type="number">
      <label for="start">Available from</label>
      <input id="start" name="start_date" type="date">
    </form>
    """
    candidate = Candidate(
        full_name="Ada Lovelace",
        email="ada@example.com",
        available_from=None,
    )
    plan = FieldExecutionPlanner().build(
        analyzed_fields(html), candidate=candidate, resume=None
    )
    salary = by_label(list(plan.fields), "Expected salary")
    assert salary.status is not FillFieldStatus.READY
    assert salary.proposed_value is None

    resume = SimpleNamespace(
        id=uuid.uuid4(),
        original_filename="resume.pdf",
        parsed_data={"email": "resume@example.com"},
        skills=[],
    )
    resume_plan = FieldExecutionPlanner().build(
        analyzed_fields(
            '<form><label for="email">Email address</label>'
            '<input id="email" name="email" type="email" required></form>'
        ),
        candidate=None,
        resume=resume,  # type: ignore[arg-type]
    )
    email = next(iter(resume_plan.fields))
    # Resume-only data is a suggestion, never a verified fact.
    assert email.status is not FillFieldStatus.READY
    assert email.proposed_value is None


def test_planner_never_fills_unknown_or_existing_required_fields() -> None:
    html = """
    <form>
      <label for="mystery">Security clearance level</label>
      <input id="mystery" name="clearance" type="text" required>
      <label for="existing">Email address</label>
      <input id="existing" name="email" type="email" value="already@example.com">
    </form>
    """
    plan = FieldExecutionPlanner().build(
        analyzed_fields(html),
        candidate=Candidate(full_name="Ada", email="ada@example.com"),
        resume=None,
    )
    fields = {item.label: item for item in plan.fields}
    assert fields["Security clearance level"].status is FillFieldStatus.NEEDS_HUMAN_INPUT
    assert fields["Email address"].status is FillFieldStatus.ALREADY_FILLED


def test_verifier_checks_exact_observed_values() -> None:
    verifier = FieldValueVerifier()
    field = PlannedField(
        field_id="f1",
        label="Email address",
        field_type=analyzed_fields()[1].field_type,
        classification=analyzed_fields()[1].classification,
        required=True,
        action=FillAction.FILL,
        status=FillFieldStatus.READY,
        proposed_value="ada@example.com",
    )
    from app.integrations.browser.field_actions import FieldActionResult

    assert verifier.verify(
        field, FieldActionResult(True, "f1", observed_value="ADA@example.com")
    )[0]
    assert not verifier.verify(
        field, FieldActionResult(True, "f1", observed_value="other@example.com")
    )[0]
    assert not verifier.verify(
        field, FieldActionResult(False, "f1", reason="already filled")
    )[0]


async def test_interactor_only_maps_planned_actions() -> None:
    handle = FakeFillHandle(GREENHOUSE_URL, FILL_FORM)
    item = PlannedField(
        field_id="f1",
        label="Full name",
        field_type=analyzed_fields()[0].field_type,
        classification=analyzed_fields()[0].classification,
        required=True,
        action=FillAction.FILL,
        status=FillFieldStatus.READY,
        proposed_value="Ada Lovelace",
    )
    result = await PlaywrightFieldInteractor().execute(handle, item)
    assert result.ok
    assert result.observed_value == "Ada Lovelace"
    assert handle.actions[0].kind is FieldActionKind.FILL
    assert handle.actions[0].text == "Ada Lovelace"

    none_item = PlannedField(
        field_id="f2",
        label="Legal",
        field_type=item.field_type,
        classification=item.classification,
        required=True,
        action=FillAction.NONE,
        status=FillFieldStatus.NEEDS_HUMAN_INPUT,
    )
    refused = await PlaywrightFieldInteractor().execute(handle, none_item)
    assert not refused.ok
    assert len(handle.actions) == 1


# ---------------------------------------------------------------------------
# API integration with the fake page
# ---------------------------------------------------------------------------
async def prepare_application(
    client: AsyncClient,
    session_factory: SessionFactory,
    *,
    email: str = "fill@example.com",
    google_sub: str = "google-sub-fill",
) -> tuple[Any, dict[str, Any], Any]:
    user = await create_user(session_factory, email=email, google_sub=google_sub)
    job = await create_job(session_factory, user, job_url=GREENHOUSE_URL)
    resume = await create_resume(session_factory, user)
    await prepare_profile(client, user)
    response = await client.post(
        f"{APPLICATIONS}/analyze",
        json={"job_id": str(job.id)},
        headers=bearer(user),
    )
    assert response.status_code == 200, response.text
    return user, response.json(), resume


async def start_session(client: AsyncClient, user: Any, application_id: str):
    return await client.post(
        BROWSER,
        json={"application_id": application_id},
        headers=bearer(user),
    )


def fill_url(application_id: str, suffix: str = "") -> str:
    return f"{BROWSER}/by-application/{application_id}/fill{suffix}"


async def test_fill_plan_executes_safe_fields_and_skips_human_steps(
    client: AsyncClient,
    session_factory: SessionFactory,
    browser_runtime: Any,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "RESUME_STORAGE_DIR", str(tmp_path))
    user, preview, resume = await prepare_application(client, session_factory)
    headers = bearer(user)
    application_id = preview["application_id"]

    # Make the selected resume readable through the real storage abstraction.
    storage_key = f"{uuid.uuid4()}/{uuid.uuid4()}.pdf"
    (tmp_path / storage_key).parent.mkdir(parents=True)
    (tmp_path / storage_key).write_bytes(b"%PDF-1.4 test resume")
    async with session_factory() as session:
        row = await session.get(type(resume), resume.id)
        assert row is not None
        row.storage_key = storage_key
        await session.commit()

    assert (
        await start_session(client, user, application_id)
    ).status_code == 200
    analyzed = await client.post(
        f"{BROWSER}/by-application/{application_id}/form-analysis",
        headers=headers,
    )
    assert analyzed.status_code == 200, analyzed.text

    planned = await client.get(fill_url(application_id, "-plan"), headers=headers)
    assert planned.status_code == 200, planned.text
    plan = planned.json()
    assert plan["analysis_id"] == analyzed.json()["analysis_id"]
    assert plan["resume"]["will_upload"] is True
    assert plan["eligible_count"] >= 4
    assert plan["human_input_count"] >= 3
    assert plan["already_filled_count"] == 1
    plan_fields = {item["label"]: item for item in plan["fields"]}
    assert plan_fields["Full name"]["proposed_value"] == "Ada Lovelace"
    assert plan_fields["Preferred name"]["status"] == "already_filled"
    assert plan_fields["I certify that the information is true and complete"][
        "status"
    ] == "needs_human_input"
    assert plan_fields["I certify that the information is true and complete"][
        "proposed_value"
    ] is None
    assert "Submit" not in json.dumps(plan_fields)

    started = await client.post(fill_url(application_id), json={}, headers=headers)
    assert started.status_code == 200, started.text
    execution = started.json()
    assert execution["status"] == "needs_user_input"
    assert execution["succeeded_count"] >= 4
    assert execution["failed_count"] == 0
    assert execution["verified_count"] == execution["succeeded_count"]
    assert execution["selected_resume_filename"] == resume.original_filename
    assert execution["can_cancel"] is True

    actions = browser_runtime.driver.handles[0].actions
    kinds = [action.kind.value for action in actions]
    assert "fill" in kinds
    assert "select" in kinds
    assert "upload" in kinds
    assert all(action.kind.value != "submit" for action in actions)
    assert not any(
        "certify" in (action.name or "") or "captcha" in (action.name or "")
        for action in actions
    )

    # The employer's form was not submitted and the application state is intact.
    application = await client.get(f"{APPLICATIONS}/{application_id}", headers=headers)
    assert application.status_code == 200
    assert application.json()["submitted_at"] is None
    assert application.json()["status"] != "submitted"

    # Repeating the start call is idempotent for the same analysis version.
    before = len(actions)
    repeated = await client.post(fill_url(application_id), json={}, headers=headers)
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["execution_id"] == execution["execution_id"]
    assert len(actions) == before

    progress = await client.get(fill_url(application_id), headers=headers)
    assert progress.status_code == 200
    assert progress.json()["execution_id"] == execution["execution_id"]
    assert progress.json()["pending_human_input_count"] >= 3

    async with session_factory() as session:
        execution_row = await session.scalar(select(ApplicationFieldExecution))
        assert execution_row is not None
        assert execution_row.status is FieldExecutionStatus.NEEDS_USER_INPUT
        stored = json.dumps(
            {
                "plan": execution_row.plan,
                "results": execution_row.results,
                "summary": execution_row.summary,
                "resume": execution_row.selected_resume_filename,
            }
        )
    # Proposed values are not persisted with the execution record.
    assert "Ada Lovelace" not in stored
    assert "ada@example.com" not in stored
    assert "test-site-key" not in stored
    assert "hunter" not in stored.lower()
    for forbidden in ("cookie", "storage_state", "auth_token", "csrf", "sitekey"):
        assert forbidden not in stored.lower()

    # Explicit run controls.
    paused = await client.post(
        fill_url(application_id, "/pause"), json={"reason": "Checking"}, headers=headers
    )
    assert paused.status_code == 200, paused.text
    assert paused.json()["status"] == "paused"
    assert paused.json()["can_resume"] is True

    conflict = await client.post(
        fill_url(application_id), json={}, headers=headers
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "field_fill_paused"

    resumed = await client.post(fill_url(application_id, "/resume"), headers=headers)
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["status"] == "needs_user_input"
    # Already-succeeded fields are not re-typed.
    assert len(actions) == before

    cancelled = await client.post(fill_url(application_id, "/cancel"), headers=headers)
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"

    after_cancel = await client.post(
        fill_url(application_id, "/resume"), headers=headers
    )
    assert after_cancel.status_code == 409


async def test_fill_plan_is_owner_scoped_and_requires_a_live_session(
    client: AsyncClient, session_factory: SessionFactory, browser_runtime: Any
) -> None:
    owner, preview, _resume = await prepare_application(client, session_factory)
    application_id = preview["application_id"]

    missing = await client.get(fill_url(application_id, "-plan"), headers=bearer(owner))
    assert missing.status_code == 404
    assert missing.json()["code"] == "browser_session_not_found"

    await start_session(client, owner, application_id)
    intruder = await create_user(
        session_factory,
        email="intruder-fill@example.com",
        google_sub="google-sub-intruder-fill",
    )
    for response in (
        await client.get(fill_url(application_id, "-plan"), headers=bearer(intruder)),
        await client.post(fill_url(application_id), json={}, headers=bearer(intruder)),
        await client.get(fill_url(application_id), headers=bearer(intruder)),
        await client.post(
            fill_url(application_id, "/pause"), headers=bearer(intruder)
        ),
        await client.post(
            fill_url(application_id, "/cancel"), headers=bearer(intruder)
        ),
    ):
        assert response.status_code == 404
        assert response.json()["code"] == "application_not_found"


async def test_analysis_from_another_session_is_refused(
    client: AsyncClient,
    session_factory: SessionFactory,
    browser_runtime: Any,
) -> None:
    user, preview, _resume = await prepare_application(client, session_factory)
    application_id = preview["application_id"]
    await start_session(client, user, application_id)
    analyzed = await client.post(
        f"{BROWSER}/by-application/{application_id}/form-analysis",
        headers=bearer(user),
    )
    assert analyzed.status_code == 200, analyzed.text

    async with session_factory() as session:
        row = await session.scalar(select(ApplicationFormAnalysis))
        assert row is not None
        row.browser_session_id = uuid.uuid4()
        await session.commit()

    response = await client.get(fill_url(application_id, "-plan"), headers=bearer(user))
    assert response.status_code == 409
    assert response.json()["code"] == "analysis_session_mismatch"

    started = await client.post(fill_url(application_id), json={}, headers=bearer(user))
    assert started.status_code == 409
    assert browser_runtime.driver.handles[0].actions == []


async def test_fill_requires_authentication(client: AsyncClient) -> None:
    application_id = str(uuid.uuid4())
    assert (await client.get(fill_url(application_id, "-plan"))).status_code == 401
    assert (
        await client.post(fill_url(application_id), json={})
    ).status_code == 401
    assert (await client.get(fill_url(application_id))).status_code == 401
