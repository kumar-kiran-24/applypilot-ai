"""Phase 2F.2.5 tests: recommendation integration in application preparation.

The application workflow must reuse the Phase 2F.2.4 recommendation, must never
replace a resume the user explicitly chose, and must never approve or submit an
application on its own. All data is synthetic.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from app.core.config import settings
from app.core.security import create_access_token
from app.models.job import Job
from app.models.resume import Resume, ResumeParsingStatus
from app.models.user import User
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

SessionFactory = async_sessionmaker[AsyncSession]

APPLICATIONS = "/api/v1/applications"
PROFILE = "/api/v1/profile"

WEAK_PARSED = {
    "full_name": "Ada Lovelace",
    "email": "ada@example.com",
    "professional_summary": "Visual designer focused on brand systems.",
}

STRONG_PARSED = {
    "full_name": "Ada Lovelace",
    "email": "ada@example.com",
    "professional_summary": "Backend engineer with Python and FastAPI experience.",
    "experience": [
        {
            "title": "Senior Backend Engineer",
            "company": "Acme",
            "start_date": "Jan 2019",
            "end_date": None,
            "is_current": True,
            "description": "Built Python FastAPI services with PostgreSQL and Docker.",
        }
    ],
}


@pytest.fixture(autouse=True)
def automation_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise the guided manual path; nothing may auto-approve or submit."""
    monkeypatch.setattr(settings, "APPLICATION_AUTOMATION_ENABLED", False)


