"""Resume request/response schemas.

No schema exposes a storage key, a filesystem path or extracted resume text
beyond the structured fields listed here.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.resume import ResumeParsingStatus


class ParsedEducationEntry(BaseModel):
    """One education entry found in a resume."""

    institution: str | None = None
    degree: str | None = None
    field_of_study: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    details: str | None = None


class ParsedExperienceEntry(BaseModel):
    """One work experience entry found in a resume."""

    title: str | None = None
    company: str | None = None
    location: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    is_current: bool = False
    description: str | None = None


class ParsedProjectEntry(BaseModel):
    """One project entry found in a resume."""

    name: str
    description: str | None = None
    technologies: list[str] = Field(default_factory=list)
    url: str | None = None


class ParsedResumeData(BaseModel):
    """Structured information extracted from a resume PDF."""

    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    professional_summary: str | None = None
    education: list[ParsedEducationEntry] = Field(default_factory=list)
    experience: list[ParsedExperienceEntry] = Field(default_factory=list)
    projects: list[ParsedProjectEntry] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)


class ResumeResponse(BaseModel):
    """Public view of an uploaded resume."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    original_filename: str
    file_size: int
    content_type: str
    is_default: bool
    parsing_status: ResumeParsingStatus
    parsing_error: str | None = None
    parsed_at: datetime | None = None
    uploaded_at: datetime
    skills: list[str] = Field(default_factory=list)
    parsed_data: ParsedResumeData = Field(default_factory=ParsedResumeData)


__all__ = [
    "ParsedEducationEntry",
    "ParsedExperienceEntry",
    "ParsedProjectEntry",
    "ParsedResumeData",
    "ResumeResponse",
]
