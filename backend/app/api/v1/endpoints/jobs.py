"""Authenticated job storage and matching endpoints.

Discovery is the only place that contacts Gmail. It reuses the existing
read-only Glassdoor extraction and stores the postings so matching has stable
job ids. Every read and write is scoped to the authenticated user; a job owned
by another account is reported as missing.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.serializers import job_matches_response, job_response
from app.core.dependencies import get_authenticated_user
from app.core.logging import get_logger
from app.db.session import get_db
from app.models.user import User
from app.schemas.common import ErrorResponse
from app.schemas.matching import (
    DiscoverJobsResponse,
    JobListResponse,
    JobMatchesResponse,
    JobResponse,
    MatchJobRequest,
)
from app.services.job_discovery import fetch_glassdoor_jobs, store_glassdoor_jobs
from app.services.job_service import JobService
from app.services.matching_service import MatchingService

logger = get_logger(__name__)

router = APIRouter(prefix="/jobs", tags=["Jobs"])

CurrentUser = Annotated[User, Depends(get_authenticated_user)]
Database = Annotated[AsyncSession, Depends(get_db)]
JobId = Annotated[UUID, Path(description="Persisted job UUID")]

ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}


@router.post(
    "/discover",
    response_model=DiscoverJobsResponse,
    summary="Discover and store Glassdoor jobs",
    description=(
        "Reads recent Glassdoor alert emails from the connected inbox, extracts "
        "the postings and stores them per user. Existing postings are updated "
        "rather than duplicated. Nothing is scraped and no application is "
        "submitted."
    ),
    responses=ERROR_RESPONSES,
)
async def discover_jobs(
    current_user: CurrentUser,
    db: Database,
    days: Annotated[int, Query(ge=1, le=365, description="How far back to search")] = 30,
    max_messages: Annotated[
        int, Query(ge=1, le=100, description="Maximum emails to inspect")
    ] = 25,
) -> DiscoverJobsResponse:
    """Scan Gmail through the existing Glassdoor extraction, then persist."""
    discovery = await fetch_glassdoor_jobs(
        db,
        current_user,
        days=days,
        max_messages=max_messages,
    )
    stored, created, updated = await store_glassdoor_jobs(
        db, current_user, discovery.jobs
    )
    best = await MatchingService(db).best_matches_for_jobs(
        current_user, [job.id for job in stored]
    )

    logger.info(
        "Glassdoor jobs stored",
        extra={
            "stage": "job_discovery_store",
            "user_id": str(current_user.id),
            "jobs_found": len(discovery.jobs),
            "jobs_created": created,
            "jobs_updated": updated,
        },
    )
    return DiscoverJobsResponse(
        jobs_found=len(discovery.jobs),
        jobs_created=created,
        jobs_updated=updated,
        jobs=[job_response(job, best=best.get(job.id)) for job in stored],
    )


@router.get(
    "",
    response_model=JobListResponse,
    summary="List stored jobs",
    description=(
        "Returns the user's stored postings newest first, each with the "
        "recommended resume when matches have already been computed."
    ),
    responses=ERROR_RESPONSES,
)
async def list_jobs(
    current_user: CurrentUser,
    db: Database,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> JobListResponse:
    service = JobService(db)
    jobs = await service.list_jobs(current_user, limit=limit, offset=offset)
    total = await service.count_jobs(current_user)
    best = await MatchingService(db).best_matches_for_jobs(
        current_user, [job.id for job in jobs]
    )
    return JobListResponse(
        total=total,
        jobs=[job_response(job, best=best.get(job.id)) for job in jobs],
    )


@router.get(
    "/{job_id}",
    response_model=JobResponse,
    summary="Get one stored job",
    responses=ERROR_RESPONSES,
)
async def get_job(job_id: JobId, current_user: CurrentUser, db: Database) -> JobResponse:
    job = await JobService(db).get_job(current_user, job_id)
    best = await MatchingService(db).best_matches_for_jobs(current_user, [job.id])
    return job_response(job, best=best.get(job.id))


@router.post(
    "/{job_id}/match",
    response_model=JobMatchesResponse,
    summary="Match every resume against a job",
    description=(
        "Evaluates all of the user's uploaded resumes, ranks them by "
        "explainable compatibility and stores the result. Unchanged inputs are "
        "reused unless `force` is true. When no resume has been uploaded the "
        "response is an empty, successful result."
    ),
    responses=ERROR_RESPONSES,
)
async def match_job(
    job_id: JobId,
    current_user: CurrentUser,
    db: Database,
    body: MatchJobRequest | None = None,
) -> JobMatchesResponse:
    matching = MatchingService(db)
    job, rows = await matching.match_job(
        current_user, job_id, force=bool(body and body.force)
    )
    message = None
    if not rows:
        message = "Upload a PDF resume to match this job."
    return job_matches_response(job, rows, message=message)


@router.get(
    "/{job_id}/matches",
    response_model=JobMatchesResponse,
    summary="Get stored matches for a job",
    description=(
        "Returns previously computed matches without recomputing. Use the "
        "POST variant to compute or refresh them."
    ),
    responses=ERROR_RESPONSES,
)
async def get_job_matches(
    job_id: JobId, current_user: CurrentUser, db: Database
) -> JobMatchesResponse:
    job = await JobService(db).get_job(current_user, job_id)
    rows = await MatchingService(db).get_job_matches(current_user, job)
    message = None if rows else "No matches have been computed for this job yet."
    return job_matches_response(job, rows, message=message)


__all__ = ["router"]
