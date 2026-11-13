"""Phase 2E browser-session API schemas.

No schema carries a cookie, storage state, credential, OTP, CAPTCHA answer or
authentication token. ``view`` describes whether a live browser can actually be
shown; it is never a fabricated preview URL.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StartBrowserSessionRequest(BaseModel):
    application_id: UUID
    #: Start a new run even if a previous (non-live) run exists.
    restart: bool = False


class PauseBrowserSessionRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class BrowserViewInfo(BaseModel):
    """Whether this deployment can render the live browser to the user."""

    available: bool
    mode: str
    user_can_interact: bool
    reason: str
    required_component: str | None = None


class BrowserSessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    session_id: UUID
    application_id: UUID
    job_id: UUID | None
    job_title: str
    company: str | None
    application_url: str
    #: The original discovered listing, preserved even after a redirect.
    listing_url: str | None = None
    #: The validated application form reached after the user started Easy Apply.
    application_destination_url: str | None = None
    destination_platform: str | None = None
    destination_validated: bool = False
    redirect_status: str = "not_started"
    redirect_flow: bool = False
    run_number: int
    state: str
    browser_status: str
    current_url: str | None
    allowed_origins: list[str]
    pause_reason: str | None
    failure_code: str | None
    failure_reason: str | None
    checkpoint: dict[str, Any]
    active: bool
    can_pause: bool
    can_resume: bool
    can_cancel: bool
    started_at: datetime | None
    last_activity_at: datetime | None
    expires_at: datetime | None
    stopped_at: datetime | None
    view: BrowserViewInfo


class ApplicationRedirectResponse(BaseModel):
    """Where an Easy Apply hand-off landed, with honest validation state."""

    application_id: UUID
    browser_session_id: UUID
    status: str
    message: str
    listing_url: str | None = None
    destination_url: str | None = None
    application_platform: str | None = None
    platform_name: str | None = None
    application_step: str | None = None
    step_supported: bool = False
    destination_validated: bool = False
    awaiting_user_action: bool = False
    can_analyze: bool = False
    authentication_required: bool = False
    warnings: list[str] = Field(default_factory=list)


class BrowserSessionListResponse(BaseModel):
    total: int
    sessions: list[BrowserSessionResponse]


class BrowserSessionCapabilitiesResponse(BaseModel):
    view: BrowserViewInfo
    automation_enabled: bool
    session_ttl_seconds: int
    final_submission_note: str = (
        "ApplyPilot never clicks the final Submit/Apply button. The agent stops "
        "at ready-for-review; you inspect the employer's page and submit it "
        "yourself."
    )


__all__ = [
    "ApplicationRedirectResponse",
    "BrowserSessionCapabilitiesResponse",
    "BrowserSessionListResponse",
    "BrowserSessionResponse",
    "BrowserViewInfo",
    "PauseBrowserSessionRequest",
    "StartBrowserSessionRequest",
]
