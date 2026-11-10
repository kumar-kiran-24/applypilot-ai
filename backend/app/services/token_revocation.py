"""Server-side JWT revocation (logout invalidation).

A signed access token is valid until it expires. To make logout immediate, the
token's random ``jti`` claim is recorded here and :mod:`app.core.dependencies`
rejects any token whose ``jti`` is present. The table stores no token material,
so a database leak does not hand out credentials.

This is a database-backed denylist, not a Redis cache: the deployment runs a
single API process (browser sessions are process-local), so adding Redis just
for logouts would add an operational dependency without removing the existing
scaling limit. The per-request lookup is a primary-key-style unique index hit.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.revoked_token import RevokedToken

logger = get_logger(__name__)


def _expiry_from_claims(claims: dict[str, object]) -> datetime | None:
    raw = claims.get("exp")
    if not isinstance(raw, int | float):
        return None
    try:
        return datetime.fromtimestamp(float(raw), tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


async def revoke_token(
    session: AsyncSession,
    *,
    jti: str,
    user_id: uuid.UUID,
    claims: dict[str, object],
    reason: str = "logout",
) -> bool:
    """Record one token as revoked. Idempotent (a repeat logout succeeds).

    Returns True when a new row was written, False when it was already revoked.
    """
    expires_at = _expiry_from_claims(claims)
    if expires_at is None:
        # A token without a usable expiry cannot be bounded in the table; the
        # decoder already requires ``exp``, so this is defensive only.
        return False

    session.add(
        RevokedToken(
            jti=jti,
            user_id=user_id,
            expires_at=expires_at,
            reason=reason[:40],
        )
    )
    try:
        await session.commit()
    except IntegrityError:
        # Two concurrent logouts for the same token: both are successful.
        await session.rollback()
        return False
    logger.info(
        "Access token revoked",
        extra={"stage": "jwt_revoke", "user_id": str(user_id)},
    )
    return True


async def is_token_revoked(session: AsyncSession, jti: str) -> bool:
    """Return True when this token id has been revoked."""
    row = await session.scalar(select(RevokedToken.id).where(RevokedToken.jti == jti))
    return row is not None


async def purge_expired_revocations(session: AsyncSession) -> int:
    """Delete rows for tokens that have already expired. Returns rows removed."""
    result = await session.execute(
        delete(RevokedToken).where(
            RevokedToken.expires_at < datetime.now(timezone.utc)
        )
    )
    await session.commit()
    return int(result.rowcount or 0)


__all__ = [
    "is_token_revoked",
    "purge_expired_revocations",
    "revoke_token",
]
