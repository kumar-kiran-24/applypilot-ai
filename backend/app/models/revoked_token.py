"""Revoked JWT access tokens.

Access tokens are stateless, so logout is implemented with a small denylist of
``jti`` values. Only a random token identifier, the owner and the original
expiry are stored - never the token itself. Rows are deleted once the token
would have expired anyway, which bounds table growth.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RevokedToken(Base):
    """One access token that must no longer authenticate."""

    __tablename__ = "revoked_tokens"
    __table_args__ = (
        # The per-request revocation lookup is by jti (unique already), the
        # purge job scans by expiry, and incident response finds a user's
        # tokens by user_id.
        Index("ix_revoked_tokens_expires_at", "expires_at"),
        Index("ix_revoked_tokens_user_id", "user_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    jti: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Token expiry as claimed at issue time: once this passes, the row is dead
    # weight because the token is rejected by signature/expiry validation too.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str] = mapped_column(String(40), nullable=False, default="logout")
    revoked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


__all__ = ["RevokedToken"]
