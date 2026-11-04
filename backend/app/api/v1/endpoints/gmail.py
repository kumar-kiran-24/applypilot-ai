"""Gmail connection endpoints.

The Gmail flow is deliberately separate from Google login:

``GET /gmail/connect`` requires an ApplyPilot JWT, stores the authenticated user
id in the signed OAuth session cookie and returns Google's consent URL with the
incremental ``gmail.readonly`` scope. ``GET /gmail/callback`` is a browser
navigation: it validates the OAuth state from that cookie, exchanges the
authorization code, verifies the Google ``sub`` against the ApplyPilot user and
stores the refresh token encrypted. The callback never trusts a user id from the
query string and never returns a token.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any
from urllib.parse import urlsplit
from uuid import UUID

from authlib.integrations.base_client.errors import MismatchingStateError, OAuthError
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.dependencies import get_authenticated_user
from app.core.logging import get_logger
from app.db.session import get_db
from app.models.gmail_connection import GmailConnection
from app.models.user import User
from app.schemas.gmail import (
    GlassdoorJobResponse,
    GlassdoorJobsResponse,
    GmailConnectionResponse,
    GmailConnectResponse,
    GmailDisconnectResponse,
    GmailStatusResponse,
)
from app.services.glassdoor_jobs import (
    extract_glassdoor_jobs_from_messages,
    glassdoor_search_query,
)
from app.services.gmail_oauth import (
    GMAIL_AUTHORIZATION_PARAMS,
    GMAIL_READONLY_SCOPE,
    GMAIL_SCOPES,
    GMAIL_SESSION_USER_KEY,
    gmail_oauth,
    gmail_redirect_uri,
)
from app.services.gmail_service import (
    GmailClient,
    GmailCredentialsError,
    GmailNotConnectedError,
    refresh_gmail_access_token,
    revoke_gmail_access,
)
from app.services.gmail_token_encryption import (
    GmailTokenEncryptionError,
    encrypt_refresh_token,
    validate_encryption_key,
)
from app.services.google_oauth import describe_oauth_error, fetch_google_userinfo

logger = get_logger(__name__)

router = APIRouter(prefix="/gmail", tags=["Gmail"])

GMAIL_START_FAILED_DETAIL = "Gmail authorization could not be started."
GMAIL_NOT_CONFIGURED_DETAIL = "Gmail integration is not configured on this server."
GMAIL_ENCRYPTION_NOT_CONFIGURED_DETAIL = (
    "Gmail connections are unavailable because credential encryption is not configured."
)
GMAIL_REDIRECT_NOT_CONFIGURED_DETAIL = (
    "Gmail connections are unavailable because the callback URL is not configured."
)
GMAIL_STATE_DETAIL = (
    "This Gmail connection request could not be verified. Start the Gmail "
    "connection again from ApplyPilot in the same browser."
)
GMAIL_OAUTH_FAILED_DETAIL = "Gmail authorization was unsuccessful."
GMAIL_IDENTITY_DETAIL = "Google did not return verified identity information."
GMAIL_UNVERIFIED_EMAIL_DETAIL = "A verified Google account is required."
GMAIL_ACCOUNT_MISMATCH_DETAIL = "Use the same Google account you use to sign in to ApplyPilot."
GMAIL_SCOPE_DETAIL = (
    "Gmail read-only access was not granted. Reconnect Gmail and allow read-only access."
)
GMAIL_REFRESH_TOKEN_DETAIL = (
    "Google did not return a refresh token. Reconnect Gmail and accept the consent screen."
)
GMAIL_SESSION_USER_DETAIL = "The ApplyPilot account for this Gmail request is unavailable."
GMAIL_STORAGE_DETAIL = "Gmail credentials could not be stored securely."
GMAIL_STORAGE_CONFLICT_DETAIL = "The Gmail connection could not be saved. Try again."


def gmail_configuration_problem() -> tuple[str, str] | None:
    """Return (reason, safe message) when Gmail cannot be used, else ``None``.

    Called before the browser is sent to Google so misconfiguration fails fast
    instead of after the user has granted consent. The reason is a fixed code;
    no configuration value (and no secret) is returned or logged.
    """
    if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET:
        return "google_credentials", GMAIL_NOT_CONFIGURED_DETAIL
    try:
        validate_encryption_key(settings.GMAIL_TOKEN_ENCRYPTION_KEY or "")
    except GmailTokenEncryptionError:
        return "encryption_key", GMAIL_ENCRYPTION_NOT_CONFIGURED_DETAIL
    if settings.is_production:
        # Settings already enforce this at startup; checking again keeps the
        # endpoint safe if a production instance is assembled differently.
        redirect_uri = settings.GMAIL_REDIRECT_URI or ""
        if not redirect_uri or urlsplit(redirect_uri).scheme.lower() != "https":
            return "redirect_uri", GMAIL_REDIRECT_NOT_CONFIGURED_DETAIL
    return None


def _is_email_verified(value: Any) -> bool:
    """Accept Google's boolean flag (and the occasional string form)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1"}
    return False


