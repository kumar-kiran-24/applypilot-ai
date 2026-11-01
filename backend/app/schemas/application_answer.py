"""Application answer request/response schemas."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from pydantic import Field, field_validator, model_validator

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

    @field_validator("question_key")
    @classmethod
    def _normalise_key(cls, value: str) -> str:
        return value.strip().lower()

    @model_validator(mode="after")
    def _validate_verification(self) -> ApplicationAnswerBase:
        if self.verification_status is VerificationStatus.VERIFIED and not self.answer:
            msg = "a verified answer must include an answer value"
            raise ValueError(msg)
        return self


class ApplicationAnswerCreate(ApplicationAnswerBase):
    """Payload for storing a reusable answer.

    ``verification_status`` defaults to UNKNOWN: nothing is treated as
    user-confirmed unless the caller explicitly says so.
    """


class ApplicationAnswerUpdate(ORMModel):
    answer: str | None = Field(default=None, max_length=5000)
    answer_type: AnswerType | None = None
    verification_status: VerificationStatus | None = None
    question_text: str | None = Field(default=None, min_length=1, max_length=1000)


class ApplicationAnswerResponse(ApplicationAnswerBase):
    """Reusable answer as returned by the API."""

    id: UUID
    candidate_id: UUID
    verified_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


def utc_now() -> datetime:
    """Timezone-aware UTC timestamp helper."""
    return datetime.now(timezone.utc)
