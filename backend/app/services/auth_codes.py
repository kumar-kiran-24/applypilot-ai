"""Single-use, short-lived login codes for the browser handoff.

The Google callback never renders a JWT. It redirects the browser to the
frontend with an opaque, random code instead. This module owns the code
lifecycle:

* 256 bits of randomness from :mod:`secrets`;
* only a SHA-256 hash is stored, so a database leak cannot be replayed;
* a hard 60-second (configurable, never longer) expiry;
* redemption is a single conditional ``UPDATE`` that only succeeds while the
  row is unconsumed and unexpired. The loser of a concurrent race updates zero
  rows, so a code can be used exactly once.

Neither the code nor the issued JWT is ever logged by this module.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.auth_code import AuthCode

# token_urlsafe(32) produces 43 URL-safe characters.
CODE_BYTES = 32
CODE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,128}$")

# Consumed/expired rows are pruned once they are this old, so the table stays
# small without ever deleting a row that could still be redeemed.
PURGE_AFTER = timedelta(minutes=10)


class AuthCodeFailure(str, Enum):
    """Why a code could not be redeemed (safe to show to the user)."""

    invalid = "invalid"
    expired = "expired"
    used = "used"


@dataclass(slots=True, frozen=True)
class AuthCodeRedemption:
    """Result of redeeming a code: exactly one of the fields is set."""

    user_id: UUID | None = None
    failure: AuthCodeFailure | None = None


def generate_auth_code() -> str:
    """Return a cryptographically secure, URL-safe authorization code."""
    return secrets.token_urlsafe(CODE_BYTES)


def hash_auth_code(code: str) -> str:
    """Hash a code for storage and lookup (never store the plaintext)."""
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def normalize_auth_code(code: str) -> str | None:
    """Return the trimmed code when it matches the expected shape, else None."""
    candidate = code.strip()
    if not CODE_PATTERN.fullmatch(candidate):
        return None
    return candidate


def _utc(value: datetime) -> datetime:
    """Treat a naive datetime read back from SQLite as UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


async def issue_auth_code(
    db: AsyncSession,
    user_id: UUID,
    *,
    ttl_seconds: int | None = None,
) -> str:
    """Create a code for ``user_id`` and return its plaintext exactly once.

    The caller commits the session before redirecting the browser; otherwise
    the follow-up exchange would not find the code.
    """
    now = datetime.now(timezone.utc)
    ttl = settings.AUTH_CODE_EXPIRE_SECONDS if ttl_seconds is None else ttl_seconds

    # Opportunistic cleanup: every row matching this filter is already dead.
    await db.execute(delete(AuthCode).where(AuthCode.expires_at < now - PURGE_AFTER))

    code = generate_auth_code()
    db.add(
        AuthCode(
            code_hash=hash_auth_code(code),
            user_id=user_id,
            expires_at=now + timedelta(seconds=ttl),
        )
    )
    return code


async def redeem_auth_code(db: AsyncSession, code: str) -> AuthCodeRedemption:
    """Atomically consume ``code`` and return its user.

    The ``UPDATE`` only matches an unconsumed, unexpired row; it commits before
    returning, so a second attempt with the same code always fails. When no row
    is updated, a follow-up read only decides which safe error to report.
    """
    normalized = normalize_auth_code(code)
    if normalized is None:
        return AuthCodeRedemption(failure=AuthCodeFailure.invalid)

    now = datetime.now(timezone.utc)
    digest = hash_auth_code(normalized)

    result = await db.execute(
        update(AuthCode)
        .where(
            AuthCode.code_hash == digest,
            AuthCode.consumed_at.is_(None),
            AuthCode.expires_at > now,
        )
        .values(consumed_at=now)
    )

    if result.rowcount == 1:
        user_id = await db.scalar(
            select(AuthCode.user_id).where(AuthCode.code_hash == digest)
        )
        await db.commit()
        if user_id is None:  # pragma: no cover - row cannot vanish mid-transaction
            return AuthCodeRedemption(failure=AuthCodeFailure.invalid)
        return AuthCodeRedemption(user_id=user_id)

    failure = await _describe_failure(db, digest, now)
    await db.commit()
    return AuthCodeRedemption(failure=failure)


async def _describe_failure(
    db: AsyncSession, digest: str, now: datetime
) -> AuthCodeFailure:
    """Classify an unredeemable code without changing its state."""
    row = (
        await db.execute(
            select(AuthCode.consumed_at, AuthCode.expires_at).where(
                AuthCode.code_hash == digest
            )
        )
    ).first()

    if row is None:
        return AuthCodeFailure.invalid

    consumed_at, expires_at = row
    if consumed_at is not None:
        return AuthCodeFailure.used
    if _utc(expires_at) <= now:
        return AuthCodeFailure.expired
    # The row looks valid but the conditional UPDATE matched nothing: another
    # request consumed it in the meantime. Never re-issue a session for it.
    return AuthCodeFailure.used


__all__ = [
    "AuthCodeFailure",
    "AuthCodeRedemption",
    "generate_auth_code",
    "hash_auth_code",
    "issue_auth_code",
    "normalize_auth_code",
    "redeem_auth_code",
]
