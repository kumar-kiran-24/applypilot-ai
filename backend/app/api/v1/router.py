
"""API v1 router aggregation."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.endpoints import auth, candidates, health

api_router = APIRouter()

api_router.include_router(health.router)
api_router.include_router(candidates.router)
api_router.include_router(auth.router)

__all__ = ["api_router"]
