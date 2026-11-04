"""Tests for the Gmail connection endpoints.

Google's network calls and the Gmail client are mocked, so no real account or
credential is needed. The OAuth state machine runs against the real Authlib
Starlette integration and the app's real SessionMiddleware.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock

import app.api.v1.endpoints.gmail as gmail_endpoints
import pytest
from app.core.config import settings
from app.core.exceptions import ConfigurationError
from app.core.security import create_access_token
from app.models.gmail_connection import GmailConnection
from app.models.user import User
from app.services.gmail_oauth import GMAIL_SCOPES, gmail_oauth, gmail_redirect_uri
from app.services.gmail_service import (
    GmailAccessToken,
    GmailCredentialsError,
    GmailMessage,
)
from app.services.gmail_token_encryption import (
    decrypt_refresh_token,
    encrypt_refresh_token,
)
from authlib.integrations.base_client.errors import MismatchingStateError, OAuthError
from cryptography.fernet import Fernet
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

CONNECT_PATH = "/api/v1/gmail/connect"
CALLBACK_PATH = "/api/v1/gmail/callback"
STATUS_PATH = "/api/v1/gmail/status"
DISCONNECT_PATH = "/api/v1/gmail/disconnect"
JOBS_PATH = "/api/v1/gmail/glassdoor/jobs"

REFRESH_TOKEN = "1//test-refresh-token"
GOOGLE_SUB = "google-sub-123"

SessionFactory = async_sessionmaker[AsyncSession]

ALERT_HTML = """
<table>
  <tr><td><h2>Data Engineer</h2></td></tr>
  <tr><td>Acme Corp</td></tr>
  <tr><td>Austin, TX</td></tr>
  <tr><td>
    <a href="https://www.glassdoor.com/job-listing/de-JV_1.htm?jobListingId=1234">View job</a>
  </td></tr>
