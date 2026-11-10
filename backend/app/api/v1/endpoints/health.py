"""Liveness and readiness endpoints.

``/health`` is a static liveness check: the process is up. ``/ready`` is the
readiness probe an orchestrator should use: it verifies the database and
reports the process-local browser-streaming capability. Neither endpoint
exposes credentials, cookies, environment values or filesystem paths.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db
from app.integrations.browser.view import browser_view_capability
from app.schemas.common import HealthResponse, ReadinessCheck, ReadinessResponse
from app.services.browser_session_manager import live_browser_count
from app.services.browser_view_service import active_browser_views

router = APIRouter()

Database = Annotated[AsyncSession, Depends(get_db)]

SINGLE_PROCESS_NOTE = (
    "Live browser sessions and view tickets are held in process memory. Run a "
    "single API worker, or route each user to the same worker and to a shared "
    "remote browser service before scaling horizontally."
)


@router.get(
    "/health",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    summary="Service health",
    description="Returns a static liveness payload; does not touch the database.",
    tags=["health"],
)
async def health_check() -> HealthResponse:
    return HealthResponse(
        status="healthy",
        service=settings.APP_NAME,
        version=settings.APP_VERSION,
    )


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    summary="Service readiness",
    description=(
        "Checks database connectivity and reports whether live browser "
        "streaming is available in this deployment. Returns 503 when a "
        "required dependency is not ready."
    ),
    responses={503: {"model": ReadinessResponse}},
    tags=["health"],
)
async def readiness_check(db: Database, response: Response) -> ReadinessResponse:
    checks: list[ReadinessCheck] = []
    try:
        await db.execute(text("SELECT 1"))
        checks.append(ReadinessCheck(name="database", ok=True))
        database_ready = True
    except Exception:
        checks.append(
            ReadinessCheck(
                name="database",
                ok=False,
                detail="The database is not reachable.",
            )
        )
        database_ready = False

    capability = browser_view_capability()
    checks.append(
        ReadinessCheck(
            name="browser_streaming",
            ok=capability.available,
            detail=capability.reason if not capability.available else None,
        )
    )

    ready = database_ready
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(
        status="ready" if ready else "degraded",
        service=settings.APP_NAME,
        version=settings.APP_VERSION,
        checks=checks,
        automation_enabled=settings.APPLICATION_AUTOMATION_ENABLED,
        browser_streaming_available=capability.available,
        active_browser_sessions=live_browser_count(),
        active_browser_views=active_browser_views(),
        single_process=True,
        note=SINGLE_PROCESS_NOTE,
    )


__all__ = ["router"]
