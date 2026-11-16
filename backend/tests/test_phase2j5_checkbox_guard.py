"""Phase 2J.5: a checkbox changed after approval is never touched.

The real-browser evidence lives in ``test_phase2j5_checkbox_browser.py``. This
module covers the service mapping that turns a driver refusal into a field the
user has to review again, and proves the refusal does not block the rest of the
form. It uses the deterministic fake handle from the Phase 2J.3 suite.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.integrations.browser.field_actions import FieldActionResult
from app.models.application import (
    AnswerSource,
    ApplicationQuestion,
    QuestionStatus,
    QuestionType,
)
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.test_applications import bearer
from tests.test_phase2j3_unified_fields import (
    BROWSER,
    browser_runtime,  # noqa: F401 - pytest fixture
    fill_url,
    prepare_plan,
)

SessionFactory = async_sessionmaker[AsyncSession]

NAME_ID = "form0-name"
EMAIL_ID = "form0-email"
DEGREE_ID = "form0-degree"

#: A single checkbox the planner treats like any other field: it is only ready
#: because the questionnaire answer for ``education.degree`` was confirmed.
CHECKBOX_FORM = """
<html><body>
<form id="application">
  <label for="name">Full name</label>
  <input id="name" name="full_name" type="text" required>
  <label for="email">Email address</label>
  <input id="email" name="email" type="email">
  <label for="degree">Do you have a bachelor degree?</label>
  <input id="degree" name="degree" type="checkbox">
</form>
</body></html>
"""


async def _confirm_degree(
    session_factory: SessionFactory,
    application_id: str,
    *,
    answer: str = "No",
) -> None:
    """Record a user-confirmed answer for the degree checkbox."""
    identifier = uuid.UUID(application_id)
    async with session_factory() as session:
        existing = await session.scalar(
            select(ApplicationQuestion).where(
                ApplicationQuestion.application_id == identifier,
                ApplicationQuestion.question_key == "education.degree",
            )
        )
        if existing is None:
            session.add(
                ApplicationQuestion(
                    application_id=identifier,
                    question_key="education.degree",
                    field_identifier="degree",
                    section="education",
                    question_text="Do you have a bachelor degree?",
                    field_type=QuestionType.BOOLEAN,
                    required=False,
                    options=[],
                    answer=answer,
                    answer_source=AnswerSource.USER_INPUT,
                    confidence=1.0,
                    requires_confirmation=False,
                    status=QuestionStatus.CONFIRMED,
                    order_index=0,
                )
            )
        else:
            existing.answer = answer
            existing.status = QuestionStatus.CONFIRMED
            existing.answer_source = AnswerSource.USER_INPUT
        await session.commit()


async def _confirmed_plan(
    client: AsyncClient,
    session_factory: SessionFactory,
    user: Any,
    application_id: str,
    *,
    answer: str = "No",
) -> dict[str, Any]:
    """Re-analyse after confirming the degree answer and return the new plan."""
    await _confirm_degree(session_factory, application_id, answer=answer)
    headers = bearer(user)
    analyzed = await client.post(
        f"{BROWSER}/by-application/{application_id}/form-analysis", headers=headers
    )
    assert analyzed.status_code == 200, analyzed.text
    planned = await client.get(fill_url(application_id, "-plan"), headers=headers)
    assert planned.status_code == 200, planned.text
    return planned.json()


def _rows(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["field_id"]: item for item in plan["fields"]}


async def test_a_checkbox_refused_by_the_driver_requires_renewed_review(
    client: AsyncClient,
    session_factory: SessionFactory,
    browser_runtime: Any,  # noqa: F811
) -> None:
    """The driver's review signal becomes a pending field, not a retry."""
    user, application_id, _plan, handle = await prepare_plan(
        client,
        session_factory,
        browser_runtime,
        html=CHECKBOX_FORM,
        email="checkbox-guard@example.com",
        google_sub="google-sub-checkbox-guard",
    )
    plan = await _confirmed_plan(client, session_factory, user, application_id)
    degree = _rows(plan)[DEGREE_ID]
    assert degree["status"] == "ready"
    assert degree["action"] == "check"
    assert degree["proposed_value"] == "no"

    original = handle.apply_field_action

    async def refusing(action: Any) -> FieldActionResult:
        if action.field_id == DEGREE_ID:
            handle.actions.append(action)
            return FieldActionResult(
                False,
                action.field_id,
                observed_value="yes",
                reason=(
                    "This checkbox changed after you approved the plan, so "
                    "ApplyPilot left it untouched."
                ),
                requires_review=True,
            )
        return await original(action)

    handle.apply_field_action = refusing

    started = await client.post(
        fill_url(application_id),
        json={"approval_fingerprint": plan["approval_fingerprint"]},
        headers=bearer(user),
    )
    assert started.status_code == 200, started.text
    body = started.json()
    results = {item["field_id"]: item for item in body["results"]}

    # The changed checkbox goes back to the user for a fresh review.
    assert results[DEGREE_ID]["status"] == "pending_human_input"
    assert "changed after you approved" in results[DEGREE_ID]["reason"]
    assert "Review this field again" in results[DEGREE_ID]["reason"]
    assert results[DEGREE_ID]["verified"] is False

    # It is not a failure that blocks the rest of the form.
    assert results[NAME_ID]["status"] == "succeeded"
    assert results[EMAIL_ID]["status"] == "succeeded"
    assert body["succeeded_count"] == 2
    assert body["failed_count"] == 0
    assert body["pending_human_input_count"] == 1

    # The pause names the field so the UI can point the user at it.
    assert body["status"] == "needs_user_input"
    assert "Do you have a bachelor degree?" in (body["pause_reason"] or "")
    assert "left them untouched" in (body["pause_reason"] or "")
