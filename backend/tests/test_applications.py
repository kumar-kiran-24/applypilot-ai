"""Tests for platform detection, the questionnaire and the application workflow."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from app.core.config import settings
from app.core.security import create_access_token
from app.models.candidate import Candidate
from app.models.job import Job
from app.models.resume import Resume, ResumeParsingStatus
from app.models.user import User
from app.services.application_platforms import (
    ApplicationPlatform,
    ApplicationUrlError,
    detect_platform,
    normalize_application_url,
)
from app.services.application_questionnaire import build_questionnaire
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

SessionFactory = async_sessionmaker[AsyncSession]

APPLICATIONS = "/api/v1/applications"
PROFILE = "/api/v1/profile"


@pytest.fixture(autouse=True)
def automation_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every workflow test exercises the guided manual path by default."""
    monkeypatch.setattr(settings, "APPLICATION_AUTOMATION_ENABLED", False)


async def create_user(
    session_factory: SessionFactory,
    *,
    email: str = "ada@example.com",
    google_sub: str = "google-sub-app",
    full_name: str = "Ada Lovelace",
) -> User:
    async with session_factory() as session:
        user = User(
            google_sub=google_sub,
            email=email,
            email_verified=True,
            full_name=full_name,
            is_active=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


def bearer(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


async def create_job(
    session_factory: SessionFactory,
    user: User,
    *,
    job_url: str = "https://www.glassdoor.com/job-listing/backend-JV_1.htm",
) -> Job:
    async with session_factory() as session:
        job = Job(
            user_id=user.id,
            dedupe_key=f"glassdoor-id:{uuid.uuid4().int}",
            source="glassdoor",
            job_title="Senior Backend Engineer",
            company="Acme",
            location="Remote",
            job_url=job_url,
            job_description="Requirements:\n- Python, FastAPI, PostgreSQL and Docker\n",
            email_subject="Jobs for you",
            message_id="msg-1",
            received_at=datetime(2026, 11, 2, tzinfo=timezone.utc),
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job


async def create_resume(
    session_factory: SessionFactory,
    user: User,
    *,
    filename: str = "strong.pdf",
    skills: list[str] | None = None,
) -> Resume:
    async with session_factory() as session:
        resume = Resume(
            user_id=user.id,
            original_filename=filename,
            storage_key=f"users/{user.id}/{uuid.uuid4()}.pdf",
            storage_backend="local",
            content_type="application/pdf",
            file_size=1024,
            checksum=uuid.uuid4().hex + uuid.uuid4().hex,
            is_default=True,
            parsing_status=ResumeParsingStatus.COMPLETED,
            parsed_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
            parsed_data={
                "full_name": "Ada Lovelace",
                "email": "ada@example.com",
                "professional_summary": "Backend engineer.",
            },
            skills=skills or ["Python", "FastAPI", "PostgreSQL", "Docker"],
        )
        session.add(resume)
        await session.commit()
        await session.refresh(resume)
        return resume


async def prepare_profile(client: AsyncClient, user: User) -> dict[str, Any]:
    response = await client.put(
        PROFILE,
        json={
            "phone": "+1 555 0100",
            "country": "United States",
            "city": "Austin",
            "work_authorization": "Authorized to work in the US",
            "requires_sponsorship": False,
            "notice_period_days": 30,
        },
        headers=bearer(user),
    )
    assert response.status_code == 200, response.text
    return response.json()


def flatten_questions(preview: dict[str, Any]) -> list[dict[str, Any]]:
    return [question for section in preview["sections"] for question in section["questions"]]


def question_by_key(preview: dict[str, Any], key: str) -> dict[str, Any]:
    return next(item for item in flatten_questions(preview) if item["question_key"] == key)


# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("url", "platform"),
    [
        ("https://boards.greenhouse.io/acme/jobs/123", ApplicationPlatform.GREENHOUSE),
        ("https://jobs.lever.co/acme/123", ApplicationPlatform.LEVER),
        ("https://jobs.ashbyhq.com/acme/123", ApplicationPlatform.ASHBY),
        (
            "https://jobs.smartrecruiters.com/Acme/123",
            ApplicationPlatform.SMARTRECRUITERS,
        ),
        (
            "https://acme.wd1.myworkdayjobs.com/en-US/External/job/Austin/Engineer_R1",
            ApplicationPlatform.WORKDAY,
        ),
        ("https://careers-acme.icims.com/jobs/123/engineer/job", ApplicationPlatform.ICIMS),
        ("https://careers.acme.com/apply/123", ApplicationPlatform.COMPANY_HOSTED),
    ],
)
def test_detects_each_supported_platform(url: str, platform: ApplicationPlatform) -> None:
    detection = detect_platform(url)

    assert detection.platform is platform
    assert detection.is_job_board_link is False
    assert detection.application_url == url


def test_embedded_greenhouse_job_id_is_detected() -> None:
    detection = detect_platform("https://careers.acme.com/job?gh_jid=12345")

    assert detection.platform is ApplicationPlatform.GREENHOUSE
    assert detection.confidence.value == "medium"


def test_glassdoor_link_is_not_treated_as_an_application_form() -> None:
    detection = detect_platform(
        "https://www.glassdoor.com/job-listing/backend-JV_1.htm?jl=100"
    )

    assert detection.is_job_board_link is True
    assert detection.is_automation_capable is False
    assert "job board" in detection.reason.lower()


@pytest.mark.parametrize(
    "url",
    [
        "ftp://boards.greenhouse.io/acme/jobs/1",
        "https://user:pass@boards.greenhouse.io/acme/jobs/1",
        "http://127.0.0.1:8000/apply",
        "http://localhost/apply",
        "https://10.0.0.5/apply",
        "https://example.com:8443/apply",
    ],
)
def test_unsafe_application_urls_are_rejected(url: str) -> None:
    with pytest.raises(ApplicationUrlError):
        normalize_application_url(url)


# ---------------------------------------------------------------------------
# Questionnaire provenance
# ---------------------------------------------------------------------------
def test_questionnaire_distinguishes_answer_sources() -> None:
    user = User(
        google_sub="sub",
        email="ada@example.com",
        full_name="Ada Lovelace",
        email_verified=True,
        is_active=True,
    )
    candidate = Candidate(
        full_name="Ada Lovelace",
        email="ada@example.com",
        phone="+1 555 0100",
        phone_country_code="+1",
        work_authorization="US citizen",
        requires_sponsorship=True,
        notice_period_days=30,
    )
    resume = Resume(
        original_filename="ada.pdf",
        skills=["Python", "FastAPI"],
        parsed_data={"professional_summary": "Backend engineer."},
    )

    questions = build_questionnaire(
        candidate=candidate,
        user=user,
        resume=resume,
        approved_answers={"compensation.expected_salary": "120000 USD"},
    )
    by_key = {question.question_key: question for question in questions}

    full_name = by_key["personal.full_name"]
    assert full_name.answer == "Ada Lovelace"
    assert full_name.answer_source.value == "profile"
    assert full_name.status.value == "confirmed"
    assert full_name.requires_confirmation is False

    sponsorship = by_key["authorization.sponsorship"]
    assert sponsorship.answer == "Yes"
    assert sponsorship.is_sensitive is True
    assert sponsorship.requires_confirmation is True
    assert sponsorship.status.value == "suggested"

    skills = by_key["skills.primary"]
    assert skills.answer_source.value == "resume"
    assert skills.status.value == "suggested"
    assert skills.requires_confirmation is True

    salary = by_key["compensation.expected_salary"]
    assert salary.answer == "120000 USD"
    assert salary.answer_source.value == "previously_approved"
    assert salary.status.value == "confirmed"

    declaration = by_key["declarations.accuracy"]
    assert declaration.answer is None
    assert declaration.is_legal is True
    assert declaration.status.value == "missing"
    assert declaration.requires_confirmation is True


# ---------------------------------------------------------------------------
# End-to-end workflow
# ---------------------------------------------------------------------------
async def test_application_requires_authentication(client: AsyncClient) -> None:
    assert (await client.get(APPLICATIONS)).status_code == 401
    assert (await client.get(f"{APPLICATIONS}/platforms")).status_code == 401


async def test_application_workflow_with_manual_submission(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory)
    job = await create_job(session_factory, user)
    resume = await create_resume(session_factory, user)
    await prepare_profile(client, user)
    headers = bearer(user)

    analyzed = await client.post(
        f"{APPLICATIONS}/analyze",
        json={"job_id": str(job.id)},
        headers=headers,
    )
    assert analyzed.status_code == 200, analyzed.text
    preview = analyzed.json()
    application_id = preview["application_id"]

    # The Glassdoor link is never treated as the application form.
    assert preview["platform"] == "company_hosted"
    assert preview["automation_mode"] == "manual"
    assert preview["resume"]["id"] == str(resume.id)
    assert preview["resume"]["filename"] == "strong.pdf"
    assert preview["status"] == "needs_information"
    assert preview["missing_required"]
    assert preview["sections"]
    documents = question_by_key(preview, "documents.resume")
    assert documents["answer"] == "strong.pdf"
    assert documents["status"] == "confirmed"

    # Supplying the employer's own URL re-detects the platform.
    inspected = await client.post(
        f"{APPLICATIONS}/{application_id}/inspect",
        json={"application_url": "https://boards.greenhouse.io/acme/jobs/123"},
        headers=headers,
    )
    assert inspected.status_code == 200, inspected.text
    preview = inspected.json()
    assert preview["platform"] == "greenhouse"
    assert preview["automation_mode"] == "assisted"

    # Answer and confirm every required question that is still open.
    answered_keys: set[str] = set()
    for section in preview["sections"]:
        for question in section["questions"]:
            if not question["required"] or question["status"] == "confirmed":
                continue
            value = question["answer"] or (
                "No" if question["question_key"] == "authorization.sponsorship" else "Yes"
            )
            response = await client.patch(
                f"{APPLICATIONS}/{application_id}/questions/{question['id']}",
                json={"answer": value, "confirmed": True},
                headers=headers,
            )
            assert response.status_code == 200, response.text
            answered_keys.add(question["question_key"])

    assert {"declarations.accuracy", "authorization.sponsorship"} <= answered_keys
    ready = (await client.get(f"{APPLICATIONS}/{application_id}", headers=headers)).json()
    assert ready["status"] == "ready_for_review"
    assert ready["missing_required"] == []
    assert ready["ready_to_approve"] is True

    approved = await client.post(
        f"{APPLICATIONS}/{application_id}/approve",
        json={"approved": True},
        headers=headers,
    )
    assert approved.status_code == 200, approved.text
    approved_preview = approved.json()
    assert approved_preview["status"] == "approved"
    assert approved_preview["can_submit"] is True
    assert approved_preview["approved_at"]

    # Editing after approval invalidates the approval.
    cover_letter = question_by_key(approved_preview, "company.cover_letter")
    edited = await client.patch(
        f"{APPLICATIONS}/{application_id}/questions/{cover_letter['id']}",
        json={"answer": "I build reliable APIs.", "confirmed": True},
        headers=headers,
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["status"] == "ready_for_review"
    assert edited.json()["approved_at"] is None

    blocked = await client.post(f"{APPLICATIONS}/{application_id}/submit", headers=headers)
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "approval_required"

    # Re-approve, then submit: the manual platform submits nothing itself.
    await client.post(
        f"{APPLICATIONS}/{application_id}/approve",
        json={"approved": True},
        headers=headers,
    )
    submitted = await client.post(f"{APPLICATIONS}/{application_id}/submit", headers=headers)
    assert submitted.status_code == 200, submitted.text
    outcome = submitted.json()
    assert outcome["submitted"] is False
    assert outcome["manual_required"] is True
    assert outcome["steps"]
    assert outcome["application"]["status"] == "manual_required"

    confirmed = await client.post(
        f"{APPLICATIONS}/{application_id}/mark-submitted",
        json={"reference": "APP-123"},
        headers=headers,
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "submitted"
    assert confirmed.json()["stage"] == "applied"

    tracked = await client.patch(
        f"{APPLICATIONS}/{application_id}/stage",
        json={"stage": "interview"},
        headers=headers,
    )
    assert tracked.status_code == 200, tracked.text
    assert tracked.json()["stage"] == "interview"

    listed = await client.get(APPLICATIONS, headers=headers)
    assert listed.status_code == 200
    body = listed.json()
    assert body["total"] == 1
    assert body["applications"][0]["status"] == "submitted"


async def test_reanalysis_preserves_confirmed_answers(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory)
    job = await create_job(session_factory, user)
    await create_resume(session_factory, user)
    await prepare_profile(client, user)
    headers = bearer(user)

    first = (
        await client.post(
            f"{APPLICATIONS}/analyze", json={"job_id": str(job.id)}, headers=headers
        )
    ).json()
    application_id = first["application_id"]
    cover = question_by_key(first, "company.cover_letter")
    await client.patch(
        f"{APPLICATIONS}/{application_id}/questions/{cover['id']}",
        json={"answer": "My own words.", "confirmed": True},
        headers=headers,
    )

    second = await client.post(
        f"{APPLICATIONS}/analyze", json={"job_id": str(job.id)}, headers=headers
    )
    assert second.status_code == 200, second.text
    assert second.json()["application_id"] == application_id
    assert question_by_key(second.json(), "company.cover_letter")["answer"] == "My own words."


async def test_resume_override_and_ownership(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    owner = await create_user(session_factory)
    other = await create_user(
        session_factory, email="other@example.com", google_sub="google-sub-other"
    )
    job = await create_job(session_factory, owner)
    first = await create_resume(session_factory, owner, filename="first.pdf")
    second = await create_resume(session_factory, owner, filename="second.pdf")
    await prepare_profile(client, owner)

    analyzed = await client.post(
        f"{APPLICATIONS}/analyze", json={"job_id": str(job.id)}, headers=bearer(owner)
    )
    application_id = analyzed.json()["application_id"]
    assert analyzed.json()["resume"]["id"] in {str(first.id), str(second.id)}

    changed = await client.patch(
        f"{APPLICATIONS}/{application_id}/resume",
        json={"resume_id": str(second.id)},
        headers=bearer(owner),
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["resume"]["id"] == str(second.id)
    assert (
        question_by_key(changed.json(), "documents.resume")["answer"] == "second.pdf"
    )

    # Another user cannot see or change the application, its job or its resume.
    assert (
        await client.get(f"{APPLICATIONS}/{application_id}", headers=bearer(other))
    ).status_code == 404
    assert (
        await client.post(
            f"{APPLICATIONS}/analyze", json={"job_id": str(job.id)}, headers=bearer(other)
        )
    ).status_code == 404
    assert (
        await client.patch(
            f"{APPLICATIONS}/{application_id}/resume",
            json={"resume_id": str(first.id)},
            headers=bearer(other),
        )
    ).status_code == 404


async def test_profile_expansion_and_certifications(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory)
    headers = bearer(user)

    created = await client.get(PROFILE, headers=headers)
    assert created.status_code == 200, created.text
    assert created.json()["full_name"] == "Ada Lovelace"
    assert created.json()["email"] == "ada@example.com"

    updated = await client.put(
        PROFILE,
        json={
            "preferred_name": "Ada",
            "phone_country_code": "+1",
            "address_line1": "1 Analytical Engine Way",
            "city": "Austin",
            "region": "TX",
            "postal_code": "78701",
            "country": "United States",
            "work_authorization": "US citizen",
            "requires_sponsorship": False,
            "notice_period_days": 14,
            "available_from": "2026-12-01",
        },
        headers=headers,
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["preferred_name"] == "Ada"
    assert body["city"] == "Austin"
    assert body["requires_sponsorship"] is False
    assert body["notice_period_days"] == 14

    certification = await client.post(
        f"{PROFILE}/certifications",
        json={"name": "AWS Solutions Architect", "issuer": "Amazon"},
        headers=headers,
    )
    assert certification.status_code == 201, certification.text
    certification_id = certification.json()["id"]
    assert (await client.get(PROFILE, headers=headers)).json()["certifications"][0][
        "name"
    ] == "AWS Solutions Architect"

    deleted = await client.delete(
        f"{PROFILE}/certifications/{certification_id}", headers=headers
    )
    assert deleted.status_code == 200
    assert (await client.get(PROFILE, headers=headers)).json()["certifications"] == []
