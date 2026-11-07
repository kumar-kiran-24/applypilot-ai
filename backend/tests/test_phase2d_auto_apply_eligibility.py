"""Phase 2D: deterministic auto-apply eligibility and approvals."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from app.core.config import settings
from app.models.application import Application, ApplicationStatus
from app.models.job import Job
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.test_applications import (
    bearer,
    create_job,
    create_resume,
    create_user,
    prepare_profile,
)

SessionFactory = async_sessionmaker[AsyncSession]

AUTO_APPLY = "/api/v1/auto-apply"
APPLICATIONS = "/api/v1/applications"
PROFILE = "/api/v1/profile"
GREENHOUSE_URL = "https://boards.greenhouse.io/acme/jobs/123"
WORKDAY_URL = (
    "https://acme.wd1.myworkdayjobs.com/en-US/External/job/Austin/Engineer_R1"
)


@pytest.fixture(autouse=True)
def automation_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Phase 2D never opens a browser; force the manual inspection path."""
    monkeypatch.setattr(settings, "APPLICATION_AUTOMATION_ENABLED", False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def complete_profile(client: AsyncClient, user: Any) -> None:
    """Fill every profile field the eligibility engine treats as required."""
    await prepare_profile(client, user)
    headers = bearer(user)
    updated = await client.put(
        PROFILE,
        json={
            "address_line1": "1 Analytical Engine Way",
            "city": "Austin",
            "country": "United States",
            "skills": ["Python", "FastAPI", "PostgreSQL", "Docker"],
            "requires_sponsorship": False,
        },
        headers=headers,
    )
    assert updated.status_code == 200, updated.text
    for path, payload in (
        (
            f"{PROFILE}/education",
            {
                "institution": "University of London",
                "degree": "BSc Computer Science",
            },
        ),
        (
            f"{PROFILE}/experience",
            {
                "company": "Acme",
                "designation": "Senior Backend Engineer",
                "employment_type": "full_time",
                "is_current": True,
            },
        ),
    ):
        response = await client.post(path, json=payload, headers=headers)
        assert response.status_code == 201, response.text


async def prepare_application(
    client: AsyncClient,
    user: Any,
    job: Job,
    *,
    url: str = GREENHOUSE_URL,
) -> dict[str, Any]:
    response = await client.post(
        f"{APPLICATIONS}/analyze",
        json={"job_id": str(job.id), "application_url": url},
        headers=bearer(user),
    )
    assert response.status_code == 200, response.text
    return response.json()


def default_answer(question: dict[str, Any]) -> str:
    if question["question_key"] == "authorization.sponsorship":
        return "No"
    if question["question_key"] == "declarations.accuracy":
        return "Yes"
    if question["question_key"] == "authorization.status":
        return "Authorized to work in the United States"
    return question["answer"] or "Yes"


async def answer_required(
    client: AsyncClient,
    user: Any,
    application_id: str,
    preview: dict[str, Any],
    *,
    skip: set[str] | None = None,
) -> dict[str, Any]:
    skip = skip or set()
    headers = bearer(user)
    for section in preview["sections"]:
        for question in section["questions"]:
            if (
                not question["required"]
                or question["status"] == "confirmed"
                or question["question_key"] in skip
            ):
                continue
            response = await client.patch(
                f"{APPLICATIONS}/{application_id}/questions/{question['id']}",
                json={"answer": default_answer(question), "confirmed": True},
                headers=headers,
            )
            assert response.status_code == 200, (
                question["question_key"],
                response.text,
            )
    latest = await client.get(f"{APPLICATIONS}/{application_id}", headers=headers)
    assert latest.status_code == 200, latest.text
    return latest.json()


async def confirm_question(
    client: AsyncClient, user: Any, application_id: str, preview: dict[str, Any], key: str
) -> dict[str, Any]:
    question = next(
        item
        for section in preview["sections"]
        for item in section["questions"]
        if item["question_key"] == key
    )
    response = await client.patch(
        f"{APPLICATIONS}/{application_id}/questions/{question['id']}",
        json={"answer": default_answer(question), "confirmed": True},
        headers=bearer(user),
    )
    assert response.status_code == 200, response.text
    return response.json()


def checks(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {check["name"]: check for check in report["checks"]}


async def evaluate(client: AsyncClient, user: Any, application_id: str) -> dict[str, Any]:
    response = await client.get(
        f"{AUTO_APPLY}/eligibility/{application_id}", headers=bearer(user)
    )
    assert response.status_code == 200, response.text
    return response.json()


async def update_settings(
    client: AsyncClient, user: Any, **values: Any
) -> dict[str, Any]:
    response = await client.put(
        f"{AUTO_APPLY}/settings", json=values, headers=bearer(user)
    )
    assert response.status_code == 200, response.text
    return response.json()


async def set_job_field(
    session_factory: SessionFactory, job: Job, **values: Any
) -> None:
    async with session_factory() as session:
        row = await session.get(Job, job.id)
        assert row is not None
        for field, value in values.items():
            setattr(row, field, value)
        await session.commit()


async def set_application_fields(
    session_factory: SessionFactory, application_id: str, **values: Any
) -> None:
    async with session_factory() as session:
        row = await session.get(Application, uuid.UUID(application_id))
        assert row is not None
        for field, value in values.items():
            setattr(row, field, value)
        await session.commit()


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
async def test_conservative_defaults_and_partial_updates(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory, email="settings@example.com")
    headers = bearer(user)

    response = await client.get(f"{AUTO_APPLY}/settings", headers=headers)
    assert response.status_code == 200, response.text
    defaults = response.json()
    assert defaults["automation_enabled"] is False
    assert defaults["require_user_approval"] is True
    assert defaults["require_user_review"] is True
    assert defaults["minimum_resume_match_percentage"] == 60
    assert defaults["max_applications_per_day"] == 5
    assert defaults["minimum_salary"] is None
    assert defaults["maximum_salary"] is None
    assert defaults["relocation_willingness"] == "unknown"
    assert defaults["preferred_job_titles"] == []
    assert defaults["excluded_companies"] == []

    updated = await update_settings(
        client,
        user,
        automation_enabled=True,
        preferred_companies=["Acme", "Acme", "  Globex  "],
        minimum_resume_match_percentage=75,
    )
    assert updated["automation_enabled"] is True
    assert updated["preferred_companies"] == ["Acme", "Globex"]
    assert updated["minimum_resume_match_percentage"] == 75
    # Omitted fields keep their conservative defaults.
    assert updated["require_user_approval"] is True
    assert updated["require_user_review"] is True
    assert updated["max_applications_per_day"] == 5
    assert updated["minimum_salary"] is None
    assert updated["relocation_willingness"] == "unknown"

    with_salary = await update_settings(
        client,
        user,
        minimum_salary="100000",
        maximum_salary="180000",
        currency="eur",
        relocation_willingness="negotiable",
    )
    assert with_salary["currency"] == "EUR"
    assert with_salary["relocation_willingness"] == "negotiable"

    invalid = await client.put(
        f"{AUTO_APPLY}/settings",
        json={"minimum_salary": "200000", "maximum_salary": "100000"},
        headers=headers,
    )
    assert invalid.status_code == 422

    other = await create_user(
        session_factory, email="settings-other@example.com", google_sub="sub-other"
    )
    other_settings = (
        await client.get(f"{AUTO_APPLY}/settings", headers=bearer(other))
    ).json()
    assert other_settings["automation_enabled"] is False
    assert other_settings["preferred_companies"] == []


async def test_auto_apply_endpoints_require_authentication(client: AsyncClient) -> None:
    assert (await client.get(f"{AUTO_APPLY}/settings")).status_code == 401
    assert (
        await client.put(f"{AUTO_APPLY}/settings", json={"automation_enabled": True})
    ).status_code == 401
    assert (await client.get(f"{AUTO_APPLY}/eligibility")).status_code == 401
    application_id = str(uuid.uuid4())
    assert (
        await client.get(f"{AUTO_APPLY}/eligibility/{application_id}")
    ).status_code == 401
    assert (
        await client.post(f"{AUTO_APPLY}/eligibility/{application_id}/evaluate")
    ).status_code == 401
    assert (
        await client.post(
            f"{AUTO_APPLY}/eligibility/{application_id}/approve",
            json={"report_version": "elig-1-x"},
        )
    ).status_code == 401


# ---------------------------------------------------------------------------
# Happy path and approval
# ---------------------------------------------------------------------------
async def test_complete_application_becomes_ready_and_approval_is_versioned(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory, email="ready@example.com")
    job = await create_job(session_factory, user)
    await create_resume(session_factory, user)
    await complete_profile(client, user)
    preview = await prepare_application(client, user, job)
    application_id = preview["application_id"]
    ready = await answer_required(client, user, application_id, preview)

    report = await evaluate(client, user, application_id)
    by_name = checks(report)
    assert report["overall_decision"] == "ready_for_user_review"
    assert report["blocking_issues"] == []
    assert report["resume_selected"] is True
    assert report["resume_filename"] == "strong.pdf"
    assert report["resume_match_score"] is not None
    assert by_name["resume_suitability"]["status"] == "eligible"
    # Optional sensitive questions may still need confirmation; only required
    # questions can block, so the check is a non-blocking warning here.
    assert by_name["answer_readiness"]["blocking"] is False
    assert by_name["answer_readiness"]["status"] in {"eligible", "needs_confirmation"}
    assert by_name["automation_settings"]["status"] == "needs_user_approval"
    assert by_name["automation_settings"]["blocking"] is False
    assert by_name["user_approval"]["status"] == "needs_user_approval"
    assert report["approval"]["approved"] is False
    assert report["report_version"]

    await update_settings(client, user, automation_enabled=True)
    gated = await evaluate(client, user, application_id)
    assert gated["overall_decision"] == "ready_for_user_review"
    assert gated["approval"]["approved"] is False

    approved = await client.post(
        f"{AUTO_APPLY}/eligibility/{application_id}/approve",
        json={"report_version": gated["report_version"], "approved": True},
        headers=bearer(user),
    )
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["overall_decision"] == "ready_to_prepare"
    assert body["approval"]["approved"] is True
    assert body["approval"]["current"] is True
    assert body["approval"]["report_version"] == gated["report_version"]
    assert body["approval"]["approved_by_user_id"] == str(user.id)
    assert body["approval"]["approved_at"] is not None

    # Changing settings changes the report version and invalidates approval.
    await update_settings(client, user, minimum_resume_match_percentage=50)
    changed = await evaluate(client, user, application_id)
    assert changed["report_version"] != gated["report_version"]
    assert changed["approval"]["current"] is False
    assert changed["overall_decision"] == "ready_for_user_review"

    stale_attempt = await client.post(
        f"{AUTO_APPLY}/eligibility/{application_id}/approve",
        json={"report_version": gated["report_version"], "approved": True},
        headers=bearer(user),
    )
    assert stale_attempt.status_code == 409
    assert stale_attempt.json()["code"] == "approval_version_mismatch"

    reapproved = await client.post(
        f"{AUTO_APPLY}/eligibility/{application_id}/approve",
        json={"report_version": changed["report_version"], "approved": True},
        headers=bearer(user),
    )
    assert reapproved.status_code == 200, reapproved.text
    assert reapproved.json()["overall_decision"] == "ready_to_prepare"

    # The report never exposes resume storage details or parsed text.
    serialised = json.dumps(body)
    assert "storage_key" not in serialised
    assert "parsed_data" not in serialised

    # Withdrawing approval returns the application to review.
    withdrawn = await client.post(
        f"{AUTO_APPLY}/eligibility/{application_id}/approve",
        json={"report_version": changed["report_version"], "approved": False},
        headers=bearer(user),
    )
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()["approval"]["approved"] is False
    assert withdrawn.json()["overall_decision"] == "ready_for_user_review"
    assert ready["status"] in {"ready_for_review", "awaiting_user_information"}


async def test_blocked_application_cannot_be_approved(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory, email="blocked-approve@example.com")
    job = await create_job(session_factory, user)
    await create_resume(session_factory, user)
    # Only the minimal profile: required fields are missing.
    preview = await prepare_application(client, user, job)
    application_id = preview["application_id"]
    report = await evaluate(client, user, application_id)
    assert report["overall_decision"] == "blocked"
    assert report["blocking_issues"]

    attempt = await client.post(
        f"{AUTO_APPLY}/eligibility/{application_id}/approve",
        json={"report_version": report["report_version"], "approved": True},
        headers=bearer(user),
    )
    assert attempt.status_code == 409
    assert attempt.json()["code"] == "application_not_eligible"


# ---------------------------------------------------------------------------
# Blocking rules
# ---------------------------------------------------------------------------
async def test_missing_profile_information_blocks(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory, email="missing-profile@example.com")
    job = await create_job(session_factory, user)
    await create_resume(session_factory, user)
    preview = await prepare_application(client, user, job)
    report = await evaluate(client, user, preview["application_id"])

    profile = checks(report)["profile_completeness"]
    assert profile["status"] == "needs_information"
    assert profile["blocking"] is True
    assert profile["details"]["missing_fields"]
    assert report["overall_decision"] == "blocked"


async def test_missing_resume_blocks(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory, email="missing-resume@example.com")
    job = await create_job(session_factory, user)
    await complete_profile(client, user)
    preview = await prepare_application(client, user, job)
    report = await evaluate(client, user, preview["application_id"])

    resume = checks(report)["resume_suitability"]
    assert resume["status"] == "needs_information"
    assert resume["blocking"] is True
    assert report["resume_selected"] is False
    assert report["overall_decision"] == "blocked"


async def test_low_resume_match_blocks_even_with_a_complete_profile(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory, email="low-match@example.com")
    job = await create_job(session_factory, user)
    await create_resume(session_factory, user, filename="weak.pdf", skills=["COBOL"])
    await complete_profile(client, user)
    preview = await prepare_application(client, user, job)
    await answer_required(client, user, preview["application_id"], preview)
    await update_settings(client, user, minimum_resume_match_percentage=100)

    report = await evaluate(client, user, preview["application_id"])
    resume = checks(report)["resume_suitability"]
    assert report["resume_match_score"] is not None
    assert report["resume_match_score"] < 100
    assert resume["status"] == "ineligible"
    assert resume["blocking"] is True
    assert resume["details"]["minimum_score"] == 100
    assert report["overall_decision"] == "blocked"


async def test_stale_required_answer_needs_confirmation(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory, email="stale@example.com")
    job = await create_job(session_factory, user)
    await create_resume(session_factory, user)
    await complete_profile(client, user)
    headers = bearer(user)

    cleared = await client.put(PROFILE, json={"phone": None}, headers=headers)
    assert cleared.status_code == 200, cleared.text
    stored = await client.put(
        f"{PROFILE}/answers/contact.phone",
        json={
            "question_text": "Phone number",
            "answer": "+1 555 0199",
            "verification_status": "verified",
        },
        headers=headers,
    )
    assert stored.status_code == 200, stored.text

    preview = await prepare_application(client, user, job)
    report = await evaluate(client, user, preview["application_id"])
    answers = checks(report)["answer_readiness"]
    assert answers["status"] == "needs_confirmation"
    assert answers["blocking"] is True
    assert answers["details"]["required_remaining"] >= 1
    assert answers["details"]["stale_answers"] >= 1


async def test_conflicting_answers_need_confirmation(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory, email="conflict@example.com")
    job = await create_job(session_factory, user)
    await create_resume(session_factory, user)
    await complete_profile(client, user)
    stored = await client.put(
        f"{PROFILE}/answers/contact.phone",
        json={
            "question_text": "Phone number",
            "answer": "+1 555 0199",
            "verification_status": "verified",
        },
        headers=bearer(user),
    )
    assert stored.status_code == 200, stored.text

    preview = await prepare_application(client, user, job)
    report = await evaluate(client, user, preview["application_id"])
    answers = checks(report)["answer_readiness"]
    assert answers["status"] == "needs_confirmation"
    assert answers["blocking"] is True
    assert answers["details"]["conflicts"] >= 1
    assert report["overall_decision"] == "blocked"


async def test_sensitive_and_legal_answers_are_never_silently_approved(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory, email="legal@example.com")
    job = await create_job(session_factory, user)
    await create_resume(session_factory, user)
    await complete_profile(client, user)
    preview = await prepare_application(client, user, job)
    application_id = preview["application_id"]

    partially_answered = await answer_required(
        client,
        user,
        application_id,
        preview,
        skip={"declarations.accuracy", "authorization.status"},
    )
    first = await evaluate(client, user, application_id)
    answers = checks(first)["answer_readiness"]
    assert answers["status"] == "needs_confirmation"
    assert answers["blocking"] is True
    assert answers["details"]["legal_confirmations"] >= 1
    assert answers["details"]["sensitive_confirmations"] >= 1

    after_sensitive = await confirm_question(
        client, user, application_id, partially_answered, "authorization.status"
    )
    second = await evaluate(client, user, application_id)
    assert checks(second)["answer_readiness"]["blocking"] is True
    assert checks(second)["answer_readiness"]["details"]["legal_confirmations"] >= 1

    await confirm_question(
        client, user, application_id, after_sensitive, "declarations.accuracy"
    )
    third = await evaluate(client, user, application_id)
    assert checks(third)["answer_readiness"]["blocking"] is False
    assert third["overall_decision"] != "blocked"


async def test_excluded_company_and_job_title_are_ineligible(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory, email="excluded@example.com")
    job = await create_job(session_factory, user)
    await create_resume(session_factory, user)
    await complete_profile(client, user)
    preview = await prepare_application(client, user, job)

    await update_settings(client, user, excluded_companies=["Acme"])
    company_report = await evaluate(client, user, preview["application_id"])
    company = checks(company_report)["job_preferences"]
    assert company["status"] == "ineligible"
    assert company["blocking"] is True

    await update_settings(client, user, excluded_companies=[], excluded_job_titles=["Backend"])
    title_report = await evaluate(client, user, preview["application_id"])
    title = checks(title_report)["job_preferences"]
    assert title["status"] == "ineligible"
    assert title["blocking"] is True


async def test_location_mismatch_respects_relocation_willingness(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory, email="location@example.com")
    job = await create_job(session_factory, user)
    await create_resume(session_factory, user)
    await complete_profile(client, user)
    preview = await prepare_application(client, user, job)

    await update_settings(
        client,
        user,
        preferred_locations=["New York, NY"],
        relocation_willingness="unknown",
    )
    blocked = await evaluate(client, user, preview["application_id"])
    assert checks(blocked)["job_preferences"]["status"] == "ineligible"

    await update_settings(client, user, relocation_willingness="yes")
    allowed = await evaluate(client, user, preview["application_id"])
    preferences = checks(allowed)["job_preferences"]
    assert preferences["status"] == "needs_confirmation"
    assert preferences["blocking"] is False


async def test_salary_mismatch_is_ineligible(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory, email="salary@example.com")
    job = await create_job(session_factory, user)
    await create_resume(session_factory, user)
    await complete_profile(client, user)
    await set_job_field(
        session_factory,
        job,
        job_description="Backend role. Requirements: Python, FastAPI.\n"
        "Compensation: $80,000 per year.",
    )
    preview = await prepare_application(client, user, job)

    await update_settings(client, user, minimum_salary="100000", currency="USD")
    report = await evaluate(client, user, preview["application_id"])
    salary = checks(report)["salary_expectations"]
    assert salary["status"] == "ineligible"
    assert salary["blocking"] is True
    assert salary["details"]["salary_mentions"]

    await update_settings(client, user, minimum_salary="50000")
    relaxed = await evaluate(client, user, preview["application_id"])
    assert checks(relaxed)["salary_expectations"]["status"] == "eligible"


async def test_work_authorization_uncertainty_blocks(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory, email="authorization@example.com")
    job = await create_job(session_factory, user)
    await create_resume(session_factory, user)
    await complete_profile(client, user)
    cleared = await client.put(
        PROFILE, json={"requires_sponsorship": None}, headers=bearer(user)
    )
    assert cleared.status_code == 200, cleared.text
    preview = await prepare_application(client, user, job)
    report = await evaluate(client, user, preview["application_id"])

    authorization = checks(report)["work_authorization"]
    assert authorization["status"] == "needs_confirmation"
    assert authorization["blocking"] is True
    assert authorization["details"]["requires_sponsorship"] is None


async def test_duplicate_and_already_applied_are_distinguished(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory, email="duplicate@example.com")
    first_job = await create_job(session_factory, user)
    second_job = await create_job(session_factory, user)
    await create_resume(session_factory, user)
    await complete_profile(client, user)

    first = await prepare_application(
        client, user, first_job, url="https://boards.greenhouse.io/acme/jobs/1"
    )
    second = await prepare_application(
        client, user, second_job, url="https://boards.greenhouse.io/acme/jobs/2"
    )

    duplicate = await evaluate(client, user, second["application_id"])
    duplicate_check = checks(duplicate)["duplicate_application"]
    assert duplicate_check["status"] == "duplicate"
    assert duplicate_check["blocking"] is True
    assert str(first["application_id"]) in duplicate_check["details"][
        "duplicate_application_ids"
    ]

    await set_application_fields(
        session_factory,
        first["application_id"],
        status=ApplicationStatus.SUBMITTED,
        submitted_at=datetime.now(timezone.utc),
    )
    applied = await evaluate(client, user, second["application_id"])
    applied_check = checks(applied)["duplicate_application"]
    assert applied_check["status"] == "already_applied"
    assert applied_check["blocking"] is True


async def test_already_submitted_application_is_not_ready(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory, email="submitted@example.com")
    job = await create_job(session_factory, user)
    await create_resume(session_factory, user)
    await complete_profile(client, user)
    preview = await prepare_application(client, user, job)
    await answer_required(client, user, preview["application_id"], preview)
    await set_application_fields(
        session_factory,
        preview["application_id"],
        status=ApplicationStatus.SUBMITTED,
        submitted_at=datetime.now(timezone.utc),
    )

    report = await evaluate(client, user, preview["application_id"])
    assert checks(report)["application_status"]["status"] == "already_applied"
    assert report["overall_decision"] == "blocked"


async def test_unsupported_platform_and_invalid_url_block(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory, email="platform@example.com")
    job = await create_job(session_factory, user)
    await create_resume(session_factory, user)
    await complete_profile(client, user)

    unsupported = await prepare_application(client, user, job, url=WORKDAY_URL)
    workday_report = await evaluate(client, user, unsupported["application_id"])
    assert checks(workday_report)["platform_support"]["status"] == "unsupported_platform"
    assert checks(workday_report)["platform_support"]["blocking"] is True

    await set_application_fields(
        session_factory,
        unsupported["application_id"],
        application_url="http://127.0.0.1:8000/apply",
    )
    invalid_report = await evaluate(client, user, unsupported["application_id"])
    assert checks(invalid_report)["application_url"]["status"] == "ineligible"
    assert checks(invalid_report)["application_url"]["blocking"] is True
    assert checks(invalid_report)["platform_support"]["status"] == "failed"


async def test_automation_disabled_requires_explicit_user_approval(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory, email="disabled@example.com")
    job = await create_job(session_factory, user)
    await create_resume(session_factory, user)
    await complete_profile(client, user)
    preview = await prepare_application(client, user, job)
    await answer_required(client, user, preview["application_id"], preview)

    report = await evaluate(client, user, preview["application_id"])
    automation = checks(report)["automation_settings"]
    assert automation["status"] == "needs_user_approval"
    assert automation["blocking"] is False
    assert report["overall_decision"] == "ready_for_user_review"
    assert report["blocking_issues"] == []


# ---------------------------------------------------------------------------
# Ownership, listing and security
# ---------------------------------------------------------------------------
async def test_cross_user_access_is_denied(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    owner = await create_user(session_factory, email="owner-2d@example.com")
    other = await create_user(
        session_factory, email="intruder-2d@example.com", google_sub="sub-intruder-2d"
    )
    job = await create_job(session_factory, owner)
    await create_resume(session_factory, owner)
    await complete_profile(client, owner)
    preview = await prepare_application(client, owner, job)
    application_id = preview["application_id"]

    other_headers = bearer(other)
    assert (
        await client.get(
            f"{AUTO_APPLY}/eligibility/{application_id}", headers=other_headers
        )
    ).status_code == 404
    assert (
        await client.post(
            f"{AUTO_APPLY}/eligibility/{application_id}/evaluate",
            headers=other_headers,
        )
    ).status_code == 404
    assert (
        await client.post(
            f"{AUTO_APPLY}/eligibility/{application_id}/approve",
            json={"report_version": "elig-1-anything"},
            headers=other_headers,
        )
    ).status_code == 404

    listing = await client.get(f"{AUTO_APPLY}/eligibility", headers=other_headers)
    assert listing.status_code == 200
    assert listing.json()["applications"] == []
    assert listing.json()["overview"]["total"] == 0


async def test_eligibility_list_reports_every_bucket(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory, email="list@example.com")
    job = await create_job(session_factory, user)
    cancelled_job = await create_job(session_factory, user)
    await create_resume(session_factory, user)
    await complete_profile(client, user)
    preview = await prepare_application(client, user, job)
    await answer_required(client, user, preview["application_id"], preview)
    cancelled = await prepare_application(client, user, cancelled_job)
    await answer_required(client, user, cancelled["application_id"], cancelled)
    await set_application_fields(
        session_factory,
        cancelled["application_id"],
        status=ApplicationStatus.CANCELLED,
    )

    response = await client.get(f"{AUTO_APPLY}/eligibility", headers=bearer(user))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["overview"]["total"] == 2
    assert body["overview"]["ready_for_user_review"] >= 1
    assert body["overview"]["blocked"] >= 1
    assert body["overview"]["ineligible"] >= 1
    assert body["overview"]["automation_enabled"] is False
    summary = body["applications"][0]
    for field in (
        "application_id",
        "job_title",
        "overall_decision",
        "readiness_percentage",
        "blocking_count",
        "next_action",
        "report_version",
    ):
        assert field in summary


async def test_report_version_is_deterministic_between_reads(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory, email="version@example.com")
    job = await create_job(session_factory, user)
    await create_resume(session_factory, user)
    await complete_profile(client, user)
    preview = await prepare_application(client, user, job)
    await answer_required(client, user, preview["application_id"], preview)

    first = await evaluate(client, user, preview["application_id"])
    second = await evaluate(client, user, preview["application_id"])
    assert first["report_version"] == second["report_version"]
    assert first["overall_decision"] == second["overall_decision"]
