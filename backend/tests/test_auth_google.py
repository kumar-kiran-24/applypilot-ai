"""Google OAuth, one-time login-code and JWT authentication tests.

Google's network calls are mocked, so no real Google account, credentials or
internet access are required. The OAuth state machine itself runs against the
real Authlib Starlette integration and the app's real SessionMiddleware.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import app.api.v1.endpoints.auth as auth_endpoints
import jwt
import pytest
from app.core.config import settings
from app.core.security import create_access_token
from app.models.auth_code import AuthCode
from app.models.user import User
from app.services.auth_codes import hash_auth_code, issue_auth_code
from app.services.google_oauth import GOOGLE_SCOPES, oauth
from authlib.integrations.base_client.errors import MismatchingStateError
from fastapi.responses import RedirectResponse
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

LOGIN_PATH = "/api/v1/auth/google/login"
CALLBACK_PATH = "/api/v1/auth/google/callback"
EXCHANGE_PATH = "/api/v1/auth/login/exchange"
ME_PATH = "/api/v1/auth/me"
SESSION_COOKIE = "applypilot_oauth_session"
FRONTEND_CALLBACK = settings.AUTH_FRONTEND_REDIRECT_URI

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


def redirect_query(response: Any) -> dict[str, list[str]]:
    """Return the decoded query string of a redirect response."""
    assert response.status_code == 303, response.text
    location = response.headers["location"]
    assert location.startswith(FRONTEND_CALLBACK)
    return parse_qs(urlsplit(location).query)


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


async def stored_codes(session_factory: SessionFactory) -> list[AuthCode]:
    async with session_factory() as session:
        result = await session.execute(select(AuthCode))
        return list(result.scalars())


async def issue_code_for(
    session_factory: SessionFactory,
    user_id: Any,
    *,
    ttl_seconds: int = 60,
) -> str:
    async with session_factory() as session:
        code = await issue_auth_code(session, user_id, ttl_seconds=ttl_seconds)
        await session.commit()
        return code


def as_utc(value: datetime) -> datetime:
    """SQLite drops tzinfo; stored values are always UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


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

    assert callback.status_code == 303, callback.text
    assert "code" in redirect_query(callback)
    assert len(await list_users(session_factory)) == 1


async def test_callback_rejects_unknown_state_without_leaking_it(
    client: AsyncClient,
) -> None:
    response = await client.get(f"{CALLBACK_PATH}?code=secret-code&state=unknown-state")

    assert redirect_query(response)["error"] == [auth_endpoints.LOGIN_ERROR_STATE]
    assert response.text == ""
    assert "secret-code" not in response.headers["location"]


async def test_mismatching_state_is_rejected(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        oauth.google,
        "authorize_access_token",
        AsyncMock(side_effect=MismatchingStateError()),
    )

    response = await client.get(f"{CALLBACK_PATH}?code=auth-code&state=state-123")

    assert redirect_query(response)["error"] == [auth_endpoints.LOGIN_ERROR_STATE]


async def test_oauth_provider_error_is_rejected(client: AsyncClient) -> None:
    response = await client.get(
        f"{CALLBACK_PATH}?error=access_denied&error_description=User+denied&state=abc"
    )

    assert redirect_query(response)["error"] == [auth_endpoints.LOGIN_ERROR_OAUTH]
    assert "access_denied" not in response.headers["location"]


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

    assert redirect_query(response)["error"] == [auth_endpoints.LOGIN_ERROR_IDENTITY]


