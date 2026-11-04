"""Pydantic schemas for the Gmail integration.

No schema in this module can carry an access token or a refresh token: the
connection response exposes only the Gmail address, when it was connected and the
granted scope names.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class GmailConnectResponse(BaseModel):
    """Authorization URL the authenticated client must open in a browser."""

    authorization_url: str
    required_scopes: list[str]
    message: str = Field(
        examples=["Open this URL in a browser to authorize read-only Gmail access."]
    )


class GmailConnectionResponse(BaseModel):
    """Result of a successful Gmail authorization (never contains tokens)."""

    connected: bool = True
    gmail_email: str
    connected_at: datetime | None = None
    granted_scopes: list[str] = Field(default_factory=list)
    message: str


class GmailStatusResponse(BaseModel):
    """Current Gmail connection state for the authenticated user."""

    connected: bool
    is_active: bool = False
    gmail_email: str | None = None
    connected_at: datetime | None = None
    granted_scopes: list[str] = Field(default_factory=list)


class GmailDisconnectResponse(BaseModel):
    """Result of disconnecting Gmail."""

    disconnected: bool
    token_revoked: bool
    message: str


class GlassdoorJobResponse(BaseModel):
    """One job recommendation extracted from a Glassdoor email."""

    model_config = ConfigDict(from_attributes=True)

    job_title: str
    company: str | None = None
    location: str | None = None
    job_url: str
    email_subject: str
    received_at: datetime | None = None
    job_description: str | None = None
    message_id: str


class GlassdoorJobsResponse(BaseModel):
    """Read-only discovery result; nothing is persisted by this endpoint."""

    messages_scanned: int
    jobs_found: int
    jobs: list[GlassdoorJobResponse] = Field(default_factory=list)


__all__ = [
    "GlassdoorJobResponse",
    "GlassdoorJobsResponse",
    "GmailConnectResponse",
    "GmailConnectionResponse",
    "GmailDisconnectResponse",
    "GmailStatusResponse",
]
