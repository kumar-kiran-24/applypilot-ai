"""ApplyPilot AI backend application entry point.

Run with: ``uvicorn app.main:app --reload``
Docs:     http://127.0.0.1:8000/docs
Health:   http://127.0.0.1:8000/api/v1/health
"""

from __future__ import annotations

import asyncio
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
from app.core.exceptions import AppError
from app.core.logging import get_logger, setup_logging
from app.db.init_db import check_database_connection
from app.db.session import dispose_engine

logger = get_logger(__name__)

DB_STARTUP_TIMEOUT_SECONDS = 20.0

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
    logger.info(
        "Starting application",
        extra={
            "app": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "environment": settings.ENVIRONMENT,
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

    try:
        yield
    finally:
        await dispose_engine()
        logger.info("Application stopped")


app = FastAPI(
    title=settings.APP_NAME,
    description=DESCRIPTION,
    version=settings.APP_VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
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

app.include_router(api_router, prefix=settings.API_V1_PREFIX)


@app.exception_handler(AppError)
async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    """Translate domain errors into a stable error envelope."""
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.to_payload(),
        headers={"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None,
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
async def unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
    """Log unexpected failures and return a non-leaking 500 response."""
    logger.exception("Unhandled application error")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error.", "code": "internal_error"},
    )
