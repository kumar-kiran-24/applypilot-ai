"""Phase 2E Stage C schemas: previewable fill plans and execution progress.

A plan may show a proposed value because the user is previewing their own
verified profile data. Sensitive, legal and verification fields never carry a
value. Persisted execution records keep only identifiers, statuses,
verification flags and reasons - no filled values and no resume contents.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.application_field import ApplicationField


class FillFieldStatus(str, Enum):
    """Where a field stands in the plan or in a run."""

    # Plan states.
    READY = "ready"
    NEEDS_CONFIRMATION = "needs_confirmation"
    NEEDS_HUMAN_INPUT = "needs_human_input"
    ALREADY_FILLED = "already_filled"
    SKIPPED = "skipped"
    # Result states.
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PENDING_CONFIRMATION = "pending_confirmation"
    PENDING_HUMAN_INPUT = "pending_human_input"


class FillAction(str, Enum):
    """The narrow action the engine plans for a field."""

    FILL = "fill"
    SELECT = "select"
    CHECK = "check"
    UPLOAD = "upload"
    NONE = "none"


class FillResumeSelection(BaseModel):
    """The application resume and whether a file input will receive it."""

    resume_id: UUID | None = None
    filename: str | None = None
    will_upload: bool = False
    file_field_ids: list[str] = Field(default_factory=list)
    reason: str = ""


class FillPlanField(ApplicationField):
    """A canonical application field plus the planner's decision for it.

The preview serializes this exact shape, so the row a user approves is the
row the execution engine will act on; no separate preview model exists.
"""

    action: FillAction = FillAction.NONE
    status: FillFieldStatus = FillFieldStatus.SKIPPED
    reason: str = ""


class FillPlanResponse(BaseModel):
    """A preview of exactly what would be filled, and what never will be.

    ``approval_fingerprint`` is the digest of this preview. Starting or resuming
    a run must echo it; the backend recomputes the digest from the live form and
    refuses to fill when anything material changed.
    """

    application_id: UUID
    analysis_id: UUID
    analysis_version: int
    browser_session_id: UUID
    generated_at: datetime
    page_url: str | None = None
    application_step: str | None = None
    analyzed_at: datetime | None = None
    structure_fingerprint: str = ""
    approval_fingerprint: str = ""
    total_fields: int
    eligible_count: int
    confirmation_count: int
    human_input_count: int
    already_filled_count: int
    skipped_count: int
    resume: FillResumeSelection
    fields: list[FillPlanField] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    executable: bool = False
    final_submission_note: str = (
        "ApplyPilot never clicks Submit, Apply or Finish. The final review and "
        "submission remain under your control."
    )


class StartFieldFillRequest(BaseModel):
    """Start a fill run for the plan the user approved.

    ``approval_fingerprint`` must equal the fingerprint of the current plan; a
    mismatch is rejected instead of typing values the user did not review.
    """

    approval_fingerprint: str = Field(min_length=32, max_length=64)
    restart: bool = False


class ResumeFieldFillRequest(BaseModel):
    """Resume a paused run only while the approved plan is still current."""

    approval_fingerprint: str = Field(min_length=32, max_length=64)


class FillFieldResult(BaseModel):
    field_id: str
    label: str
    action: FillAction
    status: FillFieldStatus
    verified: bool = False
    reason: str = ""
    source: str = "none"


class FillExecutionResponse(BaseModel):
    """Persisted execution progress for one owned application."""

    execution_id: UUID
    application_id: UUID
    analysis_id: UUID
    analysis_version: int
    browser_session_id: UUID
    run_number: int
    status: str
    approval_fingerprint: str | None = None
    total_fields: int
    ready_count: int
    succeeded_count: int
    failed_count: int
    skipped_count: int
    verified_count: int
    pending_confirmation_count: int
    pending_human_input_count: int
    selected_resume_id: UUID | None = None
    selected_resume_filename: str | None = None
    results: list[FillFieldResult] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    pause_reason: str | None = None
    failure_code: str | None = None
    failure_reason: str | None = None
    can_pause: bool = False
    can_resume: bool = False
    can_cancel: bool = False
    started_at: datetime | None = None
    paused_at: datetime | None = None
    completed_at: datetime | None = None
    last_activity_at: datetime | None = None
    final_submission_note: str = (
        "ApplyPilot never clicks Submit, Apply or Finish. The final review and "
        "submission remain under your control."
    )


__all__ = [
    "FillAction",
    "FillExecutionResponse",
    "FillFieldResult",
    "FillFieldStatus",
    "FillPlanField",
    "FillPlanResponse",
    "FillResumeSelection",
    "ResumeFieldFillRequest",
    "StartFieldFillRequest",
]