def _granted_scopes(token: dict[str, Any]) -> tuple[str, ...]:
    """Return the scopes Google granted, from either payload shape."""
    raw = token.get("scope")
    if isinstance(raw, str):
        return tuple(scope for scope in raw.split() if scope)
    raw_list = token.get("scopes")
    if isinstance(raw_list, (list, tuple, set)):
        return tuple(str(scope) for scope in raw_list if scope)
    return ()


def _split_scopes(stored: str | None) -> list[str]:
    if not stored:
        return []
    return sorted({scope for scope in stored.split() if scope})


def _connection_payload(connection: GmailConnection) -> dict[str, Any]:
    """Public view of a connection; encrypted credentials are never exposed."""
    return {
        "gmail_email": connection.gmail_email,
        "connected_at": connection.connected_at,
        "granted_scopes": _split_scopes(connection.granted_scopes),
    }


async def _get_connection(db: AsyncSession, user_id: UUID) -> GmailConnection | None:
    return await db.scalar(select(GmailConnection).where(GmailConnection.user_id == user_id))


@router.get(
    "/connect",
    response_model=GmailConnectResponse,
    summary="Start Gmail read-only authorization",
    description=(
        "Requires an ApplyPilot bearer token. Returns the Google consent URL with "
        "the incremental `gmail.readonly` scope and binds the OAuth state to the "
        "authenticated user in the signed session cookie. Open the returned URL "
        "in a browser; the callback then redirects back to the API host."
    ),
    responses={
        401: {"description": "Missing or invalid ApplyPilot token"},
        503: {"description": "Gmail integration is not configured"},
    },
)
async def start_gmail_connection(
    request: Request,
    current_user: Annotated[User, Depends(get_authenticated_user)],
) -> GmailConnectResponse:
    """Create the OAuth state and return Google's consent URL."""
    problem = gmail_configuration_problem()
    if problem is not None:
        reason, detail = problem
        logger.warning(
            "Gmail connection blocked by configuration",
            extra={"stage": "gmail_connect", "reason": reason},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=detail,
        )

    redirect_uri = gmail_redirect_uri(request)
    try:
        authorization = await gmail_oauth.google.create_authorization_url(
            redirect_uri,
            **GMAIL_AUTHORIZATION_PARAMS,
            login_hint=current_user.email,
        )
        await gmail_oauth.google.save_authorize_data(
            request,
            redirect_uri=redirect_uri,
            **authorization,
        )
    except Exception as exc:
        logger.warning(
            "Gmail authorization could not be started",
            extra={"stage": "gmail_connect", "error_class": type(exc).__name__},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=GMAIL_START_FAILED_DETAIL,
        ) from None

    authorization_url = authorization.get("url")
    if not isinstance(authorization_url, str) or not authorization_url:
        logger.warning(
            "Gmail authorization URL was missing",
            extra={"stage": "gmail_connect"},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=GMAIL_START_FAILED_DETAIL,
        )

    # Binding the flow to this user: the value lives inside the signed session
    # cookie, never in the callback URL.
    request.session[GMAIL_SESSION_USER_KEY] = str(current_user.id)

    logger.info(
        "Gmail authorization started",
        extra={"stage": "gmail_connect", "user_id": str(current_user.id)},
    )
    return GmailConnectResponse(
        authorization_url=authorization_url,
        required_scopes=GMAIL_SCOPES.split(),
        message=(
            "Open the authorization URL in a browser to grant read-only Gmail access "
            "with the same Google account."
        ),
    )


