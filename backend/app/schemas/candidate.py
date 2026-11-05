"""Candidate request/response schemas."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import EmailStr, Field, HttpUrl, field_validator

from app.schemas.application_answer import ApplicationAnswerResponse
from app.schemas.certification import CertificationResponse
from app.schemas.common import ORMModel
from app.schemas.education import EducationResponse
from app.schemas.experience import ExperienceResponse
from app.schemas.preference import JobPreferenceResponse
from app.schemas.project import ProjectResponse


class CandidateBase(ORMModel):
    full_name: str = Field(min_length=1, max_length=200)
    preferred_name: str | None = Field(default=None, max_length=200)
    email: EmailStr
    phone: str | None = Field(default=None, max_length=32)
    phone_country_code: str | None = Field(default=None, max_length=8)
    location: str | None = Field(default=None, max_length=200)
    address_line1: str | None = Field(default=None, max_length=300)
    address_line2: str | None = Field(default=None, max_length=300)
    city: str | None = Field(default=None, max_length=120)
    region: str | None = Field(default=None, max_length=120)
    postal_code: str | None = Field(default=None, max_length=32)
    country: str | None = Field(default=None, max_length=100)
    linkedin_url: HttpUrl | None = None
    github_url: HttpUrl | None = None
    portfolio_url: HttpUrl | None = None
    professional_summary: str | None = Field(default=None, max_length=5000)
    work_authorization: str | None = Field(default=None, max_length=200)
    requires_sponsorship: bool | None = None
    notice_period_days: int | None = Field(default=None, ge=0, le=365)
    available_from: date | None = None

    @field_validator("full_name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            msg = "full_name must not be blank"
            raise ValueError(msg)
        return cleaned

    @field_validator("email")
    @classmethod
    def _normalise_email(cls, value: str) -> str:
        return value.strip().lower()


class CandidateCreate(CandidateBase):
    """Payload for creating a candidate profile."""


class CandidateUpdate(ORMModel):
    """Partial update payload; unset fields are left untouched."""

    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    preferred_name: str | None = Field(default=None, max_length=200)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=32)
    phone_country_code: str | None = Field(default=None, max_length=8)
    location: str | None = Field(default=None, max_length=200)
    address_line1: str | None = Field(default=None, max_length=300)
    address_line2: str | None = Field(default=None, max_length=300)
    city: str | None = Field(default=None, max_length=120)
    region: str | None = Field(default=None, max_length=120)
    postal_code: str | None = Field(default=None, max_length=32)
    country: str | None = Field(default=None, max_length=100)
    linkedin_url: HttpUrl | None = None
    github_url: HttpUrl | None = None
    portfolio_url: HttpUrl | None = None
    professional_summary: str | None = Field(default=None, max_length=5000)
    work_authorization: str | None = Field(default=None, max_length=200)
    requires_sponsorship: bool | None = None
    notice_period_days: int | None = Field(default=None, ge=0, le=365)
    available_from: date | None = None

    @field_validator("email")
    @classmethod
    def _normalise_email(cls, value: str | None) -> str | None:
        return value.strip().lower() if value else value


class CandidateResponse(CandidateBase):
    """Candidate summary returned by collection endpoints."""

    id: UUID
    created_at: datetime
    updated_at: datetime


class CandidateDetailResponse(CandidateResponse):
    """Full candidate profile including nested records."""

    education: list[EducationResponse] = Field(default_factory=list)
    experiences: list[ExperienceResponse] = Field(default_factory=list)
    projects: list[ProjectResponse] = Field(default_factory=list)
    preference: JobPreferenceResponse | None = None
    certifications: list[CertificationResponse] = Field(default_factory=list)
    application_answers: list[ApplicationAnswerResponse] = Field(default_factory=list)
