"""Phase 2E Stage D schemas: human interventions, reconciliation and review.

The queue exposes only safe field metadata. It never returns a stored field
value, a legal declaration, a CAPTCHA/OTP/MFA answer, a password, browser
cookies, storage state or authentication tokens. A review checklist may include
the user's own profile facts (name, contact, education, experience, skills)
because it is their data, but no secret or browser state is included.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.application_intervention import InterventionStatus, InterventionType
from app.schemas.application_fill import FillExecutionResponse, FillResumeSelection
from app.schemas.browser_session import BrowserViewInfo


class InterventionResponse(BaseModel):
    intervention_id: UUID
    application_id: UUID
    browser_session_id: UUID | None
    execution_id: UUID | None
    analysis_id: UUID | None
    analysis_version: int | None
    field_id: str | None
    field_label: str
    field_category: str
    required: bool
    reason: str
    intervention_type: InterventionType
    status: InterventionStatus
    resolution_reason: str | None
    created_at: datetime
    updated_at: datetime
    user_working_at: datetime | None
    resolved_at: datetime | None
    cancelled_at: datetime | None
    expired_at: datetime | None


class InterventionListResponse(BaseModel):
    application_id: UUID
    application_url: str
    browser_session_id: UUID | None
    view: BrowserViewInfo
    total: int
    pending_count: int
    user_working_count: int
    blocking_count: int
    interventions: list[InterventionResponse] = Field(default_factory=list)
    final_submission_note: str


class FormFieldChange(BaseModel):
    """A safe diff between two analysis versions; values are never included."""

    change: str
    field_id: str | None
    label: str
    previous_label: str | None = None
    field_type: str | None = None
    previous_field_type: str | None = None
    required: bool | None = None
    previous_required: bool | None = None
    options_changed: bool = False
    now_has_value: bool = False
    previously_had_value: bool = False


class HumanLoopReconcileResponse(BaseModel):
    application_id: UUID
    analysis_id: UUID
    analysis_version: int
    previous_analysis_id: UUID | None = None
    previous_analysis_version: int | None = None
    page_url: str | None
    field_count: int
    changes: list[FormFieldChange] = Field(default_factory=list)
    newly_completed_fields: list[str] = Field(default_factory=list)
    blocked_count: int = 0
    interventions: list[InterventionResponse] = Field(default_factory=list)
    execution: FillExecutionResponse | None = None
    view: BrowserViewInfo
    manual_handoff_required: bool
    verification_note: str
    final_submission_note: str


class ReviewItem(BaseModel):
    label: str
    present: bool
    optional: bool = False
    detail: str | None = None


class ReviewSection(BaseModel):
    key: str
    label: str
    status: str
    items: list[ReviewItem] = Field(default_factory=list)


class FinalReviewResponse(BaseModel):
    application_id: UUID
    execution_id: UUID | None = None
    execution_status: str
    analysis_version: int | None = None
    ready_for_final_review: bool
    generated_at: datetime
    resume: FillResumeSelection
    sections: list[ReviewSection] = Field(default_factory=list)
    filled_field_labels: list[str] = Field(default_factory=list)
    verified_field_labels: list[str] = Field(default_factory=list)
    skipped_field_labels: list[str] = Field(default_factory=list)
    confirmation_fields: list[str] = Field(default_factory=list)
    blocking_interventions: list[str] = Field(default_factory=list)
    missing_optional_fields: list[str] = Field(default_factory=list)
    filled_count: int = 0
    verified_count: int = 0
    skipped_count: int = 0
    blocker_count: int = 0
    view: BrowserViewInfo
    manual_submission_required: bool = True
    final_submission_note: str


__all__ = [
    "FinalReviewResponse",
    "FormFieldChange",
    "HumanLoopReconcileResponse",
    "InterventionListResponse",
    "InterventionResponse",
    "ReviewItem",
    "ReviewSection",
]
