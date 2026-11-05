"""API v1 router aggregation."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.endpoints import (
    applications,
    auth,
    candidates,
    gmail,
    health,
    jobs,
    matching,
    profile,
    resumes,
)

api_router = APIRouter()

api_router.include_router(health.router)
api_router.include_router(candidates.router)
api_router.include_router(profile.router)
api_router.include_router(auth.router)
api_router.include_router(gmail.router)
api_router.include_router(resumes.router)
api_router.include_router(jobs.router)
api_router.include_router(matching.router)
api_router.include_router(applications.router)

__all__ = ["api_router"]
