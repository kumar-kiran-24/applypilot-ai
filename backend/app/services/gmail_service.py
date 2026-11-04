"""Read-only Gmail API access for an authorised ApplyPilot user.

Design rules enforced here:

* the refresh token is decrypted only for the short-lived token exchange and is
  never written to a log, an error message or a response;
* access tokens are kept in memory for the duration of one operation and are
  never persisted;
* attachments are never downloaded - message bodies are decoded from ``data``
  fields and any part carrying an ``attachmentId`` is skipped;
* Gmail's ``nextPageToken`` is followed with a hard cap, so a large mailbox
  cannot produce an unbounded crawl;
* expired or revoked authorizations, missing scopes, rate limits and API errors
  map to distinct domain errors with safe messages.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote

import httpx

from app.core.config import settings
from app.core.exceptions import AppError, ConfigurationError
from app.core.logging import get_logger
from app.models.gmail_connection import GmailConnection
from app.services.gmail_token_encryption import decrypt_refresh_token

logger = get_logger(__name__)

GMAIL_API_BASE_URL = "https://gmail.googleapis.com/gmail/v1"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"

# Hard cap on a decoded message body; job alerts are far smaller than this.
MAX_BODY_CHARS = 200_000
# Hard cap on how many messages one discovery call may fetch.
MAX_MESSAGES_PER_CALL = 100

_SCOPE_ERROR_REASONS = {"insufficientpermissions", "insufficient_scope"}
_RATE_LIMIT_REASONS = {"ratelimitexceeded", "userratelimitexceeded", "quotaexceeded"}
_REVOKED_REFRESH_TOKEN_ERRORS = {"invalid_grant", "invalid_token", "unauthorized_client"}


class GmailNotConnectedError(AppError):
    """The user has no active Gmail connection."""

    status_code = 404
    code = "gmail_not_connected"
    message = "Gmail is not connected for this account."


class GmailCredentialsError(AppError):
    """The stored Gmail authorization is expired or revoked."""

    status_code = 409
    code = "gmail_credentials_invalid"
    message = "Gmail access is no longer authorized. Reconnect Gmail to continue."


class GmailScopeError(AppError):
    """The granted scopes do not include read-only Gmail access."""

    status_code = 403
    code = "gmail_scope_missing"
    message = "Gmail read-only access was not granted. Reconnect Gmail to continue."


class GmailRateLimitError(AppError):
    """Google kept returning HTTP 429 after the configured retries."""

    status_code = 429
    code = "gmail_rate_limited"
    message = "Gmail is rate limiting requests. Try again in a moment."


class GmailAPIError(AppError):
    """Gmail returned an unexpected error response."""

    status_code = 502
    code = "gmail_api_error"
    message = "Gmail could not be reached. Try again in a moment."


@dataclass(slots=True)
class GmailAccessToken:
    """A short-lived access token; never persisted."""

    access_token: str = field(repr=False)
    expires_in: int
    scopes: tuple[str, ...]


@dataclass(slots=True)
class GmailMessage:
    """Metadata and decoded text bodies of one message (no attachments)."""

    id: str
    thread_id: str | None
    subject: str
    sender: str
    received_at: datetime | None
    snippet: str
    plain_text: str
    html: str
    label_ids: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Token exchange
# ---------------------------------------------------------------------------
def _parse_scope_string(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return tuple(scope for scope in value.split() if scope)
    if isinstance(value, (list, tuple, set)):
        return tuple(str(scope) for scope in value if scope)
    return ()


def _json_body(response: httpx.Response) -> dict[str, Any]:
    """Return a JSON object from a response, or an empty dict."""
    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _error_code(payload: dict[str, Any]) -> str:
    raw = payload.get("error")
    if isinstance(raw, dict):
        raw = raw.get("status") or raw.get("message")
    return str(raw or "").strip().lower()


def _retry_delay(attempt: int) -> float:
    return min(0.5 * (2**attempt), 4.0)


async def refresh_gmail_access_token(
    connection: GmailConnection,
    *,
    client: httpx.AsyncClient | None = None,
) -> GmailAccessToken:
    """Exchange the stored refresh token for a short-lived access token.

    The refresh token is decrypted in memory for this call only. It is sent in
    the request body (never the URL) and is never logged.
    """
    client_id = settings.GOOGLE_CLIENT_ID
    client_secret = settings.GOOGLE_CLIENT_SECRET
    if not client_id or not client_secret:
        raise ConfigurationError("Google OAuth credentials are not configured.")

    refresh_token = decrypt_refresh_token(connection.encrypted_refresh_token)

    owned_client = client is None
    http = client or httpx.AsyncClient(timeout=settings.GMAIL_API_TIMEOUT_SECONDS)
    try:
        response = await _post_token_request(
            http,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
    finally:
        if owned_client:
            await http.aclose()

    payload = _json_body(response)
    status_code = response.status_code

    if status_code == 200:
        access_token = payload.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            logger.warning(
                "Gmail token refresh returned no access token",
                extra={"stage": "gmail_token_refresh"},
            )
            raise GmailAPIError()
        try:
            expires_in = int(payload.get("expires_in") or 0)
        except (TypeError, ValueError):
            expires_in = 0
        return GmailAccessToken(
            access_token=access_token,
            expires_in=expires_in,
            scopes=_parse_scope_string(payload.get("scope")),
        )

    error = _error_code(payload)
    if status_code in {400, 401} and error in _REVOKED_REFRESH_TOKEN_ERRORS:
        logger.warning(
            "Stored Gmail refresh token was rejected",
            extra={"stage": "gmail_token_refresh", "oauth_error": error},
        )
        raise GmailCredentialsError()
    if status_code == 429:
        raise GmailRateLimitError()
    if error == "invalid_client":
        raise ConfigurationError("Google OAuth client credentials were rejected.")

    logger.warning(
        "Gmail token refresh failed",
        extra={"stage": "gmail_token_refresh", "status_code": status_code},
    )
    raise GmailAPIError()


async def _post_token_request(http: httpx.AsyncClient, *, data: dict[str, str]) -> httpx.Response:
    """POST to Google's token endpoint, retrying 429 and 5xx responses."""
    last_response: httpx.Response | None = None
    for attempt in range(settings.GMAIL_MAX_RETRIES + 1):
        try:
            response = await http.post(
                GOOGLE_TOKEN_ENDPOINT,
                data=data,
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            logger.warning(
                "Gmail token endpoint request failed",
                extra={"stage": "gmail_token_refresh", "error_class": type(exc).__name__},
            )
            raise GmailAPIError() from None

        last_response = response
        retryable = response.status_code == 429 or response.status_code >= 500
        if not retryable or attempt >= settings.GMAIL_MAX_RETRIES:
            break
        await asyncio.sleep(_retry_delay(attempt))

    if last_response is None:  # pragma: no cover - loop always assigns
        raise GmailAPIError()
    return last_response


# ---------------------------------------------------------------------------
# Gmail REST client
# ---------------------------------------------------------------------------
class GmailClient:
    """Minimal async Gmail REST client bound to one access token."""

    def __init__(
        self,
        access_token: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
    ) -> None:
        if not access_token:
            raise GmailCredentialsError()
        self._access_token = access_token
        self._client = client
        self._owns_client = client is None
        self._timeout = timeout if timeout is not None else settings.GMAIL_API_TIMEOUT_SECONDS
        self._max_retries = max_retries if max_retries is not None else settings.GMAIL_MAX_RETRIES

    async def __aenter__(self) -> GmailClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=GMAIL_API_BASE_URL,
                timeout=self._timeout,
                headers={"Accept": "application/json"},
            )
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    @property
    def _optional_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("GmailClient must be used as an async context manager.")
        return self._client

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._access_token}"}

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET a Gmail resource, mapping failures to domain errors.

        A caller-injected client may already carry a ``base_url``; an owned
        client is created with ``GMAIL_API_BASE_URL``.
        """
        client = self._optional_client
        target = path if self._owns_client else f"{GMAIL_API_BASE_URL}{path}"
        response: httpx.Response | None = None

        for attempt in range(self._max_retries + 1):
            try:
                response = await client.get(
                    target,
                    params=params,
                    headers=self._headers,
                )
            except httpx.HTTPError as exc:
                logger.warning(
                    "Gmail API request failed",
                    extra={"stage": "gmail_api", "error_class": type(exc).__name__},
                )
                raise GmailAPIError() from None

            retryable = response.status_code == 429 or response.status_code >= 500
            if not retryable or attempt >= self._max_retries:
                break
            await asyncio.sleep(_retry_delay(attempt))

        if response is None:  # pragma: no cover - loop always assigns
            raise GmailAPIError()

        status_code = response.status_code
        if status_code == 200:
            payload = _json_body(response)
            if not payload:
                raise GmailAPIError()
            return payload

        payload = _json_body(response)
        if status_code == 401:
            raise GmailCredentialsError()
        if status_code == 403:
            reasons = _error_reasons(payload)
            if reasons & _RATE_LIMIT_REASONS:
                raise GmailRateLimitError()
            if reasons & _SCOPE_ERROR_REASONS:
                raise GmailScopeError()
            raise GmailScopeError(
                "Google denied access to this Gmail resource. Reconnect Gmail to continue."
            )
        if status_code == 404:
            raise GmailAPIError("The requested Gmail message was not found.")
        if status_code == 429:
            raise GmailRateLimitError()

        logger.warning(
            "Gmail API returned an error",
            extra={"stage": "gmail_api", "status_code": status_code},
        )
        raise GmailAPIError()

    async def list_message_ids(
        self,
        *,
        query: str | None = None,
        max_results: int = 25,
        page_size: int = 100,
        label_ids: tuple[str, ...] | None = None,
    ) -> list[str]:
        """Return message ids matching ``query``, following ``nextPageToken``."""
        limit = max(1, min(int(max_results), MAX_MESSAGES_PER_CALL))
        size = max(1, min(int(page_size), 500))
        message_ids: list[str] = []
        seen_tokens: set[str] = set()
        page_token: str | None = None

        while len(message_ids) < limit:
            params: dict[str, Any] = {"maxResults": min(size, limit - len(message_ids))}
            if query:
                params["q"] = query
            if label_ids:
                params["labelIds"] = list(label_ids)
            if page_token:
                params["pageToken"] = page_token

            payload = await self._get("/users/me/messages", params)
            for entry in payload.get("messages") or []:
                if not isinstance(entry, dict):
                    continue
                message_id = entry.get("id")
                if isinstance(message_id, str) and message_id:
                    message_ids.append(message_id)

            next_token = payload.get("nextPageToken")
            if not isinstance(next_token, str) or not next_token:
                break
            if next_token in seen_tokens:
                # Defensive: a repeating token would otherwise loop forever.
                break
            seen_tokens.add(next_token)
            page_token = next_token

        return message_ids[:limit]

    async def get_message(self, message_id: str, *, message_format: str = "full") -> GmailMessage:
        """Fetch one message and parse headers plus text bodies (never attachments)."""
        if not message_id:
            raise GmailAPIError("A Gmail message id is required.")
        payload = await self._get(
            f"/users/me/messages/{quote(message_id, safe='')}",
            {"format": message_format},
        )
        return parse_gmail_message(payload)

    async def get_messages(
        self, message_ids: list[str], *, message_format: str = "full"
    ) -> list[GmailMessage]:
        """Fetch messages sequentially; Gmail quotas are per-user, so no fan-out."""
        return [
            await self.get_message(message_id, message_format=message_format)
            for message_id in message_ids
        ]


def _error_reasons(payload: dict[str, Any]) -> set[str]:
    """Return Gmail's machine-readable error reasons, lowercased."""
    errors = payload.get("error")
    if not isinstance(errors, dict):
        return set()
    reasons: set[str] = set()
    for item in errors.get("errors") or []:
        if not isinstance(item, dict):
            continue
        reason = str(item.get("reason") or "").strip().lower()
        if reason:
            reasons.add(reason)
    status = str(errors.get("status") or "").strip().lower()
    if status:
        reasons.add(status)
    return reasons


