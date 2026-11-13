"""Per-user saved/dismissed state for a discovered job posting.

The `jobs` table is the shared record of a posting as it appeared in the user's
own alert email; it is deduplicated per user and must not carry recommendation
decisions. Which postings a user saved or dismissed is a separate, per-user
decision, so it lives here keyed by ``(user_id, job_id)``.

A single row holds both booleans so a job can never have two competing state
rows: saving and dismissing are two attributes of one decision record, not two
independent resources. Dismissal is reversible (``is_dismissed`` returns to
false) which keeps the original row and its history intact.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.job import Job
    from app.models.user import User


class JobState(Base, TimestampMixin):
    """The authenticated user's saved/dismissed decision for one posting."""

    __tablename__ = "job_states"
    __table_args__ = (
        UniqueConstraint("user_id", "job_id", name="uq_job_states_user_id_job_id"),
        Index("ix_job_states_user_id_is_saved", "user_id", "is_saved"),
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
        index=True,
    )
    is_saved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_dismissed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    saved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    job: Mapped[Job] = relationship()
    user: Mapped[User] = relationship()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<JobState job={self.job_id} saved={self.is_saved} "
            f"dismissed={self.is_dismissed}>"
        )


__all__ = ["JobState"]
