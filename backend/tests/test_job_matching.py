"""API tests for stored jobs, resume matching, ranking and ownership."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from app.core.security import create_access_token
from app.models.job import Job
from app.models.resume import Resume, ResumeParsingStatus
from app.models.user import User
from app.services.glassdoor_jobs import GlassdoorJob
from app.services.job_discovery import GlassdoorDiscoveryResult
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

SessionFactory = async_sessionmaker[AsyncSession]

JOBS = "/api/v1/jobs"
MATCHING = "/api/v1/matching"

STRONG_JD = """Requirements:
- 5+ years of experience building backend services
- Python, FastAPI, PostgreSQL and Docker
- Bachelor's degree in Computer Science
Preferred:
- Kubernetes
Responsibilities:
- Build and operate APIs
"""

STRONG_PARSED = {
    "full_name": "Ada Lovelace",
    "professional_summary": "Backend engineer with Python and FastAPI experience.",
    "experience": [
        {
            "title": "Senior Backend Engineer",
            "company": "Acme",
            "start_date": "Jan 2018",
            "end_date": None,
            "is_current": True,
            "description": "Built Python FastAPI services with PostgreSQL and Docker.",
        }
    ],
    "education": [
        {
            "institution": "State University",
            "degree": "Bachelor of Science",
            "field_of_study": "Computer Science",
        }
    ],
    "projects": [
        {
            "name": "Payments API",
            "description": "FastAPI service with PostgreSQL",
            "technologies": ["FastAPI", "PostgreSQL", "Docker"],
        }
    ],
}


async def create_user(
    session_factory: SessionFactory,
    *,
    email: str,
    google_sub: str,
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


async def create_resume(
    session_factory: SessionFactory,
    user: User,
    *,
    filename: str,
    skills: list[str],
    parsed_data: dict[str, Any] | None = None,
    is_default: bool = False,
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
            is_default=is_default,
            parsing_status=ResumeParsingStatus.COMPLETED,
            parsed_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
            parsed_data=parsed_data or {},
            skills=skills,
        )
        session.add(resume)
        await session.commit()
        await session.refresh(resume)
        return resume


async def create_job(
    session_factory: SessionFactory,
    user: User,
    *,
    title: str = "Senior Backend Engineer",
    description: str | None = STRONG_JD,
    dedupe_key: str | None = None,
) -> Job:
    async with session_factory() as session:
        job = Job(
            user_id=user.id,
            dedupe_key=dedupe_key or f"glassdoor-id:{uuid.uuid4().int}",
            source="glassdoor",
            job_title=title,
            company="Acme",
            location="Remote",
            job_url="https://www.glassdoor.com/job-listing/backend-engineer-JV_1.htm",
            job_description=description,
            email_subject="5 new jobs for you",
            message_id="msg-1",
            received_at=datetime(2026, 11, 2, tzinfo=timezone.utc),
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job


async def post_match(
    client: AsyncClient,
    user: User,
    job_id: Any,
    *,
    force: bool = False,
) -> Any:
    return await client.post(
        f"{JOBS}/{job_id}/match",
        json={"force": force},
        headers=bearer(user),
    )


# ---------------------------------------------------------------------------
# Authentication and ownership
# ---------------------------------------------------------------------------
async def test_matching_requires_authentication(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")
    job = await create_job(session_factory, user)

    response = await client.post(f"{JOBS}/{job.id}/match")

    assert response.status_code == 401


async def test_job_ownership_isolation(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    owner = await create_user(session_factory, email="owner@example.com", google_sub="g-o")
    other = await create_user(session_factory, email="other@example.com", google_sub="g-x")
    job = await create_job(session_factory, owner)

    match_response = await post_match(client, other, job.id)
    detail_response = await client.get(f"{JOBS}/{job.id}", headers=bearer(other))
    list_response = await client.get(JOBS, headers=bearer(other))
    bulk_response = await client.post(
        f"{MATCHING}/bulk",
        json={"job_ids": [str(job.id)]},
        headers=bearer(other),
    )

    assert match_response.status_code == 404
    assert detail_response.status_code == 404
    assert bulk_response.status_code == 404
    assert list_response.status_code == 200
    assert list_response.json()["jobs"] == []
    assert list_response.json()["total"] == 0


async def test_resume_ownership_isolation(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    first = await create_user(session_factory, email="first@example.com", google_sub="g-1")
    second = await create_user(session_factory, email="second@example.com", google_sub="g-2")
    job = await create_job(session_factory, first)
    await create_resume(
        session_factory,
        second,
        filename="second-strong.pdf",
        skills=["Python", "FastAPI", "PostgreSQL", "Docker", "Kubernetes"],
        parsed_data=STRONG_PARSED,
    )

    response = await post_match(client, first, job.id)

    assert response.status_code == 200
    body = response.json()
    assert body["matches"] == []
    assert body["recommended_resume_id"] is None
    assert body["resumes_evaluated"] == 0


async def test_invalid_job_id_is_rejected(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")

    response = await client.post(
        f"{JOBS}/not-a-uuid/match", json={}, headers=bearer(user)
    )

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


# ---------------------------------------------------------------------------
# Ranking and explanation
# ---------------------------------------------------------------------------
async def test_match_ranks_resumes_and_recommends_best(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")
    job = await create_job(session_factory, user)
    strong = await create_resume(
        session_factory,
        user,
        filename="strong.pdf",
        skills=["Python", "FastAPI", "PostgreSQL", "Docker", "Kubernetes"],
        parsed_data=STRONG_PARSED,
        is_default=True,
    )
    weak = await create_resume(
        session_factory,
        user,
        filename="weak.pdf",
        skills=["Python"],
        parsed_data={"professional_summary": "Junior developer."},
    )

    response = await post_match(client, user, job.id)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["recommended_resume_id"] == str(strong.id)
    assert body["resumes_evaluated"] == 2
    assert [row["resume_id"] for row in body["matches"]] == [
        str(strong.id),
        str(weak.id),
    ]
    assert [row["rank"] for row in body["matches"]] == [1, 2]
    assert body["matches"][0]["is_recommended"] is True
    assert body["matches"][1]["is_recommended"] is False
    assert body["matches"][0]["overall_score"] > body["matches"][1]["overall_score"]
    assert body["matches"][0]["explanation"]
    assert body["matches"][1]["missing_required_skills"] == [
        "Docker",
        "FastAPI",
        "PostgreSQL",
    ]
    assert "not hiring probabilities" in body["disclaimer"]

    cached = await client.get(f"{JOBS}/{job.id}/matches", headers=bearer(user))
    assert cached.status_code == 200
    assert [row["resume_id"] for row in cached.json()["matches"]] == [
        str(strong.id),
        str(weak.id),
    ]

    jobs = await client.get(JOBS, headers=bearer(user))
    listed = jobs.json()["jobs"][0]
    assert listed["best_match"]["resume_id"] == str(strong.id)
    assert listed["matches_count"] == 2
    assert listed["requirements"]["required_skills"] == [
        "Docker",
        "FastAPI",
        "PostgreSQL",
        "Python",
    ]


async def test_empty_resume_inventory_returns_success(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")
    job = await create_job(session_factory, user)

    response = await post_match(client, user, job.id)

    assert response.status_code == 200
    body = response.json()
    assert body["matches"] == []
    assert body["recommended_resume_id"] is None
    assert body["resumes_evaluated"] == 0
    assert "Upload a PDF resume" in body["message"]


async def test_missing_description_is_handled_without_invention(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")
    job = await create_job(
        session_factory,
        user,
        title="React Developer",
        description=None,
    )
    await create_resume(
        session_factory,
        user,
        filename="react.pdf",
        skills=["React", "TypeScript"],
        parsed_data={"professional_summary": "Frontend developer."},
    )

    response = await post_match(client, user, job.id)

    assert response.status_code == 200
    body = response.json()
    requirements = body["job"]["requirements"]
    assert requirements["has_description"] is False
    assert requirements["required_skills"] == []
    assert requirements["preferred_skills"] == []
    assert requirements["experience_years"] is None
    assert requirements["education_level"] is None
    assert body["matches"][0]["confidence"] == "low"
    assert any(
        "no job description" in item.lower()
        for item in body["matches"][0]["suggestions"]
    )


async def test_scores_stay_within_bounds(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")
    job = await create_job(session_factory, user)
    await create_resume(
        session_factory,
        user,
        filename="empty.pdf",
        skills=[],
        parsed_data={},
    )

    response = await post_match(client, user, job.id)

    row = response.json()["matches"][0]
    assert 0 <= row["overall_score"] <= 100
    for value in row["scores"].values():
        assert value is None or 0 <= value <= 100


# ---------------------------------------------------------------------------
# Caching, determinism and bulk
# ---------------------------------------------------------------------------
async def test_results_are_cached_and_deterministic(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")
    job = await create_job(session_factory, user)
    await create_resume(
        session_factory,
        user,
        filename="strong.pdf",
        skills=["Python", "FastAPI", "PostgreSQL", "Docker"],
        parsed_data=STRONG_PARSED,
    )

    first = (await post_match(client, user, job.id)).json()
    second = (await post_match(client, user, job.id)).json()

    assert first["computed_at"] == second["computed_at"]
    assert first["matches"] == second["matches"]

    forced = (await post_match(client, user, job.id, force=True)).json()
    stable_fields = (
        "resume_id",
        "rank",
        "overall_score",
        "scores",
        "missing_required_skills",
        "strengths",
        "suggestions",
    )
    assert [
        {field: row[field] for field in stable_fields} for row in forced["matches"]
    ] == [
        {field: row[field] for field in stable_fields} for row in first["matches"]
    ]


async def test_bulk_match_and_summary(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")
    first = await create_job(session_factory, user, title="Backend Engineer")
    second = await create_job(session_factory, user, title="Python Engineer")
    resume = await create_resume(
        session_factory,
        user,
        filename="strong.pdf",
        skills=["Python", "FastAPI", "PostgreSQL", "Docker"],
        parsed_data=STRONG_PARSED,
        is_default=True,
    )

    bulk = await client.post(
        f"{MATCHING}/bulk",
        json={"job_ids": [str(first.id), str(second.id)]},
        headers=bearer(user),
    )

    assert bulk.status_code == 200, bulk.text
    body = bulk.json()
    assert body["matched_jobs"] == 2
    assert len(body["results"]) == 2
    assert all(row["best_match"]["resume_id"] == str(resume.id) for row in body["results"])

    summary = await client.get(f"{MATCHING}/summary", headers=bearer(user))
    assert summary.status_code == 200
    data = summary.json()
    assert data["jobs_total"] == 2
    assert data["jobs_matched"] == 2
    assert data["resumes_total"] == 1
    assert data["resumes_matched"] == 1
    assert data["resume_stats"][0]["resume_id"] == str(resume.id)
    assert data["resume_stats"][0]["recommended_jobs"] == 2
    assert data["best_match"]["resume_id"] == str(resume.id)


# ---------------------------------------------------------------------------
# Discovery persistence (Gmail itself is exercised by the existing tests)
# ---------------------------------------------------------------------------
def glassdoor_job(*, title: str, job_id: str) -> GlassdoorJob:
    return GlassdoorJob(
        job_title=title,
        company="Acme",
        location="Remote",
        job_url=(
            "https://www.glassdoor.com/job-listing/"
            f"backend-JV_1.htm?jl={job_id}"
        ),
        email_subject="Jobs for you",
        received_at=datetime(2026, 11, 2, tzinfo=timezone.utc),
        job_description="Requirements:\n- Python and FastAPI\n",
        message_id="msg-1",
    )


async def test_discovery_persists_and_dedupes_jobs(
    client: AsyncClient,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")

    first_scan = GlassdoorDiscoveryResult(
        jobs=[
            glassdoor_job(title="Backend Engineer", job_id="1001"),
            glassdoor_job(title="Data Engineer", job_id="1002"),
        ],
        messages_scanned=2,
    )
    second_scan = GlassdoorDiscoveryResult(
        jobs=[
            glassdoor_job(title="Senior Backend Engineer", job_id="1001"),
            glassdoor_job(title="Platform Engineer", job_id="1003"),
        ],
        messages_scanned=1,
    )

    async def fake_fetch(*_args: Any, **_kwargs: Any) -> GlassdoorDiscoveryResult:
        return first_scan

    monkeypatch.setattr("app.api.v1.endpoints.jobs.fetch_glassdoor_jobs", fake_fetch)
    first = await client.post(f"{JOBS}/discover?days=30&max_messages=10", headers=bearer(user))
    assert first.status_code == 200, first.text
    assert first.json()["jobs_created"] == 2

    async def fake_second(*_args: Any, **_kwargs: Any) -> GlassdoorDiscoveryResult:
        return second_scan

    monkeypatch.setattr("app.api.v1.endpoints.jobs.fetch_glassdoor_jobs", fake_second)
    second = await client.post(f"{JOBS}/discover?days=30&max_messages=10", headers=bearer(user))
    assert second.status_code == 200, second.text
    assert second.json()["jobs_created"] == 1
    assert second.json()["jobs_updated"] == 1

    listing = await client.get(JOBS, headers=bearer(user))
    assert listing.status_code == 200
    body = listing.json()
    assert body["total"] == 3
    titles = {job["job_title"] for job in body["jobs"]}
    assert "Senior Backend Engineer" in titles
    assert "Platform Engineer" in titles
