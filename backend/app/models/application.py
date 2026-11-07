"""Job application drafts, their questionnaire and their submission state.

An application is owned by an authenticated user and points at one discovered
job. It stores nothing secret: no company password, OTP, session token or
authentication cookie is ever persisted here. Every question keeps its
provenance (profile, resume, previously approved answer, AI suggestion or user
input) so an unverified value can never be submitted as a fact.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
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
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.candidate import Candidate
    from app.models.job import Job
    from app.models.resume import Resume
    from app.models.user import User


def _enum_column(enum: type[Enum], name: str) -> SAEnum:
    """Store enums as short, portable strings (no native PostgreSQL enum)."""
    return SAEnum(
        enum,
        name=name,
        native_enum=False,
        length=32,
        values_callable=lambda members: [member.value for member in members],
    )


class ApplicationStatus(str, Enum):
    """Where an application is in the prepare -> approve -> submit flow.

    The legal moves between these states live in
    :mod:`app.services.application_state`; nothing changes the status directly.
    """

    DRAFT = "draft"
    ANALYZING = "analyzing"
    AWAITING_USER_INFORMATION = "awaiting_user_information"
    READY_FOR_REVIEW = "ready_for_review"
    AWAITING_APPROVAL = "awaiting_approval"
    SUBMITTING = "submitting"
    SUBMITTED = "submitted"
    FAILED = "failed"
    NEEDS_USER_ACTION = "needs_user_action"
    CANCELLED = "cancelled"


class ApplicationStage(str, Enum):
    """Post-submission tracking, updated by the user."""

    APPLIED = "applied"
    SCREENING = "screening"
    INTERVIEW = "interview"
    OFFER = "offer"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class AutomationMode(str, Enum):
    """How far ApplyPilot can drive this particular application."""

    AUTOMATED = "automated"
    ASSISTED = "assisted"
    MANUAL = "manual"


class InspectionMode(str, Enum):
    """How the question list was produced."""

    AUTOMATED = "automated"
    DECLARED = "declared"
    MANUAL = "manual"


class AnswerSource(str, Enum):
    """Provenance of a suggested answer. Never conflate these.

    The canonical Phase 2C names are declared first. The Phase 2B names remain
    as aliases of the same members so existing stored values keep their exact
    meaning (``verified_profile`` is persisted as ``profile``, and so on).
    """

    VERIFIED_PROFILE = "profile"
    RESUME_SUGGESTION = "resume"
    CONFIRMED_REUSABLE_ANSWER = "previously_approved"
    AI_DRAFT = "ai_suggestion"
    USER_ENTERED = "user_input"
    NEEDS_CONFIRMATION = "needs_confirmation"

    # Phase 2B aliases, kept for existing callers and stored-data semantics.
    PROFILE = "profile"
    RESUME = "resume"
    PREVIOUS_ANSWER = "previously_approved"
    AI_SUGGESTION = "ai_suggestion"
    USER_INPUT = "user_input"


class QuestionStatus(str, Enum):
    """Whether a question still needs the user's attention."""

    MISSING = "missing"
    SUGGESTED = "suggested"
    CONFIRMED = "confirmed"


class QuestionType(str, Enum):
    """Field shapes shared by the adapters and the questionnaire UI."""

    TEXT = "text"
    TEXTAREA = "textarea"
    EMAIL = "email"
    PHONE = "phone"
    URL = "url"
    NUMBER = "number"
    DATE = "date"
    BOOLEAN = "boolean"
    SINGLE_CHOICE = "single_choice"
    MULTI_CHOICE = "multi_choice"
    RADIO = "radio"
    CHECKBOX = "checkbox"
    FILE = "file"
    DECLARATION = "declaration"


