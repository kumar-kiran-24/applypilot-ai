"""Phase 2E Stage F workflow snapshot schemas.

The snapshot is **derived** from the existing application, browser-session,
form-analysis, execution and intervention records. It is never persisted as a
second source of truth, so it cannot drift from the state machines that own
those records.

Values are non-secret: identifiers, counts, status strings and short reasons.
No answer value, cookie, storage state, OTP/MFA code or credential is included.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class WorkflowAction(BaseModel):
    """One next step the UI may offer, with an honest reason when disabled."""

    action: str
    label: str
    enabled: bool = True
    reason: str | None = None


class WorkflowBrowserSummary(BaseModel):
    session_id: UUID | None = None
    run_number: int | None = None
    state: str | None = None
    browser_status: str | None = None
    current_url: str | None = None
    live: bool = False
    expired: bool = False
    view_available: bool = False


class WorkflowAnalysisSummary(BaseModel):
    analysis_id: UUID | None = None
    version: int | None = None
    field_count: int = 0
    page_url: str | None = None
    analyzed_at: datetime | None = None


class WorkflowExecutionSummary(BaseModel):
    execution_id: UUID | None = None
    execution_status: str | None = None
    run_number: int | None = None
    analysis_version: int | None = None
    succeeded_count: int = 0
    verified_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    pending_confirmation_count: int = 0
    can_pause: bool = False
    can_resume: bool = False


class WorkflowInterventionSummary(BaseModel):
    total: int = 0
    pending_count: int = 0
    user_working_count: int = 0
    blocking_count: int = 0


class SubmissionEvidence(BaseModel):
    """What the user (and employer, when available) confirmed. Never inferred."""

    confirmed_by_user: bool = False
    confirmed_at: datetime | None = None
    reference: str | None = None
    confirmation_url: str | None = None
    confirmation_message: str | None = None
    employer_confirmation_detected: bool = False


class ApplicationWorkflowResponse(BaseModel):
    """One consistent view of where the application is and what comes next."""

    application_id: UUID
    job_id: UUID
    job_title: str
    company: str | None = None
    application_url: str
    status: str
    workflow_stage: str
    submission_phase: str | None = None
    ready_for_final_review: bool = False
    missing_required_count: int = 0
    blocking_items: list[str] = Field(default_factory=list)
    next_actions: list[WorkflowAction] = Field(default_factory=list)
    browser: WorkflowBrowserSummary = Field(default_factory=WorkflowBrowserSummary)
    analysis: WorkflowAnalysisSummary = Field(default_factory=WorkflowAnalysisSummary)
    execution: WorkflowExecutionSummary = Field(default_factory=WorkflowExecutionSummary)
    interventions: WorkflowInterventionSummary = Field(
        default_factory=WorkflowInterventionSummary
    )
    submission: SubmissionEvidence = Field(default_factory=SubmissionEvidence)
    recovery: list[str] = Field(default_factory=list)
    as_of: datetime
    final_submission_note: str


__all__ = [
    "ApplicationWorkflowResponse",
    "SubmissionEvidence",
    "WorkflowAction",
    "WorkflowAnalysisSummary",
    "WorkflowBrowserSummary",
    "WorkflowExecutionSummary",
    "WorkflowInterventionSummary",
]
