"""Schemas for the missing-information queue.

The queue exposes derived state only: which questions still block an
application, which answers are stale or conflicting, and which sensitive or
legal answers need a fresh confirmation. It never exposes a stored credential.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.application import AnswerSource, QuestionType
from app.models.missing_information import (
    MissingInformationItemStatus,
    MissingInformationStatus,
)


class MissingInformationItemResponse(BaseModel):
    """One question in the queue with its suggestion and provenance."""

    question_key: str
    question_text: str
    section: str
    field_type: QuestionType
    required: bool
    status: MissingInformationItemStatus
    reason: str
    answer: str | None = None
    answer_source: AnswerSource | None = None
    suggested_answer: str | None = None
    suggested_source: AnswerSource | None = None
    confidence: float = 0.0
    requires_confirmation: bool = True
    is_sensitive: bool = False
    is_legal: bool = False
    blocks_application: bool = False
    source_record_id: str | None = None
    last_confirmed_at: datetime | None = None
    options: list[str] = Field(default_factory=list)


class MissingInformationConflictResponse(BaseModel):
    """A reusable answer that disagrees with the profile or another answer."""

    question_key: str
    question_text: str
    category: str | None = None
    stored_answer: str
    conflicting_answer: str
    conflicting_source: str
    reason: str
    resolution_options: list[str] = Field(default_factory=list)
    blocks_application: bool = False


class MissingInformationSummaryResponse(BaseModel):
    """Compact queue row used by the queue list and application cards."""

    application_id: UUID
    job_id: UUID
    job_title: str
    company: str | None = None
    application_url: str
    application_status: str
    queue_status: MissingInformationStatus
    ready_to_complete: bool = False
    required_total: int
    required_resolved: int
    required_remaining: int
    optional_unanswered: int
    stale_count: int
    conflict_count: int
    sensitive_confirmation_count: int
    matched_count: int
    progress_percent: int
    last_recalculated_at: datetime | None = None
    resolved_at: datetime | None = None


class MissingInformationDetailResponse(MissingInformationSummaryResponse):
    """Everything the Missing Information page shows for one application."""

    required_questions: list[MissingInformationItemResponse] = Field(default_factory=list)
    optional_questions: list[MissingInformationItemResponse] = Field(default_factory=list)
    matched_questions: list[MissingInformationItemResponse] = Field(default_factory=list)
    stale_answers: list[MissingInformationItemResponse] = Field(default_factory=list)
    sensitive_confirmations: list[MissingInformationItemResponse] = Field(default_factory=list)
    dismissed_questions: list[MissingInformationItemResponse] = Field(default_factory=list)
    conflicts: list[MissingInformationConflictResponse] = Field(default_factory=list)


class MissingInformationQueueResponse(BaseModel):
    """Every active application's missing-information summary."""

    total: int
    applications: list[MissingInformationSummaryResponse] = Field(default_factory=list)


class SubmitMissingAnswerRequest(BaseModel):
    """A direct answer to one queued question."""

    answer: str = Field(min_length=1, max_length=5000)
    save_for_reuse: bool = Field(
        default=False,
        description=(
            "Store a non-sensitive answer for future applications. Sensitive and "
            "legal answers are never stored for reuse."
        ),
    )


class ConfirmSuggestedAnswerRequest(BaseModel):
    """Confirm a suggested answer, optionally overriding its value."""

    answer: str | None = Field(default=None, max_length=5000)
    save_for_reuse: bool = False


__all__ = [
    "ConfirmSuggestedAnswerRequest",
    "MissingInformationConflictResponse",
    "MissingInformationDetailResponse",
    "MissingInformationItemResponse",
    "MissingInformationQueueResponse",
    "MissingInformationSummaryResponse",
    "SubmitMissingAnswerRequest",
]
