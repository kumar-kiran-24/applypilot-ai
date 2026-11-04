"""Unit tests for the Gmail API service.

Google's endpoints are replaced by ``httpx.MockTransport``: no network call is
made and no real credential is used. Tokens in these tests are obvious
placeholders, never real secrets.
"""

from __future__ import annotations

import base64
from typing import Any
from uuid import uuid4

import httpx
import pytest
from app.core.config import settings
from app.models.gmail_connection import GmailConnection
from app.services.gmail_service import (
    GMAIL_API_BASE_URL,
    GOOGLE_REVOKE_ENDPOINT,
    GOOGLE_TOKEN_ENDPOINT,
    GmailAPIError,
    GmailClient,
    GmailCredentialsError,
    GmailRateLimitError,
    GmailScopeError,
    parse_gmail_message,
    refresh_gmail_access_token,
    revoke_gmail_access,
)
from app.services.gmail_token_encryption import (
    GmailTokenDecryptionError,
    encrypt_refresh_token,
)

REFRESH_TOKEN = "1//test-refresh-token"
ACCESS_TOKEN = "ya29.test-access-token"


@pytest.fixture(autouse=True)
def gmail_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    from cryptography.fernet import Fernet

    monkeypatch.setattr(settings, "GMAIL_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setattr(settings, "GMAIL_MAX_RETRIES", 0)


def make_connection(**overrides: Any) -> GmailConnection:
    values: dict[str, Any] = {
        "user_id": uuid4(),
        "gmail_email": "candidate@example.com",
        "google_sub": "google-sub-1",
        "encrypted_refresh_token": encrypt_refresh_token(REFRESH_TOKEN),
        "granted_scopes": "https://www.googleapis.com/auth/gmail.readonly",
        "is_active": True,
    }
    values.update(overrides)
    return GmailConnection(**values)


def encode_body(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def client_with(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------
# Token refresh
# ---------------------------------------------------------------------------
async def test_refresh_exchanges_the_stored_refresh_token() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.content.decode()
        return httpx.Response(
            200,
            json={
                "access_token": ACCESS_TOKEN,
                "expires_in": 3599,
                "scope": ("openid email profile https://www.googleapis.com/auth/gmail.readonly"),
            },
        )

    async with client_with(handler) as http:
        result = await refresh_gmail_access_token(make_connection(), client=http)

    assert result.access_token == ACCESS_TOKEN
    assert result.expires_in == 3599
    assert "https://www.googleapis.com/auth/gmail.readonly" in result.scopes
    # The refresh token travels in the POST body, never in the URL.
    assert seen["url"] == GOOGLE_TOKEN_ENDPOINT
    assert REFRESH_TOKEN not in seen["url"]
    assert "grant_type=refresh_token" in seen["body"]
    assert REFRESH_TOKEN not in repr(result)


async def test_refresh_maps_invalid_grant_to_credentials_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    async with client_with(handler) as http:
        with pytest.raises(GmailCredentialsError) as excinfo:
            await refresh_gmail_access_token(make_connection(), client=http)

    assert REFRESH_TOKEN not in str(excinfo.value)
    assert excinfo.value.status_code == 409


async def test_refresh_maps_missing_access_token_to_api_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"expires_in": 100})

    async with client_with(handler) as http:
        with pytest.raises(GmailAPIError):
            await refresh_gmail_access_token(make_connection(), client=http)


async def test_refresh_maps_rate_limits() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "rate_limit_exceeded"})

    async with client_with(handler) as http:
        with pytest.raises(GmailRateLimitError) as excinfo:
            await refresh_gmail_access_token(make_connection(), client=http)

    assert excinfo.value.status_code == 429


async def test_refresh_maps_server_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    async with client_with(handler) as http:
        with pytest.raises(GmailAPIError):
            await refresh_gmail_access_token(make_connection(), client=http)


async def test_refresh_retries_transient_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "GMAIL_MAX_RETRIES", 1)
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(500, text="temporary")
        return httpx.Response(200, json={"access_token": ACCESS_TOKEN, "expires_in": 60})

    async with client_with(handler) as http:
        result = await refresh_gmail_access_token(make_connection(), client=http)

    assert result.access_token == ACCESS_TOKEN
    assert calls["count"] == 2


async def test_refresh_rejects_undecryptable_credentials() -> None:
    connection = make_connection(encrypted_refresh_token="not-a-fernet-token")

    async with client_with(lambda _: httpx.Response(500)) as http:
        with pytest.raises(GmailTokenDecryptionError):
            await refresh_gmail_access_token(connection, client=http)


# ---------------------------------------------------------------------------
# Gmail REST client
# ---------------------------------------------------------------------------
def gmail_messages_handler(request: httpx.Request) -> httpx.Response:
    params = request.url.params
    assert request.headers["authorization"] == f"Bearer {ACCESS_TOKEN}"
    if params.get("pageToken") == "page-2":
        return httpx.Response(200, json={"messages": [{"id": "m3"}]})
    return httpx.Response(
        200,
        json={
            "messages": [{"id": "m1"}, {"id": "m2"}],
            "nextPageToken": "page-2",
            "resultSizeEstimate": 3,
        },
    )


async def test_list_message_ids_follows_pagination() -> None:
    async with (
        client_with(gmail_messages_handler) as http,
        GmailClient(ACCESS_TOKEN, client=http) as gmail,
    ):
        ids = await gmail.list_message_ids(query="from:glassdoor.com", max_results=10)

    assert ids == ["m1", "m2", "m3"]


async def test_list_message_ids_stops_at_max_results() -> None:
    async with (
        client_with(gmail_messages_handler) as http,
        GmailClient(ACCESS_TOKEN, client=http) as gmail,
    ):
        ids = await gmail.list_message_ids(max_results=3, page_size=1)

    assert ids == ["m1", "m2", "m3"]


