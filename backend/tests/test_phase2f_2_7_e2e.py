"""Phase 2F.2.7 end-to-end intelligence workflow validation.

This suite exercises the complete workflow through the real HTTP API, services
and database:

    job discovery -> job intelligence -> resume upload -> resume intelligence
    -> advanced matching -> comparison/recommendation -> application preparation
    -> explicit resume selection -> application review

Every document is synthetic and generated in memory. Gmail discovery itself is
not contacted: a posting is stored through the production
``store_glassdoor_jobs`` path so the persisted record, dedupe key and listing
URL are exactly what discovery would create.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from app.core.config import settings
from app.core.security import create_access_token
from app.models.job import Job
from app.models.job_match import JobResumeMatch
from app.models.resume import Resume
from app.models.resume_intelligence import STATUS_COMPLETED, ResumeIntelligence
from app.models.user import User
from app.services.glassdoor_jobs import GlassdoorJob
from app.services.job_discovery import store_glassdoor_jobs
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.pdf_factory import RESUME_LINES, make_pdf, scanned_pdf

SessionFactory = async_sessionmaker[AsyncSession]

JOBS = "/api/v1/jobs"
RESUMES = "/api/v1/resumes"
APPLICATIONS = "/api/v1/applications"
PROFILE = "/api/v1/profile"

JOB_URL = (
    "https://www.glassdoor.com/job-listing/senior-backend-engineer-acme-"
    "JV_IC123_KO0,24_KE25,29.htm?jl=1001234567890"
)
JOB_DESCRIPTION = (
    "Senior Backend Engineer at Acme. Build and operate APIs for our data platform.\n"
    "\n"
    "Requirements:\n"
    "- 5+ years of experience building backend services\n"
    "- Python, FastAPI, PostgreSQL and Docker\n"
    "- Bachelor's degree in Computer Science\n"
    "\n"
    "Preferred:\n"
    "- AWS\n"
    "- Kubernetes\n"
    "\n"
    "Responsibilities:\n"
    "- Build and operate APIs\n"
    "- Review code and mentor engineers\n"
)

MODERATE_LINES: tuple[str, ...] = (
    "Ada Lovelace",
    "ada@example.com",
    "Professional Summary",
    "Backend developer working with Python and PostgreSQL.",
    "Technical Skills",
    "Python, PostgreSQL, FastAPI",
    "Work Experience",
    "Backend Developer | Babbage Systems | Remote",
    "2021 - Present",
    "Built internal REST services for reporting.",
    "Education",
    "University of London",
    "BSc Computer Science",
    "2014 - 2018",
)

MANUAL_REASON = "Resume selected manually by you for this application."


@pytest.fixture(autouse=True)
def resume_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep uploads in a per-test temporary directory and disable automation."""
    storage_dir = (tmp_path / "resumes").resolve()
    monkeypatch.setattr(settings, "RESUME_STORAGE_DIR", str(storage_dir))
    monkeypatch.setattr(settings, "RESUME_MAX_FILE_SIZE_BYTES", 1024 * 1024)
    monkeypatch.setattr(settings, "APPLICATION_AUTOMATION_ENABLED", False)
    return storage_dir


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


async def discover_job(
    session_factory: SessionFactory,
    user: User,
    *,
    title: str = "Senior Backend Engineer",
    description: str = JOB_DESCRIPTION,
    job_url: str = JOB_URL,
    message_id: str = "msg-2f-2-7",
) -> Job:
    """Persist a posting through the real discovery storage path."""
    extracted = GlassdoorJob(
        job_title=title,
        company="Acme",
        location="Remote",
        job_url=job_url,
        email_subject="Jobs for you: Senior Backend Engineer at Acme",
        received_at=datetime(2026, 11, 2, tzinfo=timezone.utc),
        job_description=description,
        message_id=message_id,
    )
    async with session_factory() as session:
        stored, created, _updated = await store_glassdoor_jobs(
            session, user, [extracted]
        )
    assert created == 1
    return stored[0]


async def upload_resume(
    client: AsyncClient,
    user: User,
    *,
    filename: str,
    lines: tuple[str, ...] = RESUME_LINES,
) -> dict[str, Any]:
    response = await client.post(
        f"{RESUMES}/upload",
        files={"file": (filename, make_pdf(lines), "application/pdf")},
        headers=bearer(user),
    )
    assert response.status_code == 201, response.text
    return response.json()


