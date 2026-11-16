"""Phase 2J.4: resume must respect values already present on the live page.

The Phase 2J.3 approval bound a run to the plan the user reviewed, but that plan
is derived from a stored analysis. If the user edits the live page between the
analysis and the resume, the stored plan is stale; without a live read the
engine would type over what the user entered.

These tests mutate the fake handle's HTML between execution and resume, which is
exactly what a manual edit in the browser does
(``PersistentBrowserHandle.content`` mirrors live control values into the
snapshot). No real Chromium is launched here; real-browser journeys stay in the
``-m browser`` suites.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.models.application import ApplicationQuestion, QuestionStatus
from app.models.candidate import Candidate
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.test_applications import bearer, create_user
from tests.test_phase2e_field_fill import FILL_FORM
from tests.test_phase2j3_unified_fields import (
    BROWSER,
    browser_runtime,  # noqa: F401 - pytest fixture
    fill_url,
    prepare_plan,
)

SessionFactory = async_sessionmaker[AsyncSession]

NAME_ID = "form0-name"
PHONE_ID = "form0-phone"
CITY_ID = "form0-city"
PREF_ID = "form0-pref"


def _with_value(html: str, html_id: str, value: str) -> str:
    """Add or replace a control's value exactly as the live snapshot would."""
    marker = f'<input id="{html_id}"'
    start = html.index(marker)
    end = html.index(">", start)
    tag = html[start:end].split(" value=")[0]
    return html[:start] + f'{tag} value="{value}"' + html[end:]


def _without_value(html: str, html_id: str) -> str:
    marker = f'<input id="{html_id}"'
    start = html.index(marker)
    end = html.index(">", start)
    tag = html[start:end].split(" value=")[0]
    return html[:start] + tag + html[end:]


def _results(execution: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["field_id"]: item for item in execution["results"]}


async def _resume(client: AsyncClient, user: Any, application_id: str, approved: str):
    return await client.post(
        fill_url(application_id, "/resume"),
        json={"approval_fingerprint": approved},
        headers=bearer(user),
    )


async def _start_paused(
    client: AsyncClient,
    session_factory: SessionFactory,
    runtime: Any,
    *,
    email: str,
    google_sub: str,
) -> tuple[Any, str, dict[str, Any], Any]:
    """Approve a plan and start a run that pauses before touching a field.

    ``user_input_active`` makes the coordination guard pause the run, which is
    how a user interacting with the page mid-run behaves. The page is therefore
    still empty when a test simulates the user's own edits.
    """
    user, application_id, plan, handle = await prepare_plan(
        client, session_factory, runtime, email=email, google_sub=google_sub
    )
    state = {"touched": True}
    handle.user_input_active = lambda seconds=2.0: state["touched"]
    started = await client.post(
        fill_url(application_id),
        json={"approval_fingerprint": plan["approval_fingerprint"]},
        headers=bearer(user),
    )
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "needs_user_input"
    assert handle.actions == []
    state["touched"] = False
    return user, application_id, started.json(), handle


# ---------------------------------------------------------------------------
# Live values are re-read immediately before resuming
# ---------------------------------------------------------------------------
async def test_resume_never_overwrites_a_value_the_user_entered(
    client: AsyncClient,
    session_factory: SessionFactory,
    browser_runtime: Any,  # noqa: F811
) -> None:
    user, application_id, execution, handle = await _start_paused(
        client,
        session_factory,
        browser_runtime,
        email="live-resume@example.com",
        google_sub="google-sub-live-resume",
    )
    approved = execution["approval_fingerprint"]
    # The user typed their own phone number while the run was paused.
    handle.html = _with_value(FILL_FORM, "phone", "+1 555 0199")

    resumed = await _resume(client, user, application_id, approved)
    assert resumed.status_code == 200, resumed.text
    body = resumed.json()
    assert body["status"] == "needs_user_input"

    phone = _results(body)[PHONE_ID]
    assert phone["status"] == "pending_human_input"
    assert "left your value untouched" in phone["reason"]
    assert "Phone number" in (body["pause_reason"] or "")
    # The user's value was not replaced, and other fields were still filled.
    assert PHONE_ID not in {action.field_id for action in handle.actions}
    assert NAME_ID in {action.field_id for action in handle.actions}
    # Execution responses never echo filled values.
    assert "+1 555 0199" not in resumed.text


async def test_resume_does_not_replace_a_changed_approved_value(
    client: AsyncClient,
    session_factory: SessionFactory,
    browser_runtime: Any,  # noqa: F811
) -> None:
    user, application_id, execution, handle = await _start_paused(
        client,
        session_factory,
        browser_runtime,
        email="changed@example.com",
        google_sub="google-sub-changed",
    )
    approved = execution["approval_fingerprint"]
    # The plan proposed the profile's name; the user typed a different one.
    handle.html = _with_value(FILL_FORM, "name", "Grace Hopper")

    resumed = await _resume(client, user, application_id, approved)
    assert resumed.status_code == 200, resumed.text
    results = _results(resumed.json())
    assert results[NAME_ID]["status"] == "pending_human_input"
    assert NAME_ID not in {action.field_id for action in handle.actions}


