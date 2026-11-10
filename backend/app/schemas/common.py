"""Shared schema primitives."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    """Base for response models built from ORM instances."""

    model_config = ConfigDict(from_attributes=True)


class HealthResponse(BaseModel):
    """Payload returned by the health endpoint."""

    status: str = Field(examples=["healthy"])
    service: str = Field(examples=["ApplyPilot AI Backend"])
    version: str = Field(examples=["0.1.0"])


class ReadinessCheck(BaseModel):
    """One readiness probe result, without internal details."""

    name: str
    ok: bool
    detail: str | None = None


class ReadinessResponse(BaseModel):
    """Production-safe readiness payload.

    It reports whether the process can serve requests right now. Browser
    streaming is process-local, so horizontal scaling requires sticky routing
    or a remote browser service; that limitation is stated explicitly.
    """

    status: str = Field(examples=["ready"])
    service: str
    version: str
    checks: list[ReadinessCheck] = Field(default_factory=list)
    automation_enabled: bool = False
    browser_streaming_available: bool = False
    active_browser_sessions: int = 0
    active_browser_views: int = 0
    single_process: bool = True
    note: str


class DeleteResponse(BaseModel):
    """Payload returned after a successful delete."""

    id: UUID
    deleted: bool = True


class ErrorResponse(BaseModel):
    """Normalised error payload returned by exception handlers."""

    detail: str
    code: str
    details: list[dict[str, Any]] | None = None
