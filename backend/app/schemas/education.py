"""Education request/response schemas."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas.common import ORMModel


class EducationBase(ORMModel):
    institution: str = Field(min_length=1, max_length=200)
    degree: str = Field(min_length=1, max_length=200)
    specialization: str | None = Field(default=None, max_length=200)
    start_date: date | None = None
    end_date: date | None = None
    cgpa: Decimal | None = Field(default=None, ge=0, le=10, max_digits=4, decimal_places=2)
    description: str | None = Field(default=None, max_length=5000)

    @model_validator(mode="after")
    def _validate_dates(self) -> EducationBase:
        if self.start_date and self.end_date and self.end_date < self.start_date:
            msg = "end_date must be on or after start_date"
            raise ValueError(msg)
        return self


class EducationCreate(EducationBase):
    """Payload for adding an education entry."""


class EducationUpdate(ORMModel):
    institution: str | None = Field(default=None, min_length=1, max_length=200)
    degree: str | None = Field(default=None, min_length=1, max_length=200)
    specialization: str | None = Field(default=None, max_length=200)
    start_date: date | None = None
    end_date: date | None = None
    cgpa: Decimal | None = Field(default=None, ge=0, le=10, max_digits=4, decimal_places=2)
    description: str | None = Field(default=None, max_length=5000)


class EducationResponse(EducationBase):
    """Education entry as returned by the API."""

    id: UUID
    candidate_id: UUID
