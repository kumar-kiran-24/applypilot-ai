"""Response builders shared by the job and matching endpoints."""

from __future__ import annotations

from datetime import datetime, timezone

from app.models.job import Job
from app.models.job_match import JobResumeMatch
from app.schemas.matching import (
    BestMatchSummary,
    JobMatchesResponse,
    JobRequirementsResponse,
    JobResponse,
    MatchScoreBreakdown,
    ResumeMatchResponse,
)
from app.services.job_requirements import JobRequirements
from app.services.matching_engine import SCORE_DISCLAIMER
from app.services.matching_service import requirements_for


def as_utc(value: datetime | None) -> datetime | None:
    """Keep timestamps identical whether they came from PostgreSQL or SQLite."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _requirements_payload(requirements: JobRequirements) -> JobRequirementsResponse:
    return JobRequirementsResponse(
        required_skills=list(requirements.required_skills),
        preferred_skills=list(requirements.preferred_skills),
        mentioned_skills=list(requirements.mentioned_skills),
        experience_years=requirements.experience_years,
        education_level=requirements.education_level,
        education_field=requirements.education_field,
        responsibilities=list(requirements.responsibilities),
        has_description=requirements.has_description,
        skills_source=requirements.skills_source,
        confidence=requirements.confidence,
    )


def best_match_summary(row: JobResumeMatch) -> BestMatchSummary:
    """Build the compact card summary; ``row.resume`` must be eager-loaded."""
    return BestMatchSummary(
        resume_id=row.resume_id,
        resume_filename=row.resume.original_filename if row.resume else None,
        overall_score=row.overall_score,
        skill_score=row.skill_score,
        rank=row.rank,
        explanation=row.explanation,
    )


def job_response(
    job: Job,
    *,
    best: tuple[JobResumeMatch, int] | None = None,
    requirements: JobRequirements | None = None,
) -> JobResponse:
    requirements = requirements or requirements_for(job)
    best_match = best_match_summary(best[0]) if best is not None else None
    return JobResponse(
        id=job.id,
        job_title=job.job_title,
        company=job.company,
        location=job.location,
        job_url=job.job_url,
        job_description=job.job_description,
        email_subject=job.email_subject,
        received_at=as_utc(job.received_at),
        source=job.source,
        discovered_at=as_utc(job.created_at) or job.created_at,
        requirements=_requirements_payload(requirements),
        best_match=best_match,
        matches_count=int(best[1]) if best is not None else 0,
    )


def match_response(row: JobResumeMatch) -> ResumeMatchResponse:
    return ResumeMatchResponse(
        resume_id=row.resume_id,
        resume_filename=row.resume.original_filename if row.resume else "",
        rank=row.rank,
        is_recommended=row.is_recommended,
        overall_score=row.overall_score,
        scores=MatchScoreBreakdown(
            skills=row.skill_score,
            experience=row.experience_score,
            education=row.education_score,
            title=row.title_score,
            projects=row.project_score,
        ),
        matched_required_skills=list(row.matched_required_skills or []),
        matched_preferred_skills=list(row.matched_preferred_skills or []),
        missing_required_skills=list(row.missing_required_skills or []),
        missing_preferred_skills=list(row.missing_preferred_skills or []),
        strengths=list(row.strengths or []),
        suggestions=list(row.suggestions or []),
        explanation=row.explanation,
        confidence=row.confidence,
        computed_at=as_utc(row.computed_at) or row.computed_at,
    )


def job_matches_response(
    job: Job,
    rows: list[JobResumeMatch],
    *,
    message: str | None = None,
) -> JobMatchesResponse:
    computed_at: datetime | None = as_utc(
        max((row.computed_at for row in rows), default=None)
    )
    recommended = next((row for row in rows if row.is_recommended), None)
    best = (rows[0], len(rows)) if rows else None
    return JobMatchesResponse(
        job=job_response(job, best=best),
        matches=[match_response(row) for row in rows],
        recommended_resume_id=recommended.resume_id if recommended else None,
        resumes_evaluated=len(rows),
        computed_at=computed_at,
        disclaimer=SCORE_DISCLAIMER,
        message=message,
    )


__all__ = [
    "best_match_summary",
    "job_matches_response",
    "job_response",
    "match_response",
]
