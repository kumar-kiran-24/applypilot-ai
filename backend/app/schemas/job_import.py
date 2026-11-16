"""Schemas for importing a job listing from a user-pasted URL."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.matching import JobResponse


class ImportJobRequest(BaseModel):
    """The pasted listing URL; validated server-side before any work."""

    url: str = Field(
        min_length=1,
        max_length=1000,
        description="A public Glassdoor job listing URL",
    )


class JobImportExtractionResponse(BaseModel):
    """What the import could read from the URL, and what it deliberately could not."""

    source: str
    title_source: str
    description_available: bool
    missing_fields: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ImportJobResponse(BaseModel):
    """The stored posting plus truthful import provenance."""

    job: JobResponse
    created: bool
    reused: bool
    extraction: JobImportExtractionResponse


__all__ = [
    "ImportJobRequest",
    "ImportJobResponse",
    "JobImportExtractionResponse",
]
