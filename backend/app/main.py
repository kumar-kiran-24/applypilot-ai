"""ApplyPilot AI backend application entry point.

Run with: ``uvicorn app.main:app --reload``
Docs:     http://127.0.0.1:8000/docs
Health:   http://127.0.0.1:8000/api/v1/health
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.error_reporting import report_unhandled_error
from app.core.exceptions import AppError
from app.core.logging import get_logger, setup_logging
from app.core.metrics import record_counter, record_http_request
from app.core.rate_limit import RateLimitError, check_api_rate_limit
from app.core.request_context import request_id_var
from app.core.secrets_audit import settings_fingerprints
from app.db.init_db import check_database_connection
from app.db.session import async_session_factory, dispose_engine
from app.services.browser_session_manager import (
    shutdown_browser_sessions,
    sweep_browser_sessions,
)
from app.services.token_revocation import purge_expired_revocations

logger = get_logger(__name__)

#: Strong references to fire-and-forget tasks (otherwise they can be garbage
#: collected before they run).
_background_tasks: set[asyncio.Task[None]] = set()

DB_STARTUP_TIMEOUT_SECONDS = 20.0
# Expired browsers are closed even when no request observes their session row.
BROWSER_SWEEP_INTERVAL_SECONDS = 30.0
# Revoked-token rows only matter until the token's own expiry; hourly cleanup
# keeps the denylist tiny without a second infrastructure dependency.
REVOCATION_PURGE_INTERVAL_SECONDS = 3600.0


async def _sweep_browser_sessions_periodically() -> None:
    """Close expired/crashed browsers on a timer until shutdown."""
    while True:
        await asyncio.sleep(BROWSER_SWEEP_INTERVAL_SECONDS)
        try:
            closed = await sweep_browser_sessions()
        except Exception as exc:  # pragma: no cover - defensive background loop
            logger.warning(
                "Browser session sweep failed",
                extra={"stage": "browser_sweep", "error_class": type(exc).__name__},
            )
            continue
        if closed:
            logger.info(
                "Expired browser sessions closed",
                extra={"stage": "browser_sweep", "closed": closed},
            )


async def _purge_revocations_periodically() -> None:
    """Delete revoked-token rows whose token has already expired."""
    while True:
        await asyncio.sleep(REVOCATION_PURGE_INTERVAL_SECONDS)
        try:
            async with async_session_factory() as session:
                removed = await purge_expired_revocations(session)
        except Exception as exc:  # pragma: no cover - defensive background loop
            logger.warning(
                "Revoked-token purge failed",
                extra={"stage": "jwt_revocation_purge", "error_class": type(exc).__name__},
            )
            continue
        if removed:
            logger.info(
                "Expired revocations purged",
                extra={"stage": "jwt_revocation_purge", "removed": removed},
            )

DESCRIPTION = """
**ApplyPilot AI - Your Intelligent Job Application Agent**

*Discover. Analyze. Match. Apply.*

Core backend providing candidate profile management (education, experience,
projects, job preferences and reusable verified application answers). Job
discovery, description analysis, resume matching and authorized application
automation are added module by module on top of this foundation.
"""


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Application startup and shutdown."""
    setup_logging(settings)
    secret_fingerprints = settings_fingerprints(settings)
    logger.info(
        "Starting application",
        extra={
            "app": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "environment": settings.ENVIRONMENT,
            # Truncated hashes only: operators can confirm which secrets are
            # loaded (and that they match the backup) without logging them.
            **secret_fingerprints,
        },
    )

    # Connectivity is reported, never fatal: the API must stay able to serve
    # health checks while the database is being provisioned.
    try:
        await asyncio.wait_for(check_database_connection(), timeout=DB_STARTUP_TIMEOUT_SECONDS)
        logger.info("Database connection verified")
    except Exception as exc:
        logger.warning(
            "Database not reachable at startup; continuing without it",
            extra={"error": type(exc).__name__},
        )

    sweeper = asyncio.create_task(_sweep_browser_sessions_periodically())
    revocation_purger = asyncio.create_task(_purge_revocations_periodically())
    try:
        yield
    finally:
        for task in (sweeper, revocation_purger):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        # Never leave a Chromium process behind on shutdown or reload.
        await shutdown_browser_sessions()
        await dispose_engine()
        logger.info("Application stopped")


def docs_urls(cfg: object) -> dict[str, str | None]:
    """Return the FastAPI documentation paths for this configuration.

    Public API documentation is a production information leak: it advertises
    every route and schema to unauthenticated visitors. It stays available
    outside production for local work and the tests that inspect it.
    """
    enabled = bool(getattr(cfg, "docs_enabled", False))
    return {
        "docs_url": "/docs" if enabled else None,
        "redoc_url": "/redoc" if enabled else None,
        "openapi_url": "/openapi.json" if enabled else None,
    }


