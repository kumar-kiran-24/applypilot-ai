"""Cached Phase 2F job understanding.

One row per stored posting. The structured extraction lives in ``data`` so the
shape can evolve with the extractor, while the fingerprint and extractor version
make a cached reading reusable only while the posting text is unchanged.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    Boolean,
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
    from app.models.job import Job
    from app.models.user import User


class JobIntelligence(Base, TimestampMixin):
    """Deterministic + LLM-assisted understanding of one posting."""

    __tablename__ = "job_intelligence"
    __table_args__ = (
        UniqueConstraint("job_id", name="uq_job_intelligence_job_id"),
        Index("ix_job_intelligence_user_id_job_id", "user_id", "job_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
    )

    extractor_version: Mapped[str] = mapped_column(String(16), nullable=False)
    # Identity of the posting text the extraction was computed from.
    job_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence: Mapped[str] = mapped_column(String(16), nullable=False, default="low")
    llm_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # used | disabled | not_configured | failed | skipped_no_description
    llm_status: Mapped[str] = mapped_column(String(32), nullable=False, default="disabled")
    llm_model: Mapped[str | None] = mapped_column(String(100))

    # Validated JobIntelligencePayload: fields, provenance, confidence per field,
    # contradictions, warnings and dropped LLM claims.
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    extracted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    user: Mapped[User] = relationship()
    job: Mapped[Job] = relationship()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<JobIntelligence job={self.job_id} llm_status={self.llm_status!r} "
            f"confidence={self.confidence}>"
        )


__all__ = ["JobIntelligence"]
