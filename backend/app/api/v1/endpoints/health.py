"""Liveness endpoint."""

from __future__ import annotations

from fastapi import APIRouter, status

from app.core.config import settings
from app.schemas.common import HealthResponse

router = APIRouter()


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
