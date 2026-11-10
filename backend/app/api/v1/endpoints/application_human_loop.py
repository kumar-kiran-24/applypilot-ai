"""Authenticated Phase 2E Stage D human-in-the-loop endpoints.

Every route resolves the application through the authenticated user and then
checks the owned browser session/execution association in the service. There is
no endpoint that accepts legal declarations, CAPTCHA/OTP/MFA codes, browser
storage, cookies or a client-supplied "resolved" status. Resolution is only
possible after a fresh analysis proves the condition is gone.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_authenticated_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.application_fill import FillExecutionResponse
from app.schemas.application_human_loop import (
    FinalReviewResponse,
    HumanLoopReconcileResponse,
    InterventionListResponse,
    InterventionResponse,
)
from app.schemas.common import ErrorResponse
from app.services.application_human_loop_service import ApplicationHumanLoopService

router = APIRouter(prefix="/browser-sessions", tags=["Browser automation"])

CurrentUser = Annotated[User, Depends(get_authenticated_user)]
Database = Annotated[AsyncSession, Depends(get_db)]
ApplicationId = Annotated[UUID, Path(description="Application UUID")]
InterventionId = Annotated[UUID, Path(description="Intervention UUID")]

ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    400: {"model": ErrorResponse},
    401: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
    503: {"model": ErrorResponse},
}


@router.get(
    "/by-application/{application_id}/interventions",
    response_model=InterventionListResponse,
    summary="List human interventions for an application",
    description=(
        "Returns persisted, non-secret intervention items. The queue is "
        "synchronised from the latest completed execution; it never exposes a "
        "field value or browser state."
    ),
    responses=ERROR_RESPONSES,
)
async def list_interventions(
    application_id: ApplicationId, current_user: CurrentUser, db: Database
) -> InterventionListResponse:
    return await ApplicationHumanLoopService(db).list_interventions(
        current_user, application_id
    )


@router.get(
    "/by-application/{application_id}/interventions/{intervention_id}",
    response_model=InterventionResponse,
    summary="Get one human intervention",
    responses=ERROR_RESPONSES,
)
async def get_intervention(
    application_id: ApplicationId,
    intervention_id: InterventionId,
    current_user: CurrentUser,
    db: Database,
) -> InterventionResponse:
    return await ApplicationHumanLoopService(db).get_intervention(
        current_user, application_id, intervention_id
    )


@router.post(
    "/by-application/{application_id}/interventions/{intervention_id}/working",
    response_model=InterventionResponse,
    summary="Mark an intervention as being handled",
    description=(
        "Records that the user is working on the item. This never resolves it "
        "and never changes the employer's form."
    ),
    responses=ERROR_RESPONSES,
)
async def mark_intervention_working(
    application_id: ApplicationId,
    intervention_id: InterventionId,
    current_user: CurrentUser,
    db: Database,
) -> InterventionResponse:
    return await ApplicationHumanLoopService(db).mark_working(
        current_user, application_id, intervention_id
    )


@router.post(
    "/by-application/{application_id}/interventions/{intervention_id}/resolve",
    response_model=InterventionResponse,
    summary="Resolve an intervention after fresh verification",
    description=(
        "Re-analyzes the live form and resolves only when the required condition "
        "is genuinely satisfied. Sensitive, legal and CAPTCHA/OTP/MFA items can "
        "never be resolved by this endpoint."
    ),
    responses=ERROR_RESPONSES,
)
async def resolve_intervention(
    application_id: ApplicationId,
    intervention_id: InterventionId,
    current_user: CurrentUser,
    db: Database,
) -> InterventionResponse:
    return await ApplicationHumanLoopService(db).resolve_intervention(
        current_user, application_id, intervention_id
    )


@router.post(
    "/by-application/{application_id}/interventions/{intervention_id}/cancel",
    response_model=InterventionResponse,
    summary="Dismiss an intervention",
    responses=ERROR_RESPONSES,
)
async def cancel_intervention(
    application_id: ApplicationId,
    intervention_id: InterventionId,
    current_user: CurrentUser,
    db: Database,
) -> InterventionResponse:
    return await ApplicationHumanLoopService(db).cancel_intervention(
        current_user, application_id, intervention_id
    )


@router.post(
    "/by-application/{application_id}/human-loop/reanalyze",
    response_model=HumanLoopReconcileResponse,
    summary="Re-analyze and reconcile after human intervention",
    description=(
        "Stores a new immutable analysis version, diffs field metadata against "
        "the previous version, reconciles the intervention queue, and reports "
        "field completion without exposing values. The backend Playwright page "
        "is not a user-visible browser; external changes cannot be observed."
    ),
    responses=ERROR_RESPONSES,
)
async def reanalyze_for_human_loop(
    application_id: ApplicationId, current_user: CurrentUser, db: Database
) -> HumanLoopReconcileResponse:
    return await ApplicationHumanLoopService(db).reanalyze(
        current_user, application_id
    )


@router.post(
    "/by-application/{application_id}/human-loop/resume",
    response_model=FillExecutionResponse,
    summary="Resume filling after human intervention",
    description=(
        "Uses the latest analysis, preserves successful field results, fills only "
        "remaining ready fields, and pauses again if new information is needed. "
        "It never creates a browser session or submits the application."
    ),
    responses=ERROR_RESPONSES,
)
async def resume_for_human_loop(
    application_id: ApplicationId, current_user: CurrentUser, db: Database
) -> FillExecutionResponse:
    return await ApplicationHumanLoopService(db).resume(current_user, application_id)


@router.get(
    "/by-application/{application_id}/final-review",
    response_model=FinalReviewResponse,
    summary="Get the final review checklist",
    description=(
        "Summarises filled, verified, skipped and pending items, the selected "
        "resume and profile sections. Ready for final review does not mean the "
        "application was submitted; submission remains manual."
    ),
    responses=ERROR_RESPONSES,
)
async def get_final_review(
    application_id: ApplicationId, current_user: CurrentUser, db: Database
) -> FinalReviewResponse:
    return await ApplicationHumanLoopService(db).final_review(
        current_user, application_id
    )


__all__ = ["router"]