@router.get(
    "/callback",
    response_model=GmailConnectionResponse,
    summary="Gmail OAuth callback (browser navigation)",
    description=(
        "Validates the OAuth state from the signed session cookie, verifies that "
        "the Google account matches the ApplyPilot user, and stores the refresh "
        "token encrypted. Tokens are never returned."
    ),
    responses={
        400: {"description": "Invalid state, missing code or missing scope"},
        401: {"description": "Unverified Google identity"},
        403: {"description": "Different Google account"},
        409: {"description": "Missing refresh token"},
        503: {"description": "Encryption not configured"},
    },
)
async def gmail_callback(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> GmailConnectionResponse | RedirectResponse:
    """Complete the Gmail authorization and persist the encrypted refresh token."""
    bound_user_id = request.session.pop(GMAIL_SESSION_USER_KEY, None)
    if not isinstance(bound_user_id, str) or not bound_user_id:
        logger.warning(
            "Gmail callback had no bound ApplyPilot session",
            extra={"stage": "gmail_state"},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=GMAIL_STATE_DETAIL,
        )

    try:
        token = await gmail_oauth.google.authorize_access_token(request)
    except MismatchingStateError as exc:
        logger.warning(
            "Gmail OAuth state validation failed",
            extra={"stage": "gmail_state_validation", **describe_oauth_error(exc)},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=GMAIL_STATE_DETAIL,
        ) from None
    except OAuthError as exc:
        logger.warning(
            "Gmail OAuth token exchange failed",
            extra={"stage": "gmail_token_exchange", **describe_oauth_error(exc)},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=GMAIL_OAUTH_FAILED_DETAIL,
        ) from None

    try:
        user_id = UUID(bound_user_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=GMAIL_STATE_DETAIL,
        ) from None

    user = await db.scalar(select(User).where(User.id == user_id))
    if user is None or not user.is_active:
        logger.warning(
            "Gmail callback referenced an unavailable ApplyPilot account",
            extra={"stage": "gmail_user_lookup"},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=GMAIL_SESSION_USER_DETAIL,
        )

    user_info = await fetch_google_userinfo(token)
    if not user_info:
        logger.warning(
            "Gmail callback had no Google identity information",
            extra={"stage": "gmail_userinfo"},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=GMAIL_IDENTITY_DETAIL,
        )

    google_sub = str(user_info.get("sub") or "").strip()
    raw_email = user_info.get("email")
    if not google_sub or not isinstance(raw_email, str) or not raw_email.strip():
        logger.warning(
            "Gmail callback identity was incomplete",
            extra={"stage": "gmail_identity_validation"},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=GMAIL_IDENTITY_DETAIL,
        )

    if google_sub != user.google_sub:
        logger.warning(
            "Gmail callback used a different Google account",
            extra={"stage": "gmail_identity_validation", "user_id": str(user.id)},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=GMAIL_ACCOUNT_MISMATCH_DETAIL,
        )

    if not _is_email_verified(user_info.get("email_verified")):
        logger.warning(
            "Gmail callback identity had an unverified email",
            extra={"stage": "gmail_identity_validation", "user_id": str(user.id)},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=GMAIL_UNVERIFIED_EMAIL_DETAIL,
        )

    granted_scopes = _granted_scopes(token)
    if GMAIL_READONLY_SCOPE not in granted_scopes:
        logger.warning(
            "Gmail read-only scope was not granted",
            extra={"stage": "gmail_scope_validation", "user_id": str(user.id)},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=GMAIL_SCOPE_DETAIL,
        )

    existing = await _get_connection(db, user.id)
    refresh_token = token.get("refresh_token")
    if isinstance(refresh_token, str) and refresh_token.strip():
        try:
            encrypted_refresh_token = encrypt_refresh_token(refresh_token)
        except GmailTokenEncryptionError as exc:
            logger.error(
                "Gmail refresh token could not be encrypted",
                extra={"stage": "gmail_storage", "error_code": exc.code},
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=GMAIL_STORAGE_DETAIL,
            ) from None
    elif existing is not None and existing.is_active and existing.encrypted_refresh_token:
        # Google omits the refresh token when the consent already exists; keep
        # the previously encrypted credential instead of failing the reconnect.
        encrypted_refresh_token = existing.encrypted_refresh_token
    else:
        logger.warning(
            "Gmail callback returned no refresh token",
            extra={"stage": "gmail_token_exchange", "user_id": str(user.id)},
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=GMAIL_REFRESH_TOKEN_DETAIL,
        )

    gmail_email = raw_email.strip().lower()
    scopes_text = " ".join(granted_scopes)
    connected_at = datetime.now(timezone.utc)

    connection = existing or GmailConnection(user_id=user.id)
    connection.gmail_email = gmail_email
    connection.google_sub = google_sub
    connection.encrypted_refresh_token = encrypted_refresh_token
    connection.granted_scopes = scopes_text
    connection.connected_at = connected_at
    connection.is_active = True

    if existing is None:
        db.add(connection)

    try:
        await db.commit()
    except IntegrityError:
        # A concurrent callback may have inserted the (unique) connection row.
        await db.rollback()
        logger.warning(
            "Concurrent Gmail connection insert detected",
            extra={"stage": "gmail_storage", "user_id": str(user.id)},
        )
        connection = await _get_connection(db, user.id)
        if connection is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=GMAIL_STORAGE_CONFLICT_DETAIL,
            ) from None
        connection.gmail_email = gmail_email
        connection.google_sub = google_sub
        connection.encrypted_refresh_token = encrypted_refresh_token
        connection.granted_scopes = scopes_text
        connection.connected_at = connected_at
        connection.is_active = True
        await db.commit()

    await db.refresh(connection)

    logger.info(
        "Gmail connection stored",
        extra={
            "stage": "gmail_storage",
            "user_id": str(user.id),
            "reconnected": existing is not None,
        },
    )

    # A browser navigation should never end on a raw JSON page. The target is
    # server-configured only (never taken from the request) and its origin is
    # validated against BACKEND_CORS_ORIGINS when settings load.
    if settings.GMAIL_FRONTEND_REDIRECT_URI:
        response = RedirectResponse(
            settings.GMAIL_FRONTEND_REDIRECT_URI,
            status_code=status.HTTP_303_SEE_OTHER,
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    return GmailConnectionResponse(
        **_connection_payload(connection),
        message="Gmail connected. ApplyPilot can now read job-related emails.",
    )


@router.get(
    "/status",
    response_model=GmailStatusResponse,
    summary="Get Gmail connection status",
    description="Returns the connected Gmail address and timestamp; never a token.",
)
async def gmail_status(
    current_user: Annotated[User, Depends(get_authenticated_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> GmailStatusResponse:
    """Report whether Gmail is connected for the authenticated user."""
    connection = await _get_connection(db, current_user.id)
    if connection is None or not connection.is_active:
        return GmailStatusResponse(connected=False, is_active=False)

    return GmailStatusResponse(
        connected=True,
        is_active=True,
        gmail_email=connection.gmail_email,
        connected_at=connection.connected_at,
        granted_scopes=_split_scopes(connection.granted_scopes),
    )


@router.delete(
    "/disconnect",
    response_model=GmailDisconnectResponse,
    summary="Disconnect Gmail",
    description=(
        "Revokes the refresh token at Google where possible and deletes the stored "
        "encrypted credential. Revocation failures never expose secrets and do not "
        "prevent the local disconnect."
    ),
    responses={404: {"description": "Gmail is not connected"}},
)
async def disconnect_gmail(
    current_user: Annotated[User, Depends(get_authenticated_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> GmailDisconnectResponse:
    """Revoke and remove the stored Gmail credentials."""
    connection = await _get_connection(db, current_user.id)
    if connection is None:
        raise GmailNotConnectedError()

    revoked = await revoke_gmail_access(connection)
    await db.delete(connection)
    await db.commit()

    logger.info(
        "Gmail disconnected",
        extra={
            "stage": "gmail_disconnect",
            "user_id": str(current_user.id),
            "token_revoked": revoked,
        },
    )
    return GmailDisconnectResponse(
        disconnected=True,
        token_revoked=revoked,
        message=(
            "Gmail disconnected and stored credentials removed."
            if revoked
            else "Gmail disconnected and stored credentials removed. "
            "Google revocation was not confirmed."
        ),
    )


@router.get(
    "/glassdoor/jobs",
    response_model=GlassdoorJobsResponse,
    summary="Discover Glassdoor job emails (read-only)",
    description=(
        "Searches recent Glassdoor emails, extracts job recommendations and returns "
        "them without storing anything. Attachments are never downloaded and "
        "Glassdoor itself is never scraped."
    ),
    responses={
        404: {"description": "Gmail is not connected"},
        409: {"description": "Gmail authorization expired or revoked"},
        429: {"description": "Gmail rate limit"},
    },
)
async def list_glassdoor_jobs(
    current_user: Annotated[User, Depends(get_authenticated_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    days: Annotated[int, Query(ge=1, le=365, description="How far back to search")] = 30,
    max_messages: Annotated[int, Query(ge=1, le=100, description="Maximum emails to inspect")] = 25,
) -> GlassdoorJobsResponse:
    """Search Gmail for Glassdoor job alerts and extract their jobs."""
    connection = await db.scalar(
        select(GmailConnection).where(
            GmailConnection.user_id == current_user.id,
            GmailConnection.is_active.is_(True),
        )
    )
    if connection is None:
        raise GmailNotConnectedError()

    try:
        access = await refresh_gmail_access_token(connection)
    except GmailCredentialsError:
        # The stored credential is dead: reflect that in the status endpoint.
        connection.is_active = False
        await db.commit()
        raise

    async with GmailClient(access.access_token) as gmail:
        message_ids = await gmail.list_message_ids(
            query=glassdoor_search_query(days),
            max_results=max_messages,
        )
        messages = await gmail.get_messages(message_ids)

    jobs = extract_glassdoor_jobs_from_messages(messages)
    logger.info(
        "Glassdoor job discovery completed",
        extra={
            "stage": "glassdoor_discovery",
            "user_id": str(current_user.id),
            "messages_scanned": len(messages),
            "jobs_found": len(jobs),
        },
    )
    return GlassdoorJobsResponse(
        messages_scanned=len(messages),
        jobs_found=len(jobs),
        jobs=[GlassdoorJobResponse.model_validate(job) for job in jobs],
    )


__all__ = ["router"]
