"""Persistent missing-information queue state for one application.

The queue never duplicates questions or answers. Every item is derived from the
application's own :class:`~app.models.application.ApplicationQuestion` rows and
the candidate's reusable answer bank, then summarised here so progress survives
a reload. Only decisions that cannot be derived - dismissed optional questions
and the recalculated status - are stored on this row.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.application import Application


class MissingInformationStatus(str, Enum):
    """Whether an application still needs answers before it can proceed."""

    PENDING = "pending"
    READY_TO_COMPLETE = "ready_to_complete"


class MissingInformationItemStatus(str, Enum):
    """Why one question appears in the missing-information queue."""

    MISSING = "missing"
    SUGGESTED = "suggested"
    STALE = "stale"
    SENSITIVE_CONFIRMATION = "sensitive_confirmation"
    LEGAL_CONFIRMATION = "legal_confirmation"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


def _enum_column(enum: type[Enum], name: str) -> SAEnum:
    """Store enums as short, portable strings (no native PostgreSQL enum)."""
    return SAEnum(
        enum,
        name=name,
        native_enum=False,
        length=32,
        values_callable=lambda members: [member.value for member in members],
    )


class MissingInformationQueue(Base, TimestampMixin):
    """The recalculated summary of one application's information gaps."""

    __tablename__ = "missing_information_queues"
    __table_args__ = (
        UniqueConstraint("application_id", name="uq_missing_information_queues_application_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    application_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("applications.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[MissingInformationStatus] = mapped_column(
        _enum_column(MissingInformationStatus, "missing_information_status"),
        nullable=False,
        default=MissingInformationStatus.PENDING,
    )

    # Denormalised counters make the queue list cheap to read and audit.
    required_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    required_resolved: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    required_remaining: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    optional_unanswered: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stale_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    conflict_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sensitive_confirmation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    matched_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Optional questions the user explicitly skipped. A required question is
    # never suppressed by this list, even if the form later makes it required.
    dismissed_question_keys: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    last_recalculated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    application: Mapped[Application] = relationship()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<MissingInformationQueue application={self.application_id} "
            f"status={self.status.value} remaining={self.required_remaining}>"
        )


__all__ = [
    "MissingInformationItemStatus",
    "MissingInformationQueue",
    "MissingInformationStatus",
]
