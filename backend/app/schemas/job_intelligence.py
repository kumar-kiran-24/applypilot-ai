"""Schemas for Phase 2F structured job understanding.

The response never presents a missing value as a negative fact: ``None`` and
empty lists mean the posting did not state that information. Every field also
carries provenance and a confidence so a model-derived value can never be
mistaken for text the employer actually wrote.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.experience import EmploymentType
from app.models.preference import WorkMode

#: Source labels used in ``provenance``. Kept as plain strings in the stored
#: JSON so old rows stay readable if a new source is introduced.
SOURCE_JOB_RECORD = "job_record"
SOURCE_DETERMINISTIC = "deterministic"
SOURCE_LLM = "llm"
SOURCE_NOT_STATED = "not_stated"

CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"

#: LLM enrichment outcomes. ``failed`` is retried on the next explicit
#: ``/analyze`` request; ``not_configured`` is retried once a key appears.
LLM_STATUS_USED = "used"
LLM_STATUS_DISABLED = "disabled"
LLM_STATUS_NOT_CONFIGURED = "not_configured"
LLM_STATUS_FAILED = "failed"
LLM_STATUS_NO_DESCRIPTION = "skipped_no_description"
LLM_STATUS_NOT_ANALYZED = "not_analyzed"

JOB_INTELLIGENCE_DISCLAIMER = (
    "This analysis reports only information stated in the posting. Missing "
    "values mean the posting did not state them; nothing here is a hiring "
    "probability or a substitute for the employer's own description."
)


class ExperienceLevel(str, Enum):
    """Seniority explicitly stated or classified for the posting."""

    INTERN = "intern"
    JUNIOR = "junior"
    MID = "mid"
    SENIOR = "senior"
    LEAD = "lead"
    PRINCIPAL = "principal"
    DIRECTOR = "director"
    UNKNOWN = "unknown"


class JobIntelligenceFields(BaseModel):
    """Structured understanding of one posting.

    ``description`` is the stored original text, unchanged. All other fields are
    either literal evidence extracted from the posting or (only when empty) a
    verified LLM reading of that same text.
    """

    model_config = ConfigDict(extra="forbid")

    job_title: str
    company: str | None = None
    description: str | None = None
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    experience_years: float | None = None
    experience_years_max: float | None = None
    education_level: str | None = None
    education_field: str | None = None
    education_requirement: str | None = None
    required_qualifications: list[str] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    employment_type: EmploymentType | None = None
    work_location: str | None = None
    work_mode: WorkMode | None = None
    salary_range: str | None = None
    experience_level: ExperienceLevel = ExperienceLevel.UNKNOWN
    required_certifications: list[str] = Field(default_factory=list)
    application_url: str | None = None
    application_url_is_listing: bool = True


class JobIntelligencePayload(BaseModel):
    """Persisted extraction payload (validated on every read)."""

    model_config = ConfigDict(extra="forbid")

    fields: JobIntelligenceFields
    provenance: dict[str, str] = Field(default_factory=dict)
    field_confidence: dict[str, str] = Field(default_factory=dict)
    confidence: str = CONFIDENCE_LOW
    contradictions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    dropped_llm_claims: list[str] = Field(default_factory=list)
    supplemented_fields: list[str] = Field(default_factory=list)


class JobIntelligenceExtraction(BaseModel):
    """Provenance and confidence for one extraction run."""

    confidence: str
    provenance: dict[str, str] = Field(default_factory=dict)
    field_confidence: dict[str, str] = Field(default_factory=dict)
    contradictions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    llm_used: bool = False
    llm_status: str = LLM_STATUS_DISABLED
    llm_model: str | None = None
    extractor_version: str
    dropped_llm_claims: list[str] = Field(default_factory=list)
    supplemented_fields: list[str] = Field(default_factory=list)
    computed_at: datetime
    cached: bool = False


class JobIntelligenceResponse(BaseModel):
    """Public response for both analyze and read endpoints."""

    job_id: UUID
    fields: JobIntelligenceFields
    extraction: JobIntelligenceExtraction
    disclaimer: str = JOB_INTELLIGENCE_DISCLAIMER
    message: str | None = None


class AnalyzeJobRequest(BaseModel):
    """Options for one analysis run."""

    force: bool = Field(
        default=False,
        description="Recompute even when the stored analysis matches the posting.",
    )
    use_llm: bool = Field(
        default=True,
        description="Allow LLM enrichment when Groq is configured.",
    )


__all__ = [
    "CONFIDENCE_HIGH",
    "CONFIDENCE_LOW",
    "CONFIDENCE_MEDIUM",
    "JOB_INTELLIGENCE_DISCLAIMER",
    "LLM_STATUS_DISABLED",
    "LLM_STATUS_FAILED",
    "LLM_STATUS_NOT_ANALYZED",
    "LLM_STATUS_NOT_CONFIGURED",
    "LLM_STATUS_NO_DESCRIPTION",
    "LLM_STATUS_USED",
    "SOURCE_DETERMINISTIC",
    "SOURCE_JOB_RECORD",
    "SOURCE_LLM",
    "SOURCE_NOT_STATED",
    "AnalyzeJobRequest",
    "ExperienceLevel",
    "JobIntelligenceExtraction",
    "JobIntelligenceFields",
    "JobIntelligencePayload",
    "JobIntelligenceResponse",
]
