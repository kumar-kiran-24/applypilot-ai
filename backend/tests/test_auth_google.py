"""Google OAuth and JWT authentication tests.

Google's network calls are mocked, so no real Google account, credentials or
internet access are required. The OAuth state machine itself runs against the
real Authlib Starlette integration and the app's real SessionMiddleware.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock

import app.api.v1.endpoints.auth as auth_endpoints
import jwt
import pytest
from app.core.config import settings
from app.core.security import create_access_token
from app.models.user import User
from app.services.google_oauth import GOOGLE_SCOPES, oauth
from authlib.integrations.base_client.errors import MismatchingStateError
from fastapi.responses import RedirectResponse
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

LOGIN_PATH = "/api/v1/auth/google/login"
CALLBACK_PATH = "/api/v1/auth/google/callback"
ME_PATH = "/api/v1/auth/me"
SESSION_COOKIE = "applypilot_oauth_session"

GOOGLE_USER: dict[str, Any] = {
    "sub": "google-sub-123",
    "email": "Ada@Example.com",
    "email_verified": True,
    "name": "Ada Lovelace",
    "picture": "https://example.com/ada.png",
}

SessionFactory = async_sessionmaker[AsyncSession]


def google_token(**user_overrides: Any) -> dict[str, Any]:
    """Build the token payload Authlib would return for a successful login."""
    userinfo = dict(GOOGLE_USER)
    userinfo.update(user_overrides)
    return {"access_token": "google-access-token", "userinfo": userinfo}


def mock_authorize_access_token(
    monkeypatch: pytest.MonkeyPatch, token: dict[str, Any]
) -> AsyncMock:
    exchange = AsyncMock(return_value=token)
    monkeypatch.setattr(oauth.google, "authorize_access_token", exchange)
    return exchange


async def create_user(session_factory: SessionFactory, **overrides: Any) -> User:
    values: dict[str, Any] = {
        "google_sub": "existing-google-sub",
        "email": "existing@example.com",
        "email_verified": True,
        "full_name": "Existing User",
        "is_active": True,
    }
    values.update(overrides)
    async with session_factory() as session:
        user = User(**values)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def list_users(session_factory: SessionFactory) -> list[User]:
    async with session_factory() as session:
        result = await session.execute(select(User))
        return list(result.scalars())


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Login redirect
# ---------------------------------------------------------------------------
async def test_login_redirects_browser_to_google(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    authorize = AsyncMock(
        return_value=RedirectResponse(
            "https://accounts.google.com/o/oauth2/v2/auth?state=abc", status_code=302
        )
    )
    monkeypatch.setattr(oauth.google, "authorize_redirect", authorize)

    response = await client.get(LOGIN_PATH)

    assert response.status_code == 302
    assert response.headers["location"].startswith("https://accounts.google.com")
    args, _ = authorize.await_args
    assert str(args[1]).endswith("/api/v1/auth/google/callback")


def test_google_client_requests_only_basic_scopes() -> None:
    assert GOOGLE_SCOPES == "openid email profile"
    assert oauth.google.client_kwargs["scope"] == GOOGLE_SCOPES


# ---------------------------------------------------------------------------
# OAuth state / session cookie
# ---------------------------------------------------------------------------
async def test_state_cookie_survives_the_redirect_flow(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, session_factory: SessionFactory
) -> None:
    async def fake_create_authorization_url(redirect_uri=None, **_: Any) -> dict[str, Any]:
        return {
            "url": "https://accounts.google.com/o/oauth2/v2/auth?state=state-123",
            "state": "state-123",
        }

    monkeypatch.setattr(
        oauth.google,
        "create_authorization_url",
        AsyncMock(side_effect=fake_create_authorization_url),
    )
    monkeypatch.setattr(
        oauth.google,
        "fetch_access_token",
        AsyncMock(return_value=google_token()),
    )

    login = await client.get(LOGIN_PATH)
    assert login.status_code == 302
    assert SESSION_COOKIE in client.cookies

    callback = await client.get(f"{CALLBACK_PATH}?code=auth-code&state=state-123")

    assert callback.status_code == 200, callback.text
    assert len(await list_users(session_factory)) == 1


async def test_callback_rejects_unknown_state_without_leaking_it(client: AsyncClient) -> None:
    response = await client.get(f"{CALLBACK_PATH}?code=secret-code&state=unknown-state")

    assert response.status_code == 400
    assert response.json() == {"detail": auth_endpoints.STATE_FAILURE_DETAIL}
    assert "secret-code" not in response.text
    assert "CSRF" not in response.text


async def test_mismatching_state_is_rejected(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        oauth.google,
        "authorize_access_token",
        AsyncMock(side_effect=MismatchingStateError()),
    )

    response = await client.get(f"{CALLBACK_PATH}?code=auth-code&state=state-123")

    assert response.status_code == 400
    assert response.json()["detail"] == auth_endpoints.STATE_FAILURE_DETAIL


async def test_oauth_provider_error_is_rejected(client: AsyncClient) -> None:
    response = await client.get(
        f"{CALLBACK_PATH}?error=access_denied&error_description=User+denied&state=abc"
    )

    assert response.status_code == 400
    assert response.json() == {"detail": auth_endpoints.SAFE_AUTH_FAILURE_DETAIL}
    assert "access_denied" not in response.text


# ---------------------------------------------------------------------------
# Identity validation
# ---------------------------------------------------------------------------
async def test_missing_user_information_is_rejected(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_authorize_access_token(monkeypatch, {"access_token": "google-access-token"})
    monkeypatch.setattr(
        auth_endpoints, "fetch_google_userinfo", AsyncMock(return_value=None)
    )

    response = await client.get(f"{CALLBACK_PATH}?code=auth-code&state=state-123")

    assert response.status_code == 401
    assert response.json()["detail"] == "Google did not return verified identity information."


async def test_unverified_email_is_rejected(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_authorize_access_token(monkeypatch, google_token(email_verified=False))

    response = await client.get(f"{CALLBACK_PATH}?code=auth-code&state=state-123")

    assert response.status_code == 401
    assert response.json()["detail"] == "A verified Google account is required."


async def test_missing_google_subject_is_rejected(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_authorize_access_token(monkeypatch, google_token(sub=""))

    response = await client.get(f"{CALLBACK_PATH}?code=auth-code&state=state-123")

    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Account creation / lookup
# ---------------------------------------------------------------------------
async def test_new_user_is_registered_and_can_call_me(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, session_factory: SessionFactory
) -> None:
    mock_authorize_access_token(monkeypatch, google_token())

    response = await client.get(f"{CALLBACK_PATH}?code=auth-code&state=state-123")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    assert body["user"]["email"] == "ada@example.com"  # normalised

    users = await list_users(session_factory)
    assert len(users) == 1
    user = users[0]
    assert user.google_sub == "google-sub-123"
    assert user.email_verified is True
    assert user.is_active is True
    assert str(user.id) == body["user"]["id"]

    me = await client.get(ME_PATH, headers=bearer(body["access_token"]))
    assert me.status_code == 200, me.text
    assert me.json()["email"] == "ada@example.com"


async def test_existing_user_is_reused(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, session_factory: SessionFactory
) -> None:
    existing = await create_user(
        session_factory, google_sub="google-sub-123", email="old@example.com"
    )
    mock_authorize_access_token(monkeypatch, google_token())

    response = await client.get(f"{CALLBACK_PATH}?code=auth-code&state=state-123")

    assert response.status_code == 200, response.text
    assert response.json()["user"]["id"] == str(existing.id)
    assert len(await list_users(session_factory)) == 1


async def test_email_only_match_is_not_auto_linked(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, session_factory: SessionFactory
) -> None:
    await create_user(
        session_factory, google_sub="another-google-sub", email="ada@example.com"
    )
    mock_authorize_access_token(monkeypatch, google_token())

    response = await client.get(f"{CALLBACK_PATH}?code=auth-code&state=state-123")

    assert response.status_code == 409
    assert "already exists" in response.json()["detail"]
    assert len(await list_users(session_factory)) == 1


async def test_inactive_user_is_rejected(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, session_factory: SessionFactory
) -> None:
    await create_user(session_factory, google_sub="google-sub-123", is_active=False)
    mock_authorize_access_token(monkeypatch, google_token())

    response = await client.get(f"{CALLBACK_PATH}?code=auth-code&state=state-123")

    assert response.status_code == 403
    assert response.json()["detail"] == "This ApplyPilot account is inactive."


# ---------------------------------------------------------------------------
# JWT authentication on protected routes
# ---------------------------------------------------------------------------
async def test_me_returns_current_user(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory)

    response = await client.get(ME_PATH, headers=bearer(create_access_token(user.id)))

    assert response.status_code == 200
    assert response.json() == {
        "id": str(user.id),
        "email": user.email,
        "full_name": user.full_name,
        "profile_picture": None,
    }


async def test_me_requires_a_token(client: AsyncClient) -> None:
    response = await client.get(ME_PATH)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


async def test_me_rejects_invalid_token(client: AsyncClient) -> None:
    response = await client.get(ME_PATH, headers=bearer("not-a-jwt"))

    assert response.status_code == 401


async def test_me_rejects_expired_token(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory)
    expired = create_access_token(user.id, expires_delta=timedelta(minutes=-5))

    response = await client.get(ME_PATH, headers=bearer(expired))

    assert response.status_code == 401


async def test_me_rejects_non_uuid_subject(client: AsyncClient) -> None:
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "sub": "not-a-uuid",
            "type": "access",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
        },
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )

    response = await client.get(ME_PATH, headers=bearer(token))

    assert response.status_code == 401


async def test_me_rejects_non_access_token(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory)
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "sub": str(user.id),
            "type": "refresh",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
        },
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )

    response = await client.get(ME_PATH, headers=bearer(token))

    assert response.status_code == 401


async def test_me_rejects_unknown_user(client: AsyncClient) -> None:
    token = create_access_token("00000000-0000-0000-0000-000000000000")

    response = await client.get(ME_PATH, headers=bearer(token))

    assert response.status_code == 401


async def test_me_rejects_inactive_user(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory, is_active=False)

    response = await client.get(ME_PATH, headers=bearer(create_access_token(user.id)))

    assert response.status_code == 403


async def test_openapi_exposes_bearer_scheme(client: AsyncClient) -> None:
    schema = (await client.get("/openapi.json")).json()

    scheme = schema["components"]["securitySchemes"]["ApplyPilotBearer"]
    assert scheme["type"] == "http"
    assert scheme["scheme"] == "bearer"
    assert schema["paths"][ME_PATH]["get"]["security"] == [{"ApplyPilotBearer": []}]
