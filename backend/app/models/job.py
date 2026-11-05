"""Discovered job postings owned by an ApplyPilot user.

Glassdoor discovery originally returned jobs without storing them, which left no
stable identity for a posting. This model adds the minimum needed for resume
matching: one row per posting per user, deduplicated by the posting's Glassdoor
identity (or its canonical URL when no id is present). Nothing here is scraped:
rows are created only from emails the user already received.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.user import User


class Job(Base, TimestampMixin):
    """A job posting discovered from the user's own Glassdoor alert emails."""

    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("user_id", "dedupe_key", name="uq_jobs_user_id_dedupe_key"),
        Index("ix_jobs_user_id_received_at", "user_id", "received_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Stable identity of the posting: Glassdoor job id when available, otherwise
    # the canonical job URL. Scoped to the user by the unique constraint above.
    dedupe_key: Mapped[str] = mapped_column(String(500), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="glassdoor")
    job_title: Mapped[str] = mapped_column(String(300), nullable=False)
    company: Mapped[str | None] = mapped_column(String(200))
    location: Mapped[str | None] = mapped_column(String(200))
    job_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    job_description: Mapped[str | None] = mapped_column(Text)
    email_subject: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    message_id: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Job id={self.id} title={self.job_title!r}>"


__all__ = ["Job"]
