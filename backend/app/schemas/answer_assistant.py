"""Schemas for the Phase 2I.2 AI application-answer assistant.

A draft is not an answer: every response separates the generated text from the
evidence it may cite, the information that is missing, conflicts with stored
answers and an explicit confidence/provenance record. Sensitive and legal
questions never receive an invented draft.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.models.application import QuestionType
from app.models.application_answer import AnswerType
from app.schemas.application_answer import AnswerConflictResponse

DRAFT_STATUS_DRAFTED = "drafted"
DRAFT_STATUS_NEEDS_CONFIRMATION = "needs_confirmation"
DRAFT_STATUS_INSUFFICIENT = "insufficient_information"
DRAFT_STATUS_UNAVAILABLE = "unavailable"

LLM_STATUS_USED = "used"
LLM_STATUS_DISABLED = "disabled"
LLM_STATUS_NOT_CONFIGURED = "not_configured"
LLM_STATUS_FAILED = "failed"

ANSWER_ASSISTANT_DISCLAIMER = (
    "This draft only uses evidence from your verified profile, your resume and "
    "the job posting. It is a suggestion, not a fact: review and edit it before "
    "using it. Missing information means ApplyPilot could not find it, not that "
    "you lack it."
)

#: Evidence kinds shared with the UI.
EVIDENCE_PROFILE = "profile"
EVIDENCE_RESUME = "resume"
EVIDENCE_JOB = "job"
EVIDENCE_ANSWER_BANK = "answer_bank"


class DraftAnswerRequest(BaseModel):
    """One question to draft, optionally tied to an existing application.

    ``question_id`` associates the draft with an existing application question
    and makes the backend use that question's canonical key and text. Otherwise
    ``question_text`` is required and the key is derived deterministically.
    """

    application_id: UUID | None = Field(
        default=None, description="Existing application that supplies the job."
    )
    job_id: UUID | None = Field(
        default=None, description="Stored job to use when no application is selected."
    )
    question_id: UUID | None = Field(
        default=None,
        description="Application question to associate the draft with.",
    )
    question_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        pattern=r"^[a-z0-9][a-z0-9_.-]*$",
    )
    question_text: str | None = Field(default=None, min_length=3, max_length=1000)
    answer_type: AnswerType = AnswerType.TEXT
    allow_llm: bool = Field(
        default=True,
        description="Allow the configured LLM to draft; evidence is always deterministic.",
    )

    @model_validator(mode="after")
    def _require_context(self) -> DraftAnswerRequest:
        if self.application_id is None and self.job_id is None:
            msg = "application_id or job_id is required"
            raise ValueError(msg)
        if self.question_id is not None and self.application_id is None:
            msg = "question_id requires application_id"
            raise ValueError(msg)
        if self.question_id is None and not (self.question_text or "").strip():
            msg = "question_text is required when no application question is selected"
            raise ValueError(msg)
        return self


class DraftEvidence(BaseModel):
    """One deterministic piece of candidate or job evidence."""

    id: str
    kind: Literal["profile", "resume", "job", "answer_bank"]
    label: str
    detail: str
    source: str
    verified: bool = False
    confidence: Literal["high", "medium", "low"] = "medium"
    used: bool = False


class MissingInformation(BaseModel):
    """A fact the draft needs but could not find in verified evidence."""

    field: str
    question: str
    reason: str
    sensitive: bool = False


class AnswerDraftResponse(BaseModel):
    """Generated draft plus everything needed to review it safely."""

    application_id: UUID | None = None
    job_id: UUID
    job_title: str
    company: str | None = None
    question_id: UUID | None = None
    question_key: str
    question_text: str
    question_type: QuestionType | None = None
    answer_type: AnswerType = AnswerType.TEXT
    is_sensitive: bool = False
    is_legal: bool = False
    status: Literal[
        "drafted",
        "needs_confirmation",
        "insufficient_information",
        "unavailable",
    ]
    draft_answer: str | None = None
    confidence: float = Field(default=0.0, ge=0, le=1)
    confidence_label: Literal["high", "medium", "low"] = "low"
    evidence: list[DraftEvidence] = Field(default_factory=list)
    missing_information: list[MissingInformation] = Field(default_factory=list)
    conflicts: list[AnswerConflictResponse] = Field(default_factory=list)
    inferred_claims: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    save_reusable_allowed: bool = False
    llm_status: str = LLM_STATUS_DISABLED
    llm_model: str | None = None
    disclaimer: str = ANSWER_ASSISTANT_DISCLAIMER


__all__ = [
    "ANSWER_ASSISTANT_DISCLAIMER",
    "AnswerDraftResponse",
    "DRAFT_STATUS_DRAFTED",
    "DRAFT_STATUS_INSUFFICIENT",
    "DRAFT_STATUS_NEEDS_CONFIRMATION",
    "DRAFT_STATUS_UNAVAILABLE",
    "DraftAnswerRequest",
    "DraftEvidence",
    "EVIDENCE_ANSWER_BANK",
    "EVIDENCE_JOB",
    "EVIDENCE_PROFILE",
    "EVIDENCE_RESUME",
    "LLM_STATUS_DISABLED",
    "LLM_STATUS_FAILED",
    "LLM_STATUS_NOT_CONFIGURED",
    "LLM_STATUS_USED",
    "MissingInformation",
]
