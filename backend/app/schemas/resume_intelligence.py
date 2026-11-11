"""Schemas for Phase 2F.2 structured resume intelligence.

Every extracted fact carries its own provenance: where it came from, how
confident the extractor is, the verbatim snippet it was read from and the rule
that produced it. Nothing here is a candidate-profile truth: ``parsed_data`` and
these facts are *resume evidence* until the user explicitly confirms something.

Absence is never a negative fact. An empty list or a ``not_stated`` source means
the resume did not state that information, not that the candidate lacks it.

Source labels follow the Phase 2F.1 job-intelligence convention: plain strings
stored in JSON, so old rows stay readable when a new source is introduced.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

#: The value was read from the resume document text.
SOURCE_RESUME_TEXT = "resume_text"
#: The value came from rules applied to previously parsed resume fields.
SOURCE_DETERMINISTIC = "deterministic"
#: The value was produced by a validated model reading of the resume text.
SOURCE_LLM = "llm"
#: The candidate explicitly confirmed the value (never set by extraction).
SOURCE_USER_VERIFIED = "user_verified"
#: The resume did not state the field. Absence is not evidence of absence.
SOURCE_NOT_STATED = "not_stated"

CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"

RESUME_INTELLIGENCE_DISCLAIMER = (
    "This analysis reports only evidence found in the uploaded resume. Missing "
    "skills, experience or qualifications mean the resume did not mention them, "
    "not that the candidate lacks them. Nothing here has been added to the "
    "candidate profile and the uploaded file is never modified."
)

#: Extraction rules that produce a fact. Kept as plain strings for forward
#: compatibility with stored payloads.
METHOD_SKILLS_SECTION = "skills_section"
METHOD_LABELLED_SKILL_GROUP = "labelled_skill_group"
METHOD_KNOWN_SKILL_VOCABULARY = "known_skill_vocabulary"
METHOD_STORED_SKILL_LIST = "stored_skill_list"
METHOD_EXPERIENCE_ENTRY = "parsed_experience_entry"
METHOD_PROJECT_ENTRY = "parsed_project_entry"
METHOD_EDUCATION_ENTRY = "parsed_education_entry"
METHOD_CERTIFICATION_ENTRY = "parsed_certification_entry"


class SkillCategory(str, Enum):
    """Grouping of one skill. The grouping is inferred unless labelled."""

    LANGUAGE = "language"
    FRAMEWORK = "framework"
    LIBRARY = "library"
    DATABASE = "database"
    CLOUD = "cloud"
    TOOL = "tool"
    AI_ML = "ai_ml"
    SOFT_SKILL = "soft_skill"
    OTHER = "other"


class ResumeFact(BaseModel):
    """Provenance shared by every extracted resume fact."""

    model_config = ConfigDict(extra="forbid")

    #: One of the ``SOURCE_*`` values.
    source: str = SOURCE_DETERMINISTIC
    #: One of the ``CONFIDENCE_*`` values.
    confidence: str = CONFIDENCE_MEDIUM
    #: Verbatim (whitespace-normalised) snippet from the resume text.
    evidence: str | None = None
    #: Rule that produced the fact (one of the ``METHOD_*`` values).
    method: str = ""


class ResumeSkillFact(ResumeFact):
    """One skill with its category, explicit/graded evidence and confidence."""

    name: str
    category: SkillCategory = SkillCategory.OTHER
    #: True when the resume itself lists the skill (skills section or prose).
    explicit: bool = True
    #: True when the category was assigned by our vocabulary rather than by an
    #: explicit heading such as ``Databases: ...`` in the resume.
    category_inferred: bool = True


class ResumeExperienceFact(ResumeFact):
    """One role. Every value is copied from the resume or left empty."""

    company: str | None = None
    role: str | None = None
    location: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    is_current: bool = False
    #: Only set when the role has a usable start date.
    duration_months: int | None = None
    responsibilities: list[str] = Field(default_factory=list)
    achievements: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)


class ResumeProjectFact(ResumeFact):
    """One project with its stated contribution and outcomes."""

    title: str
    description: str | None = None
    technologies: list[str] = Field(default_factory=list)
    #: A bullet the resume wrote in the candidate's own voice, when present.
    contribution: str | None = None
    #: Skills inferred from the project text (a superset of ``technologies``).
    demonstrated_skills: list[str] = Field(default_factory=list)
    #: Only outcomes with a number the resume actually stated.
    measurable_outcomes: list[str] = Field(default_factory=list)
    url: str | None = None


class ResumeEducationFact(ResumeFact):
    """One education entry with the year and honours only when stated."""

    institution: str | None = None
    degree: str | None = None
    specialization: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    graduation_year: int | None = None
    achievements: list[str] = Field(default_factory=list)


class ResumeCertificationFact(ResumeFact):
    """One certification, with issuer and date only when recognisable."""

    name: str
    issuing_organization: str | None = None
    #: Verbatim date text from the resume, e.g. ``2022`` or ``March 2022``.
    issued_on: str | None = None


class ResumeIntelligenceFields(BaseModel):
    """Structured evidence extracted from one resume."""

    model_config = ConfigDict(extra="forbid")

    summary: str | None = None
    #: Sum of dated role durations. ``None`` when no role had usable dates.
    total_experience_months: int | None = None
    has_dated_experience: bool = False
    skills: list[ResumeSkillFact] = Field(default_factory=list)
    experience: list[ResumeExperienceFact] = Field(default_factory=list)
    projects: list[ResumeProjectFact] = Field(default_factory=list)
    education: list[ResumeEducationFact] = Field(default_factory=list)
    certifications: list[ResumeCertificationFact] = Field(default_factory=list)
    sections_found: list[str] = Field(default_factory=list)

    def skill_names(self, *categories: SkillCategory) -> list[str]:
        """Names in the requested categories (all skills when none is given)."""
        wanted = set(categories)
        return [
            skill.name
            for skill in self.skills
            if not wanted or skill.category in wanted
        ]


class ResumeIntelligencePayload(BaseModel):
    """Persisted extraction payload (validated on every read)."""

    model_config = ConfigDict(extra="forbid")

    fields: ResumeIntelligenceFields
    #: Top-level field -> one of the ``SOURCE_*`` values.
    provenance: dict[str, str] = Field(default_factory=dict)
    #: Top-level field -> one of the ``CONFIDENCE_*`` values.
    field_confidence: dict[str, str] = Field(default_factory=dict)
    confidence: str = CONFIDENCE_LOW
    warnings: list[str] = Field(default_factory=list)
    dropped_llm_claims: list[str] = Field(default_factory=list)
    supplemented_fields: list[str] = Field(default_factory=list)


class ResumeIntelligenceExtraction(BaseModel):
    """Provenance, cache state and safe failure information for one reading.

    ``analysis_state`` is one of ``ready`` (a current stored reading),
    ``failed`` (a current stored failure), ``stale`` (an older stored reading
    exists but this response was computed on the fly) or ``missing`` (nothing
    has been stored yet).
    """

    status: str
    analysis_state: str
    confidence: str
    provenance: dict[str, str] = Field(default_factory=dict)
    field_confidence: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    #: Safe, machine-readable code - never an exception message or a path.
    error_code: str | None = None
    extractor_version: str
    computed_at: datetime
    cached: bool = False


class ResumeIntelligenceResponse(BaseModel):
    """Authenticated resume-intelligence analyze/read response."""

    resume_id: UUID
    fields: ResumeIntelligenceFields
    extraction: ResumeIntelligenceExtraction
    disclaimer: str = RESUME_INTELLIGENCE_DISCLAIMER
    message: str | None = None


class AnalyzeResumeRequest(BaseModel):
    """Options for one resume analysis run."""

    force: bool = Field(
        default=False,
        description="Recompute even when a stored reading matches the resume.",
    )


__all__ = [
    "CONFIDENCE_HIGH",
    "CONFIDENCE_LOW",
    "CONFIDENCE_MEDIUM",
    "METHOD_CERTIFICATION_ENTRY",
    "METHOD_EDUCATION_ENTRY",
    "METHOD_EXPERIENCE_ENTRY",
    "METHOD_KNOWN_SKILL_VOCABULARY",
    "METHOD_LABELLED_SKILL_GROUP",
    "METHOD_PROJECT_ENTRY",
    "METHOD_SKILLS_SECTION",
    "METHOD_STORED_SKILL_LIST",
    "RESUME_INTELLIGENCE_DISCLAIMER",
    "SOURCE_DETERMINISTIC",
    "SOURCE_LLM",
    "SOURCE_NOT_STATED",
    "SOURCE_RESUME_TEXT",
    "SOURCE_USER_VERIFIED",
    "AnalyzeResumeRequest",
    "ResumeCertificationFact",
    "ResumeEducationFact",
    "ResumeExperienceFact",
    "ResumeFact",
    "ResumeIntelligenceExtraction",
    "ResumeIntelligenceFields",
    "ResumeIntelligencePayload",
    "ResumeIntelligenceResponse",
    "ResumeProjectFact",
    "ResumeSkillFact",
    "SkillCategory",
]