async def create_user(
    session_factory: SessionFactory,
    *,
    email: str = "ada@example.com",
    google_sub: str = "google-sub-app-reco",
) -> User:
    async with session_factory() as session:
        user = User(
            google_sub=google_sub,
            email=email,
            email_verified=True,
            full_name="Ada Lovelace",
            is_active=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


def bearer(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


async def create_job(session_factory: SessionFactory, user: User) -> Job:
    async with session_factory() as session:
        job = Job(
            user_id=user.id,
            dedupe_key=f"glassdoor-id:{uuid.uuid4().int}",
            source="glassdoor",
            job_title="Senior Backend Engineer",
            company="Acme",
            location="Remote",
            job_url="https://www.glassdoor.com/job-listing/backend-JV_1.htm",
            job_description=(
                "Requirements:\n"
                "- 5+ years of experience building backend services\n"
                "- Python, FastAPI, PostgreSQL and Docker\n"
            ),
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
    filename: str,
    skills: list[str],
    parsed_data: dict[str, Any],
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
            is_default=False,
            parsing_status=ResumeParsingStatus.COMPLETED,
            parsed_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
            parsed_data=parsed_data,
            skills=skills,
        )
        session.add(resume)
        await session.commit()
        await session.refresh(resume)
        return resume


async def prepare_profile(client: AsyncClient, user: User) -> None:
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


def question_by_key(preview: dict[str, Any], key: str) -> dict[str, Any]:
    for section in preview["sections"]:
        for question in section["questions"]:
            if question["question_key"] == key:
                return question
    raise AssertionError(f"question {key} not found")


async def analyze(
    client: AsyncClient,
    user: User,
    job: Job,
    *,
    resume_id: Any | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"job_id": str(job.id)}
    if resume_id is not None:
        payload["resume_id"] = str(resume_id)
    response = await client.post(
        f"{APPLICATIONS}/analyze", json=payload, headers=bearer(user)
    )
    assert response.status_code == 200, response.text
    return response.json()


async def two_resumes(
    session_factory: SessionFactory, user: User
) -> tuple[Resume, Resume]:
    strong = await create_resume(
        session_factory,
        user,
        filename="strong.pdf",
        skills=["Python", "FastAPI", "PostgreSQL", "Docker"],
        parsed_data=STRONG_PARSED,
    )
    weak = await create_resume(
        session_factory,
        user,
        filename="weak.pdf",
        skills=["Python"],
        parsed_data=WEAK_PARSED,
    )
    return strong, weak


# ---------------------------------------------------------------------------
# Recommendation integration
# ---------------------------------------------------------------------------
async def test_application_uses_the_recommended_resume(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory)
    job = await create_job(session_factory, user)
    strong, _weak = await two_resumes(session_factory, user)
    await prepare_profile(client, user)

    preview = await analyze(client, user, job)

    assert preview["resume"]["id"] == str(strong.id)
    assert preview["resume"]["filename"] == "strong.pdf"
    assert preview["resume"]["score"] and preview["resume"]["score"] > 0
    assert preview["resume"]["reason"].startswith("Recommended by resume comparison:")
    assert question_by_key(preview, "documents.resume")["answer"] == "strong.pdf"
    # Preparation never approves or submits anything.
    assert preview["status"] == "awaiting_user_information"
    assert preview["approved_at"] is None
    assert preview["submitted_at"] is None
    assert preview["can_submit"] is False


async def test_analysis_required_reason_is_explicit(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory)
    job = await create_job(session_factory, user)
    await create_resume(
        session_factory,
        user,
        filename="only.pdf",
        skills=["Python", "FastAPI", "PostgreSQL", "Docker"],
        parsed_data=STRONG_PARSED,
    )
    await prepare_profile(client, user)

    preview = await analyze(client, user, job)

    assert preview["resume"]["id"] is not None
    assert "Resume analysis is not current" in preview["resume"]["reason"]
    # Existing workflow still proceeds; the state is reported, not hidden.
    assert preview["status"] == "awaiting_user_information"


# ---------------------------------------------------------------------------
# Explicit user selection is preserved
# ---------------------------------------------------------------------------
async def test_manual_resume_selection_survives_reanalysis(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory)
    job = await create_job(session_factory, user)
    strong, weak = await two_resumes(session_factory, user)
    await prepare_profile(client, user)
    headers = bearer(user)

    first = await analyze(client, user, job)
    assert first["resume"]["id"] == str(strong.id)

    selected = await client.patch(
        f"{APPLICATIONS}/{first['application_id']}/resume",
        json={"resume_id": str(weak.id)},
        headers=headers,
    )
    assert selected.status_code == 200, selected.text
    assert selected.json()["resume"]["id"] == str(weak.id)

    second = await analyze(client, user, job)

    assert second["resume"]["id"] == str(weak.id)
    assert second["resume"]["reason"] == (
        "Resume selected manually by you for this application."
    )
    assert question_by_key(second, "documents.resume")["answer"] == "weak.pdf"


async def test_explicit_resume_id_survives_reanalysis(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory)
    job = await create_job(session_factory, user)
    _strong, weak = await two_resumes(session_factory, user)
    await prepare_profile(client, user)

    first = await analyze(client, user, job, resume_id=weak.id)
    assert first["resume"]["id"] == str(weak.id)

    second = await analyze(client, user, job)

    assert second["resume"]["id"] == str(weak.id)
    assert second["resume"]["reason"].startswith("Resume selected manually by you")


async def test_application_works_without_any_resume(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory)
    job = await create_job(session_factory, user)
    await prepare_profile(client, user)

    preview = await analyze(client, user, job)

    assert preview["resume"]["id"] is None
    assert "Upload one" in preview["resume"]["reason"]
    assert preview["status"] == "awaiting_user_information"


async def test_selecting_a_resume_does_not_approve_the_application(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory)
    job = await create_job(session_factory, user)
    _strong, weak = await two_resumes(session_factory, user)
    await prepare_profile(client, user)

    first = await analyze(client, user, job)
    changed = await client.patch(
        f"{APPLICATIONS}/{first['application_id']}/resume",
        json={"resume_id": str(weak.id)},
        headers=bearer(user),
    )

    assert changed.status_code == 200, changed.text
    assert changed.json()["approved_at"] is None
    assert changed.json()["status"] != "awaiting_approval"
    assert changed.json()["can_submit"] is False
