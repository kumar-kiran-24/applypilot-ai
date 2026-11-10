"""Phase 2E Stage D: durable human-intervention items for one application.

Stage C pauses when a field needs a human. This table records *why* each field
needs the user, so the queue survives reloads and browser expiry. It extends the
existing missing-information queue rather than duplicating its answer data: the
Phase 2C queue still owns question values and answer provenance, while this
table records field-level execution interventions.

Deliberately absent: field values, legal declarations, CAPTCHA/OTP/MFA answers,
passwords, browser cookies, storage state and authentication tokens. A row
contains only the field identifier, label, category, required flag, reason and
status timestamps.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.application import Application
    from app.models.application_field_execution import ApplicationFieldExecution
    from app.models.application_form_analysis import ApplicationFormAnalysis
    from app.models.browser_session import BrowserSession
    from app.models.user import User


def _enum_column(enum: type[Enum], name: str) -> SAEnum:
    return SAEnum(
        enum,
        name=name,
        native_enum=False,
        length=40,
        values_callable=lambda members: [member.value for member in members],
    )


class InterventionType(str, Enum):
    """Why the user must act before automation can continue."""

    MISSING_INFORMATION = "missing_information"
    USER_CONFIRMATION = "user_confirmation"
    STALE_INFORMATION = "stale_information"
    CONFLICTING_INFORMATION = "conflicting_information"
    SENSITIVE_INFORMATION = "sensitive_information"
    UNSUPPORTED_FIELD = "unsupported_field"
    BROWSER_SESSION_EXPIRED = "browser_session_expired"
    FORM_CHANGED = "form_changed"


class InterventionStatus(str, Enum):
    """Lifecycle of one intervention. Only verified conditions resolve."""

    PENDING = "pending"
    USER_WORKING = "user_working"
    RESOLVED = "resolved"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class ApplicationIntervention(Base, TimestampMixin):
    """One field-level or session-level human intervention."""

    __tablename__ = "application_interventions"
    __table_args__ = (
        Index(
            "ix_application_interventions_application_status",
            "application_id",
            "status",
        ),
        Index(
            "ix_application_interventions_execution_field",
            "application_field_execution_id",
            "field_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("applications.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    browser_session_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "browser_sessions.id",
            name="fk_application_interventions_browser_session",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    application_field_execution_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "application_field_executions.id",
            name="fk_application_interventions_execution",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    analysis_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "application_form_analyses.id",
            name="fk_application_interventions_analysis",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    analysis_version: Mapped[int | None] = mapped_column(Integer)

    field_id: Mapped[str | None] = mapped_column(String(255))
    field_label: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    field_category: Mapped[str] = mapped_column(
        String(60), nullable=False, default="unknown"
    )
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    intervention_type: Mapped[InterventionType] = mapped_column(
        _enum_column(InterventionType, "intervention_type"),
        nullable=False,
    )
    status: Mapped[InterventionStatus] = mapped_column(
        _enum_column(InterventionStatus, "intervention_status"),
        nullable=False,
        default=InterventionStatus.PENDING,
        index=True,
    )
    resolution_reason: Mapped[str | None] = mapped_column(Text)
    user_working_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship()
    application: Mapped[Application] = relationship()
    browser_session: Mapped[BrowserSession | None] = relationship()
    execution: Mapped[ApplicationFieldExecution | None] = relationship()
    analysis: Mapped[ApplicationFormAnalysis | None] = relationship()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<ApplicationIntervention application={self.application_id} "
            f"field={self.field_id!r} type={self.intervention_type.value} "
            f"status={self.status.value}>"
        )


__all__ = [
    "ApplicationIntervention",
    "InterventionStatus",
    "InterventionType",
]
