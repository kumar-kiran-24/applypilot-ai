"""Google authentication endpoints.

Flow: ``/google/login`` stores an OAuth state (and OIDC nonce) in the signed
session cookie and redirects to Google; ``/google/callback`` validates that
state, exchanges the authorization code once, verifies the Google identity and
issues an ApplyPilot JWT.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from authlib.integrations.base_client.errors import MismatchingStateError, OAuthError
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.dependencies import get_authenticated_user
from app.core.logging import get_logger
from app.core.security import create_access_token
from app.db.session import get_db
from app.models.user import User
from app.services.google_oauth import (
    GOOGLE_SCOPES,
    describe_oauth_error,
    fetch_google_userinfo,
    google_redirect_uri,
    oauth,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["Authentication"])

SAFE_AUTH_FAILURE_DETAIL = "Google authentication was unsuccessful."
STATE_FAILURE_DETAIL = (
    "Google login could not be verified because the OAuth session cookie was "
    "missing or expired. Start the login again from the same host you are "
    "redirected back to and allow cookies for that site."
)


def _is_email_verified(value: Any) -> bool:
    """Accept Google's boolean flag (and the occasional string form)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1"}
    return False


def _public_user(user: User) -> dict[str, Any]:
    return {
        "id": str(user.id),
        "email": user.email,
        "full_name": user.full_name,
        "profile_picture": user.profile_picture,
    }


@router.get(
    "/google/login",
    summary="Start Google login (open in a browser, not Swagger 'Execute')",
    description=(
        "Redirects the browser to Google's consent screen using the OAuth 2.0 "
        "authorization code flow with the `openid email profile` scopes. This "
        "endpoint is a browser redirect: use a normal navigation or a link, "
        "not Swagger UI's 'Execute' button, which performs a cross-origin fetch."
    ),
    status_code=status.HTTP_302_FOUND,
)
async def google_login(request: Request):
    """Redirect the user to Google for authentication."""
    redirect_uri = google_redirect_uri(request)

    redirect_host = urlparse(redirect_uri).netloc
    request_host = request.url.netloc
    if redirect_host and redirect_host != request_host:
        # Most common local cause of "state not equal": the session cookie is
        # bound to the host the flow started on, while Google sends the browser
        # back to a different host (localhost vs 127.0.0.1).
        logger.warning(
            "OAuth redirect URI host differs from the request host; the session "
            "cookie will not be sent to the callback",
            extra={
                "stage": "login",
                "redirect_host": redirect_host,
                "request_host": request_host,
            },
        )

    logger.info(
        "Starting Google OAuth flow",
        extra={
            "stage": "login",
            "scope": GOOGLE_SCOPES,
            "redirect_host": redirect_host,
        },
    )
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/google/callback")
async def google_callback(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Validate Google's identity response and create or retrieve the user."""
    try:
        token = await oauth.google.authorize_access_token(request)
    except MismatchingStateError as exc:
        logger.warning(
            "OAuth state validation failed",
            extra={"stage": "state_validation", **describe_oauth_error(exc)},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=STATE_FAILURE_DETAIL,
        ) from None
    except OAuthError as exc:
        logger.warning(
            "Google OAuth token exchange failed",
            extra={"stage": "token_exchange", **describe_oauth_error(exc)},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=SAFE_AUTH_FAILURE_DETAIL,
        ) from None

    logger.info("Google token exchange succeeded", extra={"stage": "token_exchange"})

    user_info = await fetch_google_userinfo(token)
    if not user_info:
        logger.warning(
            "Google returned no identity information",
            extra={"stage": "userinfo"},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Google did not return verified identity information.",
        )

    google_sub = str(user_info.get("sub") or "").strip()
    raw_email = user_info.get("email")
    email_verified = _is_email_verified(user_info.get("email_verified"))

    if not google_sub or not isinstance(raw_email, str) or not raw_email.strip():
        logger.warning(
            "Google identity is missing a subject or email",
            extra={"stage": "identity_validation", "has_sub": bool(google_sub)},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A verified Google account is required.",
        )

    if not email_verified:
        logger.warning(
            "Google identity has an unverified email",
            extra={"stage": "identity_validation"},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A verified Google account is required.",
        )

    email = raw_email.strip().lower()
    logger.info("Google identity validated", extra={"stage": "identity_validation"})

    result = await db.execute(select(User).where(User.google_sub == google_sub))
    user = result.scalar_one_or_none()

    if user is None:
        # Never link an existing account using email alone.
        existing_email_id = await db.scalar(select(User.id).where(User.email == email))
        if existing_email_id is not None:
            logger.warning(
                "Google login conflicts with an existing email-only account",
                extra={"stage": "user_lookup"},
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "An account with this email already exists. "
                    "Account linking must be completed separately."
                ),
            )

        full_name = str(user_info.get("name") or "").strip() or email
        picture = user_info.get("picture")
        user = User(
            google_sub=google_sub,
            email=email,
            email_verified=True,
            full_name=full_name,
            profile_picture=str(picture) if picture else None,
            is_active=True,
        )
        db.add(user)

        try:
            await db.commit()
            await db.refresh(user)
        except IntegrityError:
            await db.rollback()
            logger.warning(
                "User creation conflicted with a concurrent request",
                extra={"stage": "user_create"},
            )
            # A parallel login may have inserted the same Google account.
            user = (
                await db.execute(select(User).where(User.google_sub == google_sub))
            ).scalar_one_or_none()
            if user is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Account creation conflict. Please try signing in again.",
                ) from None

        logger.info(
            "User account created",
            extra={"stage": "user_create", "user_id": str(user.id)},
        )
    else:
        logger.info(
            "Existing user account found",
            extra={"stage": "user_lookup", "user_id": str(user.id)},
        )

    if not user.is_active:
        logger.warning(
            "Inactive user rejected",
            extra={"stage": "user_lookup", "user_id": str(user.id)},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This ApplyPilot account is inactive.",
        )

    access_token = create_access_token(subject=user.id)
    logger.info(
        "Access token issued",
        extra={"stage": "jwt_issue", "user_id": str(user.id)},
    )

    return {
        "message": "Google authentication successful.",
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        "user": _public_user(user),
    }


@router.get("/me", summary="Get the authenticated user")
async def get_current_user(
    current_user: User = Depends(get_authenticated_user),
) -> dict[str, Any]:
    """Return the current authenticated user."""
    return _public_user(current_user)
