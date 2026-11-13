"""API v1 router aggregation."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.endpoints import (
    application_fill,
    application_human_loop,
    applications,
    assistant,
    auth,
    auto_apply,
    browser_sessions,
    browser_view,
    candidates,
    gmail,
    health,
    jobs,
    matching,
    metrics,
    missing_information,
    profile,
    resumes,
)

api_router = APIRouter()

api_router.include_router(health.router)
api_router.include_router(metrics.router)
api_router.include_router(candidates.router)
api_router.include_router(profile.router)
api_router.include_router(auth.router)
api_router.include_router(gmail.router)
api_router.include_router(resumes.router)
api_router.include_router(jobs.router)
api_router.include_router(matching.router)
api_router.include_router(applications.router)
api_router.include_router(assistant.router)
api_router.include_router(missing_information.router)
api_router.include_router(auto_apply.router)
api_router.include_router(browser_sessions.router)
api_router.include_router(browser_view.router)
api_router.include_router(application_fill.router)
api_router.include_router(application_human_loop.router)

__all__ = ["api_router"]
