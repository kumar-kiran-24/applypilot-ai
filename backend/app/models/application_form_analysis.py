"""Phase 2E Stage B: stored form-detection results.

Only non-secret detection metadata is persisted: field labels, types,
classification, required flags, source provenance and confidence. Passwords,
OTP/MFA codes, CAPTCHA answers, cookies, tokens and raw hidden security values
are never extracted, so they cannot be stored here.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.application import Application
    from app.models.browser_session import BrowserSession
    from app.models.user import User


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ApplicationFormAnalysis(Base, TimestampMixin):
    """One immutable analysis version for an application's current page."""

    __tablename__ = "application_form_analyses"
    __table_args__ = (
        UniqueConstraint(
            "application_id",
            "version",
            name="uq_application_form_analyses_application_version",
        ),
        Index(
            "ix_application_form_analyses_application_version",
            "application_id",
            "version",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "users.id",
            name="fk_application_form_analyses_user_id_users",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "applications.id",
            name="fk_application_form_analyses_application_id_applications",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )
    browser_session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "browser_sessions.id",
            # PostgreSQL identifiers are capped at 63 characters.
            name="fk_application_form_analyses_browser_session",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    # Origin + path only: the session's sanitizer strips query/fragment tokens.
    page_url: Mapped[str | None] = mapped_column(String(1000))
    form_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    field_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Counts and human-readable warnings only; no page HTML or values.
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    # Serialized DetectedFormField records (no secret values).
    fields: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    analyzed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    user: Mapped[User] = relationship()
    application: Mapped[Application] = relationship()
    browser_session: Mapped[BrowserSession] = relationship()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<ApplicationFormAnalysis application={self.application_id} "
            f"version={self.version} fields={self.field_count}>"
        )


__all__ = ["ApplicationFormAnalysis"]