async def test_resume_accepts_a_value_that_already_equals_the_approval(
    client: AsyncClient,
    session_factory: SessionFactory,
    browser_runtime: Any,  # noqa: F811
) -> None:
    user, application_id, execution, handle = await _start_paused(
        client,
        session_factory,
        browser_runtime,
        email="equal@example.com",
        google_sub="google-sub-equal",
    )
    approved = execution["approval_fingerprint"]
    handle.html = _with_value(FILL_FORM, "name", "Ada Lovelace")

    resumed = await _resume(client, user, application_id, approved)
    assert resumed.status_code == 200, resumed.text
    name = _results(resumed.json())[NAME_ID]
    # The field already holds the approved value: not retyped, not a conflict.
    assert NAME_ID not in {action.field_id for action in handle.actions}
    assert name["status"] == "skipped"
    assert "already contains the approved value" in name["reason"]


async def test_resume_leaves_a_cleared_value_alone(
    client: AsyncClient,
    session_factory: SessionFactory,
    browser_runtime: Any,  # noqa: F811
) -> None:
    user, application_id, execution, handle = await _start_paused(
        client,
        session_factory,
        browser_runtime,
        email="cleared@example.com",
        google_sub="google-sub-cleared",
    )
    approved = execution["approval_fingerprint"]
    # "pref" was already filled at analysis time, so it was never part of the
    # approved plan. Clearing it must not make a stale plan write it back.
    handle.html = _without_value(FILL_FORM, "pref")

    resumed = await _resume(client, user, application_id, approved)
    assert resumed.status_code == 200, resumed.text
    assert PREF_ID not in {action.field_id for action in handle.actions}
    assert _results(resumed.json())[PREF_ID]["status"] == "skipped"


# ---------------------------------------------------------------------------
# Scope cannot expand without a new approval
# ---------------------------------------------------------------------------
async def test_newly_visible_field_requires_a_fresh_analysis(
    client: AsyncClient,
    session_factory: SessionFactory,
    browser_runtime: Any,  # noqa: F811
) -> None:
    user, application_id, execution, handle = await _start_paused(
        client,
        session_factory,
        browser_runtime,
        email="newfield@example.com",
        google_sub="google-sub-newfield",
    )
    handle.html = FILL_FORM.replace(
        "</form>",
        '<label for="linkedin">LinkedIn profile</label>'
        '<input id="linkedin" name="linkedin" type="url"></form>',
    )

    resumed = await _resume(
        client, user, application_id, execution["approval_fingerprint"]
    )
    assert resumed.status_code == 409
    assert resumed.json()["code"] == "approval_stale"
    assert "changed since it was analyzed" in resumed.json()["detail"]
    assert handle.actions == []


async def test_a_newly_fillable_field_requires_a_fresh_plan(
    client: AsyncClient,
    session_factory: SessionFactory,
    browser_runtime: Any,  # noqa: F811
) -> None:
    """A field that becomes fillable after approval is not silently in scope."""
    user, application_id, _plan, handle = await prepare_plan(
        client,
        session_factory,
        browser_runtime,
        email="newvalue@example.com",
        google_sub="google-sub-newvalue",
    )
    headers = bearer(user)

    # Approve a plan in which the phone field has no source and is out of scope.
    async with session_factory() as session:
        candidate = await session.scalar(select(Candidate))
        assert candidate is not None
        assert candidate.phone
        candidate.phone = None
        question = await session.scalar(
            select(ApplicationQuestion).where(
                ApplicationQuestion.question_key == "contact.phone"
            )
        )
        assert question is not None
        question.answer = None
        question.status = QuestionStatus.MISSING
        await session.commit()
    assert (
        await client.post(
            f"{BROWSER}/by-application/{application_id}/form-analysis", headers=headers
        )
    ).status_code == 200
    refreshed = await client.get(fill_url(application_id, "-plan"), headers=headers)
    assert refreshed.status_code == 200, refreshed.text
    phone_row = _results({"results": refreshed.json()["fields"]})[PHONE_ID]
    assert phone_row["status"] == "skipped"
    assert phone_row["proposed_value"] is None
    approved = refreshed.json()["approval_fingerprint"]

    state = {"touched": True}
    handle.user_input_active = lambda seconds=2.0: state["touched"]
    started = await client.post(
        fill_url(application_id),
        json={"approval_fingerprint": approved},
        headers=headers,
    )
    assert started.status_code == 200, started.text
    assert handle.actions == []
    state["touched"] = False

    # The user supplies a phone number in ApplyPilot and re-analyses, so the
    # newest plan would now fill a field the approved plan never covered.
    async with session_factory() as session:
        candidate = await session.scalar(select(Candidate))
        assert candidate is not None
        candidate.phone = "+1 555 0111"
        question = await session.scalar(
            select(ApplicationQuestion).where(
                ApplicationQuestion.question_key == "contact.phone"
            )
        )
        assert question is not None
        question.answer = "+1 555 0111"
        question.status = QuestionStatus.CONFIRMED
        await session.commit()
    assert (
        await client.post(
            f"{BROWSER}/by-application/{application_id}/form-analysis", headers=headers
        )
    ).status_code == 200

    refused = await _resume(client, user, application_id, approved)
    assert refused.status_code == 409
    assert refused.json()["code"] == "approval_stale"
    assert handle.actions == []

    # Reviewing and approving the current plan is the supported way to expand it.
    fresh = await client.get(fill_url(application_id, "-plan"), headers=headers)
    assert fresh.status_code == 200, fresh.text
    fresh_phone = _results({"results": fresh.json()["fields"]})[PHONE_ID]
    assert fresh_phone["status"] == "ready"
    assert fresh_phone["proposed_value"]
    accepted = await _resume(
        client, user, application_id, fresh.json()["approval_fingerprint"]
    )
    assert accepted.status_code == 200, accepted.text
    assert PHONE_ID in {action.field_id for action in handle.actions}


