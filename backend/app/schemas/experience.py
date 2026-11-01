"""Experience request/response schemas."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.models.experience import EmploymentType
from app.schemas.common import ORMModel


class ExperienceBase(ORMModel):
    company: str = Field(min_length=1, max_length=200)
    designation: str = Field(min_length=1, max_length=200)
    employment_type: EmploymentType | None = None
    location: str | None = Field(default=None, max_length=200)
    start_date: date | None = None
    end_date: date | None = None
    is_current: bool = False
    description: str | None = Field(default=None, max_length=8000)
    technologies: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("technologies")
    @classmethod
    def _clean_technologies(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item and item.strip()]
        if any(len(item) > 60 for item in cleaned):
            msg = "each technology must be at most 60 characters"
            raise ValueError(msg)
        return cleaned

    @model_validator(mode="after")
    def _validate_period(self) -> ExperienceBase:
        if self.start_date and self.end_date and self.end_date < self.start_date:
            msg = "end_date must be on or after start_date"
            raise ValueError(msg)
        if self.is_current and self.end_date:
            msg = "end_date must be empty when is_current is true"
            raise ValueError(msg)
        return self


class ExperienceCreate(ExperienceBase):
    """Payload for adding a work experience."""


class ExperienceUpdate(ORMModel):
    company: str | None = Field(default=None, min_length=1, max_length=200)
    designation: str | None = Field(default=None, min_length=1, max_length=200)
    employment_type: EmploymentType | None = None
    location: str | None = Field(default=None, max_length=200)
    start_date: date | None = None
    end_date: date | None = None
    is_current: bool | None = None
    description: str | None = Field(default=None, max_length=8000)
    technologies: list[str] | None = Field(default=None, max_length=50)


class ExperienceResponse(ExperienceBase):
    """Work experience as returned by the API."""

    id: UUID
    candidate_id: UUID
