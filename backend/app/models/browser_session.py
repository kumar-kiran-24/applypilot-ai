"""Persistent, non-secret metadata for one Phase 2E browser execution.

The browser itself lives in process memory and is owned by
:mod:`app.services.browser_session_manager`. This row is the durable record the
API and UI read: the execution state, the approved navigation origins, the
current (sanitised) URL, checkpoints and honest failure information.

Deliberately absent: employer passwords, OTP/MFA secrets, CAPTCHA answers,
browser cookies, storage state, authentication headers and session tokens.
Nothing in this module may be used to replay an employer session.
"""

from __future__ import annotations

import uuid
from datetime import datetime
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


def _enum_column(enum: type[Enum], name: str) -> SAEnum:
    return SAEnum(
        enum,
        name=name,
        native_enum=False,
        length=32,
        values_callable=lambda members: [member.value for member in members],
    )


class BrowserSessionState(str, Enum):
    """Execution state machine shared by the API, UI and future agent stages.

    Only :mod:`app.services.browser_session_state` changes this value. The
    final two states are terminal; ``FAILED`` can be restarted explicitly.
    """

    CREATED = "created"
    BROWSER_OPENING = "browser_opening"
    BROWSER_READY = "browser_ready"
    ANALYZING_FORM = "analyzing_form"
    FILLING_FIELDS = "filling_fields"
    NEEDS_USER_INPUT = "needs_user_input"
    NEEDS_CAPTCHA = "needs_captcha"
    NEEDS_VERIFICATION = "needs_verification"
    NEEDS_LEGAL_CONFIRMATION = "needs_legal_confirmation"
    READY_FOR_REVIEW = "ready_for_review"
    USER_REVIEWED = "user_reviewed"
    COMPLETED_BY_USER = "completed_by_user"
    PAUSED = "paused"
    FAILED = "failed"
    CANCELLED = "cancelled"


class BrowserSessionStatus(str, Enum):
    """Whether the in-memory browser behind this row is usable right now."""

    OPENING = "opening"
    OPEN = "open"
    CLOSED = "closed"
    EXPIRED = "expired"
    UNAVAILABLE = "unavailable"


class BrowserSession(Base, TimestampMixin):
    """One user's live (or most recent) browser run for one application."""

    __tablename__ = "browser_sessions"
    __table_args__ = (
        # One execution row per application: starting again is idempotent and
        # cannot create a second browser for the same form.
        UniqueConstraint("application_id", name="uq_browser_sessions_application_id"),
        Index("ix_browser_sessions_user_id_browser_status", "user_id", "browser_status"),
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
    # Incremented when an expired/failed run is reopened. Keeps history honest
    # instead of implying the old browser was reused.
    run_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    state: Mapped[BrowserSessionState] = mapped_column(
        _enum_column(BrowserSessionState, "browser_session_state"),
        nullable=False,
        default=BrowserSessionState.CREATED,
    )
    browser_status: Mapped[BrowserSessionStatus] = mapped_column(
        _enum_column(BrowserSessionStatus, "browser_session_status"),
        nullable=False,
        default=BrowserSessionStatus.CLOSED,
    )
    # Origin + path only. Query strings and fragments can carry single-use
    # tokens, so they are stripped before this value is ever written.
    current_url: Mapped[str | None] = mapped_column(String(1000))
    # Exact origins the session may navigate to (scheme://host:port).
    allowed_origins: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    paused_from_state: Mapped[str | None] = mapped_column(String(32))
    pause_reason: Mapped[str | None] = mapped_column(Text)
    failure_code: Mapped[str | None] = mapped_column(String(80))
    failure_reason: Mapped[str | None] = mapped_column(Text)
    # Non-secret execution detail for the next agent stage (detected field
    # counts, last completed step, ...). Must never hold credentials or cookies.
    checkpoint: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship()
    application: Mapped[Application] = relationship()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<BrowserSession id={self.id} application={self.application_id} "
            f"state={self.state.value} browser={self.browser_status.value}>"
        )


__all__ = ["BrowserSession", "BrowserSessionState", "BrowserSessionStatus"]
