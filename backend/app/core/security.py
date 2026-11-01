"""Security primitives.

Scope: JWT access-token helpers used by future authenticated flows. There is no
placeholder authentication dependency and no password column anywhere in the
data model - when user accounts are introduced, only password *hashes* may be
stored (never plaintext).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import jwt

from app.core.config import Settings, get_settings
from app.core.exceptions import AppError


class TokenError(AppError):
    """Raised when a JWT is missing, malformed, or expired."""

    status_code = 401
    code = "invalid_token"
    message = "The provided token is invalid or has expired."


def _settings(settings: Settings | None) -> Settings:
    return settings or get_settings()


def create_access_token(
    subject: str | UUID,
    *,
    expires_delta: timedelta | None = None,
    extra_claims: dict[str, Any] | None = None,
    settings: Settings | None = None,
) -> str:
    """Create a signed HS256 access token for ``subject``."""
    cfg = _settings(settings)
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(minutes=cfg.ACCESS_TOKEN_EXPIRE_MINUTES))

    claims: dict[str, Any] = {
        "sub": str(subject),
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
        "jti": str(uuid4()),
        "type": "access",
    }
    if extra_claims:
        # Never allow callers to override registered claims.
        claims.update({k: v for k, v in extra_claims.items() if k not in claims})

    return jwt.encode(claims, cfg.JWT_SECRET_KEY, algorithm=cfg.JWT_ALGORITHM)


def decode_access_token(token: str, *, settings: Settings | None = None) -> dict[str, Any]:
    """Verify signature/expiry and return the token claims."""
    cfg = _settings(settings)
    try:
        claims = jwt.decode(
            token,
            cfg.JWT_SECRET_KEY,
            algorithms=[cfg.JWT_ALGORITHM],
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError as exc:  # pragma: no cover - exact class varies by version
        raise TokenError() from exc

    if claims.get("type") != "access":
        raise TokenError("Token is not an access token.")
    return claims
