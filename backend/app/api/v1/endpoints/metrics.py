"""Prometheus metrics endpoint.

Authenticated with ``METRICS_TOKEN`` in production (the startup validator
requires it) and expected to be restricted to the monitoring network by Nginx.
The payload contains counters and labels only - no paths with ids, query
strings, user data or secrets.
"""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import PlainTextResponse

from app.core.config import settings
from app.core.metrics import render_metrics, set_gauge
from app.services.browser_session_manager import live_browser_count
from app.services.browser_view_service import active_browser_views

router = APIRouter(tags=["operations"])


def _authorize(request: Request) -> None:
    if not settings.METRICS_ENABLED:
        # Report the feature as absent rather than advertising a disabled route.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")
    expected = settings.METRICS_TOKEN
    if expected is None:
        # Only reachable outside production; the validator rejects this in
        # production, where the endpoint would otherwise be public.
        return
    header = request.headers.get("Authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(value.strip(), expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A metrics token is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )


@router.get(
    "/metrics",
    include_in_schema=False,
    response_class=PlainTextResponse,
    summary="Prometheus metrics",
)
async def metrics(
    request: Request,
    _: Annotated[None, Depends(_authorize)],
) -> PlainTextResponse:
    """Expose request, error and browser-session metrics."""
    set_gauge("applypilot_browser_sessions_active", live_browser_count())
    set_gauge("applypilot_browser_views_active", active_browser_views())
    return PlainTextResponse(
        render_metrics(
            version=settings.APP_VERSION,
            environment=settings.ENVIRONMENT,
        ),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


__all__ = ["router"]