async def test_unverified_email_is_rejected(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_authorize_access_token(monkeypatch, google_token(email_verified=False))

    response = await client.get(f"{CALLBACK_PATH}?code=auth-code&state=state-123")

    assert redirect_query(response)["error"] == [
        auth_endpoints.LOGIN_ERROR_EMAIL_UNVERIFIED
    ]


async def test_missing_google_subject_is_rejected(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_authorize_access_token(monkeypatch, google_token(sub=""))

    response = await client.get(f"{CALLBACK_PATH}?code=auth-code&state=state-123")

    assert redirect_query(response)["error"] == [auth_endpoints.LOGIN_ERROR_IDENTITY]


# ---------------------------------------------------------------------------
# Account creation / lookup
# ---------------------------------------------------------------------------
async def test_new_user_is_registered_and_can_call_me(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, session_factory: SessionFactory
) -> None:
    mock_authorize_access_token(monkeypatch, google_token())

    callback = await client.get(f"{CALLBACK_PATH}?code=auth-code&state=state-123")
    code = redirect_query(callback)["code"][0]

    users = await list_users(session_factory)
    assert len(users) == 1
    user = users[0]
    assert user.google_sub == "google-sub-123"
    assert user.email == "ada@example.com"  # normalised
    assert user.email_verified is True
    assert user.is_active is True

    exchange = await client.post(EXCHANGE_PATH, json={"code": code})
    assert exchange.status_code == 200, exchange.text
    body = exchange.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    assert body["user"]["id"] == str(user.id)
    assert body["user"]["email"] == "ada@example.com"

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

    callback = await client.get(f"{CALLBACK_PATH}?code=auth-code&state=state-123")
    code = redirect_query(callback)["code"][0]

    assert len(await list_users(session_factory)) == 1
    exchange = await client.post(EXCHANGE_PATH, json={"code": code})
    assert exchange.status_code == 200, exchange.text
    assert exchange.json()["user"]["id"] == str(existing.id)


async def test_email_only_match_is_not_auto_linked(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, session_factory: SessionFactory
) -> None:
    await create_user(
        session_factory, google_sub="another-google-sub", email="ada@example.com"
    )
    mock_authorize_access_token(monkeypatch, google_token())

    response = await client.get(f"{CALLBACK_PATH}?code=auth-code&state=state-123")

    assert redirect_query(response)["error"] == [
        auth_endpoints.LOGIN_ERROR_ACCOUNT_EXISTS
    ]
    assert len(await list_users(session_factory)) == 1
    assert await stored_codes(session_factory) == []


async def test_inactive_user_is_rejected(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, session_factory: SessionFactory
) -> None:
    await create_user(session_factory, google_sub="google-sub-123", is_active=False)
    mock_authorize_access_token(monkeypatch, google_token())

    response = await client.get(f"{CALLBACK_PATH}?code=auth-code&state=state-123")

    assert redirect_query(response)["error"] == [auth_endpoints.LOGIN_ERROR_INACTIVE]


# ---------------------------------------------------------------------------
# One-time login-code handoff
# ---------------------------------------------------------------------------
async def test_callback_redirect_carries_no_token_and_stores_only_a_hash(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, session_factory: SessionFactory
) -> None:
    mock_authorize_access_token(monkeypatch, google_token())

    response = await client.get(f"{CALLBACK_PATH}?code=auth-code&state=state-123")

    location = response.headers["location"]
    code = redirect_query(response)["code"][0]
    assert "access_token" not in location
    assert "." not in code  # a JWT, not the code, would contain dots
    assert response.text == ""
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "ap_session" not in response.headers.get("set-cookie", "")

    codes = await stored_codes(session_factory)
    assert len(codes) == 1
    assert codes[0].code_hash == hash_auth_code(code)
    assert codes[0].code_hash != code
    assert codes[0].consumed_at is None
    assert as_utc(codes[0].expires_at) > datetime.now(timezone.utc)


async def test_login_code_is_short_lived(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, session_factory: SessionFactory
) -> None:
    mock_authorize_access_token(monkeypatch, google_token())

    before = datetime.now(timezone.utc)
    await client.get(f"{CALLBACK_PATH}?code=auth-code&state=state-123")
    after = datetime.now(timezone.utc)

    codes = await stored_codes(session_factory)
    expires_at = as_utc(codes[0].expires_at)

    assert expires_at <= after + timedelta(seconds=60)
    assert expires_at >= before


async def test_exchange_consumes_the_code(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory)
    code = await issue_code_for(session_factory, user.id)

    response = await client.post(EXCHANGE_PATH, json={"code": code})

    assert response.status_code == 200, response.text
    assert response.json()["user"]["id"] == str(user.id)
    assert response.json()["access_token"]

    codes = await stored_codes(session_factory)
    assert codes[0].consumed_at is not None


async def test_replayed_code_is_rejected(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory)
    code = await issue_code_for(session_factory, user.id)

    first = await client.post(EXCHANGE_PATH, json={"code": code})
    assert first.status_code == 200, first.text

    second = await client.post(EXCHANGE_PATH, json={"code": code})

    assert second.status_code == 400
    assert second.json() == {"detail": auth_endpoints.AUTH_CODE_USED_DETAIL}
    assert "access_token" not in second.text


async def test_expired_code_is_rejected(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory)
    code = await issue_code_for(session_factory, user.id)

    async with session_factory() as session:
        row = await session.scalar(
            select(AuthCode).where(AuthCode.code_hash == hash_auth_code(code))
        )
        assert row is not None
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await session.commit()

    response = await client.post(EXCHANGE_PATH, json={"code": code})

    assert response.status_code == 400
    assert response.json() == {"detail": auth_endpoints.AUTH_CODE_EXPIRED_DETAIL}


async def test_unknown_code_is_rejected_without_echoing_it(
    client: AsyncClient,
) -> None:
    code = "unknown-code-" + "a" * 32

    response = await client.post(EXCHANGE_PATH, json={"code": code})

    assert response.status_code == 400
    assert response.json() == {"detail": auth_endpoints.AUTH_CODE_INVALID_DETAIL}
    assert code not in response.text


@pytest.mark.parametrize(
    "code",
    ["", "short", "a" * 200, "has spaces " + "b" * 32, "semi;colon" * 5],
)
async def test_malformed_code_is_rejected_before_lookup(
    client: AsyncClient, code: str
) -> None:
    response = await client.post(EXCHANGE_PATH, json={"code": code})

    assert response.status_code == 422
    if code:
        assert code not in response.text


async def test_code_for_inactive_user_is_rejected(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory, is_active=False)
    code = await issue_code_for(session_factory, user.id)

    response = await client.post(EXCHANGE_PATH, json={"code": code})

    assert response.status_code == 403
    assert "inactive" in response.json()["detail"]


async def test_code_is_not_logged(
    client: AsyncClient, session_factory: SessionFactory, caplog: pytest.LogCaptureFixture
) -> None:
    user = await create_user(session_factory)
    code = await issue_code_for(session_factory, user.id)

    with caplog.at_level("DEBUG"):
        response = await client.post(EXCHANGE_PATH, json={"code": code})

    assert response.status_code == 200, response.text
    rendered = "\n".join(record.getMessage() for record in caplog.records)
    assert code not in rendered
    assert response.json()["access_token"] not in rendered


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