</table>
"""


@pytest.fixture(autouse=True)
def gmail_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        settings, "GMAIL_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode()
    )
    monkeypatch.setattr(
        settings, "GMAIL_REDIRECT_URI", "http://testserver/api/v1/gmail/callback"
    )
    monkeypatch.setattr(settings, "GMAIL_FRONTEND_REDIRECT_URI", None)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def create_user(session_factory: SessionFactory, **overrides: Any) -> User:
    values: dict[str, Any] = {
        "google_sub": GOOGLE_SUB,
        "email": "ada@example.com",
        "email_verified": True,
        "full_name": "Ada Lovelace",
        "is_active": True,
    }
    values.update(overrides)
    async with session_factory() as session:
        user = User(**values)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def create_connection(
    session_factory: SessionFactory, user: User, **overrides: Any
) -> GmailConnection:
    values: dict[str, Any] = {
        "user_id": user.id,
        "gmail_email": user.email,
        "google_sub": user.google_sub,
        "encrypted_refresh_token": encrypt_refresh_token(REFRESH_TOKEN),
        "granted_scopes": GMAIL_SCOPES,
        "is_active": True,
    }
    values.update(overrides)
    async with session_factory() as session:
        connection = GmailConnection(**values)
        session.add(connection)
        await session.commit()
        await session.refresh(connection)
        return connection


async def list_connections(session_factory: SessionFactory) -> list[GmailConnection]:
    async with session_factory() as session:
        result = await session.execute(select(GmailConnection))
        return list(result.scalars())


async def count_users(session_factory: SessionFactory) -> int:
    async with session_factory() as session:
        result = await session.execute(select(User))
        return len(list(result.scalars()))


def gmail_token_payload(
    *,
    refresh_token: str | None = REFRESH_TOKEN,
    scope: str = GMAIL_SCOPES,
    **user_overrides: Any,
) -> dict[str, Any]:
    userinfo: dict[str, Any] = {
        "sub": GOOGLE_SUB,
        "email": "Ada@Example.com",
        "email_verified": True,
        "name": "Ada Lovelace",
    }
    userinfo.update(user_overrides)
    payload: dict[str, Any] = {
        "access_token": "test-google-access-token",
        "token_type": "Bearer",
        "expires_in": 3600,
        "scope": scope,
        "userinfo": userinfo,
    }
    if refresh_token is not None:
        payload["refresh_token"] = refresh_token
    return payload


async def start_flow(
    client: AsyncClient,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
    *,
    state: str = "state-1",
) -> tuple[Any, AsyncMock]:
    """Call /gmail/connect and return (response, mocked create_authorization_url)."""
    authorize = AsyncMock(
        return_value={
            "url": f"https://accounts.google.com/o/oauth2/v2/auth?state={state}",
            "state": state,
        }
    )
    monkeypatch.setattr(gmail_oauth.google, "create_authorization_url", authorize)

    response = await client.get(CONNECT_PATH, headers=bearer(create_access_token(user.id)))
    return response, authorize


async def complete_flow(
    client: AsyncClient,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
    *,
    token: dict[str, Any] | None = None,
    state: str = "state-1",
) -> Any:
    response, _ = await start_flow(client, user, monkeypatch, state=state)
    assert response.status_code == 200, response.text

    exchange = AsyncMock(return_value=token or gmail_token_payload())
    monkeypatch.setattr(gmail_oauth.google, "authorize_access_token", exchange)
    return await client.get(f"{CALLBACK_PATH}?code=auth-code&state={state}")


def glassdoor_message() -> GmailMessage:
    return GmailMessage(
        id="msg-1",
        thread_id="thread-1",
        subject="New jobs for you",
        sender="Glassdoor <noreply@glassdoor.com>",
        received_at=datetime(2026, 11, 2, tzinfo=timezone.utc),
        snippet="Data Engineer at Acme Corp",
        plain_text="",
        html=ALERT_HTML,
    )


# ---------------------------------------------------------------------------
# /connect
# ---------------------------------------------------------------------------
async def test_connect_requires_authentication(client: AsyncClient) -> None:
    response = await client.get(CONNECT_PATH)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


async def test_connect_returns_consent_url_with_incremental_scope(
    client: AsyncClient,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await create_user(session_factory)

    response, authorize = await start_flow(client, user, monkeypatch)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["authorization_url"].startswith("https://accounts.google.com")
    assert "https://www.googleapis.com/auth/gmail.readonly" in body["required_scopes"]
    assert "openid" in body["required_scopes"]

    _, kwargs = authorize.await_args
    assert kwargs["access_type"] == "offline"
    assert "select_account" in kwargs["prompt"]
    assert kwargs["include_granted_scopes"] == "true"
    assert kwargs["login_hint"] == user.email
    assert str(authorize.await_args.args[0]).endswith("/api/v1/gmail/callback")


async def test_connect_fails_cleanly_without_google_credentials(
    client: AsyncClient,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await create_user(session_factory)
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", None)

    response = await client.get(CONNECT_PATH, headers=bearer(create_access_token(user.id)))

    assert response.status_code == 503
    assert response.json()["detail"] == gmail_endpoints.GMAIL_NOT_CONFIGURED_DETAIL


@pytest.mark.parametrize("invalid_key", [None, "not-a-fernet-key"])
async def test_connect_blocks_consent_when_encryption_is_unavailable(
    client: AsyncClient,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
    invalid_key: str | None,
) -> None:
    user = await create_user(session_factory)
    authorize = AsyncMock()
    monkeypatch.setattr(gmail_oauth.google, "create_authorization_url", authorize)
    monkeypatch.setattr(settings, "GMAIL_TOKEN_ENCRYPTION_KEY", invalid_key)

    response = await client.get(CONNECT_PATH, headers=bearer(create_access_token(user.id)))

    assert response.status_code == 503
    assert response.json()["detail"] == (
        gmail_endpoints.GMAIL_ENCRYPTION_NOT_CONFIGURED_DETAIL
    )
    # The user was never sent to Google, so no consent is granted in vain.
    authorize.assert_not_awaited()
    assert "GMAIL_TOKEN_ENCRYPTION_KEY" not in response.text


async def test_connect_blocks_consent_when_production_redirect_is_missing(
    client: AsyncClient,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await create_user(session_factory)
    authorize = AsyncMock()
    monkeypatch.setattr(gmail_oauth.google, "create_authorization_url", authorize)
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "GMAIL_REDIRECT_URI", None)

    response = await client.get(CONNECT_PATH, headers=bearer(create_access_token(user.id)))

    assert response.status_code == 503
    assert response.json()["detail"] == gmail_endpoints.GMAIL_REDIRECT_NOT_CONFIGURED_DETAIL
    authorize.assert_not_awaited()


async def test_connect_blocks_consent_when_production_redirect_is_not_https(
    client: AsyncClient,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await create_user(session_factory)
    authorize = AsyncMock()
    monkeypatch.setattr(gmail_oauth.google, "create_authorization_url", authorize)
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(
        settings, "GMAIL_REDIRECT_URI", "http://testserver/api/v1/gmail/callback"
    )

    response = await client.get(CONNECT_PATH, headers=bearer(create_access_token(user.id)))

    assert response.status_code == 503
    assert response.json()["detail"] == gmail_endpoints.GMAIL_REDIRECT_NOT_CONFIGURED_DETAIL
    authorize.assert_not_awaited()


class _ProductionRequestStub:
    def url_for(self, _name: str) -> str:
        raise AssertionError("production must not derive the redirect from the request")


def test_gmail_redirect_uri_is_required_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "GMAIL_REDIRECT_URI", None)

    with pytest.raises(ConfigurationError):
        gmail_redirect_uri(_ProductionRequestStub())


def test_gmail_redirect_uri_is_derived_in_development(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "GMAIL_REDIRECT_URI", None)

    class _RequestStub:
        def url_for(self, name: str) -> str:
            assert name == "gmail_callback"
            return "http://localhost:8000/api/v1/gmail/callback"

    assert gmail_redirect_uri(_RequestStub()) == (
        "http://localhost:8000/api/v1/gmail/callback"
    )


# ---------------------------------------------------------------------------
# /callback - session and state validation
# ---------------------------------------------------------------------------
async def test_callback_without_a_bound_session_is_rejected(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    exchange = AsyncMock(return_value=gmail_token_payload())
    monkeypatch.setattr(gmail_oauth.google, "authorize_access_token", exchange)

    response = await client.get(f"{CALLBACK_PATH}?code=auth-code&state=state-1")

    assert response.status_code == 400
    assert response.json()["detail"] == gmail_endpoints.GMAIL_STATE_DETAIL
    exchange.assert_not_awaited()


async def test_callback_rejects_mismatching_state(
    client: AsyncClient,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await create_user(session_factory)
    response, _ = await start_flow(client, user, monkeypatch)
    assert response.status_code == 200

    monkeypatch.setattr(
        gmail_oauth.google,
        "authorize_access_token",
        AsyncMock(side_effect=MismatchingStateError()),
    )
    callback = await client.get(f"{CALLBACK_PATH}?code=auth-code&state=state-1")

    assert callback.status_code == 400
    assert callback.json()["detail"] == gmail_endpoints.GMAIL_STATE_DETAIL
    assert "state-1" not in callback.text


async def test_callback_rejects_missing_authorization_code(
    client: AsyncClient,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await create_user(session_factory)
    response, _ = await start_flow(client, user, monkeypatch)
    assert response.status_code == 200

    monkeypatch.setattr(
        gmail_oauth.google,
        "authorize_access_token",
        AsyncMock(
            side_effect=OAuthError(error="invalid_request", description="Missing code parameter")
        ),
    )
    callback = await client.get(f"{CALLBACK_PATH}?state=state-1")

    assert callback.status_code == 400
    assert callback.json()["detail"] == gmail_endpoints.GMAIL_OAUTH_FAILED_DETAIL
    assert "Missing code" not in callback.text


# ---------------------------------------------------------------------------
# /callback - identity verification
# ---------------------------------------------------------------------------
async def test_callback_rejects_unknown_google_identity(
    client: AsyncClient,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await create_user(session_factory)

    # Start the flow first so the session binding exists.
    response, _ = await start_flow(client, user, monkeypatch)
    assert response.status_code == 200
    monkeypatch.setattr(
        gmail_oauth.google,
        "authorize_access_token",
        AsyncMock(return_value={"access_token": "test", "scope": GMAIL_SCOPES}),
    )
    monkeypatch.setattr(
        gmail_endpoints,
        "fetch_google_userinfo",
        AsyncMock(return_value=None),
    )

    callback = await client.get(f"{CALLBACK_PATH}?code=auth-code&state=state-1")

    assert callback.status_code == 401
    assert callback.json()["detail"] == gmail_endpoints.GMAIL_IDENTITY_DETAIL
    assert await list_connections(session_factory) == []


async def test_callback_rejects_a_different_google_account(
    client: AsyncClient,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await create_user(session_factory)

    callback = await complete_flow(
        client,
        user,
        monkeypatch,
        token=gmail_token_payload(sub="another-google-account"),
    )

    assert callback.status_code == 403
    assert callback.json()["detail"] == gmail_endpoints.GMAIL_ACCOUNT_MISMATCH_DETAIL
    assert "another-google-account" not in callback.text
    assert await list_connections(session_factory) == []


async def test_callback_rejects_an_unverified_email(
    client: AsyncClient,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await create_user(session_factory)

    callback = await complete_flow(
        client,
        user,
        monkeypatch,
        token=gmail_token_payload(email_verified=False),
    )

    assert callback.status_code == 401
    assert callback.json()["detail"] == gmail_endpoints.GMAIL_UNVERIFIED_EMAIL_DETAIL
    assert await list_connections(session_factory) == []


async def test_callback_rejects_missing_mailbox_identity(
    client: AsyncClient,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await create_user(session_factory)

    callback = await complete_flow(
        client,
        user,
        monkeypatch,
        token=gmail_token_payload(sub="", email=""),
    )

    assert callback.status_code == 401
    assert await list_connections(session_factory) == []


async def test_callback_requires_the_readonly_scope(
    client: AsyncClient,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await create_user(session_factory)

    callback = await complete_flow(
        client,
        user,
        monkeypatch,
        token=gmail_token_payload(scope="openid email profile"),
    )

    assert callback.status_code == 400
    assert callback.json()["detail"] == gmail_endpoints.GMAIL_SCOPE_DETAIL
    assert await list_connections(session_factory) == []


# ---------------------------------------------------------------------------
# /callback - persistence
# ---------------------------------------------------------------------------
async def test_callback_stores_an_encrypted_refresh_token(
    client: AsyncClient,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await create_user(session_factory)

    callback = await complete_flow(client, user, monkeypatch)

    assert callback.status_code == 200, callback.text
    body = callback.json()
    assert body["connected"] is True
    assert body["gmail_email"] == "ada@example.com"
    assert "https://www.googleapis.com/auth/gmail.readonly" in body["granted_scopes"]
    assert body["connected_at"] is not None
    # No token can appear in the response.
    assert set(body) == {
        "connected",
        "gmail_email",
        "connected_at",
        "granted_scopes",
        "message",
    }
    assert REFRESH_TOKEN not in callback.text
    assert "access_token" not in callback.text

    connections = await list_connections(session_factory)
    assert len(connections) == 1
    stored = connections[0].encrypted_refresh_token
    assert stored != REFRESH_TOKEN
    assert REFRESH_TOKEN not in stored
    assert decrypt_refresh_token(stored) == REFRESH_TOKEN
    assert connections[0].google_sub == GOOGLE_SUB
    assert connections[0].is_active is True
    # The Gmail flow never creates another ApplyPilot user.
    assert await count_users(session_factory) == 1


async def test_callback_without_refresh_token_is_rejected(
    client: AsyncClient,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await create_user(session_factory)

    callback = await complete_flow(
        client, user, monkeypatch, token=gmail_token_payload(refresh_token=None)
    )

    assert callback.status_code == 409
    assert callback.json()["detail"] == gmail_endpoints.GMAIL_REFRESH_TOKEN_DETAIL
    assert await list_connections(session_factory) == []


async def test_callback_keeps_the_existing_token_when_google_omits_it(
    client: AsyncClient,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await create_user(session_factory)
    existing = await create_connection(session_factory, user)
    original_ciphertext = existing.encrypted_refresh_token

    callback = await complete_flow(
        client, user, monkeypatch, token=gmail_token_payload(refresh_token=None)
    )

    assert callback.status_code == 200, callback.text
    connections = await list_connections(session_factory)
    assert len(connections) == 1
    assert connections[0].encrypted_refresh_token == original_ciphertext
    assert decrypt_refresh_token(connections[0].encrypted_refresh_token) == REFRESH_TOKEN


async def test_reconnect_updates_the_single_connection(
    client: AsyncClient,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await create_user(session_factory)

    first = await complete_flow(
        client,
        user,
        monkeypatch,
        token=gmail_token_payload(refresh_token="1//first-token"),
        state="state-1",
    )
    assert first.status_code == 200, first.text

    second = await complete_flow(
        client,
        user,
        monkeypatch,
        token=gmail_token_payload(refresh_token="1//second-token"),
        state="state-2",
    )
    assert second.status_code == 200, second.text

    connections = await list_connections(session_factory)
    assert len(connections) == 1
    assert decrypt_refresh_token(connections[0].encrypted_refresh_token) == "1//second-token"


async def test_reconnect_replaces_a_stale_google_sub(
    client: AsyncClient,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await create_user(session_factory)
    await create_connection(session_factory, user, google_sub="stale-sub")

    callback = await complete_flow(client, user, monkeypatch)

    assert callback.status_code == 200, callback.text
    connections = await list_connections(session_factory)
    assert len(connections) == 1
    assert connections[0].google_sub == GOOGLE_SUB


# ---------------------------------------------------------------------------
# /status
# ---------------------------------------------------------------------------
async def test_status_requires_authentication(client: AsyncClient) -> None:
    response = await client.get(STATUS_PATH)

    assert response.status_code == 401


async def test_status_reports_not_connected(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory)

    response = await client.get(STATUS_PATH, headers=bearer(create_access_token(user.id)))

    assert response.status_code == 200
    assert response.json() == {
        "connected": False,
        "is_active": False,
        "gmail_email": None,
        "connected_at": None,
        "granted_scopes": [],
    }


async def test_status_reports_the_connected_mailbox_without_tokens(
    client: AsyncClient,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await create_user(session_factory)
    await complete_flow(client, user, monkeypatch)

    response = await client.get(STATUS_PATH, headers=bearer(create_access_token(user.id)))

    assert response.status_code == 200
    body = response.json()
    assert body["connected"] is True
    assert body["is_active"] is True
    assert body["gmail_email"] == "ada@example.com"
    assert body["connected_at"] is not None
    assert "https://www.googleapis.com/auth/gmail.readonly" in body["granted_scopes"]
    assert "token" not in response.text
    assert "encrypted" not in response.text


# ---------------------------------------------------------------------------
# /disconnect
# ---------------------------------------------------------------------------
async def test_disconnect_removes_credentials_after_revocation(
    client: AsyncClient,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await create_user(session_factory)
    await complete_flow(client, user, monkeypatch)
    revoke = AsyncMock(return_value=True)
    monkeypatch.setattr(gmail_endpoints, "revoke_gmail_access", revoke)

    response = await client.delete(DISCONNECT_PATH, headers=bearer(create_access_token(user.id)))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["disconnected"] is True
    assert body["token_revoked"] is True
    revoke.assert_awaited_once()
    assert await list_connections(session_factory) == []
    assert REFRESH_TOKEN not in response.text


async def test_disconnect_survives_a_revocation_failure(
    client: AsyncClient,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await create_user(session_factory)
    await create_connection(session_factory, user)
    monkeypatch.setattr(gmail_endpoints, "revoke_gmail_access", AsyncMock(return_value=False))

    response = await client.delete(DISCONNECT_PATH, headers=bearer(create_access_token(user.id)))

    assert response.status_code == 200, response.text
    assert response.json()["token_revoked"] is False
    assert await list_connections(session_factory) == []


async def test_disconnect_when_not_connected(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory)

    response = await client.delete(DISCONNECT_PATH, headers=bearer(create_access_token(user.id)))

    assert response.status_code == 404
    assert response.json()["code"] == "gmail_not_connected"


# ---------------------------------------------------------------------------
# /glassdoor/jobs
# ---------------------------------------------------------------------------
async def test_glassdoor_jobs_requires_a_connection(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory)

    response = await client.get(JOBS_PATH, headers=bearer(create_access_token(user.id)))

    assert response.status_code == 404
    assert response.json()["code"] == "gmail_not_connected"


async def test_glassdoor_jobs_rejects_revoked_credentials(
    client: AsyncClient,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await create_user(session_factory)
    await create_connection(session_factory, user)
    monkeypatch.setattr(
        gmail_endpoints,
        "refresh_gmail_access_token",
        AsyncMock(side_effect=GmailCredentialsError()),
    )

    response = await client.get(JOBS_PATH, headers=bearer(create_access_token(user.id)))

    assert response.status_code == 409
    assert response.json()["code"] == "gmail_credentials_invalid"
    assert REFRESH_TOKEN not in response.text

    connections = await list_connections(session_factory)
    assert connections[0].is_active is False

    status = await client.get(STATUS_PATH, headers=bearer(create_access_token(user.id)))
    assert status.json()["connected"] is False


async def test_glassdoor_jobs_returns_extracted_job_recommendations(
    client: AsyncClient,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await create_user(session_factory)
    await create_connection(session_factory, user)
    captured: dict[str, Any] = {}

    monkeypatch.setattr(
        gmail_endpoints,
        "refresh_gmail_access_token",
        AsyncMock(return_value=GmailAccessToken("test-access", 3600, ())),
    )

    class FakeGmailClient:
        def __init__(self, access_token: str, **_kwargs: Any) -> None:
            captured["access_token"] = access_token

        async def __aenter__(self) -> FakeGmailClient:
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

        async def list_message_ids(
            self, *, query: str | None = None, max_results: int = 25
        ) -> list[str]:
            captured["query"] = query
            captured["max_results"] = max_results
            return ["msg-1"]

        async def get_messages(self, message_ids: list[str]) -> list[GmailMessage]:
            captured["ids"] = message_ids
            return [glassdoor_message()]

    monkeypatch.setattr(gmail_endpoints, "GmailClient", FakeGmailClient)

    response = await client.get(
        f"{JOBS_PATH}?days=7&max_messages=5",
        headers=bearer(create_access_token(user.id)),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["messages_scanned"] == 1
    assert body["jobs_found"] == 1
    job = body["jobs"][0]
    assert job["job_title"] == "Data Engineer"
    assert job["company"] == "Acme Corp"
    assert job["location"] == "Austin, TX"
    assert job["job_url"].endswith("?jobListingId=1234")
    assert job["email_subject"] == "New jobs for you"
    assert job["received_at"] is not None
    assert job["job_description"] is None
    assert captured["query"] == "from:glassdoor.com newer_than:7d"
    assert captured["max_results"] == 5
    assert captured["ids"] == ["msg-1"]
    assert captured["access_token"] == "test-access"


# ---------------------------------------------------------------------------
# OAuth host contract: cookie flags and the frontend redirect
# ---------------------------------------------------------------------------
async def test_connect_sets_a_hardened_short_lived_state_cookie(
    client: AsyncClient,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await create_user(session_factory)

    response, _ = await start_flow(client, user, monkeypatch)

    assert response.status_code == 200
    cookie_header = response.headers.get("set-cookie", "").lower()
    assert "applypilot_oauth_session=" in cookie_header
    assert "httponly" in cookie_header
    assert "samesite=lax" in cookie_header
    assert "max-age=600" in cookie_header
    assert "path=/" in cookie_header
    # Development runs over http://localhost, so the cookie must not be Secure.
    assert "; secure" not in cookie_header
    # The browser now holds the state cookie the callback depends on.
    assert "applypilot_oauth_session" in client.cookies


async def test_callback_redirects_to_the_configured_frontend(
    client: AsyncClient,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await create_user(session_factory)
    frontend = "http://localhost:3000/gmail/callback"
    monkeypatch.setattr(settings, "GMAIL_FRONTEND_REDIRECT_URI", frontend)

    callback = await complete_flow(client, user, monkeypatch)

    assert callback.status_code == 303
    assert callback.headers["location"] == frontend
    assert callback.headers["cache-control"] == "no-store"
    assert "token" not in callback.headers["location"]
    assert len(await list_connections(session_factory)) == 1


async def test_callback_ignores_request_supplied_redirect_targets(
    client: AsyncClient,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await create_user(session_factory)
    frontend = "http://localhost:3000/gmail/callback"
    monkeypatch.setattr(settings, "GMAIL_FRONTEND_REDIRECT_URI", frontend)

    response, _ = await start_flow(client, user, monkeypatch)
    assert response.status_code == 200
    monkeypatch.setattr(
        gmail_oauth.google,
        "authorize_access_token",
        AsyncMock(return_value=gmail_token_payload()),
    )

    callback = await client.get(
        f"{CALLBACK_PATH}?code=auth-code&state=state-1"
        "&redirect=https://evil.example&redirect_uri=https://evil.example"
        "&next=//evil.example"
    )

    assert callback.status_code == 303
    assert callback.headers["location"] == frontend