app = FastAPI(
    title=settings.APP_NAME,
    description=DESCRIPTION,
    version=settings.APP_VERSION,
    **docs_urls(settings),
    lifespan=lifespan,
    contact={"name": "ApplyPilot AI"},
    license_info={"name": "Proprietary"},
)

OAUTH_SESSION_COOKIE = "applypilot_oauth_session"

if not settings.OAUTH_SESSION_SECRET:
    raise RuntimeError("OAUTH_SESSION_SECRET must be configured.")
if len(settings.OAUTH_SESSION_SECRET) < 32:
    raise RuntimeError("OAUTH_SESSION_SECRET must be at least 32 characters long.")

# Holds the OAuth state/nonce between the Google redirect and the callback. The
# cookie is signed with OAUTH_SESSION_SECRET, HttpOnly, Lax (survives the
# top-level redirect back from Google), HTTPS-only outside local development and
# short-lived: it is a one-flow artifact, not a login session.
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.OAUTH_SESSION_SECRET,
    session_cookie=OAUTH_SESSION_COOKIE,
    same_site="lax",
    https_only=settings.session_cookie_https_only,
    max_age=settings.OAUTH_SESSION_MAX_AGE_SECONDS,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.BACKEND_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
    max_age=600,
)

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


@app.middleware("http")
async def api_rate_limit_middleware(request: Request, call_next: Any) -> Any:
    """Apply the per-principal API budget before any endpoint work.

    This is added before the correlation middleware so the correlation layer
    wraps it and still assigns a request id, security headers and an access log
    line to a 429 response.
    """
    retry_after = await check_api_rate_limit(request)
    if retry_after is not None:
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={
                "detail": RateLimitError.message,
                "code": RateLimitError.code,
            },
            headers={"Retry-After": str(retry_after)},
        )
    return await call_next(request)


@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next: Any) -> Any:
    """Propagate a request id, add security headers and log one access line."""
    candidate = (request.headers.get("X-Request-ID") or "").strip()
    request_id = (
        candidate
        if _REQUEST_ID_PATTERN.fullmatch(candidate)
        else uuid.uuid4().hex
    )
    token = request_id_var.set(request_id)
    started = time.perf_counter()
    try:
        response = await call_next(request)
    finally:
        request_id_var.reset(token)

    response.headers["X-Request-ID"] = request_id
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Permissions-Policy",
        "accelerometer=(), camera=(), geolocation=(), gyroscope=(), "
        "magnetometer=(), microphone=(), payment=(), usb=()",
    )
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-site")
    response.headers.setdefault("X-Permitted-Cross-Domain-Policies", "none")
    # The API serves JSON, files and (outside production) the docs UI. A locked
    # down CSP applies to everything except the docs assets, which need their
    # own inline scripts in local development.
    if not request.url.path.startswith(("/docs", "/redoc", "/openapi.json")):
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; "
            "form-action 'none'",
        )
    if request.url.path.startswith(settings.API_V1_PREFIX):
        # Authenticated data and tokens must never sit in a shared cache.
        response.headers.setdefault("Cache-Control", "no-store")
    if settings.is_production:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    # Do not advertise the ASGI server and version.
    if "server" in response.headers:
        del response.headers["server"]

    route = request.scope.get("route")
    route_path = getattr(route, "path", None) or "unmatched"
    record_http_request(
        method=request.method,
        route=route_path,
        status_code=response.status_code,
        duration_seconds=time.perf_counter() - started,
    )

    # Operational metadata only: no query string, body or header values.
    if request.url.path not in {
        f"{settings.API_V1_PREFIX}/health",
        f"{settings.API_V1_PREFIX}/ready",
    }:
        logger.info(
            "HTTP request",
            extra={
                "stage": "http_request",
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            },
        )
    return response


app.include_router(api_router, prefix=settings.API_V1_PREFIX)


@app.exception_handler(AppError)
async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    """Translate domain errors into a stable error envelope."""
    headers = dict(exc.headers)
    if exc.status_code == 401:
        headers.setdefault("WWW-Authenticate", "Bearer")
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.to_payload(),
        headers=headers or None,
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    """Return validation failures without echoing the submitted payload."""
    details: list[dict[str, Any]] = [
        {"loc": list(error.get("loc", [])), "msg": error.get("msg", "Invalid value")}
        for error in exc.errors()
    ]
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "detail": "Request validation failed.",
            "code": "validation_error",
            "details": details,
        },
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Log, count and report unexpected failures; return a non-leaking 500."""
    logger.exception("Unhandled application error")
    record_counter("applypilot_unhandled_errors_total", error_class=type(exc).__name__)
    # Fire-and-forget: monitoring latency must never delay or fail a response.
    task = asyncio.create_task(
        report_unhandled_error(
            error=exc,
            method=request.method,
            route=getattr(request.scope.get("route"), "path", "unmatched"),
            request_id=request_id_var.get(),
        )
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error.", "code": "internal_error"},
    )
