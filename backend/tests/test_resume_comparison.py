"""Phase 2F.2.4 tests: multi-resume comparison and recommendation.

All data is synthetic (Ada Lovelace style fixtures). No real candidate
information is used. Two layers are covered:

* the service workflow against the in-memory database, including cache reuse,
  invalidation, duplicates, ownership and partial failure;
* the HTTP endpoints, including response shape and the security boundaries
  (no storage keys, no raw document content, no parsed payloads).
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from typing import Any

import pytest
from app.core.security import create_access_token
from app.models.job import Job
from app.models.job_intelligence import JobIntelligence
from app.models.job_match import JobResumeMatch
from app.models.resume import Resume, ResumeParsingStatus
from app.models.resume_intelligence import (
    STATUS_COMPLETED,
    ResumeIntelligence,
)
from app.models.resume_intelligence import (
    STATUS_FAILED as ROW_STATUS_FAILED,
)
from app.models.user import User
from app.schemas.job_intelligence import JobIntelligenceFields, JobIntelligencePayload
from app.schemas.resume_intelligence import (
    METHOD_SKILLS_SECTION,
    ResumeIntelligenceFields,
    ResumeIntelligencePayload,
    ResumeSkillFact,
)
from app.services.advanced_matching_engine import MATCHING_ALGORITHM_VERSION, STATE_READY
from app.services.job_intelligence_extraction import (
    EXTRACTOR_VERSION as JOB_INTELLIGENCE_VERSION,
)
from app.services.job_service import JobNotFoundError
from app.services.job_understanding_service import job_intelligence_fingerprint
from app.services.matching_service import job_fingerprint, resume_fingerprint
from app.services.resume_comparison_service import (
    COMPARISON_COMPLETED,
    COMPARISON_NO_RESUMES,
    COMPARISON_NO_SUITABLE,
    COMPARISON_NOT_COMPUTED,
    COMPARISON_PARTIAL,
    RESUME_ANALYSIS_REQUIRED,
    RESUME_EXTRACTION_FAILED,
    RESUME_INVALID,
    RESUME_PARTIAL,
    RESUME_READY,
    ResumeComparisonService,
)
from app.services.resume_intelligence_extraction import (
    EXTRACTOR_VERSION as RESUME_INTELLIGENCE_EXTRACTOR_VERSION,
)
from app.services.resume_intelligence_service import resume_content_fingerprint
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

SessionFactory = async_sessionmaker[AsyncSession]

AS_OF = date(2026, 11, 4)
JOBS = "/api/v1/jobs"
STRONG_JD = """Requirements:
- 5+ years of experience building backend services
- Python, FastAPI, PostgreSQL and Docker
- Bachelor's degree in Computer Science
Preferred:
- Kubernetes
- AWS
Responsibilities:
- Build and operate APIs
"""

STRONG_PARSED = {
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

WEAK_PARSED = {
    "professional_summary": "Frontend designer working with visual branding.",
    "experience": [
        {
            "title": "Visual Designer",
            "company": "Studio",
            "start_date": "Jan 2021",
            "end_date": None,
            "is_current": True,
            "description": "Designed brand guidelines.",
        }
    ],
}


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------
async def create_user(
    session_factory: SessionFactory, *, email: str, google_sub: str
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
    description: str | None = STRONG_JD,
) -> Job:
    async with session_factory() as session:
        job = Job(
            user_id=user.id,
            dedupe_key=f"glassdoor-id:{uuid.uuid4().int}",
            source="glassdoor",
            job_title=title,
            company="Acme",
            location="Remote",
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


async def create_resume(
    session_factory: SessionFactory,
    user: User,
    *,
    filename: str = "ada.pdf",
    skills: list[str] | None = None,
    parsed_data: dict[str, Any] | None = None,
    parsing_status: ResumeParsingStatus = ResumeParsingStatus.COMPLETED,
    created_at: datetime | None = None,
) -> Resume:
    async with session_factory() as session:
        resume = Resume(
            user_id=user.id,
            original_filename=filename,
            storage_key=f"users/{user.id}/{uuid.uuid4()}.pdf",
            storage_backend="local",
            content_type="application/pdf",
            file_size=2048,
            checksum=uuid.uuid4().hex + uuid.uuid4().hex,
            is_default=True,
            parsing_status=parsing_status,
            parsed_at=(
                datetime(2026, 11, 1, tzinfo=timezone.utc)
                if parsing_status is ResumeParsingStatus.COMPLETED
                else None
            ),
            parsed_data=parsed_data if parsed_data is not None else STRONG_PARSED,
            skills=(
                skills
                if skills is not None
                else ["Python", "FastAPI", "PostgreSQL", "Docker"]
            ),
            created_at=created_at,
            updated_at=created_at,
        )
        session.add(resume)
        await session.commit()
        await session.refresh(resume)
        return resume


async def store_resume_intelligence(
    session_factory: SessionFactory,
    user: User,
    resume: Resume,
    payload: ResumeIntelligencePayload | None,
    *,
    status: str = STATUS_COMPLETED,
    error_code: str | None = None,
) -> ResumeIntelligence:
    async with session_factory() as session:
        row = ResumeIntelligence(
            user_id=user.id,
            resume_id=resume.id,
            extractor_version=RESUME_INTELLIGENCE_EXTRACTOR_VERSION,
            content_fingerprint=resume_content_fingerprint(resume),
            extraction_status=status,
            confidence=(payload.confidence if payload is not None else "low"),
            error_code=error_code,
            intelligence_data=(
                payload.model_dump(mode="json") if payload is not None else {}
            ),
            provenance={},
            field_confidence={},
            warnings=[],
            extracted_at=datetime.now(timezone.utc),
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row


async def store_job_intelligence(
    session_factory: SessionFactory, user: User, job: Job
) -> JobIntelligence:
    payload = JobIntelligencePayload(
        fields=JobIntelligenceFields(
            job_title=job.job_title,
            required_skills=["Python", "FastAPI", "PostgreSQL", "Docker"],
            experience_years=5,
            education_level="bachelor",
        ),
        confidence="high",
        provenance={"required_skills": "deterministic"},
    )
    async with session_factory() as session:
        row = JobIntelligence(
            user_id=user.id,
            job_id=job.id,
            extractor_version=JOB_INTELLIGENCE_VERSION,
            job_fingerprint=job_intelligence_fingerprint(job),
            confidence="high",
            llm_used=False,
            llm_status="disabled",
            data=payload.model_dump(mode="json"),
            extracted_at=datetime.now(timezone.utc),
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row


def resume_payload(*skills: str, confidence: str = "high") -> ResumeIntelligencePayload:
    return ResumeIntelligencePayload(
        fields=ResumeIntelligenceFields(
            skills=[
                ResumeSkillFact(
                    name=name,
                    confidence="high",
                    explicit=True,
                    method=METHOD_SKILLS_SECTION,
                )
                for name in skills
            ],
        ),
        confidence=confidence,
    )


async def store_match_row(
    session_factory: SessionFactory,
    user: User,
    job: Job,
    resume: Resume,
    *,
    overall: float,
    matched: list[str] | None = None,
    experience: float | None = 60.0,
    projects: float | None = 50.0,
    education: float | None = 50.0,
    title: float | None = 60.0,
    skills: float | None = None,
    confidence: str = "medium",
) -> JobResumeMatch:
    """Insert a fresh, reusable match row with exact scores for ranking tests."""
    async with session_factory() as session:
        row = JobResumeMatch(
            user_id=user.id,
            job_id=job.id,
            resume_id=resume.id,
            overall_score=overall,
            skill_score=skills,
            experience_score=experience,
            education_score=education,
            title_score=title,
            project_score=projects,
            rank=1,
            is_recommended=False,
            matched_required_skills=matched or [],
            matched_preferred_skills=[],
            missing_required_skills=[],
            missing_preferred_skills=[],
            strengths=[f"Synthetic strength for {resume.original_filename}."],
            suggestions=[],
            explanation=f"{resume.original_filename} synthetic comparison.",
            confidence=confidence,
            details={
                "processing_state": STATE_READY,
                "algorithm_version": MATCHING_ALGORITHM_VERSION,
                "evidence": [
                    {
                        "category": "required_skill",
                        "description": "Python is evidenced by the resume.",
                        "resume_reference": "skills section",
                        "confidence": "high",
                        "match_reason": "Exact skill match.",
                    }
                ],
                "gaps": ["Synthetic gap."],
            },
            job_fingerprint=job_fingerprint(job),
            resume_fingerprint=resume_fingerprint(resume),
            computed_at=datetime.now(timezone.utc),
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row


def _instant(value: datetime) -> datetime:
    """Drop tzinfo: SQLite returns naive datetimes for stored values."""
    return value.replace(tzinfo=None)


async def compare(
    session_factory: SessionFactory,
    user: User,
    job: Job,
    *,
    force: bool = False,
) -> Any:
    async with session_factory() as session:
        return await ResumeComparisonService(session).compare(
            user, job.id, force=force, as_of=AS_OF
        )


async def match_row_count(session_factory: SessionFactory, job: Job) -> int:
    async with session_factory() as session:
        return int(
            await session.scalar(
                select(func.count(JobResumeMatch.id)).where(
                    JobResumeMatch.job_id == job.id
                )
            )
            or 0
        )


# ---------------------------------------------------------------------------
# Basic comparison behaviour
# ---------------------------------------------------------------------------
async def test_single_resume_is_compared_and_recommended(
    session_factory: SessionFactory,
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")
    job = await create_job(session_factory, user)
    resume = await create_resume(session_factory, user)

    outcome = await compare(session_factory, user, job)

    assert outcome.status == COMPARISON_COMPLETED
    assert outcome.total_resumes == 1
    assert outcome.compared_resumes == 1
    assert outcome.entries[0].state == RESUME_ANALYSIS_REQUIRED
    assert outcome.entries[0].rank == 1
    assert outcome.recommendation is not None
    best = outcome.recommendation.recommended
    assert best is not None and best.resume_id == resume.id
    assert outcome.recommendation.close_call is False
    assert outcome.recommendation.alternatives == ()
    assert outcome.processing_failures == []


async def test_multiple_resumes_are_ranked_by_overall_score(
    session_factory: SessionFactory,
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")
    job = await create_job(session_factory, user)
    strong = await create_resume(session_factory, user, filename="strong.pdf")
    weak = await create_resume(
        session_factory,
        user,
        filename="weak.pdf",
        skills=["Python"],
        parsed_data=WEAK_PARSED,
    )

    outcome = await compare(session_factory, user, job)

    assert [entry.resume_id for entry in outcome.entries] == [strong.id, weak.id]
    assert outcome.entries[0].overall_score > outcome.entries[1].overall_score
    assert outcome.entries[0].is_recommended is True
    assert outcome.entries[1].is_recommended is False
    assert outcome.recommendation is not None
    assert outcome.recommendation.recommended.resume_id == strong.id
    assert outcome.close_score is False


async def test_equal_scores_use_required_skill_coverage_tie_breaker(
    session_factory: SessionFactory,
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")
    job = await create_job(session_factory, user)
    full = await create_resume(session_factory, user, filename="full.pdf")
    partial = await create_resume(session_factory, user, filename="partial.pdf")
    await store_match_row(
        session_factory,
        user,
        job,
        full,
        overall=70.0,
        matched=["Python", "FastAPI", "PostgreSQL", "Docker"],
    )
    await store_match_row(
        session_factory,
        user,
        job,
        partial,
        overall=70.0,
        matched=["Python", "FastAPI"],
    )

    outcome = await compare(session_factory, user, job)

    assert [entry.resume_title for entry in outcome.entries] == ["full.pdf", "partial.pdf"]
    assert outcome.entries[0].required_skill_coverage == 1.0
    assert outcome.entries[1].required_skill_coverage == 0.5


async def test_equal_score_and_coverage_use_experience_tie_breaker(
    session_factory: SessionFactory,
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")
    job = await create_job(session_factory, user)
    senior = await create_resume(session_factory, user, filename="senior.pdf")
    junior = await create_resume(session_factory, user, filename="junior.pdf")
    for resume, experience in ((senior, 90.0), (junior, 40.0)):
        await store_match_row(
            session_factory,
            user,
            job,
            resume,
            overall=70.0,
            matched=["Python"],
            experience=experience,
        )

    outcome = await compare(session_factory, user, job)

    assert [entry.resume_title for entry in outcome.entries] == ["senior.pdf", "junior.pdf"]


async def test_equal_dimensions_use_evidence_confidence_tie_breaker(
    session_factory: SessionFactory,
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")
    job = await create_job(session_factory, user)
    high = await create_resume(session_factory, user, filename="high.pdf")
    medium = await create_resume(session_factory, user, filename="medium.pdf")
    for resume, confidence in ((medium, "medium"), (high, "high")):
        await store_match_row(
            session_factory,
            user,
            job,
            resume,
            overall=70.0,
            matched=["Python"],
            confidence=confidence,
        )

    outcome = await compare(session_factory, user, job)

    assert [entry.resume_title for entry in outcome.entries] == ["high.pdf", "medium.pdf"]


async def test_fully_equal_results_rank_by_creation_date_deterministically(
    session_factory: SessionFactory,
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")
    job = await create_job(session_factory, user)
    older = await create_resume(
        session_factory,
        user,
        filename="older.pdf",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    newer = await create_resume(
        session_factory,
        user,
        filename="newer.pdf",
        created_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
    )
    for resume in (newer, older):
        await store_match_row(
            session_factory,
            user,
            job,
            resume,
            overall=70.0,
            matched=["Python"],
        )

    first = await compare(session_factory, user, job)
    second = await compare(session_factory, user, job)

    expected = ["older.pdf", "newer.pdf"]
    assert [entry.resume_title for entry in first.entries] == expected
    assert [entry.resume_title for entry in second.entries] == expected
    assert [entry.rank for entry in first.entries] == [1, 2]
    assert [entry.rank for entry in second.entries] == [1, 2]


# ---------------------------------------------------------------------------
# Recommendation and close scores
# ---------------------------------------------------------------------------
async def test_close_scores_are_flagged_and_explained(
    session_factory: SessionFactory,
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")
    job = await create_job(session_factory, user)
    first = await create_resume(session_factory, user, filename="first.pdf")
    second = await create_resume(session_factory, user, filename="second.pdf")
    await store_match_row(
        session_factory,
        user,
        job,
        first,
        overall=80.0,
        matched=["Python", "FastAPI", "PostgreSQL", "Docker"],
        experience=90.0,
    )
    await store_match_row(
        session_factory,
        user,
        job,
        second,
        overall=78.0,
        matched=["Python", "FastAPI"],
        experience=40.0,
    )

    outcome = await compare(session_factory, user, job)

    recommendation = outcome.recommendation
    assert recommendation is not None
    assert recommendation.close_call is True
    assert outcome.close_score is True
    assert recommendation.close_call_threshold == 3.0
    assert recommendation.confidence == "medium"
    assert recommendation.close_call_explanation
    assert "within the 3-point close-call threshold" in (
        recommendation.close_call_explanation
    )
    assert "Required-skill coverage differs" in recommendation.close_call_explanation
    assert len(recommendation.alternatives) == 1
    alternative = recommendation.alternatives[0]
    assert alternative.resume_id == second.id
    assert alternative.score_difference == 2.0


async def test_recommendation_explanation_lists_reasons_and_gaps(
    session_factory: SessionFactory,
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")
    job = await create_job(session_factory, user)
    await create_resume(
        session_factory,
        user,
        filename="ada.pdf",
        skills=["Python", "FastAPI"],
        parsed_data={},
    )

    outcome = await compare(session_factory, user, job)

    recommendation = outcome.recommendation
    assert recommendation is not None
    assert recommendation.reasons
    assert recommendation.reasons[0].startswith("Highest overall compatibility score")
    assert any("required skills" in reason for reason in recommendation.reasons)
    assert recommendation.supporting_evidence
    assert recommendation.important_gaps
    assert recommendation.confidence in {"high", "medium", "low"}


async def test_no_suitable_resume_returns_no_recommendation(
    session_factory: SessionFactory,
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")
    job = await create_job(session_factory, user)
    # Parsed, but no skill, experience, title, project or education evidence.
    await create_resume(
        session_factory,
        user,
        filename="empty.pdf",
        skills=[],
        parsed_data={"professional_summary": "Unrelated background."},
    )

    outcome = await compare(session_factory, user, job)

    assert outcome.status == COMPARISON_NO_SUITABLE
    assert outcome.recommendation is not None
    assert outcome.recommendation.recommended is None
    assert outcome.recommendation.unavailable_reason
    assert "enough usable evidence" in (outcome.message or "")
    # The resume is still reported, with its safe state, rather than hidden.
    assert outcome.entries[0].state == RESUME_ANALYSIS_REQUIRED
    assert outcome.entries[0].rank == 1


# ---------------------------------------------------------------------------
# Processing states, partial failure and security boundaries
# ---------------------------------------------------------------------------
async def test_no_uploaded_resumes_is_a_clean_empty_result(
    session_factory: SessionFactory,
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")
    job = await create_job(session_factory, user)

    outcome = await compare(session_factory, user, job)

    assert outcome.status == COMPARISON_NO_RESUMES
    assert outcome.total_resumes == 0
    assert outcome.compared_resumes == 0
    assert outcome.entries == []
    assert outcome.recommendation is None
    assert "Upload a resume" in (outcome.message or "")


async def test_invalid_resume_is_reported_without_failing_the_comparison(
    session_factory: SessionFactory,
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")
    job = await create_job(session_factory, user)
    good = await create_resume(session_factory, user, filename="good.pdf")
    await create_resume(
        session_factory,
        user,
        filename="broken.pdf",
        skills=[],
        parsed_data={},
        parsing_status=ResumeParsingStatus.FAILED,
    )

    outcome = await compare(session_factory, user, job)

    assert outcome.status == COMPARISON_PARTIAL
    assert outcome.compared_resumes == 1
    assert outcome.recommendation is not None
    assert outcome.recommendation.recommended.resume_id == good.id
    assert len(outcome.processing_failures) == 1
    failure = outcome.processing_failures[0]
    assert failure.state == RESUME_INVALID
    assert "parsing status: failed" in failure.reason


async def test_failed_resume_intelligence_is_reported_as_extraction_failure(
    session_factory: SessionFactory,
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")
    job = await create_job(session_factory, user)
    good = await create_resume(session_factory, user, filename="good.pdf")
    failed = await create_resume(
        session_factory, user, filename="failed.pdf", skills=[], parsed_data={}
    )
    await store_resume_intelligence(
        session_factory,
        user,
        failed,
        None,
        status=ROW_STATUS_FAILED,
        error_code="ocr_required",
    )

    outcome = await compare(session_factory, user, job)

    assert outcome.status == COMPARISON_PARTIAL
    failure = outcome.processing_failures[0]
    assert failure.state == RESUME_EXTRACTION_FAILED
    assert "ocr_required" in failure.reason
    assert outcome.recommendation.recommended.resume_id == good.id


async def test_current_resume_intelligence_without_job_intelligence_is_partial(
    session_factory: SessionFactory,
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")
    job = await create_job(session_factory, user)
    resume = await create_resume(session_factory, user)
    await store_resume_intelligence(
        session_factory, user, resume, resume_payload("Python", "FastAPI")
    )

    outcome = await compare(session_factory, user, job)

    entry = outcome.entries[0]
    assert entry.state == RESUME_PARTIAL
    assert entry.rank == 1
    assert "partially available intelligence" in entry.reason


async def test_complete_intelligence_produces_ready_state(
    session_factory: SessionFactory,
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")
    job = await create_job(session_factory, user)
    resume = await create_resume(session_factory, user)
    await store_job_intelligence(session_factory, user, job)
    await store_resume_intelligence(
        session_factory, user, resume, resume_payload("Python", "FastAPI")
    )

    outcome = await compare(session_factory, user, job)

    assert outcome.entries[0].state == RESUME_READY
    assert outcome.entries[0].match_processing_state == STATE_READY


async def test_deleted_resume_disappears_from_the_comparison(
    session_factory: SessionFactory,
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")
    job = await create_job(session_factory, user)
    keep = await create_resume(session_factory, user, filename="keep.pdf")
    remove = await create_resume(session_factory, user, filename="remove.pdf")

    first = await compare(session_factory, user, job)
    assert first.total_resumes == 2

    async with session_factory() as session:
        stored = await session.get(Resume, remove.id)
        assert stored is not None
        await session.delete(stored)
        await session.commit()

    second = await compare(session_factory, user, job)

    assert second.total_resumes == 1
    assert [entry.resume_id for entry in second.entries] == [keep.id]
    # The orphaned match row is not silently retained.
    assert await match_row_count(session_factory, job) == 1


async def test_cross_user_resumes_are_never_compared(
    session_factory: SessionFactory,
) -> None:
    owner = await create_user(session_factory, email="owner@example.com", google_sub="g-o")
    other = await create_user(session_factory, email="other@example.com", google_sub="g-o2")
    job = await create_job(session_factory, owner)
    await create_resume(session_factory, owner, filename="owner.pdf")
    await create_resume(session_factory, other, filename="other.pdf")

    outcome = await compare(session_factory, owner, job)

    titles = [entry.resume_title for entry in outcome.entries]
    assert titles == ["owner.pdf"]
    assert "other.pdf" not in titles


async def test_cross_user_job_access_is_not_found(
    session_factory: SessionFactory,
) -> None:
    owner = await create_user(session_factory, email="owner@example.com", google_sub="g-o")
    other = await create_user(session_factory, email="other@example.com", google_sub="g-o2")
    job = await create_job(session_factory, owner)
    await create_resume(session_factory, owner)

    async with session_factory() as session:
        with pytest.raises(JobNotFoundError):
            await ResumeComparisonService(session).compare(other, job.id)


# ---------------------------------------------------------------------------
# Caching and duplicate prevention
# ---------------------------------------------------------------------------
async def test_unchanged_inputs_reuse_cached_matches(
    session_factory: SessionFactory,
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")
    job = await create_job(session_factory, user)
    await create_resume(session_factory, user)

    first = await compare(session_factory, user, job)
    first_computed = first.entries[0].row.computed_at
    first_score = first.entries[0].overall_score
    second = await compare(session_factory, user, job)

    assert _instant(second.entries[0].row.computed_at) == _instant(first_computed)
    assert second.entries[0].overall_score == first_score
    assert second.recommendation.recommended.resume_id == (
        first.recommendation.recommended.resume_id
    )


async def test_changed_resume_content_invalidates_the_cached_comparison(
    session_factory: SessionFactory,
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")
    job = await create_job(session_factory, user)
    resume = await create_resume(
        session_factory, user, filename="ada.pdf", skills=["Python"], parsed_data={}
    )

    first = await compare(session_factory, user, job)
    assert first.entries[0].row.missing_required_skills

    async with session_factory() as session:
        stored = await session.get(Resume, resume.id)
        assert stored is not None
        stored.parsed_data = {**STRONG_PARSED}
        stored.skills = ["Python", "FastAPI", "PostgreSQL", "Docker"]
        stored.checksum = uuid.uuid4().hex + uuid.uuid4().hex
        await session.commit()

    second = await compare(session_factory, user, job)

    assert _instant(second.entries[0].row.computed_at) > _instant(
        first.entries[0].row.computed_at
    )
    assert second.entries[0].row.missing_required_skills == []
    assert second.entries[0].overall_score > first.entries[0].overall_score


async def test_repeated_comparisons_do_not_duplicate_match_rows(
    session_factory: SessionFactory,
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")
    job = await create_job(session_factory, user)
    await create_resume(session_factory, user, filename="one.pdf")
    await create_resume(session_factory, user, filename="two.pdf")

    await compare(session_factory, user, job)
    await compare(session_factory, user, job)
    await compare(session_factory, user, job, force=True)

    assert await match_row_count(session_factory, job) == 2
    async with session_factory() as session:
        rows = list(
            (
                await session.scalars(
                    select(JobResumeMatch).where(JobResumeMatch.job_id == job.id)
                )
            ).all()
        )
    assert len({row.resume_id for row in rows}) == 2


# ---------------------------------------------------------------------------
# HTTP API
# ---------------------------------------------------------------------------
async def test_compare_endpoint_returns_a_recommendation(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")
    job = await create_job(session_factory, user)
    strongest = await create_resume(session_factory, user, filename="strongest.pdf")
    await create_resume(
        session_factory,
        user,
        filename="weaker.pdf",
        skills=["Python"],
        parsed_data=WEAK_PARSED,
    )

    response = await client.post(
        f"{JOBS}/{job.id}/compare-resumes", json={}, headers=bearer(user)
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["job_id"] == str(job.id)
    assert body["status"] == COMPARISON_COMPLETED
    assert body["total_resumes"] == 2
    assert body["compared_resumes"] == 2
    assert body["algorithm_version"] == MATCHING_ALGORITHM_VERSION
    assert body["close_score"] is False
    assert body["close_score_threshold"] == 3.0
    assert body["recommendation"]["resume_id"] == str(strongest.id)
    assert body["recommendation"]["reasons"]
    assert [entry["rank"] for entry in body["resumes"]] == [1, 2]
    assert body["resumes"][0]["processing_state"] == RESUME_ANALYSIS_REQUIRED
    assert body["resumes"][0]["required_skill_coverage"] is not None
    assert body["processing_failures"] == []

    # Security: no storage keys, file content or parsed payloads.
    serialized = json.dumps(body)
    assert "storage_key" not in serialized
    assert "users/" not in serialized
    assert "%PDF" not in serialized
    assert "parsed_data" not in serialized


async def test_recommendation_endpoint_reports_not_computed(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")
    job = await create_job(session_factory, user)
    await create_resume(session_factory, user)

    response = await client.get(f"{JOBS}/{job.id}/recommendation", headers=bearer(user))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == COMPARISON_NOT_COMPUTED
    assert body["recommendation"] is None
    assert body["resumes"][0]["processing_state"] == RESUME_ANALYSIS_REQUIRED
    assert "compare-resumes" in (body["message"] or "")


async def test_recommendation_endpoint_reuses_stored_matches(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")
    job = await create_job(session_factory, user)
    await create_resume(session_factory, user)

    computed = await client.post(
        f"{JOBS}/{job.id}/compare-resumes", json={}, headers=bearer(user)
    )
    assert computed.status_code == 200, computed.text
    stored_at = computed.json()["resumes"][0]
    assert stored_at["overall_score"] > 0

    read = await client.get(f"{JOBS}/{job.id}/recommendation", headers=bearer(user))

    assert read.status_code == 200, read.text
    body = read.json()
    assert body["status"] == COMPARISON_COMPLETED
    assert body["recommendation"]["resume_id"] == (
        computed.json()["recommendation"]["resume_id"]
    )
    assert await match_row_count(session_factory, job) == 1


async def test_compare_endpoint_rejects_cross_user_job_access(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    owner = await create_user(session_factory, email="owner@example.com", google_sub="g-o")
    other = await create_user(session_factory, email="other@example.com", google_sub="g-o2")
    job = await create_job(session_factory, owner)

    response = await client.post(
        f"{JOBS}/{job.id}/compare-resumes", json={}, headers=bearer(other)
    )
    read = await client.get(f"{JOBS}/{job.id}/recommendation", headers=bearer(other))

    assert response.status_code == 404
    assert read.status_code == 404


async def test_compare_endpoint_requires_authentication(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")
    job = await create_job(session_factory, user)

    response = await client.post(f"{JOBS}/{job.id}/compare-resumes", json={})

    assert response.status_code == 401
