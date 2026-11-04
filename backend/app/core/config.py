"""Centralised, typed application configuration.

All values are read from environment variables (and optionally a local ``.env``
file). No credential is ever hardcoded: development defaults are deliberately
insecure placeholders and are rejected when ``ENVIRONMENT=production``.
"""

from __future__ import annotations

import base64
import binascii
from functools import lru_cache
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import Field, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Development-only placeholder. Production refuses to start with this value.
DEV_JWT_SECRET_KEY = "dev-insecure-jwt-secret-change-me"
DEV_DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/applypilot"

Environment = Literal["development", "testing", "staging", "production"]


def _url_origin(value: str) -> str:
    """Return ``scheme://netloc`` for an absolute URL (used for allow-lists)."""
    parts = urlsplit(value.strip())
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}"


class Settings(BaseSettings):
    """Application settings loaded from the environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        # Settings hold secrets: never echo a submitted value back inside a
        # ValidationError (Pydantic includes ``input_value`` by default).
        hide_input_in_errors=True,
    )

    # --- Application metadata ------------------------------------------------
    APP_NAME: str = "ApplyPilot AI Backend"
    APP_VERSION: str = "0.1.0"
    ENVIRONMENT: Environment = "development"
    DEBUG: bool = False
    API_V1_PREFIX: str = "/api/v1"

    # --- Infrastructure ------------------------------------------------------
    DATABASE_URL: str = DEV_DATABASE_URL
    REDIS_URL: str = "redis://localhost:6379/0"
    # Signs the short-lived OAuth session cookie. Must be >= 32 characters;
    # generate with: python -c "import secrets; print(secrets.token_urlsafe(48))".
    OAUTH_SESSION_SECRET: str | None = None
    # Lifetime of the signed OAuth session cookie (state/nonce only). Kept short
    # on purpose: it is a one-flow artifact, not a login session.
    OAUTH_SESSION_MAX_AGE_SECONDS: int = Field(default=600, ge=60, le=86400)
    # --- AI / vector store ---------------------------------------------------
    GROQ_API_KEY: str | None = None
    GROQ_MODEL: str = "openai/gpt-oss-120b"
    GROQ_TIMEOUT_SECONDS: float = Field(default=30.0, ge=1, le=120)
    GROQ_MAX_RETRIES: int = Field(default=2, ge=0, le=5)
    GROQ_MAX_COMPLETION_TOKENS: int = Field(default=4096, ge=256, le=32768)
    GROQ_TEMPERATURE: float = Field(default=0.0, ge=0, le=2)
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_API_KEY: str | None = None

    # --- Google integrations -------------------------------------------------
    GOOGLE_CLIENT_ID: str | None = None
    GOOGLE_CLIENT_SECRET: str | None = None
    # Fernet key used to encrypt Gmail refresh tokens at rest. Separate from
    # JWT_SECRET_KEY and OAUTH_SESSION_SECRET; never generated automatically.
    GMAIL_TOKEN_ENCRYPTION_KEY: str | None = None
    # Must be registered verbatim in Google Cloud Console. Use ONE consistent
    # local host (http://127.0.0.1:8000 is the documented default): the browser
    # must use the same host so the signed OAuth session cookie is sent to the
    # callback (localhost and 127.0.0.1 are different sites for cookies).
    GOOGLE_REDIRECT_URI: str | None = None
    # Gmail API callback. Required and https in production; the documented local
    # setup sets it explicitly to the registered http://127.0.0.1:8000 callback.
    # In development it may be omitted and is then derived from the request host.
    # Register it next to GOOGLE_REDIRECT_URI in Google Cloud Console.
    GMAIL_REDIRECT_URI: str | None = None
    # Where the browser is sent after a successful Gmail authorization. Must be
    # an absolute http(s) URL whose origin is listed in BACKEND_CORS_ORIGINS, so
    # it can never become an open redirect. Empty keeps the JSON response.
    GMAIL_FRONTEND_REDIRECT_URI: str | None = None
    GMAIL_API_TIMEOUT_SECONDS: float = Field(default=30.0, ge=1, le=120)
    GMAIL_MAX_RETRIES: int = Field(default=2, ge=0, le=5)

    # --- Auth ----------------------------------------------------------------
    JWT_SECRET_KEY: str = DEV_JWT_SECRET_KEY
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=60, ge=1, le=60 * 24 * 7)

    # --- CORS ----------------------------------------------------------------
    BACKEND_CORS_ORIGINS: list[str] = Field(
        default_factory=lambda: ["http://127.0.0.1:3000"]
    )

    # --- Derived helpers -----------------------------------------------------
    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @property
    def is_testing(self) -> bool:
        return self.ENVIRONMENT == "testing"

    @property
    def session_cookie_https_only(self) -> bool:
        """Send the OAuth session cookie over HTTPS only outside local dev."""
        return self.ENVIRONMENT in {"production", "staging"}

    @property
    def database_url(self) -> str:
        """Async SQLAlchemy URL."""
        return self.DATABASE_URL

    @field_validator("BACKEND_CORS_ORIGINS", mode="before")
    @classmethod
    def _split_origins(cls, value: Any) -> Any:
        """Accept both a JSON list and a comma-separated string."""
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return []
            if raw.startswith("["):
                return value  # let pydantic parse the JSON array
            return [origin.strip() for origin in raw.split(",") if origin.strip()]
        return value

    @field_validator("DATABASE_URL")
    @classmethod
    def _require_async_driver(cls, value: str) -> str:
        if "+asyncpg" not in value:
            msg = (
                "DATABASE_URL must use the asyncpg driver, e.g. "
                "'postgresql+asyncpg://user:password@host:5432/database'."
            )
            raise ValueError(msg)
        return value

    @field_validator("OAUTH_SESSION_SECRET")
    @classmethod
    def _validate_oauth_session_secret(cls, value: str | None) -> str | None:
        """Reject OAuth session secrets that are too weak to sign cookies."""
        if value is not None and len(value) < 32:
            msg = "OAUTH_SESSION_SECRET must be at least 32 characters long."
            raise ValueError(msg)
        return value

    @field_validator("GMAIL_TOKEN_ENCRYPTION_KEY")
    @classmethod
    def _validate_gmail_encryption_key(cls, value: str | None) -> str | None:
        """Reject a malformed Fernet key early, without ever echoing it."""
        if value is None or not value.strip():
            return None
        try:
            decoded = base64.urlsafe_b64decode(value.strip().encode("ascii"))
        except (binascii.Error, UnicodeEncodeError, ValueError):
            decoded = b""
        if len(decoded) != 32:
            msg = (
                "GMAIL_TOKEN_ENCRYPTION_KEY must be a Fernet key: urlsafe-base64 of "
                "32 bytes (generate with Fernet.generate_key())."
            )
            raise ValueError(msg)
        return value.strip()

    @field_validator("GOOGLE_REDIRECT_URI")
    @classmethod
    def _blank_optional_url(cls, value: str | None) -> str | None:
        """Treat an empty template value as "derive it from the request"."""
        if value is None or not value.strip():
            return None
        return value.strip()

    @field_validator("GMAIL_REDIRECT_URI", "GMAIL_FRONTEND_REDIRECT_URI")
    @classmethod
    def _validate_gmail_redirect_urls(
        cls, value: str | None, info: ValidationInfo
    ) -> str | None:
        """Reject empty, relative or credential-carrying redirect URLs."""
        if value is None or not value.strip():
            return None
        candidate = value.strip()
        parts = urlsplit(candidate)
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
            msg = f"{info.field_name} must be an absolute http(s) URL."
            raise ValueError(msg)
        if parts.username is not None or parts.password is not None:
            msg = f"{info.field_name} must not contain credentials."
            raise ValueError(msg)
        if parts.fragment:
            msg = f"{info.field_name} must not contain a fragment."
            raise ValueError(msg)
        return candidate

    @model_validator(mode="after")
    def _validate_security_settings(self) -> Settings:
        """Fail fast on a weak JWT key, unsafe production settings and redirects."""
        if len(self.JWT_SECRET_KEY.encode("utf-8")) < 32:
            msg = "JWT_SECRET_KEY must be at least 32 bytes for HS256."
            raise ValueError(msg)

        problems: list[str] = []

        if self.is_production:
            if self.JWT_SECRET_KEY == DEV_JWT_SECRET_KEY:
                problems.append("JWT_SECRET_KEY must be set to a unique value in production")
            if not self.OAUTH_SESSION_SECRET:
                problems.append("OAUTH_SESSION_SECRET must be configured in production")
            if self.DATABASE_URL == DEV_DATABASE_URL:
                problems.append("DATABASE_URL must not use the development default")
            if self.DEBUG:
                problems.append("DEBUG must be false in production")
            if "*" in self.BACKEND_CORS_ORIGINS:
                problems.append("BACKEND_CORS_ORIGINS must not allow '*' in production")
            if not self.GMAIL_TOKEN_ENCRYPTION_KEY:
                problems.append(
                    "GMAIL_TOKEN_ENCRYPTION_KEY must be configured in production"
                )
            if not self.GMAIL_REDIRECT_URI:
                problems.append("GMAIL_REDIRECT_URI must be configured in production")
            elif urlsplit(self.GMAIL_REDIRECT_URI).scheme.lower() != "https":
                problems.append("GMAIL_REDIRECT_URI must use https in production")

        if self.GMAIL_FRONTEND_REDIRECT_URI:
            if (
                self.is_production
                and urlsplit(self.GMAIL_FRONTEND_REDIRECT_URI).scheme.lower() != "https"
            ):
                problems.append(
                    "GMAIL_FRONTEND_REDIRECT_URI must use https in production"
                )
            allowed_origins = {_url_origin(origin) for origin in self.BACKEND_CORS_ORIGINS}
            if _url_origin(self.GMAIL_FRONTEND_REDIRECT_URI) not in allowed_origins:
                problems.append(
                    "GMAIL_FRONTEND_REDIRECT_URI origin must be listed in "
                    "BACKEND_CORS_ORIGINS"
                )

        if problems:
            prefix = (
                "Invalid production configuration: "
                if self.is_production
                else "Invalid configuration: "
            )
            raise ValueError(prefix + "; ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    """Return the cached settings singleton."""
    return Settings()


settings = get_settings()
