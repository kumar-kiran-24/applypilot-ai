"""Reusable authentication dependencies."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import TokenError, decode_access_token
from app.db.session import get_db
from app.models.user import User

# ``auto_error=False`` keeps the 401 payload consistent, while the scheme still
# appears in the OpenAPI document so Swagger shows an "Authorize" button.
bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="ApplyPilotBearer",
    description="ApplyPilot access token (HS256) issued by the Google callback.",
)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_authenticated_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """Validate the JWT and return the authenticated, active user."""
    if credentials is None or not credentials.credentials:
        raise _unauthorized("Authentication required.")

    if credentials.scheme.lower() != "bearer":
        raise _unauthorized("Invalid or expired access token.")

    try:
        claims = decode_access_token(credentials.credentials)
        user_id = UUID(str(claims["sub"]))
    except (TokenError, ValueError, KeyError, TypeError):
        raise _unauthorized("Invalid or expired access token.") from None

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if user is None:
        raise _unauthorized("User account not found or inactive.")

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This ApplyPilot account is inactive.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user
