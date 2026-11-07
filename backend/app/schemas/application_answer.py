"""Application answer request/response schemas."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.models.application import AnswerSource
from app.models.application_answer import AnswerType, VerificationStatus
from app.schemas.common import ORMModel


class ApplicationAnswerBase(ORMModel):
    question_key: str = Field(
        min_length=1,
        max_length=100,
        pattern=r"^[a-z0-9][a-z0-9_.-]*$",
        examples=["work_authorization"],
    )
    question_text: str = Field(min_length=1, max_length=1000)
    answer: str | None = Field(default=None, max_length=5000)
    answer_type: AnswerType = AnswerType.TEXT
    verification_status: VerificationStatus = VerificationStatus.UNKNOWN
    is_sensitive: bool = False
    is_legal: bool = False

    @field_validator("question_key")
    @classmethod
    def _normalise_key(cls, value: str) -> str:
        return value.strip().lower()

    @model_validator(mode="after")
    def _validate_verification(self) -> ApplicationAnswerBase:
        if self.verification_status is VerificationStatus.VERIFIED and not self.answer:
            msg = "a verified answer must include an answer value"
            raise ValueError(msg)
        if self.is_legal and self.verification_status is VerificationStatus.VERIFIED:
            # Legal declarations are signed per application, never reused.
            msg = "legal declarations cannot be stored as verified reusable answers"
            raise ValueError(msg)
        return self


class ApplicationAnswerCreate(ApplicationAnswerBase):
    """Payload for storing a reusable answer.

    ``verification_status`` defaults to UNKNOWN: nothing is treated as
    user-confirmed unless the caller explicitly says so. Provenance fields are
    optional; the service derives safe defaults when they are omitted.
    """

    answer_source: AnswerSource | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    source_record_id: str | None = Field(default=None, max_length=200)
    freshness_days: int | None = Field(default=None, ge=1, le=3650)


class ApplicationAnswerUpdate(ORMModel):
    answer: str | None = Field(default=None, max_length=5000)
    answer_type: AnswerType | None = None
    verification_status: VerificationStatus | None = None
    question_text: str | None = Field(default=None, min_length=1, max_length=1000)
    is_sensitive: bool | None = None
    is_legal: bool | None = None
    # Provenance overrides are optional; the service derives safe defaults.
    answer_source: AnswerSource | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    source_record_id: str | None = Field(default=None, max_length=200)
    freshness_days: int | None = Field(default=None, ge=1, le=3650)


class ApplicationAnswerResponse(ApplicationAnswerBase):
    """Reusable answer as returned by the API."""

    id: UUID
    candidate_id: UUID
    verified_at: datetime | None = None
    answer_source: AnswerSource = AnswerSource.USER_ENTERED
    confidence: float = 1.0
    source_record_id: str | None = None
    last_confirmed_at: datetime | None = None
    freshness_days: int | None = None
    created_at: datetime
    updated_at: datetime


class ApplicationAnswerDeleteResponse(ORMModel):
    """Confirmation that a reusable answer was forgotten."""

    question_key: str
    deleted: bool = True


class StaleAnswerResponse(ORMModel):
    """A reusable answer whose confirmation has expired."""

    question_key: str
    question_text: str
    last_confirmed_at: datetime | None = None
    age_days: int | None = None
    max_age_days: int
    reason: str


class AnswerConflictResponse(ORMModel):
    """A stored answer that disagrees with the profile or another answer."""

    question_key: str
    question_text: str
    category: str | None = None
    stored_answer: str
    conflicting_answer: str
    conflicting_source: str
    reason: str
    resolution_options: list[str] = Field(default_factory=list)


class AnswerBankReviewResponse(ORMModel):
    """Freshness and conflict review of the reusable answer bank."""

    total: int
    verified: int
    stale: list[StaleAnswerResponse] = Field(default_factory=list)
    conflicts: list[AnswerConflictResponse] = Field(default_factory=list)
    unverified: list[str] = Field(default_factory=list)


class AnswerConflictResolution(ORMModel):
    """Explicit resolution of one stale or conflicting reusable answer."""

    resolution: Literal["keep_stored", "use_profile", "edited"]
    answer: str | None = Field(default=None, max_length=5000)

    @model_validator(mode="after")
    def _validate_edited_answer(self) -> AnswerConflictResolution:
        if self.resolution == "edited" and not (self.answer or "").strip():
            msg = "edited requires the corrected answer value"
            raise ValueError(msg)
        return self


def utc_now() -> datetime:
    """Timezone-aware UTC timestamp helper."""
    return datetime.now(timezone.utc)
