"""Google OAuth / OpenID Connect client configuration.

The client is registered once at import time. ``OAUTH_SESSION_SECRET`` signs the
session cookie Authlib uses to keep the ``state``/``nonce`` values between the
login redirect and the callback, so login and callback *must* be served from the
same host (``localhost`` and ``127.0.0.1`` are different cookie origins).
"""

from __future__ import annotations

import re
from typing import Any

from authlib.integrations.starlette_client import OAuth
from fastapi import Request

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Basic login only: no Drive, Gmail or Calendar scopes.
GOOGLE_SCOPES = "openid email profile"
GOOGLE_USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"

# Authorization codes, tokens and OAuth state are never written to logs.
_SENSITIVE_QUERY_PARAM = re.compile(
    r"(?i)\b(code|state|access_token|refresh_token|id_token|client_secret)=([^&\s]+)"
)

if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET:
    raise RuntimeError("Google OAuth credentials are not configured.")

oauth = OAuth()

oauth.register(
    name="google",
    client_id=settings.GOOGLE_CLIENT_ID,
    client_secret=settings.GOOGLE_CLIENT_SECRET,
    server_metadata_url=(
        "https://accounts.google.com/.well-known/openid-configuration"
    ),
    client_kwargs={
        "scope": GOOGLE_SCOPES,
    },
)


def google_redirect_uri(request: Request) -> str:
    """Return the callback URL that is sent to Google.

    An explicit ``GOOGLE_REDIRECT_URI`` wins because it is what is registered in
    Google Cloud Console. When it is not configured the URL is derived from the
    incoming request, which guarantees the browser returns to the same host that
    received the login redirect (and therefore still sends the session cookie).
    """
    if settings.GOOGLE_REDIRECT_URI:
        return settings.GOOGLE_REDIRECT_URI
    return str(request.url_for("google_callback"))


def describe_oauth_error(exc: BaseException) -> dict[str, Any]:
    """Return log-safe metadata about an OAuth failure.

    Only the exception class, its OAuth ``error`` code and a redacted, truncated
    description are exposed - never codes, tokens, cookies or full URLs.
    """
    description = getattr(exc, "description", None) or str(exc) or ""
    safe_description = _SENSITIVE_QUERY_PARAM.sub(r"\1=[REDACTED]", str(description))
    safe_description = safe_description.replace("\n", " ").strip()[:300]
    return {
        "error_class": type(exc).__name__,
        "oauth_error": getattr(exc, "error", None),
        "description": safe_description,
    }


async def fetch_google_userinfo(token: dict[str, Any]) -> dict[str, Any] | None:
    """Return the validated Google identity for this token.

    Authlib attaches ``userinfo`` after verifying the OpenID Connect ID token
    (signature, issuer, audience and nonce). When that is unavailable the
    standard OIDC userinfo endpoint is used as a fallback.
    """
    userinfo = token.get("userinfo")
    if isinstance(userinfo, dict) and userinfo:
        return dict(userinfo)

    if not token.get("access_token"):
        logger.warning(
            "Google token response contained no identity information",
            extra={"stage": "userinfo"},
        )
        return None

    try:
        response = await oauth.google.get(GOOGLE_USERINFO_ENDPOINT, token=token)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        logger.warning(
            "Google userinfo request failed",
            extra={"stage": "userinfo", "error_class": type(exc).__name__},
        )
        return None

    if not isinstance(payload, dict) or not payload:
        logger.warning("Google userinfo response was empty", extra={"stage": "userinfo"})
        return None
    return payload
