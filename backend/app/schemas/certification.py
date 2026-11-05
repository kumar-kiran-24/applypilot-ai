"""Certification request/response schemas."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl, field_validator

from app.schemas.common import ORMModel


class CertificationCreate(BaseModel):
    """Payload adding one certification to a candidate profile."""

    name: str = Field(min_length=1, max_length=200)
    issuer: str | None = Field(default=None, max_length=200)
    issue_date: date | None = None
    expiry_date: date | None = None
    credential_id: str | None = Field(default=None, max_length=200)
    credential_url: HttpUrl | None = None

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            msg = "name must not be blank"
            raise ValueError(msg)
        return cleaned


class CertificationResponse(ORMModel):
    """Public certification record."""

    id: UUID
    name: str
    issuer: str | None = None
    issue_date: date | None = None
    expiry_date: date | None = None
    credential_id: str | None = None
    credential_url: str | None = None
    created_at: datetime | None = None


__all__ = ["CertificationCreate", "CertificationResponse"]