# ---------------------------------------------------------------------------
# Unchanged fields still resume, and failures stay honest
# ---------------------------------------------------------------------------
async def test_unchanged_form_resumes_and_fills_the_remaining_fields(
    client: AsyncClient,
    session_factory: SessionFactory,
    browser_runtime: Any,  # noqa: F811
) -> None:
    user, application_id, execution, handle = await _start_paused(
        client,
        session_factory,
        browser_runtime,
        email="unchanged@example.com",
        google_sub="google-sub-unchanged",
    )
    resumed = await _resume(
        client, user, application_id, execution["approval_fingerprint"]
    )
    assert resumed.status_code == 200, resumed.text
    body = resumed.json()
    results = _results(body)
    assert results[NAME_ID]["status"] == "succeeded"
    assert results[CITY_ID]["status"] == "succeeded"
    filled = {action.field_id for action in handle.actions}
    assert {NAME_ID, CITY_ID} <= filled
    # The human-only and upload fields stay out of scope and are reported as
    # pending rather than silently attempted.
    assert results["form0-otp"]["status"] == "pending_human_input"


async def test_resume_refuses_an_expired_browser_session(
    client: AsyncClient,
    session_factory: SessionFactory,
    browser_runtime: Any,  # noqa: F811
) -> None:
    user, application_id, execution, handle = await _start_paused(
        client,
        session_factory,
        browser_runtime,
        email="expired@example.com",
        google_sub="google-sub-expired",
    )
    session_row = await client.get(
        f"{BROWSER}/by-application/{application_id}", headers=bearer(user)
    )
    assert session_row.status_code == 200
    await browser_runtime.manager.release(uuid.UUID(session_row.json()["session_id"]))

    resumed = await _resume(
        client, user, application_id, execution["approval_fingerprint"]
    )
    assert resumed.status_code in {400, 404, 409}
    assert resumed.json()["code"] in {
        "browser_session_expired",
        "browser_session_not_found",
    }
    assert handle.actions == []


async def test_resume_refuses_when_the_page_navigated(
    client: AsyncClient,
    session_factory: SessionFactory,
    browser_runtime: Any,  # noqa: F811
) -> None:
    user, application_id, execution, handle = await _start_paused(
        client,
        session_factory,
        browser_runtime,
        email="navigated@example.com",
        google_sub="google-sub-navigated",
    )
    handle.url = "https://boards.greenhouse.io/acme/jobs/999"

    resumed = await _resume(
        client, user, application_id, execution["approval_fingerprint"]
    )
    assert resumed.status_code == 409
    assert resumed.json()["code"] == "approval_stale"
    assert "page changed" in resumed.json()["detail"]
    assert handle.actions == []


async def test_a_foreign_user_cannot_resume_another_users_run(
    client: AsyncClient,
    session_factory: SessionFactory,
    browser_runtime: Any,  # noqa: F811
) -> None:
    _user, application_id, execution, handle = await _start_paused(
        client,
        session_factory,
        browser_runtime,
        email="owner@example.com",
        google_sub="google-sub-owner",
    )
    intruder = await create_user(
        session_factory,
        email="resume-intruder@example.com",
        google_sub="google-sub-resume-intruder",
    )
    resumed = await client.post(
        fill_url(application_id, "/resume"),
        json={"approval_fingerprint": execution["approval_fingerprint"]},
        headers=bearer(intruder),
    )
    assert resumed.status_code == 404
    assert resumed.json()["code"] == "application_not_found"
    assert handle.actions == []
