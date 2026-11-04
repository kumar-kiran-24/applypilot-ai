"""Production configuration rules for the Gmail integration.

These tests build ``Settings`` objects directly (with ``_env_file=None``) so they
do not depend on the developer's ``.env``. No value printed or asserted here is a
real secret.
"""

from __future__ import annotations

from typing import Any

import pytest
from app.core.config import Settings
from cryptography.fernet import Fernet
from pydantic import ValidationError

VALID_KEY = Fernet.generate_key().decode()


def production_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "_env_file": None,
        "ENVIRONMENT": "production",
        "DEBUG": False,
        "JWT_SECRET_KEY": "j" * 48,
        "OAUTH_SESSION_SECRET": "o" * 48,
        "DATABASE_URL": "postgresql+asyncpg://app:secret@db.example.com:5432/applypilot",
        "BACKEND_CORS_ORIGINS": ["https://app.example.com"],
        "GMAIL_TOKEN_ENCRYPTION_KEY": VALID_KEY,
        "GMAIL_REDIRECT_URI": "https://api.example.com/api/v1/gmail/callback",
    }
    values.update(overrides)
    return Settings(**values)


def development_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "_env_file": None,
        "ENVIRONMENT": "development",
        "GMAIL_TOKEN_ENCRYPTION_KEY": None,
        "GMAIL_REDIRECT_URI": None,
        "GMAIL_FRONTEND_REDIRECT_URI": None,
    }
    values.update(overrides)
    return Settings(**values)


# ---------------------------------------------------------------------------
# Production requirements
# ---------------------------------------------------------------------------
def test_production_configuration_with_gmail_settings_is_accepted() -> None:
    settings = production_settings(
        GMAIL_FRONTEND_REDIRECT_URI="https://app.example.com/gmail/callback"
    )

    assert settings.GMAIL_TOKEN_ENCRYPTION_KEY == VALID_KEY
    assert settings.GMAIL_REDIRECT_URI.endswith("/api/v1/gmail/callback")
    assert settings.session_cookie_https_only is True


def test_production_requires_the_gmail_encryption_key() -> None:
    with pytest.raises(ValidationError) as excinfo:
        production_settings(GMAIL_TOKEN_ENCRYPTION_KEY=None)

    assert "GMAIL_TOKEN_ENCRYPTION_KEY must be configured in production" in str(
        excinfo.value
    )


def test_production_requires_the_gmail_redirect_uri() -> None:
    with pytest.raises(ValidationError) as excinfo:
        production_settings(GMAIL_REDIRECT_URI=None)

    assert "GMAIL_REDIRECT_URI must be configured in production" in str(excinfo.value)


def test_production_requires_https_gmail_redirect_uri() -> None:
    with pytest.raises(ValidationError) as excinfo:
        production_settings(GMAIL_REDIRECT_URI="http://api.example.com/api/v1/gmail/callback")

    assert "GMAIL_REDIRECT_URI must use https in production" in str(excinfo.value)


def test_production_rejects_the_development_jwt_secret_even_with_gmail_config() -> None:
    with pytest.raises(ValidationError) as excinfo:
        production_settings(JWT_SECRET_KEY="dev-insecure-jwt-secret-change-me")

    assert "JWT_SECRET_KEY must be set to a unique value" in str(excinfo.value)


def test_configuration_error_never_echoes_the_key() -> None:
    bad_key = "not-a-fernet-key-value"

    with pytest.raises(ValidationError) as excinfo:
        production_settings(GMAIL_TOKEN_ENCRYPTION_KEY=bad_key)

    assert bad_key not in str(excinfo.value)
    assert "GMAIL_TOKEN_ENCRYPTION_KEY must be a Fernet key" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Frontend redirect allow-list (no open redirects)
# ---------------------------------------------------------------------------
def test_frontend_redirect_origin_must_be_in_cors_origins() -> None:
    with pytest.raises(ValidationError) as excinfo:
        production_settings(GMAIL_FRONTEND_REDIRECT_URI="https://evil.example/gmail/callback")

    assert "BACKEND_CORS_ORIGINS" in str(excinfo.value)


def test_production_requires_https_frontend_redirect() -> None:
    with pytest.raises(ValidationError) as excinfo:
        production_settings(
            GMAIL_FRONTEND_REDIRECT_URI="http://app.example.com/gmail/callback"
        )

    assert "GMAIL_FRONTEND_REDIRECT_URI must use https in production" in str(
        excinfo.value
    )


@pytest.mark.parametrize(
    "redirect",
    [
        "not-a-url",
        "/gmail/callback",
        "https://user:pass@localhost:3000/gmail/callback",
        "https://localhost:3000/gmail/callback#fragment",
    ],
)
def test_malformed_frontend_redirects_are_rejected(redirect: str) -> None:
    with pytest.raises(ValidationError):
        development_settings(GMAIL_FRONTEND_REDIRECT_URI=redirect)


def test_local_frontend_redirect_is_allowed_in_development() -> None:
    settings = development_settings(
        BACKEND_CORS_ORIGINS=["http://localhost:3000"],
        GMAIL_FRONTEND_REDIRECT_URI="http://localhost:3000/gmail/callback",
    )

    assert settings.GMAIL_FRONTEND_REDIRECT_URI == "http://localhost:3000/gmail/callback"
    assert settings.session_cookie_https_only is False


# ---------------------------------------------------------------------------
# Development remains usable without Gmail configuration
# ---------------------------------------------------------------------------
def test_development_allows_missing_gmail_configuration() -> None:
    settings = development_settings()

    assert settings.GMAIL_TOKEN_ENCRYPTION_KEY is None
    assert settings.GMAIL_REDIRECT_URI is None
    assert settings.GMAIL_FRONTEND_REDIRECT_URI is None
    assert settings.OAUTH_SESSION_MAX_AGE_SECONDS == 600


def test_oauth_session_cookie_lifetime_is_bounded() -> None:
    with pytest.raises(ValidationError):
        development_settings(OAUTH_SESSION_MAX_AGE_SECONDS=30)

    with pytest.raises(ValidationError):
        development_settings(OAUTH_SESSION_MAX_AGE_SECONDS=86401)
