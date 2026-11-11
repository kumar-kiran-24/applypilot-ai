"""Phase 2F.1 tests: deterministic job understanding, LLM grounding and caching."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from app.agents.job_analysis import JobDescriptionAnalysisOutput
from app.core.security import create_access_token
from app.models.job import Job
from app.models.job_intelligence import JobIntelligence
from app.models.user import User
from app.schemas.job_intelligence import ExperienceLevel
from app.services.job_intelligence_extraction import (
    extract_job_understanding,
    merge_llm_understanding,
)
from app.services.job_understanding_service import JobUnderstandingService
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

SessionFactory = async_sessionmaker[AsyncSession]
JOBS = "/api/v1/jobs"

FULL_JD = """Senior Backend Engineer - Acme

We are looking for a senior backend engineer for our payments team.

Requirements:
- 5+ years of experience building backend services
- Python, FastAPI, PostgreSQL and Docker
- Bachelor's degree in Computer Science
Preferred:
- Kubernetes
Responsibilities:
- Build and operate payment APIs
- Partner with product teams
Employment type: Full-time
Location: Berlin, Germany
Work arrangement: Hybrid, 2 days onsite per week
Salary: EUR 70,000 - 90,000 per year
Certifications:
- AWS Certified Solutions Architect
Apply at https://jobs.lever.co/acme/123
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def create_user(
    session_factory: SessionFactory,
    *,
    email: str,
    google_sub: str,
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


async def create_job(
    session_factory: SessionFactory,
    user: User,
    *,
    title: str = "Senior Backend Engineer",
    description: str | None = FULL_JD,
) -> Job:
    async with session_factory() as session:
        job = Job(
            user_id=user.id,
            dedupe_key=f"glassdoor-id:{uuid.uuid4().int}",
            source="glassdoor",
            job_title=title,
            company="Acme",
            location="Berlin, Germany",
            job_url="https://www.glassdoor.com/job-listing/backend-JV_1.htm",
            job_description=description,
            email_subject="Jobs for you",
            message_id="msg-1",
            received_at=datetime(2026, 11, 2, tzinfo=timezone.utc),
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job


def llm_output(**overrides: Any) -> JobDescriptionAnalysisOutput:
    data: dict[str, Any] = {
        "role_title": "Senior Backend Engineer",
        "company": None,
        "seniority": "senior",
        "location": None,
        "work_mode": None,
        "employment_type": None,
        "min_experience_years": None,
        "max_experience_years": None,
        "salary_range_text": None,
        "required_skills": [],
        "preferred_skills": [],
        "responsibilities": [],
        "qualifications": [],
        "required_certifications": [],
        "application_requirements": [],
        "ats_keywords": [],
        "red_flags": [],
        "summary": "A backend engineering role building and operating payment services.",
    }
    data.update(overrides)
    return JobDescriptionAnalysisOutput.model_validate(data)


class FakeAgent:
    """Injected agent double; the transport is never used in these tests."""

    def __init__(
        self,
        output: JobDescriptionAnalysisOutput | None = None,
        error: Exception | None = None,
    ) -> None:
        self.output = output
        self.error = error
        self.calls: list[Any] = []

    async def execute(self, payload: Any) -> JobDescriptionAnalysisOutput:
        self.calls.append(payload)
        if self.error is not None:
            raise self.error
        assert self.output is not None
        return self.output


def install_service(monkeypatch: pytest.MonkeyPatch, agent: FakeAgent) -> None:
    """Route the endpoint through a service with the injected agent."""

    def factory(db: AsyncSession) -> JobUnderstandingService:
        return JobUnderstandingService(db, agent=agent, agent_model="test-model")

    monkeypatch.setattr("app.api.v1.endpoints.jobs.JobUnderstandingService", factory)


# ---------------------------------------------------------------------------
# Deterministic extraction
# ---------------------------------------------------------------------------
def test_deterministic_extraction_captures_only_stated_evidence() -> None:
    understanding = extract_job_understanding(
        job_title="Senior Backend Engineer",
        company="Acme",
        location="Berlin, Germany",
        description=FULL_JD,
        job_url="https://www.glassdoor.com/job-listing/backend-JV_1.htm",
    )
    fields = understanding.fields

    assert fields.job_title == "Senior Backend Engineer"
    assert fields.company == "Acme"
    assert fields.description == FULL_JD
    assert fields.required_skills == ["Docker", "FastAPI", "PostgreSQL", "Python"]
    assert fields.preferred_skills == ["Kubernetes"]
    assert fields.experience_years == 5.0
    assert fields.experience_years_max is None
    assert fields.education_level == "bachelor"
    assert fields.education_field == "Computer Science"
    assert fields.education_requirement == "Bachelor's degree in Computer Science"
    assert fields.required_qualifications == [
        "5+ years of experience building backend services",
        "Bachelor's degree in Computer Science",
    ]
    assert fields.responsibilities == [
        "Build and operate payment APIs",
        "Partner with product teams",
    ]
    assert fields.employment_type is not None
    assert fields.employment_type.value == "full_time"
    assert fields.work_location == "Berlin, Germany"
    assert fields.work_mode is not None
    assert fields.work_mode.value == "hybrid"
    assert fields.salary_range is not None
    assert "EUR 70,000" in fields.salary_range
    assert fields.experience_level is ExperienceLevel.SENIOR
    assert fields.required_certifications == ["AWS Certified Solutions Architect"]
    assert fields.application_url == "https://jobs.lever.co/acme/123"
    assert fields.application_url_is_listing is False

    assert understanding.confidence == "high"
    assert understanding.provenance["required_skills"] == "deterministic"
    assert understanding.field_confidence["required_skills"] == "high"
    assert understanding.provenance["experience_level"] == "deterministic"
    assert understanding.provenance["company"] == "job_record"
    assert understanding.contradictions == []


def test_missing_description_is_not_invented() -> None:
    understanding = extract_job_understanding(
        job_title="Backend Engineer",
        company=None,
        location=None,
        description=None,
        job_url="https://www.glassdoor.com/job-listing/1.htm",
    )
    fields = understanding.fields

    assert fields.required_skills == []
    assert fields.preferred_skills == []
    assert fields.experience_years is None
    assert fields.education_level is None
    assert fields.work_mode is None
    assert fields.experience_level is ExperienceLevel.UNKNOWN
    assert fields.application_url == "https://www.glassdoor.com/job-listing/1.htm"
    assert fields.application_url_is_listing is True
    assert understanding.confidence == "low"
    assert any("no description" in warning.lower() for warning in understanding.warnings)


def test_contradictory_work_mode_and_seniority_are_reported() -> None:
    description = """Junior Backend Engineer

We need someone for our platform team.

Requirements:
- Python and Docker
- 8+ years of professional experience

Fully remote role. On-site collaboration is required in the office.
"""
    understanding = extract_job_understanding(
        job_title="Junior Backend Engineer",
        company="Acme",
        location=None,
        description=description,
        job_url="https://www.glassdoor.com/job-listing/1.htm",
    )

    assert understanding.fields.work_mode is None
    assert understanding.fields.experience_years == 8.0
    assert any("remote and on-site" in item for item in understanding.contradictions)
    assert any("entry-level" in item for item in understanding.contradictions)


def test_salary_without_a_number_is_reported_not_guessed() -> None:
    description = """Backend Engineer

Requirements:
- Python and PostgreSQL

We offer a competitive salary, equity and remote-friendly work.
"""
    understanding = extract_job_understanding(
        job_title="Backend Engineer",
        company="Acme",
        location=None,
        description=description,
        job_url=None,
    )

    assert understanding.fields.salary_range is None
    assert any("compensation" in warning for warning in understanding.warnings)


def test_required_and_preferred_overlap_is_reported() -> None:
    description = """Backend Engineer

Requirements:
- Python and Docker

Preferred:
- Python
- Kubernetes
"""
    understanding = extract_job_understanding(
        job_title="Backend Engineer",
        company="Acme",
        location=None,
        description=description,
        job_url=None,
    )

    assert understanding.fields.required_skills == ["Docker", "Python"]
    assert understanding.fields.preferred_skills == ["Kubernetes"]
    assert any("both required and preferred" in item for item in understanding.contradictions)


def test_skill_only_requirement_lines_are_not_qualifications() -> None:
    description = """Backend Engineer

Requirements:
- Python and FastAPI
- 3+ years of experience
"""
    understanding = extract_job_understanding(
        job_title="Backend Engineer",
        company=None,
        location=None,
        description=description,
        job_url=None,
    )

    assert understanding.fields.required_qualifications == ["3+ years of experience"]


# ---------------------------------------------------------------------------
# LLM merge and grounding
# ---------------------------------------------------------------------------
def test_merge_never_overwrites_deterministic_values() -> None:
    description = """Backend Engineer

Requirements:
- Python
- Docker
- 5+ years of experience
"""
    base = extract_job_understanding(
        job_title="Backend Engineer",
        company="Acme",
        location=None,
        description=description,
        job_url=None,
    )
    llm = llm_output(
        required_skills=["Kubernetes"],
        min_experience_years=5,
    )

    merged = merge_llm_understanding(base, llm, description=description)

    assert merged.fields.required_skills == ["Docker", "Python"]
    assert merged.provenance["required_skills"] == "deterministic"
    assert merged.fields.experience_years == 5.0
    assert merged.provenance["experience_years"] == "deterministic"
    assert "required_skills" not in merged.supplemented_fields


def test_merge_fills_empty_fields_only_with_grounded_claims() -> None:
    description = (
        "Backend engineer for a growing team. We build services with Python, "
        "Kubernetes and PostgreSQL. This is a full-time position."
    )
    base = extract_job_understanding(
        job_title="Backend Engineer",
        company=None,
        location=None,
        description=description,
        job_url=None,
    )
    llm = llm_output(
        company="Acme",
        preferred_skills=["Kubernetes", "Terraform"],
        work_mode="remote",
        min_experience_years=4,
    )

    merged = merge_llm_understanding(base, llm, description=description)

    assert merged.fields.preferred_skills == ["Kubernetes"]
    assert merged.provenance["preferred_skills"] == "llm"
    assert merged.field_confidence["preferred_skills"] == "medium"
    assert "preferred_skills" in merged.supplemented_fields
    assert any("Terraform" in claim for claim in merged.dropped_llm_claims)
    # The posting never states a work mode, company or year count.
    assert merged.fields.work_mode is None
    assert merged.fields.company is None
    assert merged.fields.experience_years is None
    # Deterministic employment type is preserved.
    assert merged.fields.employment_type is not None
    assert merged.provenance["employment_type"] == "deterministic"


def test_merge_reports_disagreements_without_overwriting() -> None:
    description = """Backend Engineer

Requirements:
- 5+ years of experience with Python
- 3 years of experience with a Master's degree
- Python
"""
    base = extract_job_understanding(
        job_title="Backend Engineer",
        company="Acme",
        location=None,
        description=description,
        job_url=None,
    )
    llm = llm_output(min_experience_years=5)

    merged = merge_llm_understanding(base, llm, description=description)

    assert merged.fields.experience_years == 3.0
    assert merged.provenance["experience_years"] == "deterministic"
    assert any("minimum experience" in item for item in merged.contradictions)


# ---------------------------------------------------------------------------
# API, ownership and caching
# ---------------------------------------------------------------------------
async def test_analysis_requires_authentication(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")
    job = await create_job(session_factory, user)

    analyze = await client.post(f"{JOBS}/{job.id}/analyze")
    read = await client.get(f"{JOBS}/{job.id}/intelligence")

    assert analyze.status_code == 401
    assert read.status_code == 401


async def test_job_ownership_isolation(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    owner = await create_user(
        session_factory, email="owner@example.com", google_sub="g-owner"
    )
    other = await create_user(
        session_factory, email="other@example.com", google_sub="g-other"
    )
    job = await create_job(session_factory, owner)

    analyze = await client.post(
        f"{JOBS}/{job.id}/analyze",
        json={"use_llm": False},
        headers=bearer(other),
    )
    read = await client.get(f"{JOBS}/{job.id}/intelligence", headers=bearer(other))

    assert analyze.status_code == 404
    assert analyze.json()["code"] == "job_not_found"
    assert read.status_code == 404


async def test_invalid_job_id_is_rejected(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")

    response = await client.post(
        f"{JOBS}/not-a-uuid/analyze", json={}, headers=bearer(user)
    )

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


async def test_deterministic_analysis_is_persisted_and_cached(
    client: AsyncClient,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")
    job = await create_job(session_factory, user)
    agent = FakeAgent(error=AssertionError("LLM must not be called"))
    install_service(monkeypatch, agent)

    first = await client.post(
        f"{JOBS}/{job.id}/analyze",
        json={"use_llm": False},
        headers=bearer(user),
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["fields"]["required_skills"] == [
        "Docker",
        "FastAPI",
        "PostgreSQL",
        "Python",
    ]
    assert body["extraction"]["llm_used"] is False
    assert body["extraction"]["llm_status"] == "disabled"
    assert body["extraction"]["cached"] is False
    assert agent.calls == []
    assert "hiring probability" in body["disclaimer"]

    second = await client.post(
        f"{JOBS}/{job.id}/analyze",
        json={"use_llm": False},
        headers=bearer(user),
    )
    assert second.status_code == 200
    assert second.json()["extraction"]["cached"] is True
    assert second.json()["extraction"]["computed_at"] == body["extraction"]["computed_at"]
    assert second.json()["fields"] == body["fields"]

    forced = await client.post(
        f"{JOBS}/{job.id}/analyze",
        json={"use_llm": False, "force": True},
        headers=bearer(user),
    )
    assert forced.status_code == 200
    assert forced.json()["extraction"]["cached"] is False


async def test_read_before_analysis_does_not_persist_or_call_llm(
    client: AsyncClient,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")
    job = await create_job(session_factory, user)
    agent = FakeAgent(error=AssertionError("LLM must not be called"))
    install_service(monkeypatch, agent)

    response = await client.get(f"{JOBS}/{job.id}/intelligence", headers=bearer(user))

    assert response.status_code == 200
    body = response.json()
    assert body["fields"]["required_skills"]
    assert body["extraction"]["cached"] is False
    assert body["extraction"]["llm_status"] == "not_analyzed"
    assert "not saved" in body["message"]
    assert agent.calls == []

    async with session_factory() as session:
        count = await session.scalar(select(func.count(JobIntelligence.id)))
    assert count == 0

    analyzed = await client.post(
        f"{JOBS}/{job.id}/analyze",
        json={"use_llm": False},
        headers=bearer(user),
    )
    assert analyzed.status_code == 200

    cached = await client.get(f"{JOBS}/{job.id}/intelligence", headers=bearer(user))
    assert cached.json()["extraction"]["cached"] is True
    assert cached.json()["extraction"]["llm_status"] == "disabled"


async def test_changed_description_invalidates_the_cache(
    client: AsyncClient,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")
    job = await create_job(
        session_factory,
        user,
        description="Backend Engineer\n\nRequirements:\n- Python\n",
    )
    install_service(monkeypatch, FakeAgent(error=AssertionError("no LLM")))

    first = await client.post(
        f"{JOBS}/{job.id}/analyze",
        json={"use_llm": False},
        headers=bearer(user),
    )
    assert first.json()["fields"]["required_skills"] == ["Python"]

    async with session_factory() as session:
        row = await session.get(Job, job.id)
        assert row is not None
        row.job_description = (
            "Backend Engineer\n\nRequirements:\n- Python\n- Kubernetes\n"
        )
        await session.commit()

    second = await client.post(
        f"{JOBS}/{job.id}/analyze",
        json={"use_llm": False},
        headers=bearer(user),
    )
    assert second.status_code == 200
    assert second.json()["extraction"]["cached"] is False
    assert second.json()["fields"]["required_skills"] == ["Kubernetes", "Python"]


async def test_llm_enrichment_is_grounded_and_cached(
    client: AsyncClient,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")
    description = (
        "Backend engineer for a growing team. We build services with Python, "
        "Kubernetes and PostgreSQL in Berlin. This is a full-time position."
    )
    job = await create_job(session_factory, user, description=description)
    agent = FakeAgent(
        llm_output(
            required_skills=["Python", "FastAPI"],
            preferred_skills=["Kubernetes"],
            location="Berlin",
        )
    )
    install_service(monkeypatch, agent)

    response = await client.post(
        f"{JOBS}/{job.id}/analyze", json={}, headers=bearer(user)
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["extraction"]["llm_used"] is True
    assert body["extraction"]["llm_status"] == "used"
    assert body["extraction"]["llm_model"] == "test-model"
    assert body["fields"]["required_skills"] == ["Python"]
    assert body["extraction"]["provenance"]["required_skills"] == "llm"
    assert any(
        "FastAPI" in claim for claim in body["extraction"]["dropped_llm_claims"]
    )
    assert len(agent.calls) == 1
    assert agent.calls[0].job_description == description
    assert agent.calls[0].job_title == "Senior Backend Engineer"

    cached = await client.post(
        f"{JOBS}/{job.id}/analyze", json={}, headers=bearer(user)
    )
    assert cached.json()["extraction"]["cached"] is True
    assert len(agent.calls) == 1


async def test_llm_failure_falls_back_and_is_retried(
    client: AsyncClient,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")
    job = await create_job(session_factory, user)
    failing = FakeAgent(error=RuntimeError("upstream exploded"))
    install_service(monkeypatch, failing)

    response = await client.post(
        f"{JOBS}/{job.id}/analyze", json={}, headers=bearer(user)
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["extraction"]["llm_used"] is False
    assert body["extraction"]["llm_status"] == "failed"
    assert body["fields"]["required_skills"] == [
        "Docker",
        "FastAPI",
        "PostgreSQL",
        "Python",
    ]
    assert any(
        "LLM enrichment failed" in warning
        for warning in body["extraction"]["warnings"]
    )
    assert "upstream exploded" not in response.text

    working = FakeAgent(llm_output(required_skills=["Python"]))
    install_service(monkeypatch, working)
    retried = await client.post(
        f"{JOBS}/{job.id}/analyze", json={}, headers=bearer(user)
    )

    assert retried.status_code == 200
    assert retried.json()["extraction"]["llm_status"] == "used"
    assert retried.json()["extraction"]["cached"] is False
    assert len(working.calls) == 1
