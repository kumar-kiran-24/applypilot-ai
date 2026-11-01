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


class DeleteResponse(BaseModel):
    """Payload returned after a successful delete."""

    id: UUID
    deleted: bool = True


class ErrorResponse(BaseModel):
    """Normalised error payload returned by exception handlers."""

    detail: str
    code: str
    details: list[dict[str, Any]] | None = None
