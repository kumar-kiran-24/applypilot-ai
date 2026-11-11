"""Tests for Phase 2F.2.3 evidence-based advanced matching.

Two layers are covered:

* the pure engine (:func:`match_resume_with_evidence`) with synthetic
  intelligence payloads - deterministic, no database and no LLM;
* the persisted service path (``MatchingService``) for processing states,
  cache invalidation, ownership and API compatibility.

All data is synthetic (Ada Lovelace style test fixtures). No real candidate
information is used anywhere.
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
    ResumeCertificationFact,
    ResumeEducationFact,
    ResumeExperienceFact,
    ResumeIntelligenceFields,
    ResumeIntelligencePayload,
    ResumeProjectFact,
    ResumeSkillFact,
)
from app.services.advanced_matching_engine import (
    EVIDENCE_CERTIFICATION,
    EVIDENCE_EXPERIENCE,
    EVIDENCE_REQUIRED_SKILL,
    MATCHING_ALGORITHM_VERSION,
    STATE_INCOMPLETE_JOB,
    STATE_INSUFFICIENT,
    STATE_PARTIAL,
    STATE_READY,
    STATE_RESUME_FAILED,
    STATUS_CURRENT,
    STATUS_FAILED,
    STATUS_MISSING,
    STATUS_STALE,
    match_resume_with_evidence,
)
from app.services.job_intelligence_extraction import (
    EXTRACTOR_VERSION as JOB_INTELLIGENCE_VERSION,
)
from app.services.job_requirements import build_job_requirements
from app.services.job_service import JobNotFoundError
from app.services.job_understanding_service import job_intelligence_fingerprint
from app.services.matching_engine import (
    DIMENSION_WEIGHTS,
    EducationEntry,
    ExperienceEntry,
    ProjectEntry,
    ResumeProfile,
    normalize_skill_key,
)
from app.services.matching_service import MatchingService
from app.services.resume_intelligence_extraction import (
    EXTRACTOR_VERSION as RESUME_INTELLIGENCE_VERSION,
)
from app.services.resume_intelligence_service import resume_content_fingerprint
from httpx import AsyncClient
from sqlalchemy import update
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


# ---------------------------------------------------------------------------
# Synthetic profiles and payloads (pure-engine tests)
# ---------------------------------------------------------------------------
def requirements(
    *, description: str | None = STRONG_JD, title: str = "Senior Backend Engineer"
) -> Any:
    return build_job_requirements(
        job_title=title,
        company="Acme",
        location="Remote",
        description=description,
    )


def entry(
    title: str,
    start: str | None,
    end: str | None,
    *,
    current: bool = False,
    skills: tuple[str, ...] = (),
    description: str = "",
) -> ExperienceEntry:
    return ExperienceEntry(
        title=title,
        company="Acme",
        description=description,
        technologies=skills,
        start_date=start,
        end_date=end,
        is_current=current,
        skill_keys=frozenset(normalize_skill_key(skill) for skill in skills),
    )


def profile(
    *,
    skills: tuple[str, ...] = (),
    experience: tuple[ExperienceEntry, ...] = (),
    education: tuple[EducationEntry, ...] = (),
    projects: tuple[ProjectEntry, ...] = (),
    summary: str | None = None,
    filename: str = "resume.pdf",
) -> ResumeProfile:
    return ResumeProfile(
        resume_id="resume-1",
        filename=filename,
        skills=skills,
        skill_keys=frozenset(normalize_skill_key(skill) for skill in skills),
        summary=summary,
        titles=tuple(item.title for item in experience if item.title),
        experience=experience,
        education=education,
        projects=projects,
        certifications=(),
    )


def job_payload(
    *,
    required: tuple[str, ...] = (),
    preferred: tuple[str, ...] = (),
    years: float | None = None,
    education: str | None = None,
    field_of_study: str | None = None,
    certifications: tuple[str, ...] = (),
    confidence: str = "high",
    title: str = "Senior Backend Engineer",
) -> JobIntelligencePayload:
    return JobIntelligencePayload(
        fields=JobIntelligenceFields(
            job_title=title,
            required_skills=list(required),
            preferred_skills=list(preferred),
            experience_years=years,
            education_level=education,
            education_field=field_of_study,
            required_certifications=list(certifications),
        ),
        confidence=confidence,
        provenance={"required_skills": "deterministic"},
    )


def resume_payload(
    *,
    skills: tuple[str, ...] = (),
    experience: tuple[ResumeExperienceFact, ...] = (),
    projects: tuple[ResumeProjectFact, ...] = (),
    education: tuple[ResumeEducationFact, ...] = (),
    certifications: tuple[ResumeCertificationFact, ...] = (),
    total_months: int | None = None,
    has_dated: bool = False,
    confidence: str = "high",
) -> ResumeIntelligencePayload:
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
            experience=list(experience),
            projects=list(projects),
            education=list(education),
            certifications=list(certifications),
            total_experience_months=total_months,
            has_dated_experience=has_dated,
        ),
        confidence=confidence,
    )


def evaluate(
    resume: ResumeProfile,
    *,
    job: JobIntelligencePayload | None = None,
    job_state: str = STATUS_CURRENT,
    intel: ResumeIntelligencePayload | None = None,
    intel_state: str = STATUS_CURRENT,
    reqs: Any | None = None,
) -> Any:
    return match_resume_with_evidence(
        reqs if reqs is not None else requirements(),
        resume,
        as_of=AS_OF,
        job_intelligence=job,
        job_intelligence_current=job_state == STATUS_CURRENT,
        job_intelligence_state=job_state,
        resume_intelligence=intel,
        resume_intelligence_current=intel_state == STATUS_CURRENT,
        resume_intelligence_state=intel_state,
    )


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------
def test_exact_required_skill_match_is_evidenced() -> None:
    job = job_payload(required=("Python", "FastAPI"))
    intel = resume_payload(skills=("Python", "FastAPI"))

    result = evaluate(
        profile(skills=("Python", "FastAPI")),
        job=job,
        intel=intel,
        reqs=requirements(description=None),
    )

    assert result.skill_score == 100.0
    assert result.matched_required_skills == ("FastAPI", "Python")
    assert result.missing_required_skills == ()
    assert result.missing_evidence == ()
    required = [item for item in result.evidence if item.category == EVIDENCE_REQUIRED_SKILL]
    assert {item.description.split(" is ")[0] for item in required} == {"Python", "FastAPI"}
    assert all(item.match_reason == "Exact skill match." for item in required)
    assert all(item.resume_reference == "skills section" for item in required)


def test_skill_aliases_match_equivalent_technologies() -> None:
    job = job_payload(required=("JavaScript", "Node.js", "Machine Learning"))
    intel = resume_payload(skills=("JS", "Node", "ML"))

    reqs = requirements(description=None, title="Machine Learning Engineer")
    result = evaluate(profile(skills=("JS", "Node", "ML")), job=job, intel=intel, reqs=reqs)

    assert result.skill_score == 100.0
    assert result.missing_required_skills == ()
    aliases = [
        item for item in result.evidence if "Equivalent skill match" in item.match_reason
    ]
    assert len(aliases) == 3
    assert "unrelated" not in " ".join(result.missing_evidence)


def test_duplicate_skills_are_not_double_counted() -> None:
    job = job_payload(required=("Python",))
    intel = resume_payload(
        skills=("Python", "Python", "python"),
        experience=(
            ResumeExperienceFact(
                role="Backend Engineer",
                technologies=["Python"],
                responsibilities=["Wrote Python services every day."],
            ),
        ),
    )
    reqs = requirements(description=None)

    result = evaluate(profile(skills=("Python", "Python")), job=job, intel=intel, reqs=reqs)

    assert result.skill_score == 100.0
    assert result.matched_required_skills == ("Python",)
    required_evidence = [
        item for item in result.evidence if item.category == EVIDENCE_REQUIRED_SKILL
    ]
    assert len(required_evidence) == 1


def test_preferred_skills_keep_the_established_bonus() -> None:
    job = job_payload(
        required=("Python", "FastAPI", "PostgreSQL", "Docker"),
        preferred=("AWS", "Kubernetes"),
    )
    reqs = requirements(description=None)
    base = evaluate(
        profile(skills=("Python", "FastAPI", "PostgreSQL", "Docker")),
        job=job,
        reqs=reqs,
    )
    with_preferred = evaluate(
        profile(skills=("Python", "FastAPI", "PostgreSQL", "Docker", "AWS", "Kubernetes")),
        job=job,
        intel=resume_payload(
            skills=("Python", "FastAPI", "PostgreSQL", "Docker", "AWS", "Kubernetes")
        ),
        reqs=reqs,
    )

    assert base.skill_score == 85.0
    assert with_preferred.skill_score == 100.0
    assert with_preferred.overall_score > base.overall_score
    assert DIMENSION_WEIGHTS == {
        "skills": 0.40,
        "experience": 0.25,
        "title": 0.15,
        "projects": 0.10,
        "education": 0.10,
    }


# ---------------------------------------------------------------------------
# Experience, title, projects, education and certifications
# ---------------------------------------------------------------------------
def test_experience_uses_dated_roles_and_flags_internships() -> None:
    job = job_payload(years=5)
    intel = resume_payload(
        experience=(
            ResumeExperienceFact(
                role="Backend Intern",
                company="Acme",
                start_date="Jan 2024",
                end_date="Jul 2024",
                duration_months=6,
                technologies=["Python"],
            ),
            ResumeExperienceFact(
                role="Backend Engineer",
                company="Babbage Systems",
                start_date="Jul 2024",
                end_date=None,
                is_current=True,
                duration_months=60,
                technologies=["FastAPI"],
            ),
        ),
        total_months=66,
        has_dated=True,
    )
    reqs = requirements(
        description="Requirements:\n- 5+ years of experience\n- Python and FastAPI\n"
    )

    result = evaluate(profile(), job=job, intel=intel, reqs=reqs)

    assert result.experience_score == 100.0
    assert result.details["estimated_experience_years"] == 5.5
    internship = [
        item
        for item in result.evidence
        if item.category == EVIDENCE_EXPERIENCE and "internship" in item.description
    ]
    assert len(internship) == 1
    assert "Python" in internship[0].match_reason


def test_experience_without_dates_is_never_an_invented_year_count() -> None:
    job = job_payload(years=5)
    intel = resume_payload(
        experience=(
            ResumeExperienceFact(role="Backend Engineer", technologies=["Python"]),
        ),
        total_months=None,
        has_dated=False,
    )
    reqs = requirements(
        description="Requirements:\n- 5+ years of experience\n- Python\n"
    )

    result = evaluate(profile(), job=job, intel=intel, reqs=reqs)

    assert result.experience_score == 40.0
    assert result.details["estimated_experience_years"] is None
    assert any("no usable dates" in item for item in result.missing_evidence)


def test_project_relevance_comes_from_project_evidence() -> None:
    job = job_payload(required=("FastAPI", "PostgreSQL"))
    intel = resume_payload(
        skills=("Python",),
        projects=(
            ResumeProjectFact(
                title="Payments API",
                description="Internal service.",
                technologies=["FastAPI", "PostgreSQL"],
                demonstrated_skills=["FastAPI", "PostgreSQL"],
            ),
        ),
    )
    reqs = requirements(description=None)

    result = evaluate(profile(), job=job, intel=intel, reqs=reqs)

    assert result.project_score == 100.0
    project = [item for item in result.evidence if item.category == "project"]
    assert len(project) == 1
    assert project[0].description == "Project 'Payments API'."
    assert "FastAPI" in project[0].match_reason


def test_job_title_alignment_uses_designations() -> None:
    job = job_payload()
    intel = resume_payload(
        experience=(ResumeExperienceFact(role="Senior Backend Engineer"),),
    )
    reqs = requirements(description=None)

    result = evaluate(profile(), job=job, intel=intel, reqs=reqs)

    assert result.title_score == 100.0


def test_project_name_gives_bounded_partial_title_credit() -> None:
    job = job_payload(title="Data Engineer")
    intel = resume_payload(
        projects=(ResumeProjectFact(title="Data Pipeline", technologies=["Python"]),),
    )
    reqs = requirements(description=None, title="Data Engineer")

    result = evaluate(profile(), job=job, intel=intel, reqs=reqs)

    assert result.title_score == 25.0


def test_education_meets_level_and_specialization() -> None:
    job = job_payload(education="bachelor", field_of_study="Computer Science")
    intel = resume_payload(
        education=(
            ResumeEducationFact(
                institution="University of London",
                degree="Master of Science",
                specialization="Computer Science",
            ),
        ),
    )
    reqs = requirements(description=None)

    result = evaluate(profile(), job=job, intel=intel, reqs=reqs)

    assert result.education_score == 100.0
    assert any(item.category == "education" for item in result.evidence)


def test_missing_education_is_unknown_not_a_disqualification() -> None:
    job = job_payload(education="bachelor")
    reqs = requirements(description=None)

    result = evaluate(profile(), job=job, reqs=reqs)

    assert result.education_score == 0.0
    assert any(
        "unknown, not a finding" in item for item in result.missing_evidence
    )


def test_certifications_support_but_do_not_change_scores() -> None:
    job = job_payload(
        required=("Python",),
        certifications=(
            "AWS Certified Solutions Architect",
            "Certified Kubernetes Administrator",
        ),
    )
    resume_cert = ResumeCertificationFact(name="AWS Certified Solutions Architect")
    intel = resume_payload(skills=("Python",), certifications=(resume_cert,))
    base = evaluate(
        profile(skills=("Python",)),
        job=job_payload(required=("Python",)),
        intel=resume_payload(skills=("Python",)),
        reqs=requirements(description=None),
    )

    result = evaluate(
        profile(skills=("Python",)), job=job, intel=intel, reqs=requirements(description=None)
    )

    assert result.overall_score == base.overall_score
    assert result.skill_score == base.skill_score
    cert_evidence = [
        item for item in result.evidence if item.category == EVIDENCE_CERTIFICATION
    ]
    assert len(cert_evidence) == 1
    assert "AWS Certified Solutions Architect" in cert_evidence[0].description
    assert any(
        "Certified Kubernetes Administrator" in item
        for item in result.missing_evidence
    )


# ---------------------------------------------------------------------------
# Missing evidence, confidence, overall score and double counting
# ---------------------------------------------------------------------------
def test_missing_required_skills_are_reported_as_missing_evidence() -> None:
    job = job_payload(required=("Python", "Kubernetes"))
    intel = resume_payload(skills=("Python",))
    reqs = requirements(description=None)

    result = evaluate(profile(skills=("Python",)), job=job, intel=intel, reqs=reqs)

    assert result.matched_required_skills == ("Python",)
    assert result.missing_required_skills == ("Kubernetes",)
    assert "Required skill 'Kubernetes' is not stated on this resume." in result.missing_evidence
    assert any(item.startswith("Required skills:") for item in result.gaps)
    assert any("does not mention it" in item for item in result.suggestions)


def test_confidence_reflects_input_quality() -> None:
    job = job_payload(required=("Python",))
    intel = resume_payload(skills=("Python",))
    reqs = requirements(
        description="Requirements:\n- Python\n- 1+ years of experience\n"
    )
    strong = evaluate(profile(skills=("Python",)), job=job, intel=intel, reqs=reqs)

    failed = evaluate(
        profile(),
        job=job,
        intel=None,
        intel_state=STATUS_FAILED,
        reqs=reqs,
    )

    assert strong.confidence == "high"
    assert failed.confidence == "low"
    assert failed.processing_state == STATE_RESUME_FAILED


def test_overall_score_is_the_weighted_category_combination() -> None:
    job = job_payload(
        required=("Python",),
        preferred=("AWS",),
        years=4,
        education="bachelor",
    )
    intel = resume_payload(
        skills=("Python",),
        experience=(
            ResumeExperienceFact(
                role="Senior Backend Engineer",
                start_date="Jan 2022",
                end_date=None,
                is_current=True,
                duration_months=48,
                technologies=["Python"],
            ),
        ),
        education=(ResumeEducationFact(degree="Bachelor of Science"),),
        total_months=48,
        has_dated=True,
    )
    reqs = requirements(description=None)

    result = evaluate(profile(skills=("Python",)), job=job, intel=intel, reqs=reqs)

    scores = {
        "skills": result.skill_score,
        "experience": result.experience_score,
        "education": result.education_score,
        "title": result.title_score,
        "projects": result.project_score,
    }
    available = {key: value for key, value in scores.items() if value is not None}
    total_weight = sum(DIMENSION_WEIGHTS[key] for key in available)
    expected = (
        sum(DIMENSION_WEIGHTS[key] * value for key, value in available.items())
        / total_weight
    )
    assert abs(result.overall_score - expected) < 0.15
    assert 0.0 <= result.overall_score <= 100.0
    assert result.details["effective_weights"] == {
        key: round(DIMENSION_WEIGHTS[key] / total_weight, 4) for key in available
    }


def test_a_skill_is_scored_once_even_when_projects_mention_it() -> None:
    job = job_payload(required=("FastAPI",))
    intel = resume_payload(
        skills=("FastAPI",),
        projects=(ResumeProjectFact(title="API", technologies=["FastAPI"]),),
    )
    reqs = requirements(description=None)

    result = evaluate(profile(skills=("FastAPI",)), job=job, intel=intel, reqs=reqs)

    assert result.matched_required_skills == ("FastAPI",)
    assert result.skill_score == 100.0
    required_evidence = [
        item for item in result.evidence if item.category == EVIDENCE_REQUIRED_SKILL
    ]
    assert len(required_evidence) == 1
    # The project dimension is independent evidence, not a second skill credit.
    assert result.project_score == 100.0


def test_processing_states_cover_missing_and_insufficient_inputs() -> None:
    partial = evaluate(
        profile(skills=("Python",)), job_state=STATUS_MISSING, reqs=requirements()
    )
    stale = evaluate(
        profile(skills=("Python",)),
        job=job_payload(required=("Python",)),
        job_state=STATUS_STALE,
        reqs=requirements(),
    )
    insufficient = evaluate(
        profile(), job_state=STATUS_MISSING, reqs=requirements()
    )
    incomplete = evaluate(
        profile(skills=("Python",)),
        job_state=STATUS_MISSING,
        reqs=requirements(description=None),
    )

    assert partial.processing_state == STATE_PARTIAL
    assert stale.processing_state == STATE_PARTIAL
    assert insufficient.processing_state == STATE_INSUFFICIENT
    # A posting with no description and no intelligence cannot be evaluated.
    assert incomplete.processing_state == STATE_INCOMPLETE_JOB
    assert partial.details["inputs"]["job_intelligence"] == STATUS_MISSING
    assert stale.details["inputs"]["job_intelligence"] == STATUS_STALE


# ---------------------------------------------------------------------------
# Database/service helpers
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
            parsing_status=ResumeParsingStatus.COMPLETED,
            parsed_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
            parsed_data=parsed_data if parsed_data is not None else STRONG_PARSED,
            skills=skills if skills is not None else ["Python", "FastAPI", "PostgreSQL", "Docker"],
        )
        session.add(resume)
        await session.commit()
        await session.refresh(resume)
        return resume


async def store_job_intelligence(
    session_factory: SessionFactory,
    user: User,
    job: Job,
    payload: JobIntelligencePayload,
    *,
    extractor_version: str = JOB_INTELLIGENCE_VERSION,
    fingerprint: str | None = None,
) -> JobIntelligence:
    async with session_factory() as session:
        row = JobIntelligence(
            user_id=user.id,
            job_id=job.id,
            extractor_version=extractor_version,
            job_fingerprint=fingerprint or job_intelligence_fingerprint(job),
            confidence=payload.confidence,
            llm_used=False,
            llm_status="disabled",
            data=payload.model_dump(mode="json"),
            extracted_at=datetime.now(timezone.utc),
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row


async def store_resume_intelligence(
    session_factory: SessionFactory,
    user: User,
    resume: Resume,
    payload: ResumeIntelligencePayload | None,
    *,
    status: str = STATUS_COMPLETED,
    error_code: str | None = None,
    extractor_version: str = RESUME_INTELLIGENCE_VERSION,
    fingerprint: str | None = None,
) -> ResumeIntelligence:
    async with session_factory() as session:
        row = ResumeIntelligence(
            user_id=user.id,
            resume_id=resume.id,
            extractor_version=extractor_version,
            content_fingerprint=fingerprint or resume_content_fingerprint(resume),
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


def _instant(value: datetime) -> datetime:
    """Drop tzinfo: SQLite returns naive datetimes for stored values."""
    return value.replace(tzinfo=None)


async def match_rows(
    session_factory: SessionFactory, user: User, job: Job, *, force: bool = False
) -> list[Any]:
    async with session_factory() as session:
        service = MatchingService(session)
        return await service.ensure_job_matches(user, job, force=force, as_of=AS_OF)


# ---------------------------------------------------------------------------
# Persisted service: states, caching, freshness and ownership
# ---------------------------------------------------------------------------
async def test_missing_job_intelligence_is_labelled_partial(
    session_factory: SessionFactory,
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")
    job = await create_job(session_factory, user)
    await create_resume(session_factory, user)

    rows = await match_rows(session_factory, user, job)

    details = rows[0].details
    assert details["processing_state"] == STATE_PARTIAL
    assert details["inputs"]["job_intelligence"] == STATUS_MISSING
    assert details["inputs"]["resume_intelligence"] == STATUS_MISSING
    assert rows[0].matched_required_skills


async def test_missing_resume_intelligence_falls_back_to_parsed_data(
    session_factory: SessionFactory,
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")
    job = await create_job(session_factory, user)
    await create_resume(
        session_factory, user, skills=["Python", "FastAPI"], parsed_data={}
    )

    rows = await match_rows(session_factory, user, job)

    assert rows[0].matched_required_skills == ["FastAPI", "Python"]
    assert rows[0].details["inputs"]["resume_intelligence"] == STATUS_MISSING
    assert rows[0].details["processing_state"] == STATE_PARTIAL


async def test_current_intelligence_produces_ready_state_and_is_used(
    session_factory: SessionFactory,
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")
    # No usable description: only job intelligence can supply requirements.
    job = await create_job(session_factory, user, description=None)
    resume = await create_resume(
        session_factory, user, skills=[], parsed_data={}
    )
    await store_job_intelligence(
        session_factory,
        user,
        job,
        job_payload(required=("Python", "FastAPI"), education="bachelor"),
    )
    await store_resume_intelligence(
        session_factory,
        user,
        resume,
        resume_payload(skills=("Python", "FastAPI"), education=()),
    )

    rows = await match_rows(session_factory, user, job)

    details = rows[0].details
    assert details["processing_state"] == STATE_READY
    assert details["inputs"]["job_intelligence"] == STATUS_CURRENT
    assert details["inputs"]["resume_intelligence"] == STATUS_CURRENT
    assert rows[0].matched_required_skills == ["FastAPI", "Python"]
    assert details["evidence"]


async def test_stale_job_intelligence_is_not_used(
    session_factory: SessionFactory,
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")
    job = await create_job(session_factory, user, description=None)
    resume = await create_resume(session_factory, user, skills=["Python"], parsed_data={})
    await store_job_intelligence(
        session_factory,
        user,
        job,
        job_payload(required=("Python",)),
        fingerprint="0" * 64,
    )
    await store_resume_intelligence(
        session_factory, user, resume, resume_payload(skills=("Python",))
    )

    rows = await match_rows(session_factory, user, job)

    assert rows[0].details["inputs"]["job_intelligence"] == STATUS_STALE
    # A description-less posting whose intelligence is unusable cannot be
    # evaluated from the posting at all, and says so instead of guessing.
    assert rows[0].details["processing_state"] == STATE_INCOMPLETE_JOB
    assert rows[0].matched_required_skills == []


async def test_stale_resume_intelligence_uses_parsed_data_instead(
    session_factory: SessionFactory,
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")
    job = await create_job(session_factory, user)
    resume = await create_resume(
        session_factory, user, skills=["Python"], parsed_data={}
    )
    await store_resume_intelligence(
        session_factory, user, resume, resume_payload(skills=("Python",)), fingerprint="f" * 64
    )

    rows = await match_rows(session_factory, user, job)

    assert rows[0].details["inputs"]["resume_intelligence"] == STATUS_STALE
    assert rows[0].matched_required_skills == ["Python"]


async def test_failed_resume_extraction_is_reported_and_not_scored_misleadingly(
    session_factory: SessionFactory,
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")
    job = await create_job(session_factory, user)
    resume = await create_resume(session_factory, user, skills=[], parsed_data={})
    await store_resume_intelligence(
        session_factory,
        user,
        resume,
        None,
        status=ROW_STATUS_FAILED,
        error_code="ocr_required",
    )

    rows = await match_rows(session_factory, user, job)

    assert rows[0].details["processing_state"] == STATE_RESUME_FAILED
    assert rows[0].details["inputs"]["resume_error_code"] == "ocr_required"
    assert rows[0].confidence == "low"
    assert rows[0].overall_score == 0.0


async def test_unchanged_inputs_reuse_the_cached_result(
    session_factory: SessionFactory,
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")
    job = await create_job(session_factory, user)
    await create_resume(session_factory, user)

    first = (await match_rows(session_factory, user, job))[0]
    second = (await match_rows(session_factory, user, job))[0]

    # SQLite returns naive timestamps; only the instant matters here.
    assert _instant(first.computed_at) == _instant(second.computed_at)
    assert first.overall_score == second.overall_score


async def test_resume_content_change_invalidates_the_cached_match(
    session_factory: SessionFactory,
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")
    job = await create_job(session_factory, user)
    resume = await create_resume(
        session_factory, user, skills=["Python"], parsed_data={}
    )

    first = (await match_rows(session_factory, user, job))[0]
    assert first.missing_required_skills

    async with session_factory() as session:
        stored = await session.get(Resume, resume.id)
        assert stored is not None
        stored.parsed_data = {**STRONG_PARSED}
        stored.skills = ["Python", "FastAPI", "PostgreSQL", "Docker"]
        stored.checksum = uuid.uuid4().hex + uuid.uuid4().hex
        await session.commit()

    second = (await match_rows(session_factory, user, job))[0]

    assert _instant(second.computed_at) > _instant(first.computed_at)
    assert second.missing_required_skills == []


async def test_new_resume_intelligence_invalidates_the_cached_match(
    session_factory: SessionFactory,
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")
    job = await create_job(session_factory, user)
    resume = await create_resume(session_factory, user, skills=["Python"])

    first = (await match_rows(session_factory, user, job))[0]
    assert first.details["inputs"]["resume_intelligence"] == STATUS_MISSING

    await store_resume_intelligence(
        session_factory, user, resume, resume_payload(skills=("Python", "FastAPI"))
    )
    second = (await match_rows(session_factory, user, job))[0]

    assert _instant(second.computed_at) > _instant(first.computed_at)
    assert second.details["inputs"]["resume_intelligence"] == STATUS_CURRENT


async def test_intelligence_extractor_version_change_invalidates(
    session_factory: SessionFactory,
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")
    job = await create_job(session_factory, user)
    resume = await create_resume(session_factory, user, skills=["Python"])
    await store_resume_intelligence(
        session_factory, user, resume, resume_payload(skills=("Python", "FastAPI"))
    )

    first = (await match_rows(session_factory, user, job))[0]
    assert first.details["inputs"]["resume_intelligence"] == STATUS_CURRENT

    async with session_factory() as session:
        await session.execute(
            update(ResumeIntelligence)
            .where(ResumeIntelligence.resume_id == resume.id)
            .values(extractor_version="0.0.0")
        )
        await session.commit()

    second = (await match_rows(session_factory, user, job))[0]

    assert _instant(second.computed_at) > _instant(first.computed_at)
    assert second.details["inputs"]["resume_intelligence"] == STATUS_STALE


async def test_matching_algorithm_version_change_invalidates(
    session_factory: SessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")
    job = await create_job(session_factory, user)
    await create_resume(session_factory, user)

    first = (await match_rows(session_factory, user, job))[0]

    monkeypatch.setattr(
        "app.services.matching_service.MATCHING_ALGORITHM_VERSION", "test-version"
    )
    second = (await match_rows(session_factory, user, job))[0]

    assert _instant(second.computed_at) > _instant(first.computed_at)
    assert first.details["algorithm_version"] == MATCHING_ALGORITHM_VERSION
    assert second.details["algorithm_version"] == MATCHING_ALGORITHM_VERSION


async def test_ownership_isolation_for_matching_and_intelligence(
    session_factory: SessionFactory,
) -> None:
    owner = await create_user(session_factory, email="owner@example.com", google_sub="g-o")
    other = await create_user(session_factory, email="other@example.com", google_sub="g-x")
    job = await create_job(session_factory, owner)
    await create_resume(session_factory, owner)
    other_resume = await create_resume(
        session_factory, other, filename="other.pdf", skills=["Kubernetes"]
    )
    await store_resume_intelligence(
        session_factory,
        other,
        other_resume,
        resume_payload(skills=("Kubernetes",)),
    )

    owner_rows = await match_rows(session_factory, owner, job)

    assert owner_rows[0].details["inputs"]["resume_intelligence"] == STATUS_MISSING
    assert "Kubernetes" not in owner_rows[0].matched_required_skills

    async with session_factory() as session:
        with pytest.raises(JobNotFoundError):
            await MatchingService(session).match_job(other, job.id)


async def test_api_response_extends_backward_compatibly(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")
    job = await create_job(session_factory, user)
    resume = await create_resume(session_factory, user)

    response = await client.post(
        f"{JOBS}/{job.id}/match", json={}, headers=bearer(user)
    )

    assert response.status_code == 200, response.text
    row = response.json()["matches"][0]
    # Pre-existing fields are unchanged.
    assert row["resume_id"] == str(resume.id)
    assert row["overall_score"] > 0
    assert row["scores"]["skills"] is not None
    assert row["matched_required_skills"] == ["Docker", "FastAPI", "PostgreSQL", "Python"]
    assert row["explanation"] and row["confidence"]
    # Phase 2F.2.3 additions.
    assert row["processing_state"] in {
        STATE_READY,
        STATE_PARTIAL,
        STATE_INSUFFICIENT,
        STATE_RESUME_FAILED,
        STATE_INCOMPLETE_JOB,
    }
    assert row["algorithm_version"] == MATCHING_ALGORITHM_VERSION
    assert row["evidence"]
    assert isinstance(row["missing_evidence"], list)
    assert {"category", "description", "resume_reference", "confidence", "match_reason"} <= set(
        row["evidence"][0]
    )
    assert row["gaps"]

    body = json.dumps(response.json())
    assert resume.storage_key not in body
    assert "%PDF" not in body
    assert "parsed_data" not in body