async def test_list_message_ids_sends_query_and_stops_on_repeated_token() -> None:
    requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(dict(request.url.params))
        return httpx.Response(
            200,
            json={
                "messages": [{"id": f"m{len(requests)}"}],
                "nextPageToken": "same-token",
            },
        )

    async with client_with(handler) as http, GmailClient(ACCESS_TOKEN, client=http) as gmail:
        ids = await gmail.list_message_ids(query="from:glassdoor.com", max_results=50)

    assert ids == ["m1", "m2"]
    assert requests[0]["q"] == "from:glassdoor.com"
    # The repeated token is detected instead of looping forever.
    assert len(requests) == 2


async def test_client_maps_unauthorized_response() -> None:
    with pytest.raises(GmailCredentialsError):
        async with (
            client_with(lambda _: httpx.Response(401)) as http,
            GmailClient(ACCESS_TOKEN, client=http) as gmail,
        ):
            await gmail.list_message_ids()


async def test_client_maps_missing_scope() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={
                "error": {
                    "code": 403,
                    "message": "Insufficient Permission",
                    "errors": [{"reason": "insufficientPermissions"}],
                    "status": "PERMISSION_DENIED",
                }
            },
        )

    async with client_with(handler) as http, GmailClient(ACCESS_TOKEN, client=http) as gmail:
        with pytest.raises(GmailScopeError) as excinfo:
            await gmail.list_message_ids()

    assert excinfo.value.status_code == 403


async def test_client_maps_403_rate_limit() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={"error": {"code": 403, "errors": [{"reason": "rateLimitExceeded"}]}},
        )

    async with client_with(handler) as http, GmailClient(ACCESS_TOKEN, client=http) as gmail:
        with pytest.raises(GmailRateLimitError):
            await gmail.list_message_ids()


async def test_client_uses_the_gmail_api_host() -> None:
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url).split("?")[0])
        return httpx.Response(200, json={"emailAddress": "candidate@example.com"})

    async with client_with(handler) as http, GmailClient(ACCESS_TOKEN, client=http) as gmail:
        await gmail.list_message_ids()

    assert urls == [f"{GMAIL_API_BASE_URL}/users/me/messages"]


# ---------------------------------------------------------------------------
# Message parsing
# ---------------------------------------------------------------------------
def sample_message_payload() -> dict[str, Any]:
    return {
        "id": "msg-1",
        "threadId": "thread-1",
        "snippet": "New jobs for you",
        "labelIds": ["INBOX", "UNREAD"],
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [
                {"name": "Subject", "value": "5 new jobs for you"},
                {
                    "name": "From",
                    "value": "Glassdoor Jobs <noreply@glassdoor.com>",
                },
                {"name": "Date", "value": "Mon, 2 Nov 2026 10:00:00 -0800"},
            ],
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": encode_body("Data Engineer\nAcme Corp\nAustin, TX")},
                },
                {
                    "mimeType": "text/html",
                    "body": {"data": encode_body("<p>Data Engineer</p>")},
                },
                {
                    "mimeType": "application/pdf",
                    "filename": "description.pdf",
                    "body": {
                        "attachmentId": "attachment-1",
                        "data": encode_body("SECRET-ATTACHMENT-BYTES"),
                        "size": 22,
                    },
                },
            ],
        },
    }


def test_parse_gmail_message_extracts_headers_and_bodies() -> None:
    message = parse_gmail_message(sample_message_payload())

    assert message.id == "msg-1"
    assert message.thread_id == "thread-1"
    assert message.subject == "5 new jobs for you"
    assert "glassdoor.com" in message.sender
    assert message.received_at is not None
    assert message.received_at.tzinfo is not None
    assert "Data Engineer" in message.plain_text
    assert "Austin, TX" in message.plain_text
    assert message.html == "<p>Data Engineer</p>"
    assert message.label_ids == ("INBOX", "UNREAD")


def test_parse_gmail_message_skips_attachments() -> None:
    message = parse_gmail_message(sample_message_payload())

    assert "SECRET-ATTACHMENT-BYTES" not in message.plain_text
    assert "SECRET-ATTACHMENT-BYTES" not in message.html


def test_parse_gmail_message_handles_missing_headers() -> None:
    message = parse_gmail_message({"id": "msg-2"})

    assert message.id == "msg-2"
    assert message.subject == ""
    assert message.sender == ""
    assert message.received_at is None
    assert message.plain_text == ""


# ---------------------------------------------------------------------------
# Revocation
# ---------------------------------------------------------------------------
async def test_revoke_sends_token_in_body() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.content.decode()
        return httpx.Response(200)

    async with client_with(handler) as http:
        revoked = await revoke_gmail_access(make_connection(), client=http)

    assert revoked is True
    assert seen["url"] == GOOGLE_REVOKE_ENDPOINT
    assert REFRESH_TOKEN not in seen["url"]
    assert "token=" in seen["body"]


async def test_revoke_failure_is_reported_without_raising() -> None:
    async with client_with(lambda _: httpx.Response(500)) as http:
        revoked = await revoke_gmail_access(make_connection(), client=http)

    assert revoked is False


async def test_revoke_treats_already_invalid_token_as_revoked() -> None:
    async with client_with(lambda _: httpx.Response(400)) as http:
        revoked = await revoke_gmail_access(make_connection(), client=http)

    assert revoked is True


async def test_revoke_handles_undecryptable_credentials() -> None:
    connection = make_connection(encrypted_refresh_token="garbage")

    async with client_with(lambda _: httpx.Response(500)) as http:
        revoked = await revoke_gmail_access(connection, client=http)

    assert revoked is False