class Application(Base, TimestampMixin):
    """One user's application for one discovered job."""

    __tablename__ = "applications"
    __table_args__ = (
        UniqueConstraint("user_id", "job_id", name="uq_applications_user_id_job_id"),
        Index("ix_applications_user_id_status", "user_id", "status"),
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
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("candidates.id", ondelete="SET NULL"),
        nullable=True,
    )

    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    platform_name: Mapped[str] = mapped_column(String(100), nullable=False)
    application_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    automation_mode: Mapped[AutomationMode] = mapped_column(
        _enum_column(AutomationMode, "automation_mode"),
        nullable=False,
        default=AutomationMode.MANUAL,
    )
    inspection_mode: Mapped[InspectionMode] = mapped_column(
        _enum_column(InspectionMode, "inspection_mode"),
        nullable=False,
        default=InspectionMode.DECLARED,
    )
    status: Mapped[ApplicationStatus] = mapped_column(
        _enum_column(ApplicationStatus, "application_status"),
        nullable=False,
        default=ApplicationStatus.DRAFT,
    )
    stage: Mapped[ApplicationStage | None] = mapped_column(
        _enum_column(ApplicationStage, "application_stage"),
        nullable=True,
    )
    requires_account: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    resume_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("resumes.id", ondelete="SET NULL"),
        nullable=True,
    )
    resume_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    resume_score: Mapped[float | None] = mapped_column(Float)
    # Audit snapshot: which file was attached, even if the resume is later edited.
    resume_filename: Mapped[str | None] = mapped_column(String(255))

    # Notes from platform detection / form inspection, shown in the UI.
    notes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    # Canonical question keys that still block submission.
    missing_fields: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    approval_fingerprint: Mapped[str | None] = mapped_column(String(64))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submission_reference: Mapped[str | None] = mapped_column(String(255))
    failure_reason: Mapped[str | None] = mapped_column(Text)
    # Non-secret diagnostic detail for the last automation attempt.
    automation_details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    user: Mapped[User] = relationship()
    job: Mapped[Job] = relationship()
    candidate: Mapped[Candidate | None] = relationship()
    resume: Mapped[Resume | None] = relationship()
    questions: Mapped[list[ApplicationQuestion]] = relationship(
        back_populates="application",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ApplicationQuestion.order_index",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Application id={self.id} job={self.job_id} status={self.status.value}>"


class ApplicationQuestion(Base, TimestampMixin):
    """One question on an application form, with its answer provenance."""

    __tablename__ = "application_questions"
    __table_args__ = (
        UniqueConstraint(
            "application_id",
            "question_key",
            name="uq_application_questions_application_id_question_key",
        ),
        Index("ix_application_questions_application_id_section", "application_id", "section"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    application_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("applications.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Stable cross-platform concept, e.g. ``contact.email``. Used for reuse.
    question_key: Mapped[str] = mapped_column(String(100), nullable=False)
    # The platform's own field name/id when the form could be inspected.
    field_identifier: Mapped[str | None] = mapped_column(String(200))
    section: Mapped[str] = mapped_column(String(50), nullable=False)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    help_text: Mapped[str | None] = mapped_column(Text)
    field_type: Mapped[QuestionType] = mapped_column(
        _enum_column(QuestionType, "question_type"),
        nullable=False,
        default=QuestionType.TEXT,
    )
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    options: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    suggested_answer: Mapped[str | None] = mapped_column(Text)
    answer: Mapped[str | None] = mapped_column(Text)
    answer_source: Mapped[AnswerSource] = mapped_column(
        _enum_column(AnswerSource, "answer_source"),
        nullable=False,
        default=AnswerSource.USER_INPUT,
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    requires_confirmation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_sensitive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_legal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[QuestionStatus] = mapped_column(
        _enum_column(QuestionStatus, "question_status"),
        nullable=False,
        default=QuestionStatus.MISSING,
    )
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Provenance detail: which profile record / reusable answer supplied this.
    source_record_id: Mapped[str | None] = mapped_column(String(200))
    # When the user last explicitly confirmed this answer for this application.
    last_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # User-entered note (never a credential) e.g. "waiting on referral".
    note: Mapped[str | None] = mapped_column(Text)

    application: Mapped[Application] = relationship(back_populates="questions")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ApplicationQuestion key={self.question_key!r} status={self.status.value}>"


__all__ = [
    "AnswerSource",
    "Application",
    "ApplicationQuestion",
    "ApplicationStage",
    "ApplicationStatus",
    "AutomationMode",
    "InspectionMode",
    "QuestionStatus",
    "QuestionType",
]
