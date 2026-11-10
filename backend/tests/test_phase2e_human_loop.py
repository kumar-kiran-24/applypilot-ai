"""Phase 2E Stage D: mocked human-in-the-loop queue and reconciliation.

No real browser is launched here. The existing Stage C fake page is reused so
the tests can deterministically exercise intervention creation, ownership,
working state, re-analysis, completion detection, safe resolution, session
expiry and the final review checklist. Real Chromium coverage lives in
``test_phase2e_human_loop_browser.py`` and is run separately.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from app.core.config import settings
from app.models.application_field_execution import (
    ApplicationFieldExecution,
    FieldExecutionStatus,
)
from app.models.application_intervention import (
    ApplicationIntervention,
    InterventionStatus,
    InterventionType,
)
from app.services import application_url_security
from app.services.browser_session_manager import (
    BrowserSessionManager,
    set_browser_session_manager,
)
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.test_applications import bearer, create_user
from tests.test_phase2e_field_fill import (
    FakeClock,
    FakeFillDriver,
    fill_url,
    prepare_application,
)

SessionFactory = async_sessionmaker[AsyncSession]
BROWSER = "/api/v1/browser-sessions"
PUBLIC_ADDRESS = "93.184.216.34"

HUMAN_FORM = """
<html><body>
<form>
  <label for="years">Years of experience</label>
  <input id="years" name="years_experience" type="number" required>

  <label for="clearance">Security clearance level</label>
  <input id="clearance" name="clearance" type="text" required>

  <label for="agree">I certify that the information is true and complete</label>
  <input id="agree" name="certify" type="checkbox">

  <div class="g-recaptcha" data-sitekey="test-site-key"></div>

  <button type="submit">Submit application</button>
