"""Phase 2J.3: unified fields, approval binding and stale-form safeguards.

Everything here runs against a fake browser handle that implements the narrow
Stage B/C contracts (``content``/``current_url``/``apply_field_action``). No real
Chromium is launched. The fake lets a test mutate the live HTML or URL between
the plan preview and the fill call, which is exactly the race the approval
fingerprint exists to close.

Real-browser coverage stays in ``test_phase2e_field_fill_browser.py`` and
``test_phase2e_stage_f_e2e_browser.py`` and is marked separately.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from app.core.config import settings
from app.models.application import ApplicationQuestion
from app.models.application_form_analysis import ApplicationFormAnalysis
from app.models.candidate import Candidate
from app.models.resume import Resume
from app.schemas.application_field import FieldSensitivity, FieldVerification
from app.schemas.application_form import (
    DetectedFormField,
    FieldCategory,
    FieldSource,
    FieldSourceMatch,
    FormFieldType,
)
from app.services import application_url_security
from app.services.application_field_normalizer import (
    normalise_fields,
    structure_fingerprint,
)
from app.services.application_field_planner import FieldExecutionPlanner
from app.services.application_form_analyzer import ApplicationFormAnalyzer
from app.services.browser_session_manager import (
    BrowserSessionManager,
    set_browser_session_manager,
)
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.test_applications import bearer
from tests.test_phase2e_field_fill import (
    FILL_FORM,
    FakeClock,
    FakeFillDriver,
    prepare_application,
    start_session,
)

SessionFactory = async_sessionmaker[AsyncSession]

BROWSER = "/api/v1/browser-sessions"
GREENHOUSE_URL = "https://boards.greenhouse.io/acme/jobs/123"
PUBLIC_ADDRESS = "93.184.216.34"


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------
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


def fill_url(application_id: str, suffix: str = "") -> str:
    return f"{BROWSER}/by-application/{application_id}/fill{suffix}"


async def prepare_plan(
    client: AsyncClient,
    session_factory: SessionFactory,
    browser_runtime: Any,
    *,
    html: str | None = None,
    email: str = "unified@example.com",
    google_sub: str = "google-sub-unified",
) -> tuple[Any, str, dict[str, Any], Any]:
    user, preview, _resume = await prepare_application(
        client, session_factory, email=email, google_sub=google_sub
    )
    if html is not None:
        browser_runtime.driver.html = html
    application_id = preview["application_id"]
    assert (await start_session(client, user, application_id)).status_code == 200
    analyzed = await client.post(
        f"{BROWSER}/by-application/{application_id}/form-analysis",
        headers=bearer(user),
    )
    assert analyzed.status_code == 200, analyzed.text
    planned = await client.get(fill_url(application_id, "-plan"), headers=bearer(user))
    assert planned.status_code == 200, planned.text
    handle = browser_runtime.driver.handles[0]
    return user, application_id, planned.json(), handle


def detected(**overrides: Any) -> DetectedFormField:
    values: dict[str, Any] = {
        "field_id": "form0-name",
        "form_index": 0,
        "label": "Full name",
        "field_type": FormFieldType.TEXT,
        "classification": FieldCategory.PERSONAL_NAME,
        "required": True,
        "confidence": 0.95,
        "requires_human_input": False,
        "requires_confirmation": False,
        "is_sensitive": False,
        "is_legal": False,
        "automation_eligible": True,
        "classification_reason": "label match",
        "source": FieldSourceMatch(source=FieldSource.NONE),
    }
    values.update(overrides)
    return DetectedFormField(**values)


# ---------------------------------------------------------------------------
# Canonical representation
# ---------------------------------------------------------------------------
def test_normalisation_produces_one_safe_canonical_shape() -> None:
    raw = detected(
        label="  Email address ",
        placeholder="you@example.com",
        field_type=FormFieldType.EMAIL,
        classification=FieldCategory.EMAIL,
        question_key="contact.email",
        current_value="kept@example.com",
        source=FieldSourceMatch(
            source=FieldSource.VERIFIED_PROFILE,
            suggested_value="ada@example.com",
            confidence=0.97,
            source_record_id="candidate:contact.email",
            explanation="Matched the verified profile.",
        ),
    )
    [field] = normalise_fields(
        [raw],
        page_url="https://jobs.example.com/apply",
        application_step="contact",
    )

    assert field.field_id == raw.field_id
    assert field.question_key == "contact.email"
    assert field.label == "Email address"
    assert field.accessible_name == "Email address"
    assert field.field_type is FormFieldType.EMAIL
    assert field.input_type == "email"
    assert field.required is True
    assert field.current_value == "kept@example.com"
    assert field.already_populated is True
    assert field.provenance.source is FieldSource.VERIFIED_PROFILE
    assert field.verification_status is FieldVerification.VERIFIED
    assert field.sensitivity is FieldSensitivity.PUBLIC
    assert field.locator_ref == f"field:{raw.field_id}"
    # The canonical object is a description, not a browser handle.
    payload = field.model_dump(mode="json")
    for forbidden in (
        "selector",
        "html",
        "cookie",
        "storage_state",
        "password",
        "otp",
        "sitekey",
    ):
        assert forbidden not in payload


def test_duplicate_controls_are_marked_and_never_planned_twice() -> None:
    duplicate = detected(field_id="form0-name", label="Full name")
    fields = normalise_fields([duplicate, duplicate])
    assert fields[0].duplicate_of is None
    assert fields[1].duplicate_of == "form0-name"
    assert fields[1].fillable is False
    assert fields[1].requires_confirmation is True

    plan = FieldExecutionPlanner().build(
        [duplicate, duplicate],
        candidate=Candidate(full_name="Ada Lovelace", email="ada@example.com"),
        resume=None,
    )
    statuses = [item.status.value for item in plan.fields]
    assert statuses[0] == "ready"
    assert statuses[1] == "needs_human_input"
    assert plan.eligible_count == 1


def test_structure_fingerprint_ignores_typed_values_but_not_structure() -> None:
    base = detected(current_value=None)
    typed = detected(current_value="Ada")
    assert structure_fingerprint([base]) == structure_fingerprint([typed])

    different_type = detected(field_type=FormFieldType.EMAIL)
    different_required = detected(required=False)
    assert structure_fingerprint([base]) != structure_fingerprint([different_type])
    assert structure_fingerprint([base]) != structure_fingerprint([different_required])


def test_required_missing_and_sensitive_fields_are_never_filled() -> None:
    fields = [
        detected(
            field_id="salary",
            label="Expected salary",
            classification=FieldCategory.EXPECTED_SALARY,
            field_type=FormFieldType.NUMBER,
            is_sensitive=True,
        ),
        detected(
            field_id="clearance",
            label="Security clearance level",
            classification=FieldCategory.UNKNOWN,
            required=True,
        ),
        detected(
            field_id="start",
            label="Earliest start date",
            classification=FieldCategory.OTHER,
            field_type=FormFieldType.DATE,
        ),
    ]
    plan = FieldExecutionPlanner().build(
        fields,
        candidate=Candidate(
            full_name="Ada Lovelace",
            email="ada@example.com",
            notice_period_days=30,
        ),
        resume=None,
    )
    by_label = {item.label: item for item in plan.fields}
    salary = by_label["Expected salary"]
    assert salary.status.value == "needs_human_input"
    assert salary.proposed_value is None
    assert salary.sensitivity is FieldSensitivity.SENSITIVE
    assert by_label["Security clearance level"].status.value == "needs_human_input"
    assert by_label["Earliest start date"].status.value == "needs_human_input"


def test_conflicting_application_and_profile_answers_need_resolution() -> None:
    field = detected(
        question_key="contact.email",
        label="Email address",
        field_type=FormFieldType.EMAIL,
        classification=FieldCategory.EMAIL,
    )
    plan = FieldExecutionPlanner().build(
        [field],
        candidate=Candidate(full_name="Ada Lovelace", email="profile@example.com"),
        resume=None,
        user_answers={"contact.email": "confirmed@example.com"},
    )
    [row] = plan.fields
    assert row.status.value == "needs_confirmation"
    assert row.conflicted is True
    # The disagreement is shown, never silently guessed.
    assert row.proposed_value == "confirmed@example.com"
    assert row.candidate_answer == "confirmed@example.com"
    assert any("Conflicting" in warning for warning in plan.warnings)


def test_existing_values_are_preserved() -> None:
    plan = FieldExecutionPlanner().build(
        [
            detected(
                field_id="email",
                label="Email address",
                field_type=FormFieldType.EMAIL,
                classification=FieldCategory.EMAIL,
                current_value="typed@example.com",
            )
        ],
        candidate=Candidate(full_name="Ada", email="ada@example.com"),
        resume=None,
    )
    [row] = plan.fields
    assert row.status.value == "already_filled"
    assert row.proposed_value is None
    assert row.already_populated is True
    assert row.current_value == "typed@example.com"


# ---------------------------------------------------------------------------
# Approval binding over the API and fake live page
# ---------------------------------------------------------------------------
async def test_preview_and_start_share_one_canonical_field_model(
    client: AsyncClient,
    session_factory: SessionFactory,
    browser_runtime: Any,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "RESUME_STORAGE_DIR", str(tmp_path))
    user, application_id, plan, handle = await prepare_plan(
        client, session_factory, browser_runtime
    )
    headers = bearer(user)
    async with session_factory() as session:
        resume = await session.scalar(select(Resume))
        assert resume is not None
        storage_key = f"{uuid.uuid4()}/{uuid.uuid4()}.pdf"
        (tmp_path / storage_key).parent.mkdir(parents=True)
        (tmp_path / storage_key).write_bytes(b"%PDF-1.4 unified resume")
        resume.storage_key = storage_key
        await session.commit()

    assert plan["approval_fingerprint"]
    assert plan["structure_fingerprint"]
    assert plan["page_url"] == GREENHOUSE_URL
    planned_by_label = {item["label"]: item for item in plan["fields"]}
    assert planned_by_label["Full name"]["verification_status"] == "verified"
    assert planned_by_label["Full name"]["sensitivity"] == "public"
    assert planned_by_label["Full name"]["page_url"] == GREENHOUSE_URL
    assert planned_by_label["Full name"]["locator_ref"].startswith("field:")
    assert planned_by_label["Full name"]["analyzed_at"] is not None
    assert planned_by_label["Preferred name"]["already_populated"] is True
    # A legal declaration is represented, visible, and not fillable.
    legal = planned_by_label["I certify that the information is true and complete"]
    assert legal["sensitivity"] == "legal"
    assert legal["fillable"] is False
    assert legal["requires_confirmation"] is True

    started = await client.post(
        fill_url(application_id),
        json={"approval_fingerprint": plan["approval_fingerprint"]},
        headers=headers,
    )
    assert started.status_code == 200, started.text
    execution = started.json()
    assert execution["approval_fingerprint"] == plan["approval_fingerprint"]
    assert execution["succeeded_count"] >= 4
    assert execution["failed_count"] == 0

    # Only canonical safe actions reached the page; no legal/CAPTCHA/OTP field.
    forbidden_names = {"certify", "otp", "g-recaptcha"}
    for action in handle.actions:
        assert action.kind.value in {"fill", "select", "check", "upload"}
        assert (action.name or "") not in forbidden_names
        assert "captcha" not in (action.name or "").casefold()


async def test_changed_page_on_the_same_session_is_refused(
    client: AsyncClient,
    session_factory: SessionFactory,
    browser_runtime: Any,
) -> None:
    user, application_id, plan, handle = await prepare_plan(
        client, session_factory, browser_runtime
    )
    handle.url = f"{GREENHOUSE_URL}/step-2"

    response = await client.post(
        fill_url(application_id),
        json={"approval_fingerprint": plan["approval_fingerprint"]},
        headers=bearer(user),
    )
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "approval_stale"
    assert handle.actions == []


async def test_changed_form_structure_is_refused(
    client: AsyncClient,
    session_factory: SessionFactory,
    browser_runtime: Any,
) -> None:
    user, application_id, plan, handle = await prepare_plan(
        client, session_factory, browser_runtime
    )
    # The employer's page re-renders with an extra required control.
    handle.html = FILL_FORM.replace(
        "</form>",
        '<label for="extra">Portfolio URL</label>'
        '<input id="extra" name="portfolio" type="url" required></form>',
    )

    response = await client.post(
        fill_url(application_id),
        json={"approval_fingerprint": plan["approval_fingerprint"]},
        headers=bearer(user),
    )
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "approval_stale"
    assert handle.actions == []


async def test_changed_application_step_is_refused(
    client: AsyncClient,
    session_factory: SessionFactory,
    browser_runtime: Any,
) -> None:
    user, application_id, plan, _handle = await prepare_plan(
        client, session_factory, browser_runtime
    )
    async with session_factory() as session:
        row = await session.scalar(select(ApplicationFormAnalysis))
        assert row is not None
        row.summary = {**row.summary, "application_step": "contact"}
        await session.commit()

    response = await client.post(
        fill_url(application_id),
        json={"approval_fingerprint": plan["approval_fingerprint"]},
        headers=bearer(user),
    )
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "approval_stale"


async def test_changed_approved_values_invalidate_the_fingerprint(
    client: AsyncClient,
    session_factory: SessionFactory,
    browser_runtime: Any,
) -> None:
    user, application_id, plan, handle = await prepare_plan(
        client, session_factory, browser_runtime
    )
    headers = bearer(user)
    # The user's confirmed answer and profile change after the preview, so the
    # same approval must not be reused for a different value.
    async with session_factory() as session:
        candidate = await session.scalar(
            select(Candidate).where(Candidate.user_id == user.id)
        )
        assert candidate is not None
        candidate.full_name = "Grace Hopper"
        question = await session.scalar(
            select(ApplicationQuestion).where(
                ApplicationQuestion.application_id == uuid.UUID(application_id),
                ApplicationQuestion.question_key == "personal.full_name",
            )
        )
        assert question is not None
        question.answer = "Grace Hopper"
        await session.commit()

    response = await client.post(
        fill_url(application_id),
        json={"approval_fingerprint": plan["approval_fingerprint"]},
        headers=headers,
    )
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "approval_stale"
    assert handle.actions == []

    refreshed = await client.get(fill_url(application_id, "-plan"), headers=headers)
    assert refreshed.status_code == 200
    new_plan = refreshed.json()
    assert new_plan["approval_fingerprint"] != plan["approval_fingerprint"]
    field = next(
        item for item in new_plan["fields"] if item["label"] == "Full name"
    )
    assert field["proposed_value"] == "Grace Hopper"

    started = await client.post(
        fill_url(application_id),
        json={"approval_fingerprint": new_plan["approval_fingerprint"]},
        headers=headers,
    )
    assert started.status_code == 200, started.text
    assert handle.actions
    assert any(action.text == "Grace Hopper" for action in handle.actions)


async def test_tampered_or_foreign_fingerprints_are_refused(
    client: AsyncClient,
    session_factory: SessionFactory,
    browser_runtime: Any,
) -> None:
    user, application_id, _plan, handle = await prepare_plan(
        client, session_factory, browser_runtime
    )
    headers = bearer(user)

    tampered = await client.post(
        fill_url(application_id),
        json={"approval_fingerprint": "0" * 64},
        headers=headers,
    )
    assert tampered.status_code == 409
    assert tampered.json()["code"] == "approval_stale"
    assert handle.actions == []

    malformed = await client.post(
        fill_url(application_id), json={}, headers=headers
    )
    assert malformed.status_code == 422

    # A plan approved for another application is not valid here.
    other_user, other_preview, _resume = await prepare_application(
        client,
        session_factory,
        email="other-unified@example.com",
        google_sub="google-sub-other-unified",
    )
    other_id = other_preview["application_id"]
    assert (await start_session(client, other_user, other_id)).status_code == 200
    other_analyzed = await client.post(
        f"{BROWSER}/by-application/{other_id}/form-analysis",
        headers=bearer(other_user),
    )
    assert other_analyzed.status_code == 200
    other_plan = await client.get(
        fill_url(other_id, "-plan"), headers=bearer(other_user)
    )
    assert other_plan.status_code == 200

    cross = await client.post(
        fill_url(application_id),
        json={"approval_fingerprint": other_plan.json()["approval_fingerprint"]},
        headers=headers,
    )
    assert cross.status_code == 409
    assert cross.json()["code"] == "approval_stale"
    assert handle.actions == []


async def test_resume_requires_the_approved_fingerprint(
    client: AsyncClient,
    session_factory: SessionFactory,
    browser_runtime: Any,
) -> None:
    user, application_id, plan, _handle = await prepare_plan(
        client, session_factory, browser_runtime
    )
    headers = bearer(user)
    started = await client.post(
        fill_url(application_id),
        json={"approval_fingerprint": plan["approval_fingerprint"]},
        headers=headers,
    )
    assert started.status_code == 200

    paused = await client.post(
        fill_url(application_id, "/pause"),
        json={"reason": "Checking the page"},
        headers=headers,
    )
    assert paused.status_code == 200

    stale = await client.post(
        fill_url(application_id, "/resume"),
        json={"approval_fingerprint": "f" * 64},
        headers=headers,
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "approval_stale"

    resumed = await client.post(
        fill_url(application_id, "/resume"),
        json={"approval_fingerprint": plan["approval_fingerprint"]},
        headers=headers,
    )
    assert resumed.status_code == 200, resumed.text


def _with_typed_values(html: str, html_ids: set[str]) -> str:
    """Mimic a live page whose controls now hold the values already typed."""
    for html_id in html_ids:
        html = html.replace(
            f'<input id="{html_id}"', f'<input id="{html_id}" value="typed"'
        )
    return html


async def test_resume_continues_a_run_after_the_live_page_changed(
    client: AsyncClient,
    session_factory: SessionFactory,
    browser_runtime: Any,
) -> None:
    """A re-analysis after progress must not refuse the run it continues.

    The live DOM now holds the values the run typed and the values the user
    completed, so re-analysis reports those controls as already populated. The
    stored approval still describes exactly which values ApplyPilot may type, so
    resuming must continue and must not retype the populated controls.
    """
    user, application_id, plan, handle = await prepare_plan(
        client, session_factory, browser_runtime
    )
    headers = bearer(user)
    started = await client.post(
        fill_url(application_id),
        json={"approval_fingerprint": plan["approval_fingerprint"]},
        headers=headers,
    )
    assert started.status_code == 200, started.text
    approved = started.json()["approval_fingerprint"]
    typed = {
        result["field_id"]
        for result in started.json()["results"]
        if result["status"] == "succeeded" and result["action"] == "fill"
    }
    assert typed

    handle.html = _with_typed_values(
        FILL_FORM, {field_id.split("-", 1)[1] for field_id in typed}
    )
    reanalyzed = await client.post(
        f"{BROWSER}/by-application/{application_id}/form-analysis", headers=headers
    )
    assert reanalyzed.status_code == 200, reanalyzed.text
    refreshed = await client.get(fill_url(application_id, "-plan"), headers=headers)
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["approval_fingerprint"] != approved
    assert all(
        item["status"] == "already_filled"
        for item in refreshed.json()["fields"]
        if item["field_id"] in typed
    )

    actions_before = len(handle.actions)
    resumed = await client.post(
        fill_url(application_id, "/resume"),
        json={"approval_fingerprint": approved},
        headers=headers,
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["approval_fingerprint"] == approved
    assert not (
        {action.field_id for action in handle.actions[actions_before:]} & typed
    )


async def test_multistep_fields_keep_their_step_and_page(
    client: AsyncClient,
    session_factory: SessionFactory,
    browser_runtime: Any,
) -> None:
    html = """
    <html><body>
      <form id="step-one">
        <label for="name">Full name</label>
        <input id="name" name="full_name" type="text" required>
      </form>
      <form id="step-two">
        <label for="email">Email address</label>
        <input id="email" name="email" type="email" required>
      </form>
    </body></html>
    """
    _user, _application_id, plan, _handle = await prepare_plan(
        client,
        session_factory,
        browser_runtime,
        html=html,
        email="multistep@example.com",
        google_sub="google-sub-multistep",
    )
    assert {item["form_index"] for item in plan["fields"]} == {0, 1}
    for field in plan["fields"]:
        assert field["page_url"] == GREENHOUSE_URL
        assert field["application_step"] == plan["application_step"]
        assert field["locator_ref"].startswith("field:")


async def test_stale_browser_session_cannot_be_approved(
    client: AsyncClient,
    session_factory: SessionFactory,
    browser_runtime: Any,
) -> None:
    user, application_id, plan, _handle = await prepare_plan(
        client, session_factory, browser_runtime
    )
    session_row = await client.get(
        f"{BROWSER}/by-application/{application_id}", headers=bearer(user)
    )
    assert session_row.status_code == 200
    await browser_runtime.manager.release(uuid.UUID(session_row.json()["session_id"]))

    response = await client.post(
        fill_url(application_id),
        json={"approval_fingerprint": plan["approval_fingerprint"]},
        headers=bearer(user),
    )
    assert response.status_code in {400, 404, 409}
    assert response.json()["code"] in {
        "browser_session_expired",
        "browser_session_not_found",
    }


async def test_approval_is_owner_scoped(
    client: AsyncClient,
    session_factory: SessionFactory,
    browser_runtime: Any,
) -> None:
    _user, application_id, plan, handle = await prepare_plan(
        client, session_factory, browser_runtime
    )
    from tests.test_applications import create_user

    intruder = await create_user(
        session_factory,
        email="approval-intruder@example.com",
        google_sub="google-sub-approval-intruder",
    )
    headers = bearer(intruder)
    for response in (
        await client.get(fill_url(application_id, "-plan"), headers=headers),
        await client.post(
            fill_url(application_id),
            json={"approval_fingerprint": plan["approval_fingerprint"]},
            headers=headers,
        ),
        await client.post(
            fill_url(application_id, "/resume"),
            json={"approval_fingerprint": plan["approval_fingerprint"]},
            headers=headers,
        ),
    ):
        assert response.status_code == 404
        assert response.json()["code"] == "application_not_found"
    assert handle.actions == []


def test_approved_value_is_exactly_what_the_interactor_types() -> None:
    """The planner row and the interaction are the same value by construction."""
    fields = list(ApplicationFormAnalyzer().analyze(FILL_FORM).fields)
    plan = FieldExecutionPlanner().build(
        fields,
        candidate=Candidate(full_name="Ada Lovelace", email="ada@example.com"),
        resume=None,
    )
    approved = [
        [item.field_id, item.action.value, item.status.value, item.proposed_value]
        for item in plan.fields
    ]
    serialised = [
        [
            item["field_id"],
            item["action"],
            item["status"],
            item["proposed_value"],
        ]
        for item in (
            row.persisted | {"proposed_value": row.proposed_value}
            for row in plan.fields
        )
    ]
    assert json.loads(json.dumps(approved)) == serialised