async def upload_scanned_resume(
    client: AsyncClient, user: User
) -> dict[str, Any]:
    response = await client.post(
        f"{RESUMES}/upload",
        files={"file": ("scanned.pdf", scanned_pdf(), "application/pdf")},
        headers=bearer(user),
    )
    assert response.status_code == 201, response.text
    return response.json()


async def analyze_job(
    client: AsyncClient,
    user: User,
    job_id: Any,
    *,
    force: bool = False,
    use_llm: bool = False,
) -> dict[str, Any]:
    response = await client.post(
        f"{JOBS}/{job_id}/analyze",
        json={"force": force, "use_llm": use_llm},
        headers=bearer(user),
    )
    assert response.status_code == 200, response.text
    return response.json()


async def analyze_resume(
    client: AsyncClient,
    user: User,
    resume_id: Any,
    *,
    force: bool = False,
) -> dict[str, Any]:
    response = await client.post(
        f"{RESUMES}/{resume_id}/analyze",
        json={"force": force},
        headers=bearer(user),
    )
    assert response.status_code == 200, response.text
    return response.json()


async def compare(
    client: AsyncClient,
    user: User,
    job_id: Any,
    *,
    force: bool = False,
) -> dict[str, Any]:
    response = await client.post(
        f"{JOBS}/{job_id}/compare-resumes",
        json={"force": force},
        headers=bearer(user),
    )
    assert response.status_code == 200, response.text
    return response.json()


async def prepare_application(
    client: AsyncClient,
    user: User,
    job_id: Any,
    *,
    resume_id: Any | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"job_id": str(job_id)}
    if resume_id is not None:
        body["resume_id"] = str(resume_id)
    response = await client.post(
        f"{APPLICATIONS}/analyze", json=body, headers=bearer(user)
    )
    assert response.status_code == 200, response.text
    return response.json()


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


async def confirm_required_questions(
    client: AsyncClient, user: User, preview: dict[str, Any]
) -> dict[str, Any]:
    """Confirm every open required question through the normal user route."""
    application_id = preview["application_id"]
    for section in preview["sections"]:
        for question in section["questions"]:
            if not question["required"] or question["status"] == "confirmed":
                continue
            value = question["answer"] or "Yes"
            response = await client.patch(
                f"{APPLICATIONS}/{application_id}/questions/{question['id']}",
                json={"answer": value, "confirmed": True},
                headers=bearer(user),
            )
            assert response.status_code == 200, response.text
    fetched = await client.get(
        f"{APPLICATIONS}/{application_id}", headers=bearer(user)
    )
    assert fetched.status_code == 200, fetched.text
    return fetched.json()


async def match_row(
    session_factory: SessionFactory, job_id: Any, resume_id: Any
) -> JobResumeMatch:
    async with session_factory() as session:
        row = await session.scalar(
            select(JobResumeMatch).where(
                JobResumeMatch.job_id == uuid.UUID(str(job_id)),
                JobResumeMatch.resume_id == uuid.UUID(str(resume_id)),
            )
        )
    assert row is not None
    return row


def required_skill_names(body: dict[str, Any]) -> set[str]:
    return {skill.strip().lower() for skill in body["fields"]["required_skills"]}


