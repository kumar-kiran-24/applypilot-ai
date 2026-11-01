"""Reusable answers to job-application questions.

Every answer carries an explicit verification status so that automation never
fabricates facts: only ``VERIFIED`` answers were confirmed by the user,
``DERIVED`` answers were computed from verified profile data, and ``UNKNOWN``
answers must be confirmed before submission.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint, Uuid
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.candidate import Candidate


class VerificationStatus(str, Enum):
    """Provenance of an application answer."""

    VERIFIED = "verified"
    DERIVED = "derived"
    UNKNOWN = "unknown"


class AnswerType(str, Enum):
    """Expected shape of an answer value."""

    TEXT = "text"
    NUMBER = "number"
    BOOLEAN = "boolean"
    DATE = "date"
    SINGLE_CHOICE = "single_choice"
    MULTI_CHOICE = "multi_choice"
    URL = "url"


class ApplicationAnswer(Base, TimestampMixin):
    """A reusable answer keyed by the platform's question identifier."""

    __tablename__ = "application_answers"
    __table_args__ = (
        UniqueConstraint(
            "candidate_id", "question_key", name="uq_application_answer_candidate_key"
        ),
        Index(
            "ix_application_answers_candidate_status",
            "candidate_id",
            "verification_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("candidates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    question_key: Mapped[str] = mapped_column(String(100), nullable=False)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str | None] = mapped_column(Text)
    answer_type: Mapped[AnswerType] = mapped_column(
        SAEnum(
            AnswerType,
            name="answer_type",
            native_enum=False,
            length=32,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        default=AnswerType.TEXT,
    )
    verification_status: Mapped[VerificationStatus] = mapped_column(
        SAEnum(
            VerificationStatus,
            name="verification_status",
            native_enum=False,
            length=32,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        default=VerificationStatus.UNKNOWN,
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    candidate: Mapped[Candidate] = relationship(back_populates="application_answers")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ApplicationAnswer id={self.id} key={self.question_key!r}>"
