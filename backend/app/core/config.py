"""Centralised, typed application configuration.

All values are read from environment variables (and optionally a local ``.env``
file). No credential is ever hardcoded: development defaults are deliberately
insecure placeholders and are rejected when ``ENVIRONMENT=production``.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Development-only placeholder. Production refuses to start with this value.
DEV_JWT_SECRET_KEY = "dev-insecure-jwt-secret-change-me"
DEV_DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/applypilot"

Environment = Literal["development", "testing", "staging", "production"]


class Settings(BaseSettings):
    """Application settings loaded from the environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
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
    # Must be registered verbatim in Google Cloud Console and must use the same
    # host (localhost *or* 127.0.0.1) the browser uses. When unset, the callback
    # URL is derived from the incoming request, which keeps the OAuth session
    # cookie and the callback on one host.
    GOOGLE_REDIRECT_URI: str | None = None

    # --- Auth ----------------------------------------------------------------
    JWT_SECRET_KEY: str = DEV_JWT_SECRET_KEY
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=60, ge=1, le=60 * 24 * 7)

    # --- CORS ----------------------------------------------------------------
    BACKEND_CORS_ORIGINS: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"]
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

    @model_validator(mode="after")
    def _validate_security_settings(self) -> Settings:
        """Fail fast on a weak JWT key, and on unsafe production configuration."""
        if len(self.JWT_SECRET_KEY.encode("utf-8")) < 32:
            msg = "JWT_SECRET_KEY must be at least 32 bytes for HS256."
            raise ValueError(msg)

        if not self.is_production:
            return self

        problems: list[str] = []
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

        if problems:
            raise ValueError("Invalid production configuration: " + "; ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    """Return the cached settings singleton."""
    return Settings()


settings = get_settings()
