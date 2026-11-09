"""Authenticated Phase 2E Stage C field-fill endpoints.

The routes reuse the browser-session prefix because every operation requires
the application's live Stage A session and its latest Stage B analysis. Each
route resolves the application through the authenticated user and then verifies
that the analysis belongs to the live browser session.

There is no route that submits, clicks Apply/Submit, or opens a replacement
browser after a session expires.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_authenticated_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.application_fill import (
    FillExecutionResponse,
    FillPlanResponse,
    StartFieldFillRequest,
)
from app.schemas.browser_session import PauseBrowserSessionRequest
from app.schemas.common import ErrorResponse
from app.services.application_field_fill_service import ApplicationFieldFillService

router = APIRouter(prefix="/browser-sessions", tags=["Browser automation"])

CurrentUser = Annotated[User, Depends(get_authenticated_user)]
Database = Annotated[AsyncSession, Depends(get_db)]
ApplicationId = Annotated[UUID, Path(description="Application UUID")]

ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    400: {"model": ErrorResponse},
    401: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
    503: {"model": ErrorResponse},
}


@router.get(
    "/by-application/{application_id}/fill-plan",
    response_model=FillPlanResponse,
    summary="Preview the intelligent field-fill plan",
    description=(
        "Builds a deterministic plan from the latest form analysis: fields that "
        "can be filled, fields needing confirmation, fields requiring human input, "
        "and fields left untouched because they already contain a value. The plan "
        "does not interact with the page."
    ),
    responses=ERROR_RESPONSES,
)
async def get_fill_plan(
    application_id: ApplicationId, current_user: CurrentUser, db: Database
) -> FillPlanResponse:
    return await ApplicationFieldFillService(db).plan(current_user, application_id)


@router.post(
    "/by-application/{application_id}/fill",
    response_model=FillExecutionResponse,
    summary="Fill the safe fields in the live application form",
    description=(
        "Types only verified profile or confirmed reusable answers into fields the "
        "planner marked ready, uploads the selected owned resume when supported, "
        "and verifies every interaction. It never submits, accepts a legal "
        "declaration, solves a CAPTCHA or enters a password/OTP/MFA code. Repeating "
        "the call for the same analysis is idempotent."
    ),
    responses=ERROR_RESPONSES,
)
async def start_field_fill(
    application_id: ApplicationId,
    payload: StartFieldFillRequest,
    current_user: CurrentUser,
    db: Database,
) -> FillExecutionResponse:
    return await ApplicationFieldFillService(db).start(
        current_user, application_id, restart=payload.restart
    )


@router.get(
    "/by-application/{application_id}/fill",
    response_model=FillExecutionResponse,
    summary="Get field-fill progress",
    description=(
        "Returns persisted, non-secret execution progress: successful, failed, "
        "skipped and pending fields with their verification status."
    ),
    responses=ERROR_RESPONSES,
)
async def get_field_fill(
    application_id: ApplicationId, current_user: CurrentUser, db: Database
) -> FillExecutionResponse:
    return await ApplicationFieldFillService(db).get_execution(
        current_user, application_id
    )


@router.post(
    "/by-application/{application_id}/fill/pause",
    response_model=FillExecutionResponse,
    summary="Pause a field-fill run without closing the browser",
    responses=ERROR_RESPONSES,
)
async def pause_field_fill(
    application_id: ApplicationId,
    current_user: CurrentUser,
    db: Database,
    payload: PauseBrowserSessionRequest | None = None,
) -> FillExecutionResponse:
    return await ApplicationFieldFillService(db).pause(
        current_user, application_id, reason=payload.reason if payload else None
    )


@router.post(
    "/by-application/{application_id}/fill/resume",
    response_model=FillExecutionResponse,
    summary="Resume a paused field-fill run",
    description=(
        "Continues only the fields that are still ready. If the page was analysed "
        "again after the run was planned, a conflict is returned instead of typing "
        "stale values into the new form."
    ),
    responses=ERROR_RESPONSES,
)
async def resume_field_fill(
    application_id: ApplicationId, current_user: CurrentUser, db: Database
) -> FillExecutionResponse:
    return await ApplicationFieldFillService(db).resume(current_user, application_id)


@router.post(
    "/by-application/{application_id}/fill/cancel",
    response_model=FillExecutionResponse,
    summary="Cancel a field-fill run",
    description=(
        "Stops the run and records the cancellation. The employer's form is left as "
        "it is; the browser session is managed separately."
    ),
    responses=ERROR_RESPONSES,
)
async def cancel_field_fill(
    application_id: ApplicationId, current_user: CurrentUser, db: Database
) -> FillExecutionResponse:
    return await ApplicationFieldFillService(db).cancel(current_user, application_id)


__all__ = ["router"]
