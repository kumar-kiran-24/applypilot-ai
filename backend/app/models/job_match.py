"""Cached resume-to-job match results.

A row is keyed by ``(job_id, resume_id)``. Fingerprints of the job posting and
the parsed resume are stored alongside the scores, so a match is recomputed only
when one of the inputs actually changed. Scores are 0-100; a ``None`` sub-score
means that dimension could not be evaluated from the available information.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.job import Job
    from app.models.resume import Resume


class JobResumeMatch(Base, TimestampMixin):
    """One explainable compatibility evaluation of a resume against a job."""

    __tablename__ = "job_resume_matches"
    __table_args__ = (
        UniqueConstraint("job_id", "resume_id", name="uq_job_resume_matches_job_id_resume_id"),
        Index("ix_job_resume_matches_user_id_job_id", "user_id", "job_id"),
        Index("ix_job_resume_matches_user_id_resume_id", "user_id", "resume_id"),
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
    resume_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("resumes.id", ondelete="CASCADE"),
        nullable=False,
    )

    overall_score: Mapped[float] = mapped_column(Float, nullable=False)
    skill_score: Mapped[float | None] = mapped_column(Float)
    experience_score: Mapped[float | None] = mapped_column(Float)
    education_score: Mapped[float | None] = mapped_column(Float)
    title_score: Mapped[float | None] = mapped_column(Float)
    project_score: Mapped[float | None] = mapped_column(Float)

    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    is_recommended: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    matched_required_skills: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    matched_preferred_skills: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    missing_required_skills: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    missing_preferred_skills: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    strengths: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    suggestions: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    explanation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    confidence: Mapped[str] = mapped_column(String(16), nullable=False, default="low")
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    job_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    resume_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    job: Mapped[Job] = relationship()
    resume: Mapped[Resume] = relationship()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<JobResumeMatch job={self.job_id} resume={self.resume_id} "
            f"score={self.overall_score}>"
        )


__all__ = ["JobResumeMatch"]
