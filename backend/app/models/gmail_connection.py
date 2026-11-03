"""Gmail connection model for Google OAuth integration."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class GmailConnection(TimestampMixin, Base):
    """Stores a user's securely connected Gmail account."""

    __tablename__ = "gmail_connections"

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )

    gmail_email: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )

    google_sub: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    encrypted_refresh_token: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    granted_scopes: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    connected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )
