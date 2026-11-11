"""Phase 2F.2.4 multi-resume comparison response schemas.

The response is descriptive only. It exposes resume display filenames (already
public elsewhere), scores, evidence references and safe processing states - never
storage keys, file paths, raw PDF content or parsed resume payloads.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.matching import MatchEvidenceResponse, MatchScoreBreakdown


class CompareResumesRequest(BaseModel):
    """Options for a multi-resume comparison."""

    force: bool = Field(
        default=False,
        description="Recompute stored matches even when their inputs are unchanged.",
    )


class ResumeComparisonEntryResponse(BaseModel):
    """One resume's comparable result or explicit unavailability reason."""

    resume_id: UUID
    resume_title: str
    rank: int | None = None
    processing_state: str
    state_reason: str
    comparable: bool = False
    is_recommended: bool = False
    overall_score: float | None = None
    scores: MatchScoreBreakdown = Field(default_factory=MatchScoreBreakdown)
    required_skill_coverage: float | None = None
    required_skills_matched: int | None = None
    required_skills_total: int | None = None
    evidence_confidence: str | None = None
    match_processing_state: str | None = None
    matched_required_skills: list[str] = Field(default_factory=list)
    missing_required_skills: list[str] = Field(default_factory=list)
    evidence: list[MatchEvidenceResponse] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    explanation: str = ""


class ResumeProcessingFailureResponse(BaseModel):
    """A resume that could not be compared, and why."""

    resume_id: UUID
    resume_title: str
    processing_state: str
    reason: str


class ResumeAlternativeResponse(BaseModel):
    """A runner-up resume relative to the recommendation."""

    resume_id: UUID
    resume_title: str
    overall_score: float
    score_difference: float
    distinguishing_factors: list[str] = Field(default_factory=list)


class ResumeRecommendationResponse(BaseModel):
    """The recommended resume, its evidence and its runner-ups."""

    resume_id: UUID
    resume_title: str
    overall_score: float
    confidence: str
    reasons: list[str] = Field(default_factory=list)
    supporting_evidence: list[MatchEvidenceResponse] = Field(default_factory=list)
    important_gaps: list[str] = Field(default_factory=list)
    close_call: bool = False
    close_call_threshold: float
    close_call_explanation: str | None = None
    alternatives: list[ResumeAlternativeResponse] = Field(default_factory=list)


class ResumeComparisonResponse(BaseModel):
    """Comparison of every owned resume against one owned job."""

    job_id: UUID
    status: str
    total_resumes: int
    compared_resumes: int
    resumes: list[ResumeComparisonEntryResponse] = Field(default_factory=list)
    recommendation: ResumeRecommendationResponse | None = None
    close_score: bool = False
    close_score_threshold: float
    processing_failures: list[ResumeProcessingFailureResponse] = Field(
        default_factory=list
    )
    algorithm_version: str
    generated_at: datetime
    disclaimer: str
    message: str | None = None


__all__ = [
    "CompareResumesRequest",
    "ResumeAlternativeResponse",
    "ResumeComparisonEntryResponse",
    "ResumeComparisonResponse",
    "ResumeProcessingFailureResponse",
    "ResumeRecommendationResponse",
]
