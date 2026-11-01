"""Job preference request/response schemas."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import Field, field_validator

from app.models.experience import EmploymentType
from app.models.preference import RelocationPreference, WorkMode
from app.schemas.common import ORMModel

RoleList = Annotated[list[str], Field(default_factory=list, max_length=50)]


def _clean_list(values: list[str], *, max_item_length: int, label: str) -> list[str]:
    cleaned = [value.strip() for value in values if value and value.strip()]
    if any(len(value) > max_item_length for value in cleaned):
        msg = f"each {label} entry must be at most {max_item_length} characters"
        raise ValueError(msg)
    return cleaned


class JobPreferenceBase(ORMModel):
    preferred_roles: RoleList = Field(default_factory=list)
    preferred_locations: RoleList = Field(default_factory=list)
    preferred_job_types: list[EmploymentType] = Field(default_factory=list, max_length=20)
    preferred_work_modes: list[WorkMode] = Field(default_factory=list, max_length=10)
    relocation_preference: RelocationPreference = RelocationPreference.UNKNOWN
    minimum_salary: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    expected_salary: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    currency: str = Field(default="USD", min_length=3, max_length=3)

    @field_validator("preferred_roles", "preferred_locations")
    @classmethod
    def _clean_text_lists(cls, value: list[str]) -> list[str]:
        return _clean_list(value, max_item_length=120, label="preference")

    @field_validator("currency")
    @classmethod
    def _normalise_currency(cls, value: str) -> str:
        return value.strip().upper()


class JobPreferenceCreate(JobPreferenceBase):
    """Payload for creating a candidate's job preferences."""


class JobPreferenceUpdate(ORMModel):
    preferred_roles: list[str] | None = Field(default=None, max_length=50)
    preferred_locations: list[str] | None = Field(default=None, max_length=50)
    preferred_job_types: list[EmploymentType] | None = Field(default=None, max_length=20)
    preferred_work_modes: list[WorkMode] | None = Field(default=None, max_length=10)
    relocation_preference: RelocationPreference | None = None
    minimum_salary: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    expected_salary: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    currency: str | None = Field(default=None, min_length=3, max_length=3)


class JobPreferenceResponse(JobPreferenceBase):
    """Job preferences as returned by the API."""

    id: UUID
    candidate_id: UUID
    created_at: datetime
    updated_at: datetime
