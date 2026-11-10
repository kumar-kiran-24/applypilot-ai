"""Stage E live browser-view transport schemas.

The ticket is a short-lived bearer secret for exactly one WebSocket handshake.
No cookie, CDP endpoint, storage state or backend credential is returned.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class BrowserViewTicketResponse(BaseModel):
    browser_session_id: UUID
    ticket: str = Field(description="Single-use, short-lived WebSocket ticket.")
    websocket_path: str = Field(
        description="Same-origin-independent API path; derive the ws/wss origin."
    )
    expires_at: datetime
    mode: str = "cdp_relay"
    frame_format: str = "jpeg"
    can_interact: bool = True
    note: str = (
        "This streams the same backend Playwright page. Complete CAPTCHA, OTP, "
        "MFA and legal declarations yourself. ApplyPilot never clicks the final "
        "Submit/Apply button."
    )


__all__ = ["BrowserViewTicketResponse"]
