"""Phase 2E Stage C: durable field-fill execution metadata.

The browser and the employer's page cannot be persisted. This row is the
durable, non-secret record of one field-fill run: the analysis version it was
planned from, the current status, per-field outcomes and honest pause/failure
information.

Deliberately absent: filled values, resume contents, cookies, storage state,
passwords, OTP/MFA codes, CAPTCHA answers and hidden authentication tokens. A
field result records only its identifier, action, status, verification flag and
a short human-readable reason.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    DateTime,
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
    from app.models.application import Application
    from app.models.user import User


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _enum_column(enum: type[Enum], name: str) -> SAEnum:
    return SAEnum(
        enum,
        name=name,
        native_enum=False,
        length=32,
        values_callable=lambda members: [member.value for member in members],
    )


class FieldExecutionStatus(str, Enum):
    """Lifecycle of one Stage C fill run."""

    PLANNED = "planned"
    RUNNING = "running"
    NEEDS_USER_INPUT = "needs_user_input"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ApplicationFieldExecution(Base, TimestampMixin):
    """The most recent field-fill run for one owned application."""

    __tablename__ = "application_field_executions"
    __table_args__ = (
        # One execution row per application. Re-planning or a new run advances
        # run_number instead of creating a second execution for the same form.
        UniqueConstraint(
            "application_id", name="uq_application_field_executions_application_id"
        ),
        Index(
            "ix_application_field_executions_user_id_status", "user_id", "status"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "users.id",
            name="fk_application_field_executions_user_id_users",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "applications.id",
            name="fk_application_field_executions_application_id_applications",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )
    browser_session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "browser_sessions.id",
            name="fk_application_field_executions_browser_session",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )
    analysis_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "application_form_analyses.id",
            name="fk_application_field_executions_analysis",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )
    analysis_version: Mapped[int] = mapped_column(Integer, nullable=False)
    run_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[FieldExecutionStatus] = mapped_column(
        _enum_column(FieldExecutionStatus, "field_execution_status"),
        nullable=False,
        default=FieldExecutionStatus.PLANNED,
    )
    selected_resume_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    selected_resume_filename: Mapped[str | None] = mapped_column(String(255))
    # Serialized plan items with the proposed values removed.
    plan: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    # Per-field outcomes: identifier, action, status, verification, reason only.
    results: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    pause_reason: Mapped[str | None] = mapped_column(Text)
    failure_code: Mapped[str | None] = mapped_column(String(80))
    failure_reason: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship()
    application: Mapped[Application] = relationship()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<ApplicationFieldExecution application={self.application_id} "
            f"run={self.run_number} status={self.status.value}>"
        )


__all__ = ["ApplicationFieldExecution", "FieldExecutionStatus"]
