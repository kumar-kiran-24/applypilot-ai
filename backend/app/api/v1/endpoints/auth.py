"""Google authentication endpoints.

Flow:

* ``/google/login`` stores an OAuth state (and OIDC nonce) in the signed session
  cookie and redirects to Google.
* ``/google/callback`` validates that state, exchanges the authorization code
  once and verifies the Google identity. It **never** renders a token: the
  browser is redirected to the dedicated frontend login callback with a
  cryptographically secure, single-use code.
* ``/login/exchange`` consumes that code atomically and returns the ApplyPilot
  JWT to the frontend's server-side BFF (never to browser JavaScript).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode, urlparse

from authlib.integrations.base_client.errors import MismatchingStateError, OAuthError
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.dependencies import get_authenticated_user
from app.core.logging import get_logger
from app.core.security import create_access_token
from app.db.session import get_db
from app.models.user import User
from app.schemas.auth import AuthCodeExchangeRequest
from app.services.auth_codes import AuthCodeFailure, issue_auth_code, redeem_auth_code
from app.services.google_oauth import (
    GOOGLE_SCOPES,
    describe_oauth_error,
    fetch_google_userinfo,
    google_redirect_uri,
    oauth,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["Authentication"])

# Short, non-sensitive identifiers passed to the frontend login callback as
# `?error=...`. The frontend owns the user-facing copy; nothing from a provider
# error (no description, no code, no token) is ever forwarded.
LOGIN_ERROR_STATE = "state"
LOGIN_ERROR_OAUTH = "oauth"
LOGIN_ERROR_IDENTITY = "identity"
LOGIN_ERROR_EMAIL_UNVERIFIED = "email_unverified"
LOGIN_ERROR_ACCOUNT_EXISTS = "account_exists"
LOGIN_ERROR_INACTIVE = "inactive"
LOGIN_ERROR_UNAVAILABLE = "unavailable"

AUTH_CODE_INVALID_DETAIL = (
    "This sign-in link is not valid. Start again from the login page."
)
AUTH_CODE_EXPIRED_DETAIL = (
    "This sign-in link has expired. Start again from the login page."
)
AUTH_CODE_USED_DETAIL = (
    "This sign-in link was already used. Start again from the login page."
)
AUTH_CODE_ACCOUNT_DETAIL = (
    "The ApplyPilot account for this sign-in link is unavailable."
)

_AUTH_CODE_FAILURE_DETAILS = {
    AuthCodeFailure.invalid: AUTH_CODE_INVALID_DETAIL,
    AuthCodeFailure.expired: AUTH_CODE_EXPIRED_DETAIL,
    AuthCodeFailure.used: AUTH_CODE_USED_DETAIL,
}


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


def _frontend_redirect(**params: str) -> RedirectResponse:
    """Redirect to the configured frontend callback, never to a request value.

    ``AUTH_FRONTEND_REDIRECT_URI`` is validated at startup: absolute http(s),
    no query/fragment, and its origin must be listed in
    ``BACKEND_CORS_ORIGINS``. Only the code (or a fixed error identifier) is
    appended, so this cannot become an open redirect.
    """
    target = settings.AUTH_FRONTEND_REDIRECT_URI
    if params:
        separator = "&" if "?" in target else "?"
        target = f"{target}{separator}{urlencode(params)}"

    response = RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)
    response.headers["Cache-Control"] = "no-store"
    # The URL carries a one-time code: never leak it through a Referer header.
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


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


@router.get(
    "/google/callback",
    summary="Google OAuth callback (browser navigation)",
    description=(
        "Validates the OAuth state from the signed session cookie, verifies the "
        "Google identity and redirects the browser to the frontend login "
        "callback with a single-use code. The ApplyPilot JWT is never rendered "
        "or placed in a URL."
    ),
    responses={303: {"description": "Redirect to the frontend login callback"}},
)
async def google_callback(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Validate Google's identity response and hand a one-time code to the app."""
    try:
        token = await oauth.google.authorize_access_token(request)
    except MismatchingStateError as exc:
        logger.warning(
            "OAuth state validation failed",
            extra={"stage": "state_validation", **describe_oauth_error(exc)},
        )
        return _frontend_redirect(error=LOGIN_ERROR_STATE)
    except OAuthError as exc:
        logger.warning(
            "Google OAuth token exchange failed",
            extra={"stage": "token_exchange", **describe_oauth_error(exc)},
        )
        return _frontend_redirect(error=LOGIN_ERROR_OAUTH)

    logger.info("Google token exchange succeeded", extra={"stage": "token_exchange"})

    user_info = await fetch_google_userinfo(token)
    if not user_info:
        logger.warning(
            "Google returned no identity information",
            extra={"stage": "userinfo"},
        )
        return _frontend_redirect(error=LOGIN_ERROR_IDENTITY)

    google_sub = str(user_info.get("sub") or "").strip()
    raw_email = user_info.get("email")
    email_verified = _is_email_verified(user_info.get("email_verified"))

    if not google_sub or not isinstance(raw_email, str) or not raw_email.strip():
        logger.warning(
            "Google identity is missing a subject or email",
            extra={"stage": "identity_validation", "has_sub": bool(google_sub)},
        )
        return _frontend_redirect(error=LOGIN_ERROR_IDENTITY)

    if not email_verified:
        logger.warning(
            "Google identity has an unverified email",
            extra={"stage": "identity_validation"},
        )
        return _frontend_redirect(error=LOGIN_ERROR_EMAIL_UNVERIFIED)

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
            return _frontend_redirect(error=LOGIN_ERROR_ACCOUNT_EXISTS)

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
                return _frontend_redirect(error=LOGIN_ERROR_UNAVAILABLE)

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
        return _frontend_redirect(error=LOGIN_ERROR_INACTIVE)

    code = await issue_auth_code(db, user.id)
    await db.commit()

    # The code is deliberately not logged (nor is the token it will yield).
    logger.info(
        "Login code issued to the frontend",
        extra={"stage": "login_code", "user_id": str(user.id)},
    )
    return _frontend_redirect(code=code)


@router.post(
    "/login/exchange",
    summary="Exchange a one-time login code for an access token",
    description=(
        "Consumes the single-use code issued by `/auth/google/callback` and "
        "returns the ApplyPilot JWT. Intended for the frontend's server-side "
        "BFF, which stores the token in the HttpOnly session cookie. A code can "
        "be redeemed exactly once and expires within 60 seconds."
    ),
    responses={
        400: {"description": "Invalid, expired or already used code"},
        401: {"description": "The code's account is unavailable"},
        403: {"description": "The account is inactive"},
    },
)
async def exchange_login_code(
    payload: AuthCodeExchangeRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Redeem a login code atomically and issue the ApplyPilot JWT."""
    redemption = await redeem_auth_code(db, payload.code)

    if redemption.failure is not None:
        logger.warning(
            "Login code rejected",
            extra={
                "stage": "login_code_exchange",
                "reason": redemption.failure.value,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_AUTH_CODE_FAILURE_DETAILS[redemption.failure],
        )

    user = (
        await db.scalar(select(User).where(User.id == redemption.user_id))
        if redemption.user_id is not None
        else None
    )
    if user is None:
        logger.warning(
            "Login code referenced an unavailable account",
            extra={"stage": "login_code_exchange"},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=AUTH_CODE_ACCOUNT_DETAIL,
        )

    if not user.is_active:
        logger.warning(
            "Inactive user rejected at code exchange",
            extra={"stage": "login_code_exchange", "user_id": str(user.id)},
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
