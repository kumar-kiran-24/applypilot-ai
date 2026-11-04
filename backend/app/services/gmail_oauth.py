"""Gmail OAuth client, kept separate from the login flow.

The login client (``app.services.google_oauth``) requests only ``openid email
profile`` and is never touched here. This module registers a *second* Authlib
client with the additional ``gmail.readonly`` scope so that Gmail access is
granted incrementally, at the moment the user asks for it.

Both clients share the same OAuth session cookie (``OAUTH_SESSION_SECRET``),
which is what lets the Gmail flow bind its authorization ``state`` to the
authenticated ApplyPilot user: the user id is written into the signed session at
``/gmail/connect`` and read back at ``/gmail/callback``. Nothing about the user
is accepted from the callback query string.
"""

from __future__ import annotations

from authlib.integrations.starlette_client import OAuth
from fastapi import Request

from app.core.config import settings
from app.core.exceptions import ConfigurationError
from app.services.google_oauth import GOOGLE_SCOPES

# Read-only Gmail access. No send, modify, delete or label-management scope.
GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"

# ``openid email profile`` is requested again so the callback can verify the
# Google ``sub`` and the verified email of the account that granted access.
GMAIL_SCOPES = f"{GOOGLE_SCOPES} {GMAIL_READONLY_SCOPE}"

# Session key holding the ApplyPilot user that started the Gmail flow. The value
# lives inside the signed session cookie, so it cannot be forged or injected.
GMAIL_SESSION_USER_KEY = "gmail_oauth_user_id"

# Google only returns a refresh token when offline access is combined with a
# consent prompt. ``select_account`` is always included so the user can pick an
# account; ``consent`` makes the refresh token reliable on re-authorization.
GMAIL_AUTHORIZATION_PARAMS = {
    "access_type": "offline",
    "prompt": "select_account consent",
    "include_granted_scopes": "true",
}

if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET:
    raise RuntimeError("Google OAuth credentials are not configured.")

gmail_oauth = OAuth()

gmail_oauth.register(
    name="google",
    client_id=settings.GOOGLE_CLIENT_ID,
    client_secret=settings.GOOGLE_CLIENT_SECRET,
    server_metadata_url=("https://accounts.google.com/.well-known/openid-configuration"),
    client_kwargs={
        "scope": GMAIL_SCOPES,
    },
)


def gmail_redirect_uri(request: Request) -> str:
    """Return the Gmail callback URL sent to Google.

    An explicit ``GMAIL_REDIRECT_URI`` (registered in Google Cloud Console) wins.
    In development the URL may be derived from the request so the callback lands
    on the same host that stored the OAuth state cookie. Production never derives
    it from the (untrusted) ``Host`` header: the setting is required there and a
    missing value is a configuration error.
    """
    if settings.GMAIL_REDIRECT_URI:
        return settings.GMAIL_REDIRECT_URI
    if settings.is_production:
        raise ConfigurationError("GMAIL_REDIRECT_URI must be configured in production.")
    return str(request.url_for("gmail_callback"))


__all__ = [
    "GMAIL_AUTHORIZATION_PARAMS",
    "GMAIL_READONLY_SCOPE",
    "GMAIL_SCOPES",
    "GMAIL_SESSION_USER_KEY",
    "gmail_oauth",
    "gmail_redirect_uri",
]
