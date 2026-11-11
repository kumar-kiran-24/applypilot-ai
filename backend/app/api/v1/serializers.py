"""Response builders shared by the job and matching endpoints."""

from __future__ import annotations

from datetime import datetime, timezone

from app.models.job import Job
from app.models.job_match import JobResumeMatch
from app.schemas.job_intelligence import (
    JOB_INTELLIGENCE_DISCLAIMER,
    JobIntelligenceExtraction,
    JobIntelligenceResponse,
)
from app.schemas.matching import (
    BestMatchSummary,
    JobMatchesResponse,
    JobRequirementsResponse,
    JobResponse,
    MatchEvidenceResponse,
    MatchScoreBreakdown,
    ResumeMatchResponse,
)
from app.schemas.resume_comparison import (
    ResumeAlternativeResponse,
    ResumeComparisonEntryResponse,
    ResumeComparisonResponse,
    ResumeProcessingFailureResponse,
    ResumeRecommendationResponse,
)
from app.services.job_requirements import JobRequirements
from app.services.job_understanding_service import JobIntelligenceResult
from app.services.matching_engine import SCORE_DISCLAIMER
from app.services.matching_service import requirements_for
from app.services.resume_comparison_service import (
    RANKABLE_STATES,
    ComparisonEntry,
    ComparisonOutcome,
)


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


def _text_list(value: object) -> list[str]:
    """Keep only strings from a stored JSON list (old rows may be malformed)."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _evidence_list(value: object) -> list[MatchEvidenceResponse]:
    """Rebuild stored evidence defensively; never surface an unknown shape."""
    if not isinstance(value, list):
        return []
    evidence: list[MatchEvidenceResponse] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        reference = item.get("resume_reference")
        evidence.append(
            MatchEvidenceResponse(
                category=str(item.get("category") or ""),
                description=str(item.get("description") or ""),
                resume_reference=(
                    reference if isinstance(reference, str) else None
                ),
                confidence=str(item.get("confidence") or "low"),
                match_reason=str(item.get("match_reason") or ""),
            )
        )
    return evidence


def match_response(row: JobResumeMatch) -> ResumeMatchResponse:
    details = row.details if isinstance(row.details, dict) else {}
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
        evidence=_evidence_list(details.get("evidence")),
        missing_evidence=_text_list(details.get("missing_evidence")),
        gaps=_text_list(details.get("gaps")),
        processing_state=str(details.get("processing_state") or "ready"),
        algorithm_version=str(details.get("algorithm_version") or ""),
    )


def _comparison_entry(
    entry: ComparisonEntry,
) -> ResumeComparisonEntryResponse:
    row = entry.row
    details = row.details if row is not None and isinstance(row.details, dict) else {}
    return ResumeComparisonEntryResponse(
        resume_id=entry.resume_id,
        resume_title=entry.resume_title,
        rank=entry.rank,
        processing_state=entry.state,
        state_reason=entry.reason,
        comparable=entry.state in RANKABLE_STATES and row is not None,
        is_recommended=entry.is_recommended,
        overall_score=entry.overall_score,
        scores=MatchScoreBreakdown(
            skills=row.skill_score if row is not None else None,
            experience=row.experience_score if row is not None else None,
            education=row.education_score if row is not None else None,
            title=row.title_score if row is not None else None,
            projects=row.project_score if row is not None else None,
        ),
        required_skill_coverage=entry.required_skill_coverage,
        required_skills_matched=entry.required_skills_matched,
        required_skills_total=entry.required_skills_total,
        evidence_confidence=entry.evidence_confidence,
        match_processing_state=entry.match_processing_state,
        matched_required_skills=list(row.matched_required_skills or []) if row else [],
        missing_required_skills=list(row.missing_required_skills or []) if row else [],
        evidence=_evidence_list(details.get("evidence")),
        gaps=_text_list(details.get("gaps")),
        explanation=row.explanation if row is not None else "",
    )


def resume_comparison_response(outcome: ComparisonOutcome) -> ResumeComparisonResponse:
    """Build the Phase 2F.2.4 comparison response from computed entries."""
    recommendation = outcome.recommendation
    recommended_payload: ResumeRecommendationResponse | None = None
    if recommendation is not None and recommendation.recommended is not None:
        best = recommendation.recommended
        recommended_payload = ResumeRecommendationResponse(
            resume_id=best.resume_id,
            resume_title=best.resume_title,
            overall_score=best.overall_score or 0.0,
            confidence=recommendation.confidence or "low",
            reasons=list(recommendation.reasons),
            supporting_evidence=_evidence_list(
                list(recommendation.supporting_evidence)
            ),
            important_gaps=list(recommendation.important_gaps),
            close_call=recommendation.close_call,
            close_call_threshold=recommendation.close_call_threshold,
            close_call_explanation=recommendation.close_call_explanation,
            alternatives=[
                ResumeAlternativeResponse(
                    resume_id=alternative.resume_id,
                    resume_title=alternative.resume_title,
                    overall_score=alternative.overall_score,
                    score_difference=alternative.score_difference,
                    distinguishing_factors=list(
                        alternative.distinguishing_factors
                    ),
                )
                for alternative in recommendation.alternatives
            ],
        )

    return ResumeComparisonResponse(
        job_id=outcome.job_id,
        status=outcome.status,
        total_resumes=outcome.total_resumes,
        compared_resumes=outcome.compared_resumes,
        resumes=[_comparison_entry(entry) for entry in outcome.entries],
        recommendation=recommended_payload,
        close_score=outcome.close_score,
        close_score_threshold=outcome.close_score_threshold,
        processing_failures=[
            ResumeProcessingFailureResponse(
                resume_id=entry.resume_id,
                resume_title=entry.resume_title,
                processing_state=entry.state,
                reason=entry.reason,
            )
            for entry in outcome.processing_failures
        ],
        algorithm_version=outcome.algorithm_version,
        generated_at=as_utc(outcome.generated_at) or outcome.generated_at,
        disclaimer=outcome.disclaimer,
        message=outcome.message,
    )


def job_intelligence_response(
    job: Job,
    result: JobIntelligenceResult,
    *,
    message: str | None = None,
) -> JobIntelligenceResponse:
    """Build the Phase 2F.1 response from a persisted or computed result."""
    return JobIntelligenceResponse(
        job_id=job.id,
        fields=result.fields,
        extraction=JobIntelligenceExtraction(
            confidence=result.confidence,
            provenance=result.provenance,
            field_confidence=result.field_confidence,
            contradictions=result.contradictions,
            warnings=result.warnings,
            llm_used=result.llm_used,
            llm_status=result.llm_status,
            llm_model=result.llm_model,
            extractor_version=result.extractor_version,
            dropped_llm_claims=result.dropped_llm_claims,
            supplemented_fields=result.supplemented_fields,
            computed_at=as_utc(result.computed_at) or result.computed_at,
            cached=result.cached,
        ),
        disclaimer=JOB_INTELLIGENCE_DISCLAIMER,
        message=message,
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
    "job_intelligence_response",
    "job_matches_response",
    "job_response",
    "match_response",
    "resume_comparison_response",
]
