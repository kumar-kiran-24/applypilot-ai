"""User-controlled auto-apply settings and explicit preparation approvals.

The settings in this module are deliberately separate from
:class:`~app.models.preference.JobPreference`: job preferences describe what the
user wants to find, while these settings gate whether ApplyPilot may *prepare*
an application. Defaults are conservative - automation stays off, and both an
explicit approval and a final user review are required.

An approval row records exactly which evaluated eligibility report the user
approved. It never stores a credential, a browser session or a submission.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.preference import RelocationPreference

if TYPE_CHECKING:
    from app.models.application import Application
    from app.models.user import User


class UserAutoApplySettings(Base, TimestampMixin):
    """One row of automation settings per authenticated user."""

    __tablename__ = "user_auto_apply_settings"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_user_auto_apply_settings_user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # --- Master switches -----------------------------------------------------
    automation_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    require_user_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    require_user_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    max_applications_per_day: Mapped[int] = mapped_column(Integer, nullable=False, default=5)

    # --- Job targeting -------------------------------------------------------
    minimum_resume_match_percentage: Mapped[int] = mapped_column(
        Integer, nullable=False, default=60
    )
    preferred_job_titles: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    excluded_job_titles: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    preferred_companies: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    excluded_companies: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    preferred_locations: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    work_modes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    employment_types: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    # --- Compensation and relocation ----------------------------------------
    # No salary is ever inferred: both bounds stay empty until the user sets them.
    minimum_salary: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    maximum_salary: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    relocation_willingness: Mapped[RelocationPreference] = mapped_column(
        SAEnum(
            RelocationPreference,
            name="auto_apply_relocation_preference",
            native_enum=False,
            length=32,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        default=RelocationPreference.UNKNOWN,
    )

    user: Mapped[User] = relationship()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<UserAutoApplySettings user={self.user_id} "
            f"automation={self.automation_enabled}>"
        )


class AutoApplyApproval(Base, TimestampMixin):
    """The user's explicit approval of one evaluated eligibility report."""

    __tablename__ = "auto_apply_approvals"
    __table_args__ = (
        UniqueConstraint("application_id", name="uq_auto_apply_approvals_application_id"),
        Index("ix_auto_apply_approvals_user_id_application_id", "user_id", "application_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
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
    # The exact report version the user approved. A later evaluation that
    # changes any input produces a different version and invalidates this row.
    report_version: Mapped[str] = mapped_column(String(80), nullable=False)
    report_decision: Mapped[str] = mapped_column(String(32), nullable=False)
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    user: Mapped[User] = relationship()
    application: Mapped[Application] = relationship()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<AutoApplyApproval application={self.application_id} "
            f"version={self.report_version!r}>"
        )


__all__ = ["AutoApplyApproval", "UserAutoApplySettings"]
