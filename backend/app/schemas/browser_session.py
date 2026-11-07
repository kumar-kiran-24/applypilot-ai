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
    "BrowserSessionCapabilitiesResponse",
    "BrowserSessionListResponse",
    "BrowserSessionResponse",
    "BrowserViewInfo",
    "PauseBrowserSessionRequest",
    "StartBrowserSessionRequest",
]
