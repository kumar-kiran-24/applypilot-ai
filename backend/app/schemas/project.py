"""Project request/response schemas."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from pydantic import Field, HttpUrl, field_validator, model_validator

from app.schemas.common import ORMModel


class ProjectBase(ORMModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=8000)
    technologies: list[str] = Field(default_factory=list, max_length=50)
    project_url: HttpUrl | None = None
    start_date: date | None = None
    end_date: date | None = None

    @field_validator("technologies")
    @classmethod
    def _clean_technologies(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item and item.strip()]
        if any(len(item) > 60 for item in cleaned):
            msg = "each technology must be at most 60 characters"
            raise ValueError(msg)
        return cleaned

    @model_validator(mode="after")
    def _validate_dates(self) -> ProjectBase:
        if self.start_date and self.end_date and self.end_date < self.start_date:
            msg = "end_date must be on or after start_date"
            raise ValueError(msg)
        return self


class ProjectCreate(ProjectBase):
    """Payload for adding a project."""


class ProjectUpdate(ORMModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=8000)
    technologies: list[str] | None = Field(default=None, max_length=50)
    project_url: HttpUrl | None = None
    start_date: date | None = None
    end_date: date | None = None


class ProjectResponse(ProjectBase):
    """Project as returned by the API."""

    id: UUID
    candidate_id: UUID
