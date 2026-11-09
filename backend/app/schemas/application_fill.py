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


class FillPlanField(BaseModel):
    field_id: str
    label: str
    field_type: str
    classification: str
    required: bool
    action: FillAction
    status: FillFieldStatus
    proposed_value: str | None = None
    source: str = "none"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    options: list[str] = Field(default_factory=list)
    reason: str = ""


class FillPlanResponse(BaseModel):
    """A preview of exactly what would be filled, and what never will be."""

    application_id: UUID
    analysis_id: UUID
    analysis_version: int
    browser_session_id: UUID
    generated_at: datetime
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
    """Start a fill run; ``restart`` explicitly starts a new run."""

    restart: bool = False


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
    "StartFieldFillRequest",
]