# ---------------------------------------------------------------------------
# Steps A-H: one complete workflow
# ---------------------------------------------------------------------------
async def test_complete_intelligence_workflow_end_to_end(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(
        session_factory, email="ada@example.com", google_sub="e2e-user-a"
    )
    headers = bearer(user)
    await prepare_profile(client, user)

    # --- Step A: job discovery ---------------------------------------------
    job = await discover_job(session_factory, user)

    stored = await client.get(f"{JOBS}/{job.id}", headers=headers)
    assert stored.status_code == 200, stored.text
    listing = stored.json()
    assert listing["job_title"] == "Senior Backend Engineer"
    assert listing["company"] == "Acme"
    assert listing["source"] == "glassdoor"
    # The original listing URL survives discovery unchanged.
    assert listing["job_url"] == JOB_URL
    assert listing["job_description"] == JOB_DESCRIPTION
    assert listing["discovered_at"]
    # Discovery belongs to the authenticated user.
    owned = await client.get(JOBS, headers=headers)
    assert [row["id"] for row in owned.json()["jobs"]] == [str(job.id)]

    # --- Step B: job intelligence ------------------------------------------
    analyzed = await analyze_job(client, user, job.id)
    fields = analyzed["fields"]
    assert fields["job_title"] == "Senior Backend Engineer"
    assert fields["company"] == "Acme"
    assert {"python", "fastapi", "postgresql", "docker"} <= required_skill_names(
        analyzed
    )
    assert {skill.lower() for skill in fields["preferred_skills"]} >= {
        "aws",
        "kubernetes",
    }
    assert fields["experience_years"] == 5
    assert fields["education_level"] == "bachelor"
    assert fields["education_field"] == "Computer Science"
    assert fields["responsibilities"]
    assert fields["work_location"] == "Remote"
    assert fields["application_url"] == JOB_URL
    assert fields["application_url_is_listing"] is True
    extraction = analyzed["extraction"]
    assert extraction["confidence"] in {"high", "medium", "low"}
    assert extraction["provenance"]["required_skills"] == "deterministic"
    assert extraction["provenance"]["experience_years"] == "deterministic"
    assert extraction["field_confidence"]["required_skills"] in {
        "high",
        "medium",
        "low",
    }
    assert extraction["llm_status"] == "disabled"
    assert extraction["cached"] is False

    cached = await analyze_job(client, user, job.id)
    assert cached["extraction"]["cached"] is True
    assert cached["extraction"]["computed_at"] == extraction["computed_at"]
    assert "Cached analysis reused" in (cached["message"] or "")

    refreshed = await analyze_job(client, user, job.id, force=True)
    assert refreshed["extraction"]["cached"] is False
    assert refreshed["fields"]["required_skills"] == fields["required_skills"]

    # --- Step C: resume upload ---------------------------------------------
    strong = await upload_resume(
        client, user, filename="ada-backend-strong.pdf", lines=RESUME_LINES
    )
    moderate = await upload_resume(
        client, user, filename="ada-backend-moderate.pdf", lines=MODERATE_LINES
    )
    scanned = await upload_scanned_resume(client, user)

    assert strong["parsing_status"] == "completed"
    assert {"Python", "FastAPI", "PostgreSQL", "Docker"} <= set(strong["skills"])
    assert moderate["parsing_status"] == "completed"
    assert scanned["parsing_status"] == "ocr_required"

    listed_resumes = await client.get(RESUMES, headers=headers)
    assert listed_resumes.status_code == 200, listed_resumes.text
    by_id = {row["id"]: row for row in listed_resumes.json()}
    assert set(by_id) == {strong["id"], moderate["id"], scanned["id"]}
    for row in by_id.values():
        assert "storage_key" not in row
        assert "storage_backend" not in row
        assert "checksum" not in row

    # --- Step D: resume intelligence ---------------------------------------
    strong_intel = await analyze_resume(client, user, strong["id"])
    strong_fields = strong_intel["fields"]
    assert strong_fields["summary"]
    assert {skill["name"] for skill in strong_fields["skills"]} >= {
        "Python",
        "FastAPI",
        "PostgreSQL",
        "Docker",
    }
    assert strong_fields["experience"]
    assert strong_fields["experience"][0]["company"]
    assert strong_fields["projects"]
    assert strong_fields["education"]
    assert {"skills", "experience", "education"} <= set(
        strong_fields["sections_found"]
    )
    assert strong_fields["certifications"]
    assert strong_intel["extraction"]["status"] == STATUS_COMPLETED
    assert strong_intel["extraction"]["analysis_state"] == "ready"
    assert strong_intel["extraction"]["provenance"]
    assert strong_intel["extraction"]["field_confidence"]
    assert strong_intel["extraction"]["confidence"] in {"high", "medium", "low"}
    assert strong_intel["extraction"]["cached"] is False

    cached_intel = await analyze_resume(client, user, strong["id"])
    assert cached_intel["extraction"]["cached"] is True
    assert cached_intel["fields"] == strong_fields

    moderate_intel = await analyze_resume(client, user, moderate["id"])
    assert moderate_intel["extraction"]["analysis_state"] == "ready"

    failed_intel = await analyze_resume(client, user, scanned["id"])
    assert failed_intel["extraction"]["status"] == "failed"
    assert failed_intel["extraction"]["analysis_state"] == "failed"
    assert failed_intel["extraction"]["error_code"]
    assert failed_intel["fields"]["skills"] == []
    assert "Nothing was inferred" in (failed_intel["message"] or "")

    # --- Step E: advanced matching -----------------------------------------
    first_comparison = await compare(client, user, job.id, force=True)
    assert first_comparison["job_id"] == str(job.id)
    assert first_comparison["total_resumes"] == 3
    assert first_comparison["compared_resumes"] == 2
    assert first_comparison["status"] == "partial"
    assert first_comparison["algorithm_version"]
    assert first_comparison["disclaimer"]

    ranked = [entry for entry in first_comparison["resumes"] if entry["rank"]]
    assert [entry["rank"] for entry in ranked] == [1, 2]
    assert ranked[0]["resume_id"] == strong["id"]
    assert ranked[1]["resume_id"] == moderate["id"]
    for entry in ranked:
        assert entry["comparable"] is True
        assert entry["overall_score"] is not None
        assert entry["match_processing_state"] == "ready"
        assert entry["required_skills_total"] == 4
        assert entry["required_skills_matched"] is not None
        assert entry["required_skill_coverage"] is not None
        assert entry["scores"]["skills"] is not None
        assert entry["scores"]["experience"] is not None
        assert entry["scores"]["title"] is not None
        assert entry["scores"]["education"] is not None
        assert entry["evidence"], "matcher must explain its evidence"
        # Gaps describe coverage without declaring the candidate disqualified.
        assert entry["gaps"]
        assert not any("disqualif" in gap.lower() for gap in entry["gaps"])
    # The strongest resume covers every required skill; the weaker one does not.
    assert ranked[0]["required_skills_matched"] == 4
    assert ranked[1]["required_skills_matched"] < 4
    assert ranked[1]["missing_required_skills"]

    failures = first_comparison["processing_failures"]
    assert [failure["resume_id"] for failure in failures] == [scanned["id"]]
    assert failures[0]["processing_state"] == "EXTRACTION_FAILED"
    assert failures[0]["reason"]

    # Repeated comparison is deterministic and does not create new rows.
    second_comparison = await compare(client, user, job.id)
    assert [
        (entry["resume_id"], entry["rank"], entry["overall_score"])
        for entry in second_comparison["resumes"]
        if entry["rank"]
    ] == [
        (entry["resume_id"], entry["rank"], entry["overall_score"])
        for entry in first_comparison["resumes"]
        if entry["rank"]
    ]
    # Matching stores one compatibility row per owned resume (including the
    # unreadable one); the comparison classifies and excludes it explicitly.
    async with session_factory() as session:
        row_count = await session.scalar(
            select(func.count(JobResumeMatch.id)).where(JobResumeMatch.job_id == job.id)
        )
    assert row_count == 3

    # --- Step F: recommendation --------------------------------------------
    recommendation_read = await client.get(
        f"{JOBS}/{job.id}/recommendation", headers=headers
    )
    assert recommendation_read.status_code == 200, recommendation_read.text
    recommendation_body = recommendation_read.json()
    assert recommendation_body["status"] == "partial"
    recommendation = recommendation_body["recommendation"]
    assert recommendation["resume_id"] == strong["id"]
    assert recommendation["resume_title"] == "ada-backend-strong.pdf"
    assert recommendation["overall_score"] == ranked[0]["overall_score"]
    assert recommendation["confidence"] in {"high", "medium", "low"}
    assert recommendation["reasons"]
    assert recommendation["supporting_evidence"]
    assert recommendation["important_gaps"] is not None
    assert recommendation["close_call_threshold"] == 3
    assert recommendation["alternatives"]
    assert recommendation["alternatives"][0]["resume_id"] == moderate["id"]
    assert recommendation["alternatives"][0]["score_difference"] > 0

    # --- Step G: application preparation -----------------------------------
    preview = await prepare_application(client, user, job.id)
    application_id = preview["application_id"]
    assert preview["resume"]["id"] == strong["id"]
    assert preview["resume"]["filename"] == "ada-backend-strong.pdf"
    assert preview["resume"]["score"] == recommendation["overall_score"]
    assert preview["resume"]["reason"].startswith("Recommended by resume comparison:")
    assert question_by_key(preview, "documents.resume")["answer"] == (
        "ada-backend-strong.pdf"
    )
    # Preparation never approves or submits.
    assert preview["status"] == "awaiting_user_information"
    assert preview["approved_at"] is None
    assert preview["submitted_at"] is None
    assert preview["can_submit"] is False
    # Legal declarations remain a manual, user-confirmed answer.
    declaration = question_by_key(preview, "declarations.accuracy")
    assert declaration["is_legal"] is True
    assert declaration["status"] != "confirmed"

    # The persisted selection is readable after a fresh request.
    reloaded = await client.get(f"{APPLICATIONS}/{application_id}", headers=headers)
    assert reloaded.status_code == 200, reloaded.text
    assert reloaded.json()["resume"]["id"] == strong["id"]

    # --- Step H: explicit alternative selection ----------------------------
    selected = await client.patch(
        f"{APPLICATIONS}/{application_id}/resume",
        json={"resume_id": moderate["id"]},
        headers=headers,
    )
    assert selected.status_code == 200, selected.text
    selected_body = selected.json()
    assert selected_body["resume"]["id"] == moderate["id"]
    assert selected_body["resume"]["filename"] == "ada-backend-moderate.pdf"
    assert selected_body["resume"]["reason"] == MANUAL_REASON
    assert selected_body["resume"]["score"] is None
    assert selected_body["approved_at"] is None
    assert selected_body["can_submit"] is False

    # Re-analysis must never silently replace the explicit choice.
    reanalyzed = await prepare_application(client, user, job.id)
    assert reanalyzed["application_id"] == application_id
    assert reanalyzed["resume"]["id"] == moderate["id"]
    assert reanalyzed["resume"]["reason"] == MANUAL_REASON
    assert question_by_key(reanalyzed, "documents.resume")["answer"] == (
        "ada-backend-moderate.pdf"
    )
    assert reanalyzed["approved_at"] is None
    assert reanalyzed["submitted_at"] is None

    # A manual selection is visible after a page reload equivalent.
    reloaded_again = await client.get(
        f"{APPLICATIONS}/{application_id}", headers=headers
    )
    assert reloaded_again.json()["resume"]["id"] == moderate["id"]

    # --- Approval is explicit and invalidated by a resume change -----------
    ready = await confirm_required_questions(client, user, reanalyzed)
    assert ready["missing_required"] == []
    assert ready["ready_to_approve"] is True
    assert ready["approved_at"] is None

    approved = await client.post(
        f"{APPLICATIONS}/{application_id}/approve",
        json={"approved": True},
        headers=headers,
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["approved_at"] is not None
    assert approved.json()["status"] == "awaiting_approval"

    changed_after_approval = await client.patch(
        f"{APPLICATIONS}/{application_id}/resume",
        json={"resume_id": strong["id"]},
        headers=headers,
    )
    assert changed_after_approval.status_code == 200, changed_after_approval.text
    final = changed_after_approval.json()
    assert final["resume"]["id"] == strong["id"]
    assert final["approved_at"] is None
    assert final["status"] != "awaiting_approval"
    assert final["can_submit"] is False
    # Nothing was submitted on the user's behalf anywhere in this flow.
    assert final["submitted_at"] is None


# ---------------------------------------------------------------------------
# Deterministic ranking and close-score handling
# ---------------------------------------------------------------------------
async def test_equal_resumes_are_ranked_deterministically_and_flagged_close(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(
        session_factory, email="equal@example.com", google_sub="e2e-user-equal"
    )
    job = await discover_job(session_factory, user, message_id="msg-equal")
    await analyze_job(client, user, job.id)

    first = await upload_resume(
        client, user, filename="ada-first.pdf", lines=RESUME_LINES
    )
    second = await upload_resume(
        client, user, filename="ada-second.pdf", lines=RESUME_LINES
    )
    await analyze_resume(client, user, first["id"])
    await analyze_resume(client, user, second["id"])

    comparison = await compare(client, user, job.id, force=True)
    assert comparison["status"] == "completed"
    assert comparison["total_resumes"] == 2
    assert comparison["compared_resumes"] == 2
    assert comparison["close_score"] is True
    recommendation = comparison["recommendation"]
    assert recommendation["close_call"] is True
    assert recommendation["close_call_threshold"] == 3
    assert recommendation["close_call_explanation"]
    assert "threshold" in recommendation["close_call_explanation"]
    # A close call can never present high confidence in a single winner.
    assert recommendation["confidence"] != "high"
    assert len(recommendation["alternatives"]) == 1
    alternative = recommendation["alternatives"][0]
    assert alternative["score_difference"] <= 3
    assert alternative["resume_id"] in {first["id"], second["id"]}
    assert alternative["resume_id"] != recommendation["resume_id"]

    ranked_order = [
        entry["resume_id"] for entry in comparison["resumes"] if entry["rank"]
    ]
    repeated = await compare(client, user, job.id)
    assert [
        entry["resume_id"] for entry in repeated["resumes"] if entry["rank"]
    ] == ranked_order

    # Persisted matches are reused, so the recommendation is stable.
    read = await client.get(f"{JOBS}/{job.id}/recommendation", headers=bearer(user))
    assert read.status_code == 200, read.text
    assert read.json()["recommendation"]["resume_id"] == (
        recommendation["resume_id"]
    )


# ---------------------------------------------------------------------------
# Empty and unsuitable resume sets
# ---------------------------------------------------------------------------
async def test_comparison_reports_no_resumes_and_no_suitable_resume(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(
        session_factory, email="empty@example.com", google_sub="e2e-user-empty"
    )
    headers = bearer(user)
    job = await discover_job(session_factory, user, message_id="msg-empty")
    await analyze_job(client, user, job.id)

    empty = await compare(client, user, job.id)
    assert empty["status"] == "no_resumes"
    assert empty["total_resumes"] == 0
    assert empty["compared_resumes"] == 0
    assert empty["recommendation"] is None
    assert empty["message"]

    scanned = await upload_scanned_resume(client, user)

    not_computed = await client.get(
        f"{JOBS}/{job.id}/recommendation", headers=headers
    )
    assert not_computed.status_code == 200, not_computed.text
    assert not_computed.json()["status"] == "not_computed"
    assert not_computed.json()["recommendation"] is None
    assert "compare-resumes" in (not_computed.json()["message"] or "")

    await analyze_resume(client, user, scanned["id"])

    unsuitable = await compare(client, user, job.id, force=True)
    assert unsuitable["status"] == "no_suitable_resume"
    assert unsuitable["total_resumes"] == 1
    assert unsuitable["compared_resumes"] == 0
    assert unsuitable["recommendation"] is None
    assert unsuitable["message"]
    assert unsuitable["processing_failures"][0]["resume_id"] == scanned["id"]
    assert unsuitable["processing_failures"][0]["processing_state"] == (
        "EXTRACTION_FAILED"
    )


# ---------------------------------------------------------------------------
# Cache invalidation on changed job or resume content
# ---------------------------------------------------------------------------
async def test_changed_job_and_resume_content_invalidate_caches(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(
        session_factory, email="cache@example.com", google_sub="e2e-user-cache"
    )
    headers = bearer(user)
    job = await discover_job(session_factory, user, message_id="msg-cache")
    resume = await upload_resume(client, user, filename="ada-cache.pdf")
    await analyze_job(client, user, job.id)
    await analyze_resume(client, user, resume["id"])

    await compare(client, user, job.id, force=True)
    initial_row = await match_row(session_factory, job.id, resume["id"])
    initial_job_fingerprint = initial_row.job_fingerprint
    initial_resume_fingerprint = initial_row.resume_fingerprint
    assert initial_job_fingerprint
    assert initial_resume_fingerprint

    # Changing the posting makes the stored job analysis stale-equivalent:
    # a read recomputes without saving, and analyze persists a new reading.
    changed_description = (
        "Product Designer at Acme.\n"
        "Requirements:\n"
        "- 3+ years of experience with Figma and visual design\n"
    )
    async with session_factory() as session:
        stored_job = await session.get(Job, job.id)
        assert stored_job is not None
        stored_job.job_description = changed_description
        await session.commit()

    stale = await client.get(f"{JOBS}/{job.id}/intelligence", headers=headers)
    assert stale.status_code == 200, stale.text
    assert stale.json()["extraction"]["cached"] is False
    assert any(
        "figma" in skill.lower()
        for skill in stale.json()["fields"]["required_skills"]
    )

    refreshed = await analyze_job(client, user, job.id)
    assert refreshed["extraction"]["cached"] is False

    # The comparison recomputes because the job fingerprint changed.
    after_job_change = await compare(client, user, job.id)
    job_row = await match_row(session_factory, job.id, resume["id"])
    assert job_row.job_fingerprint != initial_job_fingerprint
    assert after_job_change["compared_resumes"] == 1

    # Changing the resume content marks the stored analysis stale.
    async with session_factory() as session:
        stored_resume = await session.get(Resume, uuid.UUID(str(resume["id"])))
        assert stored_resume is not None
        stored_resume.checksum = uuid.uuid4().hex + uuid.uuid4().hex
        stored_resume.skills = ["Figma", "Visual Design"]
        stored_resume.parsed_data = {
            "professional_summary": "Product designer focused on visual design.",
            "experience": [],
            "education": [],
            "projects": [],
            "certifications": [],
            "links": [],
        }
        await session.commit()

    stale_resume = await client.get(
        f"{RESUMES}/{resume['id']}/intelligence", headers=headers
    )
    assert stale_resume.status_code == 200, stale_resume.text
    assert stale_resume.json()["extraction"]["analysis_state"] == "stale"
    assert stale_resume.json()["extraction"]["cached"] is False

    after_resume_change = await compare(client, user, job.id)
    resume_row = await match_row(session_factory, job.id, resume["id"])
    assert resume_row.resume_fingerprint != initial_resume_fingerprint
    assert after_resume_change["compared_resumes"] == 1

    # No duplicate match rows were created by any recomputation.
    async with session_factory() as session:
        count = await session.scalar(
            select(func.count(JobResumeMatch.id)).where(JobResumeMatch.job_id == job.id)
        )
    assert count == 1


# ---------------------------------------------------------------------------
# Failed extraction must not destroy a previous success
# ---------------------------------------------------------------------------
async def test_failed_resume_extraction_keeps_the_previous_success(
    client: AsyncClient,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await create_user(
        session_factory, email="failed@example.com", google_sub="e2e-user-failed"
    )
    resume = await upload_resume(client, user, filename="ada-failed.pdf")
    successful = await analyze_resume(client, user, resume["id"])
    assert successful["extraction"]["status"] == STATUS_COMPLETED

    import app.services.resume_intelligence_service as service_module

    def explode(**_: Any) -> None:
        raise RuntimeError("synthetic extraction failure")

    monkeypatch.setattr(service_module, "extract_resume_intelligence", explode)

    kept = await analyze_resume(client, user, resume["id"], force=True)
    assert kept["extraction"]["analysis_state"] == "ready"
    assert kept["fields"] == successful["fields"]
    assert any(
        "previous analysis was kept" in warning.lower()
        for warning in kept["extraction"]["warnings"]
    )

    async with session_factory() as session:
        rows = list(
            (
                await session.scalars(
                    select(ResumeIntelligence).where(
                        ResumeIntelligence.resume_id
                        == uuid.UUID(str(resume["id"]))
                    )
                )
            ).all()
        )
    assert len(rows) == 1
    assert rows[0].extraction_status == STATUS_COMPLETED


# ---------------------------------------------------------------------------
# Security, ownership and data-exposure regressions
# ---------------------------------------------------------------------------
async def test_cross_user_access_is_rejected_and_internals_are_never_exposed(
    client: AsyncClient,
    session_factory: SessionFactory,
    caplog: pytest.LogCaptureFixture,
    resume_settings: Path,
) -> None:
    owner = await create_user(
        session_factory, email="owner@example.com", google_sub="e2e-owner"
    )
    intruder = await create_user(
        session_factory, email="intruder@example.com", google_sub="e2e-intruder"
    )
    owner_headers = bearer(owner)
    intruder_headers = bearer(intruder)
    await prepare_profile(client, owner)
    await prepare_profile(client, intruder)

    marker = "CONFIDENTIALMARKER2F27"
    owner_job = await discover_job(session_factory, owner, message_id="msg-owner")
    owner_resume = await upload_resume(
        client,
        owner,
        filename="owner-secret.pdf",
        lines=(*RESUME_LINES, marker),
    )
    await analyze_job(client, owner, owner_job.id)
    await analyze_resume(client, owner, owner_resume["id"])
    await compare(client, owner, owner_job.id, force=True)

    # Another user's job is never visible, analyzable or comparable.
    for method, path, payload in (
        ("GET", f"{JOBS}/{owner_job.id}", None),
        ("GET", f"{JOBS}/{owner_job.id}/intelligence", None),
        ("POST", f"{JOBS}/{owner_job.id}/analyze", {}),
        ("POST", f"{JOBS}/{owner_job.id}/compare-resumes", {}),
        ("GET", f"{JOBS}/{owner_job.id}/recommendation", None),
    ):
        response = await client.request(
            method, path, json=payload, headers=intruder_headers
        )
        assert response.status_code == 404, (method, path, response.text)
        assert response.json()["code"] == "job_not_found"

    # Another user's resume is never readable, analyzable or downloadable.
    for method, path, payload in (
        ("GET", f"{RESUMES}/{owner_resume['id']}", None),
        ("GET", f"{RESUMES}/{owner_resume['id']}/intelligence", None),
        ("POST", f"{RESUMES}/{owner_resume['id']}/analyze", {}),
        ("GET", f"{RESUMES}/{owner_resume['id']}/download", None),
    ):
        response = await client.request(
            method, path, json=payload, headers=intruder_headers
        )
        assert response.status_code == 404, (method, path, response.text)
        assert response.json()["code"] == "resume_not_found"

    intruder_job = await discover_job(
        session_factory,
        intruder,
        title="Data Engineer",
        description="Requirements:\n- Python\n- SQL\n",
        job_url="https://www.glassdoor.com/job-listing/data-engineer-JV_1.htm?jl=999",
        message_id="msg-intruder",
    )
    # A resume id owned by someone else cannot be attached during preparation.
    blocked_prepare = await client.post(
        f"{APPLICATIONS}/analyze",
        json={"job_id": str(intruder_job.id), "resume_id": owner_resume["id"]},
        headers=intruder_headers,
    )
    assert blocked_prepare.status_code == 404
    assert blocked_prepare.json()["code"] == "resume_not_found"

    intruder_preview = await prepare_application(
        client, intruder, intruder_job.id
    )
    # ...nor selected afterwards on an owned application.
    blocked_select = await client.patch(
        f"{APPLICATIONS}/{intruder_preview['application_id']}/resume",
        json={"resume_id": owner_resume["id"]},
        headers=intruder_headers,
    )
    assert blocked_select.status_code == 404
    assert blocked_select.json()["code"] == "resume_not_found"

    # Responses expose display filenames and scores, never storage internals.
    exposed: list[dict[str, Any]] = [
        (await client.get(f"{RESUMES}/{owner_resume['id']}", headers=owner_headers)).json(),
        (
            await client.get(
                f"{RESUMES}/{owner_resume['id']}/intelligence", headers=owner_headers
            )
        ).json(),
        (
            await client.get(
                f"{JOBS}/{owner_job.id}/recommendation", headers=owner_headers
            )
        ).json(),
    ]
    for body in exposed:
        rendered = json.dumps(body).lower()
        assert "storage_key" not in rendered
        assert "storage_backend" not in rendered
        assert "checksum" not in rendered
        assert str(resume_settings).lower() not in rendered
        assert "users/" not in rendered

    # Resume text must never be written to the logs.
    caplog.set_level(logging.INFO)
    await upload_resume(
        client,
        owner,
        filename="owner-log-check.pdf",
        lines=(*RESUME_LINES, marker),
    )
    await client.post(
        f"{RESUMES}/{owner_resume['id']}/analyze",
        json={"force": True},
        headers=owner_headers,
    )
    assert marker not in caplog.text
    assert "Analytical Engines Ltd" not in caplog.text