</form>
</body></html>
"""


@pytest_asyncio.fixture
async def browser_runtime(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "APPLICATION_AUTOMATION_ENABLED", False)

    async def resolve(host: str, port: int) -> list[str]:
        del host, port
        return [PUBLIC_ADDRESS]

    monkeypatch.setattr(application_url_security, "_resolve_host", resolve)
    clock = FakeClock()
    driver = FakeFillDriver()
    driver.html = HUMAN_FORM
    manager = BrowserSessionManager(driver=driver, ttl_seconds=900, clock=clock)
    set_browser_session_manager(manager)
    yield SimpleNamespace(driver=driver, manager=manager, clock=clock)
    await manager.close_all()
    set_browser_session_manager(None)


async def prepare_human_loop(
    client: AsyncClient,
    session_factory: SessionFactory,
    *,
    email: str = "human-loop@example.com",
    google_sub: str = "google-sub-human-loop",
) -> tuple[Any, str, Any]:
    user, preview, _resume = await prepare_application(
        client, session_factory, email=email, google_sub=google_sub
    )
    application_id = preview["application_id"]
    started = await client.post(
        BROWSER,
        json={"application_id": application_id},
        headers=bearer(user),
    )
    assert started.status_code == 200, started.text
    analyzed = await client.post(
        f"{BROWSER}/by-application/{application_id}/form-analysis",
        headers=bearer(user),
    )
    assert analyzed.status_code == 200, analyzed.text
    filled = await client.post(fill_url(application_id), json={}, headers=bearer(user))
    assert filled.status_code == 200, filled.text
    return user, application_id, started.json()


def loop_url(application_id: str, suffix: str) -> str:
    return f"{BROWSER}/by-application/{application_id}{suffix}"


async def test_queue_created_owned_and_sensitive_items_never_resolve(
    client: AsyncClient,
    session_factory: SessionFactory,
    browser_runtime: Any,
) -> None:
    user, application_id, _session = await prepare_human_loop(
        client, session_factory
    )
    headers = bearer(user)

    response = await client.get(
        loop_url(application_id, "/interventions"), headers=headers
    )
    assert response.status_code == 200, response.text
    queue = response.json()
    assert queue["blocking_count"] >= 2
    assert queue["view"]["available"] is False
    assert queue["view"]["user_can_interact"] is False
    types = {item["intervention_type"] for item in queue["interventions"]}
    assert InterventionType.MISSING_INFORMATION.value in types
    assert InterventionType.SENSITIVE_INFORMATION.value in types
    assert InterventionType.UNSUPPORTED_FIELD.value in types

    serialized = json.dumps(queue)
    assert "Ada Lovelace" not in serialized
    assert "ada@example.com" not in serialized
    assert "test-site-key" not in serialized
    assert "cookie" not in serialized.lower()

    years = next(
        item
        for item in queue["interventions"]
        if item["field_label"] == "Years of experience"
    )
    legal = next(
        item
        for item in queue["interventions"]
        if item["field_label"] == "I certify that the information is true and complete"
    )

    worked = await client.post(
        loop_url(application_id, f"/interventions/{years['intervention_id']}/working"),
        headers=headers,
    )
    assert worked.status_code == 200, worked.text
    assert worked.json()["status"] == "user_working"
    assert worked.json()["resolved_at"] is None

    refreshed = await client.get(
        loop_url(application_id, "/interventions"), headers=headers
    )
    assert refreshed.json()["user_working_count"] == 1

    legal_resolve = await client.post(
        loop_url(application_id, f"/interventions/{legal['intervention_id']}/resolve"),
        headers=headers,
    )
    assert legal_resolve.status_code == 409
    assert legal_resolve.json()["code"] == "intervention_requires_user_action"

    # Cross-user access is refused before any item can be changed.
    intruder = await create_user(
        session_factory,
        email="human-loop-intruder@example.com",
        google_sub="google-sub-human-loop-intruder",
    )
    intruder_headers = bearer(intruder)
    for intruder_response in (
        await client.get(
            loop_url(application_id, "/interventions"), headers=intruder_headers
        ),
        await client.post(
            loop_url(
                application_id,
                f"/interventions/{years['intervention_id']}/working",
            ),
            headers=intruder_headers,
        ),
        await client.get(
            loop_url(application_id, "/final-review"), headers=intruder_headers
        ),
    ):
        assert intruder_response.status_code == 404
        assert intruder_response.json()["code"] == "application_not_found"

    async with session_factory() as session:
        rows = list(
            (
                await session.scalars(select(ApplicationIntervention))
            ).all()
        )
    assert rows
    assert all(row.user_id == user.id for row in rows)


async def test_reanalysis_detects_user_completion_and_resolves_only_safe_fields(
    client: AsyncClient,
    session_factory: SessionFactory,
    browser_runtime: Any,
) -> None:
    user, application_id, _session = await prepare_human_loop(
        client, session_factory
    )
    headers = bearer(user)
    before = await client.get(
        loop_url(application_id, "/interventions"), headers=headers
    )
    assert before.status_code == 200, before.text
    years = next(
        item
        for item in before.json()["interventions"]
        if item["field_label"] == "Years of experience"
    )

    # Stand-in for the user completing the field on the employer's page.
    handle = browser_runtime.driver.handles[0]
    actions_before = len(handle.actions)
    handle.html = handle.html.replace(
        'name="years_experience" type="number" required',
        'name="years_experience" type="number" value="12" required',
    ).replace(
        ">Security clearance level<",
        ">Security clearance (required)<",
    )

    reconciled = await client.post(
        loop_url(application_id, "/human-loop/reanalyze"), headers=headers
    )
    assert reconciled.status_code == 200, reconciled.text
    body = reconciled.json()
    assert body["previous_analysis_version"] is not None
    assert body["analysis_version"] > body["previous_analysis_version"]
    assert "Years of experience" in body["newly_completed_fields"]
    assert any(
        change["change"] == "changed" and change["now_has_value"]
        for change in body["changes"]
    )
    assert any(
        change["previous_label"] == "Security clearance level"
        for change in body["changes"]
    )
    # Re-analysis never types into the form.
    assert len(handle.actions) == actions_before
    assert body["view"]["user_can_interact"] is False
    assert body["manual_handoff_required"] is True
    assert "cannot be observed" in body["verification_note"]

    # The value itself is never returned, only the safe completion signal.
    assert '"12"' not in json.dumps(body)
    years_after = next(
        item
        for item in body["interventions"]
        if item["field_label"] == "Years of experience"
    )
    assert years_after["status"] == InterventionStatus.RESOLVED.value
    assert years_after["resolution_reason"]

    legal_after = next(
        item
        for item in body["interventions"]
        if item["field_label"] == "I certify that the information is true and complete"
    )
    assert legal_after["status"] == InterventionStatus.PENDING.value
    clearance_before = next(
        item
        for item in before.json()["interventions"]
        if item["field_label"] == "Security clearance level"
    )
    clearance_after = next(
        item
        for item in body["interventions"]
        if item["field_id"] == clearance_before["field_id"]
    )
    assert clearance_after["field_label"] == "Security clearance (required)"
    assert clearance_after["status"] == InterventionStatus.PENDING.value

    # A resolved item is idempotent; the unknown field cannot be "resolved"
    # merely because the browser was asked to re-check.
    already = await client.post(
        loop_url(application_id, f"/interventions/{years['intervention_id']}/resolve"),
        headers=headers,
    )
    assert already.status_code == 200, already.text
    assert already.json()["status"] == "resolved"

    unresolved = await client.post(
        loop_url(
            application_id,
            f"/interventions/{clearance_after['intervention_id']}/resolve",
        ),
        headers=headers,
    )
    assert unresolved.status_code == 409
    assert unresolved.json()["code"] == "intervention_not_satisfied"


async def test_resume_is_idempotent_uses_latest_analysis_and_never_submits(
    client: AsyncClient,
    session_factory: SessionFactory,
    browser_runtime: Any,
) -> None:
    user, application_id, session = await prepare_human_loop(
        client, session_factory
    )
    headers = bearer(user)
    handle = browser_runtime.driver.handles[0]
    actions_before = len(handle.actions)

    first = await client.post(
        loop_url(application_id, "/human-loop/resume"), headers=headers
    )
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "needs_user_input"
    assert len(handle.actions) == actions_before

    second = await client.post(
        loop_url(application_id, "/human-loop/resume"), headers=headers
    )
    assert second.status_code == 200, second.text
    assert second.json()["execution_id"] == first.json()["execution_id"]
    assert len(handle.actions) == actions_before

    # A new analysis is accepted by the Stage D resume path, but only its safe
    # fields are executed. Existing results are preserved.
    reanalyzed = await client.post(
        loop_url(application_id, "/human-loop/reanalyze"), headers=headers
    )
    assert reanalyzed.status_code == 200, reanalyzed.text
    resumed = await client.post(
        loop_url(application_id, "/human-loop/resume"), headers=headers
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["analysis_version"] == reanalyzed.json()["analysis_version"]
    assert resumed.json()["succeeded_count"] == first.json()["succeeded_count"]

    # The browser is gone: resume must not silently create another one.
    await browser_runtime.manager.release(uuid.UUID(session["session_id"]))
    expired = await client.post(
        loop_url(application_id, "/human-loop/resume"), headers=headers
    )
    assert expired.status_code in {400, 409, 404}
    progress = await client.get(fill_url(application_id), headers=headers)
    assert progress.status_code == 200
    assert progress.json()["status"] == "session_expired"

    queue = await client.get(
        loop_url(application_id, "/interventions"), headers=headers
    )
    assert queue.status_code == 200, queue.text
    assert any(
        item["intervention_type"] == "browser_session_expired"
        for item in queue.json()["interventions"]
    )
    # Releasing the session did not cause a replacement browser to be opened.
    assert len(browser_runtime.driver.handles) == 1

    application = await client.get(
        f"/api/v1/applications/{application_id}", headers=headers
    )
    assert application.status_code == 200
    assert application.json()["submitted_at"] is None
    assert application.json()["status"] != "submitted"

    async with session_factory() as session_row:
        rows = list(
            (
                await session_row.scalars(select(ApplicationIntervention))
            ).all()
        )
    assert rows
    stored = json.dumps(
        [
            {
                "reason": row.reason,
                "label": row.field_label,
                "category": row.field_category,
                "resolution": row.resolution_reason,
            }
            for row in rows
        ]
    )
    assert "Ada Lovelace" not in stored
    assert "test-site-key" not in stored
    assert "cookie" not in stored.lower()


async def test_final_review_checklist_is_explicit_and_never_submits(
    client: AsyncClient,
    session_factory: SessionFactory,
    browser_runtime: Any,
) -> None:
    user, application_id, _session = await prepare_human_loop(
        client, session_factory
    )
    headers = bearer(user)
    response = await client.get(
        loop_url(application_id, "/final-review"), headers=headers
    )
    assert response.status_code == 200, response.text
    review = response.json()
    assert review["manual_submission_required"] is True
    assert review["ready_for_final_review"] is False
    assert review["blocker_count"] >= 1
    assert review["view"]["available"] is False
    assert {section["key"] for section in review["sections"]} >= {
        "personal",
        "education",
        "experience",
        "skills",
        "application",
        "confirmations",
    }
    assert "Submit" not in json.dumps(review["filled_field_labels"])
    assert review["resume"]["filename"]
    assert any(
        "I certify that the information is true and complete" in label
        for label in review["confirmation_fields"]
    )
    assert review["filled_count"] == 0

    application = await client.get(
        f"/api/v1/applications/{application_id}", headers=headers
    )
    assert application.json()["submitted_at"] is None


async def test_run_in_progress_or_cancelled_blocks_reanalysis_and_closes_queue(
    client: AsyncClient,
    session_factory: SessionFactory,
    browser_runtime: Any,
) -> None:
    user, application_id, _session = await prepare_human_loop(
        client, session_factory
    )
    headers = bearer(user)
    application_uuid = uuid.UUID(application_id)

    initial = await client.get(
        loop_url(application_id, "/interventions"), headers=headers
    )
    assert initial.status_code == 200, initial.text
    assert initial.json()["pending_count"] >= 2

    async with session_factory() as db:
        row = await db.scalar(
            select(ApplicationFieldExecution).where(
                ApplicationFieldExecution.application_id == application_uuid
            )
        )
        assert row is not None
        row.status = FieldExecutionStatus.RUNNING
        await db.commit()

    busy = await client.post(
        loop_url(application_id, "/human-loop/reanalyze"), headers=headers
    )
    assert busy.status_code == 409
    assert busy.json()["code"] == "field_fill_in_progress"

    async with session_factory() as db:
        row = await db.scalar(
            select(ApplicationFieldExecution).where(
                ApplicationFieldExecution.application_id == application_uuid
            )
        )
        assert row is not None
        row.status = FieldExecutionStatus.CANCELLED
        await db.commit()

    cancelled = await client.post(
        loop_url(application_id, "/human-loop/reanalyze"), headers=headers
    )
    assert cancelled.status_code == 409
    assert cancelled.json()["code"] == "field_fill_cancelled"

    queue = await client.get(
        loop_url(application_id, "/interventions"), headers=headers
    )
    assert queue.status_code == 200, queue.text
    body = queue.json()
    assert body["pending_count"] == 0
    assert body["user_working_count"] == 0
    assert body["blocking_count"] == 0
    assert body["interventions"]
    assert all(
        item["status"] == InterventionStatus.CANCELLED.value
        for item in body["interventions"]
    )

    application = await client.get(
        f"/api/v1/applications/{application_id}", headers=headers
    )
    assert application.json()["submitted_at"] is None


async def test_pause_accepts_waiting_for_confirmation_state(
    client: AsyncClient,
    session_factory: SessionFactory,
    browser_runtime: Any,
) -> None:
    user, application_id, _session = await prepare_human_loop(
        client, session_factory
    )
    async with session_factory() as db:
        row = await db.scalar(
            select(ApplicationFieldExecution).where(
                ApplicationFieldExecution.application_id
                == uuid.UUID(application_id)
            )
        )
        assert row is not None
        row.status = FieldExecutionStatus.WAITING_FOR_CONFIRMATION
        await db.commit()

    paused = await client.post(
        f"{BROWSER}/by-application/{application_id}/fill/pause",
        json={},
        headers=bearer(user),
    )
    assert paused.status_code == 200, paused.text
    assert paused.json()["status"] == "paused"


async def test_intervention_endpoints_require_authentication(
    client: AsyncClient,
) -> None:
    application_id = "00000000-0000-0000-0000-000000000000"
    intervention_id = "00000000-0000-0000-0000-000000000001"
    assert (
        await client.get(loop_url(application_id, "/interventions"))
    ).status_code == 401
    assert (
        await client.get(loop_url(application_id, "/final-review"))
    ).status_code == 401
    assert (
        await client.post(
            loop_url(
                application_id, f"/interventions/{intervention_id}/working"
            )
        )
    ).status_code == 401
