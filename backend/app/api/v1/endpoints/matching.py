"""Authenticated bulk matching and library summary endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.serializers import as_utc, best_match_summary
from app.core.dependencies import get_authenticated_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.common import ErrorResponse
from app.schemas.matching import (
    BulkMatchRequest,
    BulkMatchResponse,
    BulkMatchResult,
    MatchingSummaryResponse,
    ResumeMatchStat,
    SummaryBestMatch,
)
from app.services.matching_engine import SCORE_DISCLAIMER
from app.services.matching_service import MatchingService

router = APIRouter(prefix="/matching", tags=["Matching"])

CurrentUser = Annotated[User, Depends(get_authenticated_user)]
Database = Annotated[AsyncSession, Depends(get_db)]

ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}


@router.post(
    "/bulk",
    response_model=BulkMatchResponse,
    summary="Match several jobs at once",
    description=(
        "Evaluates every owned resume against the requested jobs, or the most "
        "recent stored jobs when `job_ids` is omitted. Cached results are "
        "reused unless `force` is true."
    ),
    responses=ERROR_RESPONSES,
)
async def bulk_match(
    current_user: CurrentUser,
    db: Database,
    body: BulkMatchRequest | None = None,
) -> BulkMatchResponse:
    request = body or BulkMatchRequest()
    pairs = await MatchingService(db).bulk_match(
        current_user,
        job_ids=request.job_ids,
        limit=request.limit,
        force=request.force,
    )
    results: list[BulkMatchResult] = []
    matched = 0
    for job, rows in pairs:
        best = rows[0] if rows else None
        if rows:
            matched += 1
        results.append(
            BulkMatchResult(
                job_id=job.id,
                job_title=job.job_title,
                company=job.company,
                best_match=best_match_summary(best) if best is not None else None,
                matches_count=len(rows),
                resumes_evaluated=len(rows),
                computed_at=as_utc(max((row.computed_at for row in rows), default=None)),
            )
        )
    return BulkMatchResponse(
        matched_jobs=matched,
        results=results,
        disclaimer=SCORE_DISCLAIMER,
    )


@router.get(
    "/summary",
    response_model=MatchingSummaryResponse,
    summary="Matching summary for the resume library",
    description=(
        "Aggregates stored matches for the authenticated user only: how many "
        "jobs were matched, which resume is recommended most often and the "
        "strongest pairing."
    ),
    responses=ERROR_RESPONSES,
)
async def matching_summary(
    current_user: CurrentUser, db: Database
) -> MatchingSummaryResponse:
    data = await MatchingService(db).summary(current_user)
    best = data["best_match"]
    return MatchingSummaryResponse(
        jobs_total=data["jobs_total"],
        jobs_matched=data["jobs_matched"],
        resumes_total=data["resumes_total"],
        resumes_matched=data["resumes_matched"],
        average_best_score=data["average_best_score"],
        best_match=SummaryBestMatch(**best) if best else None,
        resume_stats=[ResumeMatchStat(**stat) for stat in data["resume_stats"]],
        last_computed_at=data["last_computed_at"],
        disclaimer=SCORE_DISCLAIMER,
    )


__all__ = ["router"]
