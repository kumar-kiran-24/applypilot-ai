"""Schemas for application preparation, approval and tracking.

No schema exposes a browser session, cookie, password, OTP or storage path. The
preview is the complete, reviewable submission payload.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.application import (
    AnswerSource,
    ApplicationStage,
    AutomationMode,
    InspectionMode,
    QuestionStatus,
    QuestionType,
)


class PlatformInfo(BaseModel):
    """One supported application platform."""

    platform: str
    display_name: str
    supports_automation: bool
    requires_account: bool | None = None
    documentation_url: str = ""
    notes: list[str] = Field(default_factory=list)
    declared_questions: list[dict[str, object]] = Field(default_factory=list)


class PlatformCatalogResponse(BaseModel):
    """All platforms ApplyPilot can recognise."""

    platforms: list[PlatformInfo] = Field(default_factory=list)


class ApplicationQuestionResponse(BaseModel):
    """One question, its provenance and the user's decision."""

    id: UUID
    question_key: str
    field_identifier: str | None = None
    section: str
    question_text: str
    help_text: str | None = None
    field_type: QuestionType
    required: bool
    options: list[str] = Field(default_factory=list)
    suggested_answer: str | None = None
    answer: str | None = None
    answer_source: AnswerSource
    confidence: float
    source_record_id: str | None = None
    last_confirmed_at: datetime | None = None
    requires_confirmation: bool
    is_sensitive: bool
    is_legal: bool
    status: QuestionStatus
    note: str | None = None


class PreviewSection(BaseModel):
    """Questions grouped into one logical section."""

    section: str
    label: str
    questions: list[ApplicationQuestionResponse] = Field(default_factory=list)


class MissingQuestion(BaseModel):
    """A required question that still blocks approval."""

    question_key: str
    question_text: str


class PreviewProgress(BaseModel):
    """Completion counters for the questionnaire."""

    total: int
    confirmed: int
    required_total: int
    required_confirmed: int
    percent: int


class PreviewResume(BaseModel):
    """The resume selected for this application."""

    id: UUID | None = None
    filename: str | None = None
    reason: str = ""
    score: float | None = None


class ApplicationPreviewResponse(BaseModel):
    """Everything the user reviews before approving a submission."""

    application_id: UUID
    job_id: UUID
    job_title: str
    company: str | None = None
    platform: str
    platform_name: str
    application_url: str
    automation_mode: AutomationMode
    inspection_mode: InspectionMode
    status: str
    stage: str | None = None
    approved_at: datetime | None = None
    submitted_at: datetime | None = None
    submission_reference: str | None = None
    failure_reason: str | None = None
    requires_account: bool
    notes: list[str] = Field(default_factory=list)
    resume: PreviewResume
    sections: list[PreviewSection] = Field(default_factory=list)
    missing_required: list[MissingQuestion] = Field(default_factory=list)
    progress: PreviewProgress
    ready_to_approve: bool
    can_submit: bool
    submission_mode: str
    manual_steps: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ApplicationSummaryResponse(BaseModel):
    """Compact row used by the applications list."""

    id: UUID
    job_id: UUID
    job_title: str
    company: str | None = None
    platform: str
    platform_name: str
    application_url: str
    status: str
    stage: str | None = None
    automation_mode: AutomationMode
    requires_account: bool
    resume_filename: str | None = None
    questions_total: int = 0
    questions_confirmed: int = 0
    missing_required_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None
    approved_at: datetime | None = None
    submitted_at: datetime | None = None


class ApplicationListResponse(BaseModel):
    """All applications owned by the authenticated user."""

    total: int
    applications: list[ApplicationSummaryResponse] = Field(default_factory=list)


class AnalyzeApplicationRequest(BaseModel):
    """Prepare an application for one stored job."""

    job_id: UUID
    application_url: str | None = Field(
        default=None,
        max_length=1000,
        description="The employer's application URL when the job link is a job board.",
    )
    resume_id: UUID | None = Field(
        default=None,
        description="Override the resume recommended by the matching engine.",
    )


class InspectApplicationRequest(BaseModel):
    """Supply or replace the employer's application URL."""

    application_url: str = Field(min_length=1, max_length=1000)


class UpdateQuestionRequest(BaseModel):
    """Save or confirm one answer."""

    answer: str | None = Field(default=None, max_length=5000)
    confirmed: bool = False
    note: str | None = Field(default=None, max_length=2000)


class SelectResumeRequest(BaseModel):
    """Override the recommended resume for this application."""

    resume_id: UUID


class ApproveApplicationRequest(BaseModel):
    """Explicit, affirmative approval of the previewed submission."""

    approved: bool


class SubmissionResponse(BaseModel):
    """Result of a submission attempt (automated or manual)."""

    submitted: bool
    manual_required: bool
    message: str
    reference: str | None = None
    steps: list[str] = Field(default_factory=list)
    application: ApplicationPreviewResponse


class ConfirmManualSubmissionRequest(BaseModel):
    """The user confirms they submitted on the employer's site."""

    reference: str | None = Field(default=None, max_length=255)


class UpdateStageRequest(BaseModel):
    """Post-submission tracking update."""

    stage: ApplicationStage


__all__ = [
    "AnalyzeApplicationRequest",
    "ApplicationListResponse",
    "ApplicationPreviewResponse",
    "ApplicationQuestionResponse",
    "ApplicationSummaryResponse",
    "ApproveApplicationRequest",
    "ConfirmManualSubmissionRequest",
    "InspectApplicationRequest",
    "PlatformCatalogResponse",
    "PlatformInfo",
    "SelectResumeRequest",
    "SubmissionResponse",
    "UpdateQuestionRequest",
    "UpdateStageRequest",
]
