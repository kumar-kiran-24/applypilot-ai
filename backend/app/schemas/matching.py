"""Schemas for stored jobs and explainable resume matches.

No schema here exposes a storage path, resume text or file bytes. Match results
are descriptive compatibility scores, never claims about a candidate's abilities
and never hiring probabilities.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class JobRequirementsResponse(BaseModel):
    """Evidence extracted from the posting; empty means "not stated"."""

    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    mentioned_skills: list[str] = Field(default_factory=list)
    experience_years: float | None = None
    education_level: str | None = None
    education_field: str | None = None
    responsibilities: list[str] = Field(default_factory=list)
    has_description: bool = False
    skills_source: str = "none"
    confidence: str = "low"


class BestMatchSummary(BaseModel):
    """Compact match information used on job cards."""

    model_config = ConfigDict(from_attributes=True)

    resume_id: UUID
    resume_filename: str | None = None
    overall_score: float
    skill_score: float | None = None
    rank: int = 1
    explanation: str = ""


class JobResponse(BaseModel):
    """A persisted, user-owned job posting."""

    id: UUID
    job_title: str
    company: str | None = None
    location: str | None = None
    job_url: str
    job_description: str | None = None
    email_subject: str
    received_at: datetime | None = None
    source: str
    discovered_at: datetime
    requirements: JobRequirementsResponse
    best_match: BestMatchSummary | None = None
    matches_count: int = 0


class JobListResponse(BaseModel):
    """Paginated list of stored postings."""

    total: int
    jobs: list[JobResponse] = Field(default_factory=list)


class MatchScoreBreakdown(BaseModel):
    """0-100 scores; ``None`` means the posting gave no evidence for it."""

    skills: float | None = None
    experience: float | None = None
    education: float | None = None
    title: float | None = None
    projects: float | None = None


class ResumeMatchResponse(BaseModel):
    """One ranked resume evaluation for a job."""

    resume_id: UUID
    resume_filename: str
    rank: int
    is_recommended: bool
    overall_score: float
    scores: MatchScoreBreakdown
    matched_required_skills: list[str] = Field(default_factory=list)
    matched_preferred_skills: list[str] = Field(default_factory=list)
    missing_required_skills: list[str] = Field(default_factory=list)
    missing_preferred_skills: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    explanation: str
    confidence: str
    computed_at: datetime


class JobMatchesResponse(BaseModel):
    """Ranked matches for one job, including the recommended resume id."""

    job: JobResponse
    matches: list[ResumeMatchResponse] = Field(default_factory=list)
    recommended_resume_id: UUID | None = None
    resumes_evaluated: int = 0
    computed_at: datetime | None = None
    disclaimer: str
    message: str | None = None


class DiscoverJobsResponse(BaseModel):
    """Result of persisting freshly discovered Glassdoor postings."""

    jobs_found: int
    jobs_created: int
    jobs_updated: int
    jobs: list[JobResponse] = Field(default_factory=list)


class MatchJobRequest(BaseModel):
    """Options for matching every resume against one job."""

    force: bool = Field(
        default=False,
        description="Recompute even when cached inputs are unchanged.",
    )


class BulkMatchRequest(BaseModel):
    """Match a bounded set of owned jobs."""

    job_ids: list[UUID] | None = Field(
        default=None,
        max_length=50,
        description="Specific jobs to match; omitted matches the most recent jobs.",
    )
    limit: int = Field(default=25, ge=1, le=50)
    force: bool = False


class BulkMatchResult(BaseModel):
    """Per-job outcome of a bulk match."""

    job_id: UUID
    job_title: str
    company: str | None = None
    best_match: BestMatchSummary | None = None
    matches_count: int = 0
    resumes_evaluated: int = 0
    computed_at: datetime | None = None


class BulkMatchResponse(BaseModel):
    """Bulk matching outcome."""

    matched_jobs: int
    results: list[BulkMatchResult] = Field(default_factory=list)
    disclaimer: str


class ResumeMatchStat(BaseModel):
    """How often one resume was recommended across matched jobs."""

    resume_id: UUID
    resume_filename: str
    is_default: bool
    recommended_jobs: int
    matches_count: int
    average_score: float | None = None


class SummaryBestMatch(BaseModel):
    """The strongest job/resume pairing in the library."""

    job_id: UUID
    job_title: str
    company: str | None = None
    resume_id: UUID
    resume_filename: str | None = None
    overall_score: float


class MatchingSummaryResponse(BaseModel):
    """Library-level matching statistics for the authenticated user."""

    jobs_total: int
    jobs_matched: int
    resumes_total: int
    resumes_matched: int
    average_best_score: float | None = None
    best_match: SummaryBestMatch | None = None
    resume_stats: list[ResumeMatchStat] = Field(default_factory=list)
    last_computed_at: datetime | None = None
    disclaimer: str


__all__ = [
    "BestMatchSummary",
    "BulkMatchRequest",
    "BulkMatchResponse",
    "BulkMatchResult",
    "DiscoverJobsResponse",
    "JobListResponse",
    "JobMatchesResponse",
    "JobRequirementsResponse",
    "JobResponse",
    "MatchJobRequest",
    "MatchScoreBreakdown",
    "MatchingSummaryResponse",
    "ResumeMatchResponse",
    "ResumeMatchStat",
    "SummaryBestMatch",
]
