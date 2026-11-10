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
# Development-only login handoff target. Production must configure the deployed
# frontend URL (HTTPS) and list its origin in BACKEND_CORS_ORIGINS.
DEV_AUTH_FRONTEND_REDIRECT_URI = "http://127.0.0.1:3000/auth/callback"

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
    # Interactive API documentation. ``None`` means "enabled outside
    # production"; production always disables /docs, /redoc and /openapi.json
    # because they describe internal endpoints to unauthenticated visitors.
    API_DOCS_ENABLED: bool | None = None

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

    # --- Application automation ---------------------------------------------
    # Browser automation is opt-in and off by default: every workflow works
    # without it through the guided manual path. Enabling it requires the
    # optional Playwright installation (see backend/README.md).
    APPLICATION_AUTOMATION_ENABLED: bool = False
    APPLICATION_BROWSER_HEADLESS: bool = True
    APPLICATION_BROWSER_TIMEOUT_MS: int = Field(default=30_000, ge=5_000, le=120_000)
    # Debug screenshots live outside the web root and outside version control.
    APPLICATION_SCREENSHOT_DIR: str = "var/application-screenshots"
    # Hard cap on form fields accepted from an inspected page.
    APPLICATION_MAX_FORM_FIELDS: int = Field(default=150, ge=1, le=500)
    # Phase 2E: a live browser session is short-lived on purpose. When it
    # expires the user is told plainly and can reopen the employer page; the
    # previous in-memory browser state is never presented as preserved.
    APPLICATION_BROWSER_SESSION_TTL_SECONDS: int = Field(default=900, ge=60, le=7200)
    # Only one live browser per user by default: a second session would let the
    # same candidate fill two forms in parallel from one profile.
    APPLICATION_BROWSER_MAX_ACTIVE_PER_USER: int = Field(default=1, ge=1, le=5)
    # Process-wide ceiling. Each Chromium is several hundred MB, so this is the
    # real capacity limit on a single host; new sessions get a 503 when full
    # instead of the host running out of memory.
    APPLICATION_MAX_ACTIVE_BROWSER_SESSIONS: int = Field(default=8, ge=1, le=100)
    # Per-renderer V8 heap ceiling. Chromium pads this, so the process may use
    # somewhat more; pair it with a systemd MemoryMax (see the deploy units).
    APPLICATION_BROWSER_JS_HEAP_MB: int = Field(default=512, ge=128, le=4096)
    # Pin the validated DNS answer for the employer host into Chromium's
    # resolver, closing the validate-then-resolve (rebinding) gap. Must stay on
    # in production.
    APPLICATION_BROWSER_PIN_DNS: bool = True
    # Phase 2E Stage E: stream the same persistent Playwright page to the owning
    # user over an authenticated, single-use-ticket WebSocket. No CDP port is
    # exposed; frames and narrowly validated input are relayed in-process.
    APPLICATION_BROWSER_STREAMING_ENABLED: bool = True
    APPLICATION_BROWSER_VIEW_TICKET_TTL_SECONDS: int = Field(default=45, ge=10, le=120)
    # View tickets are single use. Limiting issuance prevents ticket farming.
    APPLICATION_BROWSER_VIEW_TICKETS_PER_MINUTE: int = Field(default=6, ge=1, le=60)
    # Forwarded mouse/key/text events per live view, per second.
    APPLICATION_BROWSER_VIEW_INPUT_PER_SECOND: int = Field(default=120, ge=10, le=600)
    # A view is closed (but the browser is not cancelled) after this long.
    APPLICATION_BROWSER_VIEW_MAX_SECONDS: int = Field(default=3600, ge=60, le=7200)
    # Extra exact origins the operator trusts for employer redirects (for
    # example a corporate SSO domain). Environment format: comma-separated or
    # a JSON array, e.g. "https://login.acme.com".
    APPLICATION_BROWSER_ALLOWED_REDIRECT_ORIGINS: list[str] = Field(default_factory=list)

    # --- Resume storage ------------------------------------------------------
    # Files live outside the web root and outside version control. The backend
    # only ever hands clients an opaque resume id, never a storage path.
    RESUME_STORAGE_BACKEND: Literal["local"] = "local"
    RESUME_STORAGE_DIR: str = "var/resumes"
    RESUME_MAX_FILE_SIZE_BYTES: int = Field(
        default=10 * 1024 * 1024, ge=1024, le=50 * 1024 * 1024
    )

    # --- Auth ----------------------------------------------------------------
    JWT_SECRET_KEY: str = DEV_JWT_SECRET_KEY
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=60, ge=1, le=60 * 24 * 7)
    # Where the browser lands after Google sign-in: a dedicated frontend route
    # that exchanges the one-time code for the HttpOnly session cookie. It must
    # be an absolute http(s) URL whose origin appears in BACKEND_CORS_ORIGINS,
    # so a bad setting can never become an open redirect. The URI is fixed by
    # configuration and never taken from the (untrusted) request.
    AUTH_FRONTEND_REDIRECT_URI: str = DEV_AUTH_FRONTEND_REDIRECT_URI
    # Lifetime of a one-time login code. Deliberately short (<= 60 seconds):
    # it only has to survive the browser redirect from the API to the app.
    AUTH_CODE_EXPIRE_SECONDS: int = Field(default=60, ge=10, le=60)
    # Per-client-address limit for the public auth endpoints (OAuth start,
    # callback and code exchange). This is an in-process safety net behind the
    # reverse proxy's limit_req; it is not a distributed quota.
    AUTH_RATE_LIMIT_PER_MINUTE: int = Field(default=30, ge=1, le=600)
    # Per-principal limit for every other API route (authenticated calls are
    # keyed by user id, anonymous ones by client address). In-process, so it is
    # a per-worker budget, not a cluster quota; Nginx limit_req is the edge
    # control. Health, readiness and metrics are excluded.
    API_RATE_LIMIT_ENABLED: bool = True
    API_RATE_LIMIT_PER_MINUTE: int = Field(default=240, ge=10, le=10_000)

    # --- Observability -------------------------------------------------------
    # Prometheus text exposition at /api/v1/metrics. In production a bearer
    # token is required (METRICS_TOKEN); the reverse proxy should also restrict
    # the route to the monitoring network.
    METRICS_ENABLED: bool = True
    METRICS_TOKEN: str | None = None
    # Optional JSON webhook for unhandled errors (error tracking without a
    # vendor SDK). Payloads contain the error class, path, request id and
    # status - never a message, token, cookie or form value.
    ERROR_WEBHOOK_URL: str | None = None
    ERROR_WEBHOOK_TIMEOUT_SECONDS: float = Field(default=3.0, ge=0.5, le=10)

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
    def docs_enabled(self) -> bool:
        """Interactive docs are opt-in in production, opt-out elsewhere."""
        if self.API_DOCS_ENABLED is None:
            return not self.is_production
        return self.API_DOCS_ENABLED

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

    @field_validator("APPLICATION_BROWSER_ALLOWED_REDIRECT_ORIGINS", mode="before")
    @classmethod
    def _split_redirect_origins(cls, value: Any) -> Any:
        """Accept a JSON array or a comma-separated list of origins."""
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return []
            if raw.startswith("["):
                return value
            return [origin.strip() for origin in raw.split(",") if origin.strip()]
        return value

    @field_validator("METRICS_TOKEN")
    @classmethod
    def _validate_metrics_token(cls, value: str | None) -> str | None:
        """Require a real secret when the metrics endpoint is token-protected."""
        if value is None or not value.strip():
            return None
        candidate = value.strip()
        if len(candidate) < 24:
            msg = "METRICS_TOKEN must be at least 24 characters long."
            raise ValueError(msg)
        return candidate

    @field_validator("ERROR_WEBHOOK_URL")
    @classmethod
    def _validate_error_webhook(cls, value: str | None) -> str | None:
        """Accept only an absolute http(s) webhook URL without credentials."""
        if value is None or not value.strip():
            return None
        candidate = value.strip()
        parts = urlsplit(candidate)
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
            msg = "ERROR_WEBHOOK_URL must be an absolute http(s) URL."
            raise ValueError(msg)
        if parts.username is not None or parts.password is not None:
            msg = "ERROR_WEBHOOK_URL must not contain credentials."
            raise ValueError(msg)
        return candidate

    @field_validator("RESUME_STORAGE_DIR")
    @classmethod
    def _validate_resume_storage_dir(cls, value: str) -> str:
        """Require a non-empty directory; empty would write next to the code."""
        candidate = (value or "").strip()
        if not candidate:
            msg = "RESUME_STORAGE_DIR must not be empty."
            raise ValueError(msg)
        return candidate

    @field_validator("APPLICATION_SCREENSHOT_DIR")
    @classmethod
    def _validate_screenshot_dir(cls, value: str) -> str:
        candidate = (value or "").strip()
        if not candidate:
            msg = "APPLICATION_SCREENSHOT_DIR must not be empty."
            raise ValueError(msg)
        return candidate

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

    @field_validator("AUTH_FRONTEND_REDIRECT_URI")
    @classmethod
    def _validate_auth_frontend_redirect(cls, value: str) -> str:
        """Require an absolute, credential-free http(s) URL without a query."""
        candidate = (value or "").strip()
        if not candidate:
            # An empty template value falls back to the documented local route.
            return DEV_AUTH_FRONTEND_REDIRECT_URI
        parts = urlsplit(candidate)
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
            msg = "AUTH_FRONTEND_REDIRECT_URI must be an absolute http(s) URL."
            raise ValueError(msg)
        if parts.username is not None or parts.password is not None:
            msg = "AUTH_FRONTEND_REDIRECT_URI must not contain credentials."
            raise ValueError(msg)
        if parts.fragment:
            msg = "AUTH_FRONTEND_REDIRECT_URI must not contain a fragment."
            raise ValueError(msg)
        if parts.query:
            msg = "AUTH_FRONTEND_REDIRECT_URI must not contain a query string."
            raise ValueError(msg)
        return candidate

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
            if not self.BACKEND_CORS_ORIGINS:
                problems.append(
                    "BACKEND_CORS_ORIGINS must list the deployed frontend origin(s)"
                )
            if "*" in self.BACKEND_CORS_ORIGINS:
                problems.append("BACKEND_CORS_ORIGINS must not allow '*' in production")
            for origin in self.BACKEND_CORS_ORIGINS:
                parts = urlsplit(origin)
                if parts.scheme.lower() != "https" or not parts.hostname:
                    problems.append(
                        "BACKEND_CORS_ORIGINS entries must be absolute https origins "
                        "in production"
                    )
                    break
                if parts.hostname.lower() in {"localhost", "127.0.0.1", "::1"}:
                    problems.append(
                        "BACKEND_CORS_ORIGINS must not use a loopback host in "
                        "production"
                    )
                    break
            if not self.GMAIL_TOKEN_ENCRYPTION_KEY:
                problems.append(
                    "GMAIL_TOKEN_ENCRYPTION_KEY must be configured in production"
                )
            if not self.GMAIL_REDIRECT_URI:
                problems.append("GMAIL_REDIRECT_URI must be configured in production")
            elif urlsplit(self.GMAIL_REDIRECT_URI).scheme.lower() != "https":
                problems.append("GMAIL_REDIRECT_URI must use https in production")
            if self.docs_enabled:
                problems.append(
                    "API_DOCS_ENABLED must be false in production: /docs, /redoc "
                    "and /openapi.json must not be public"
                )
            if not self.API_RATE_LIMIT_ENABLED:
                problems.append(
                    "API_RATE_LIMIT_ENABLED must not be disabled in production"
                )
            if not self.APPLICATION_BROWSER_PIN_DNS:
                problems.append(
                    "APPLICATION_BROWSER_PIN_DNS must not be disabled in production"
                )
            if self.METRICS_ENABLED and not self.METRICS_TOKEN:
                problems.append(
                    "METRICS_TOKEN must be configured when METRICS_ENABLED is true "
                    "in production"
                )
            if self.OAUTH_SESSION_SECRET == self.JWT_SECRET_KEY:
                problems.append(
                    "OAUTH_SESSION_SECRET must differ from JWT_SECRET_KEY"
                )
            if (
                self.ERROR_WEBHOOK_URL
                and urlsplit(self.ERROR_WEBHOOK_URL).scheme.lower() != "https"
            ):
                problems.append("ERROR_WEBHOOK_URL must use https in production")

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

        auth_redirect = urlsplit(self.AUTH_FRONTEND_REDIRECT_URI)
        allowed_origins = {_url_origin(origin) for origin in self.BACKEND_CORS_ORIGINS}
        if _url_origin(self.AUTH_FRONTEND_REDIRECT_URI) not in allowed_origins:
            problems.append(
                "AUTH_FRONTEND_REDIRECT_URI origin must be listed in "
                "BACKEND_CORS_ORIGINS"
            )
        if self.is_production:
            if auth_redirect.scheme.lower() != "https":
                problems.append("AUTH_FRONTEND_REDIRECT_URI must use https in production")
            if self.AUTH_FRONTEND_REDIRECT_URI == DEV_AUTH_FRONTEND_REDIRECT_URI:
                problems.append(
                    "AUTH_FRONTEND_REDIRECT_URI must be set to the deployed "
                    "frontend URL in production"
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