# ---------------------------------------------------------------------------
# Message parsing
# ---------------------------------------------------------------------------
def _decode_body_data(data: str) -> str:
    """Decode Gmail's urlsafe-base64 body data."""
    if not data:
        return ""
    padded = data + ("=" * (-len(data) % 4))
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
    except (binascii.Error, UnicodeEncodeError, ValueError):
        return ""
    return raw.decode("utf-8", errors="replace")


def _collect_bodies(part: dict[str, Any], sink: dict[str, list[str]]) -> None:
    """Collect text/plain and text/html bodies, skipping attachments entirely."""
    if not isinstance(part, dict):
        return

    body = part.get("body") or {}
    if isinstance(body, dict) and body.get("attachmentId"):
        # Attachment payloads are never fetched or decoded.
        return

    mime_type = str(part.get("mimeType") or "").lower()
    data = body.get("data") if isinstance(body, dict) else None
    if isinstance(data, str) and data:
        if mime_type == "text/plain":
            sink["plain"].append(_decode_body_data(data))
        elif mime_type == "text/html":
            sink["html"].append(_decode_body_data(data))

    for child in part.get("parts") or []:
        _collect_bodies(child, sink)


def _parse_received_at(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def parse_gmail_message(payload: dict[str, Any]) -> GmailMessage:
    """Convert a Gmail API message resource into :class:`GmailMessage`."""
    message_payload = payload.get("payload") or {}
    raw_headers = message_payload.get("headers") or []
    headers: dict[str, str] = {}
    for header in raw_headers:
        if not isinstance(header, dict):
            continue
        name = header.get("name")
        value = header.get("value")
        if isinstance(name, str) and isinstance(value, str):
            headers[name.lower()] = value

    sink: dict[str, list[str]] = {"plain": [], "html": []}
    _collect_bodies(message_payload, sink)

    label_ids = payload.get("labelIds") or []
    return GmailMessage(
        id=str(payload.get("id") or ""),
        thread_id=str(payload["threadId"]) if payload.get("threadId") else None,
        subject=headers.get("subject", "").strip(),
        sender=headers.get("from", "").strip(),
        received_at=_parse_received_at(headers.get("date", "")),
        snippet=str(payload.get("snippet") or ""),
        plain_text="\n".join(sink["plain"])[:MAX_BODY_CHARS],
        html="\n".join(sink["html"])[:MAX_BODY_CHARS],
        label_ids=tuple(str(label) for label in label_ids),
    )


# ---------------------------------------------------------------------------
# Revocation
# ---------------------------------------------------------------------------
async def revoke_gmail_access(
    connection: GmailConnection,
    *,
    client: httpx.AsyncClient | None = None,
) -> bool:
    """Revoke the stored refresh token at Google.

    Returns ``True`` when Google confirms the token is no longer valid, and
    ``False`` on any failure. Callers still delete the local credentials; a
    revocation problem must never block a disconnect or leak the token.
    """
    try:
        refresh_token = decrypt_refresh_token(connection.encrypted_refresh_token)
    except AppError:
        logger.warning(
            "Stored Gmail credentials could not be decrypted for revocation",
            extra={"stage": "gmail_revoke"},
        )
        return False

    owned_client = client is None
    http = client or httpx.AsyncClient(timeout=settings.GMAIL_API_TIMEOUT_SECONDS)
    try:
        response = await http.post(
            GOOGLE_REVOKE_ENDPOINT,
            data={"token": refresh_token},
            headers={"Accept": "application/json"},
        )
    except httpx.HTTPError as exc:
        logger.warning(
            "Gmail token revocation request failed",
            extra={"stage": "gmail_revoke", "error_class": type(exc).__name__},
        )
        return False
    finally:
        if owned_client:
            await http.aclose()

    # 200 = revoked now, 400 = token already invalid/unknown. Both mean the
    # credential can no longer be used.
    if response.status_code in {200, 400}:
        return True
    logger.warning(
        "Gmail token revocation was rejected",
        extra={"stage": "gmail_revoke", "status_code": response.status_code},
    )
    return False


__all__ = [
    "GMAIL_API_BASE_URL",
    "GmailAPIError",
    "GmailAccessToken",
    "GmailClient",
    "GmailCredentialsError",
    "GmailMessage",
    "GmailNotConnectedError",
    "GmailRateLimitError",
    "GmailScopeError",
    "parse_gmail_message",
    "refresh_gmail_access_token",
    "revoke_gmail_access",
]
