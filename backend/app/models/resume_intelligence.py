"""Cached Phase 2F.2 resume intelligence.

One row per stored resume, extractor version and content fingerprint. The
extraction itself lives in ``intelligence_data`` so its shape can evolve with
the extractor, while the fingerprint and version make a cached reading reusable
only while the resume content and the extractor are unchanged.

A resume whose content changes keeps its earlier rows: that is what lets a
failed re-extraction fall back to the last successful reading instead of
destroying it. Raw PDF bytes are never stored here, and neither are storage
keys.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.resume import Resume
    from app.models.user import User

#: The extraction produced a full reading (possibly from stored parsed data).
STATUS_COMPLETED = "completed"
#: Nothing usable could be read; ``error_code`` says why, safely.
STATUS_FAILED = "failed"

EXTRACTION_STATUSES = frozenset({STATUS_COMPLETED, STATUS_FAILED})


class ResumeIntelligence(Base, TimestampMixin):
    """One deterministic reading of one resume at one content fingerprint."""

    __tablename__ = "resume_intelligence"
    __table_args__ = (
        UniqueConstraint(
            "resume_id",
            "extractor_version",
            "content_fingerprint",
            name="uq_resume_intelligence_resume_version_fingerprint",
        ),
        Index("ix_resume_intelligence_user_id_resume_id", "user_id", "resume_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    resume_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("resumes.id", ondelete="CASCADE"),
        nullable=False,
    )

    extractor_version: Mapped[str] = mapped_column(String(16), nullable=False)
    # Identity of the resume content the extraction was computed from.
    content_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    # completed | failed
    extraction_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=STATUS_COMPLETED
    )
    confidence: Mapped[str] = mapped_column(String(16), nullable=False, default="low")
    # Safe, machine-readable failure code - never an exception message or path.
    error_code: Mapped[str | None] = mapped_column(String(64))

    # Validated ResumeIntelligencePayload: fields, provenance, confidence per
    # field and warnings. Empty for a failed attempt.
    intelligence_data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    field_confidence: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    warnings: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    extracted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    user: Mapped[User] = relationship()
    resume: Mapped[Resume] = relationship()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<ResumeIntelligence resume={self.resume_id} "
            f"status={self.extraction_status!r} version={self.extractor_version!r}>"
        )


__all__ = [
    "EXTRACTION_STATUSES",
    "STATUS_COMPLETED",
    "STATUS_FAILED",
    "ResumeIntelligence",
]
