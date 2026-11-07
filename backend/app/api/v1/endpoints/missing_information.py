"""Authenticated missing-information queue endpoints.

Every route resolves the application through ``ApplicationService``, which
scopes it to the authenticated user. The queue holds no credentials and never
marks an application ready while a required question, stale answer or blocking
conflict remains.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_authenticated_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.application_answer import AnswerConflictResolution
from app.schemas.common import ErrorResponse
from app.schemas.missing_information import (
    ConfirmSuggestedAnswerRequest,
    MissingInformationConflictResponse,
    MissingInformationDetailResponse,
    MissingInformationItemResponse,
    MissingInformationQueueResponse,
    MissingInformationSummaryResponse,
    SubmitMissingAnswerRequest,
)
from app.services.missing_information_service import (
    MissingInformationService,
    QueueItem,
    QueueSnapshot,
)

router = APIRouter(prefix="/missing-information", tags=["Missing information"])

CurrentUser = Annotated[User, Depends(get_authenticated_user)]
Database = Annotated[AsyncSession, Depends(get_db)]
ApplicationId = Annotated[UUID, Path(description="Application UUID")]
QuestionKey = Annotated[
    str,
    Path(
        min_length=1,
        max_length=100,
        pattern=r"^[a-z0-9][a-z0-9_.-]*$",
        description="Stable identifier of the application question",
    ),
]

ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    400: {"model": ErrorResponse},
    401: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}


def _item(item: QueueItem) -> MissingInformationItemResponse:
    return MissingInformationItemResponse(
        question_key=item.question_key,
        question_text=item.question_text,
        section=item.section,
        field_type=item.field_type,
        required=item.required,
        status=item.status,
        reason=item.reason,
        answer=item.answer,
        answer_source=item.answer_source,
        suggested_answer=item.suggested_answer,
        suggested_source=item.suggested_source,
        confidence=item.confidence,
        requires_confirmation=item.requires_confirmation,
        is_sensitive=item.is_sensitive,
        is_legal=item.is_legal,
        blocks_application=item.blocks_application,
        source_record_id=item.source_record_id,
        last_confirmed_at=item.last_confirmed_at,
        options=list(item.options),
    )


def _summary(snapshot: QueueSnapshot) -> MissingInformationSummaryResponse:
    application = snapshot.application
    queue = snapshot.queue
    return MissingInformationSummaryResponse(
        application_id=application.id,
        job_id=application.job_id,
        job_title=application.job.job_title if application.job else "",
        company=application.job.company if application.job else None,
        application_url=application.application_url,
        application_status=application.status.value,
        queue_status=queue.status,
        ready_to_complete=snapshot.ready_to_complete,
        required_total=queue.required_total,
        required_resolved=queue.required_resolved,
        required_remaining=queue.required_remaining,
        optional_unanswered=queue.optional_unanswered,
        stale_count=queue.stale_count,
        conflict_count=queue.conflict_count,
        sensitive_confirmation_count=queue.sensitive_confirmation_count,
        matched_count=queue.matched_count,
        progress_percent=queue.progress_percent,
        last_recalculated_at=queue.last_recalculated_at,
        resolved_at=queue.resolved_at,
    )


def _detail(snapshot: QueueSnapshot) -> MissingInformationDetailResponse:
    summary = _summary(snapshot)
    return MissingInformationDetailResponse(
        **summary.model_dump(),
        required_questions=[_item(item) for item in snapshot.required_questions],
        optional_questions=[_item(item) for item in snapshot.optional_questions],
        matched_questions=[_item(item) for item in snapshot.matched_questions],
        stale_answers=[_item(item) for item in snapshot.stale_answers],
        sensitive_confirmations=[_item(item) for item in snapshot.sensitive_confirmations],
        dismissed_questions=[_item(item) for item in snapshot.dismissed_questions],
        conflicts=[
            MissingInformationConflictResponse(
                question_key=entry.conflict.question_key,
                question_text=entry.conflict.question_text,
                category=entry.conflict.category,
                stored_answer=entry.conflict.stored_answer,
                conflicting_answer=entry.conflict.conflicting_answer,
                conflicting_source=entry.conflict.conflicting_source,
                reason=entry.conflict.reason,
                resolution_options=list(entry.conflict.resolution_options),
                blocks_application=entry.blocks_application,
            )
            for entry in snapshot.conflicts
        ],
    )


@router.get(
    "",
    response_model=MissingInformationQueueResponse,
    summary="Get the missing-information queue",
    description=(
        "Recalculates every actionable application and returns what still "
        "blocks it. Optional unanswered questions never block an application."
    ),
    responses=ERROR_RESPONSES,
)
async def get_queue(current_user: CurrentUser, db: Database) -> MissingInformationQueueResponse:
    snapshots = await MissingInformationService(db).list_queues(current_user)
    return MissingInformationQueueResponse(
        total=len(snapshots), applications=[_summary(item) for item in snapshots]
    )


@router.get(
    "/{application_id}",
    response_model=MissingInformationDetailResponse,
    summary="Get missing questions for one application",
    description=(
        "Returns required questions, optional questions, previously matched "
        "answers, stale answers, conflicts and sensitive confirmations."
    ),
    responses=ERROR_RESPONSES,
)
async def get_application_queue(
    application_id: ApplicationId, current_user: CurrentUser, db: Database
) -> MissingInformationDetailResponse:
    snapshot = await MissingInformationService(db).get_queue(current_user, application_id)
    return _detail(snapshot)


@router.post(
    "/{application_id}/refresh",
    response_model=MissingInformationDetailResponse,
    summary="Recalculate an application's queue",
    description=(
        "Re-runs matching against the current profile and answer bank and "
        "persists the recalculated status."
    ),
    responses=ERROR_RESPONSES,
)
async def refresh_queue(
    application_id: ApplicationId, current_user: CurrentUser, db: Database
) -> MissingInformationDetailResponse:
    snapshot = await MissingInformationService(db).refresh(current_user, application_id)
    return _detail(snapshot)


@router.post(
    "/{application_id}/questions/{question_key}/answer",
    response_model=MissingInformationDetailResponse,
    summary="Submit an answer to a queued question",
    description=(
        "Validates the value, confirms it for this application, re-runs "
        "matching and recalculates the queue. Only approved non-sensitive "
        "answers are added to the reusable answer bank."
    ),
    responses=ERROR_RESPONSES,
)
async def submit_answer(
    application_id: ApplicationId,
    question_key: QuestionKey,
    payload: SubmitMissingAnswerRequest,
    current_user: CurrentUser,
    db: Database,
) -> MissingInformationDetailResponse:
    snapshot = await MissingInformationService(db).submit_answer(
        current_user,
        application_id,
        question_key,
        answer=payload.answer,
        save_for_reuse=payload.save_for_reuse,
    )
    return _detail(snapshot)


@router.post(
    "/{application_id}/questions/{question_key}/confirm",
    response_model=MissingInformationDetailResponse,
    summary="Confirm a suggested answer",
    description=(
        "Confirms the matcher's suggestion, keeping its provenance. Legal "
        "declarations cannot be confirmed without an explicit value."
    ),
    responses=ERROR_RESPONSES,
)
async def confirm_suggested_answer(
    application_id: ApplicationId,
    question_key: QuestionKey,
    payload: ConfirmSuggestedAnswerRequest,
    current_user: CurrentUser,
    db: Database,
) -> MissingInformationDetailResponse:
    snapshot = await MissingInformationService(db).confirm_suggested_answer(
        current_user,
        application_id,
        question_key,
        answer=payload.answer,
        save_for_reuse=payload.save_for_reuse,
    )
    return _detail(snapshot)


@router.post(
    "/{application_id}/questions/{question_key}/dismiss",
    response_model=MissingInformationDetailResponse,
    summary="Dismiss an optional question",
    description=(
        "Skips an unanswered optional question. Required questions cannot be "
        "dismissed, and the dismissal is persisted across reloads."
    ),
    responses=ERROR_RESPONSES,
)
async def dismiss_optional_question(
    application_id: ApplicationId,
    question_key: QuestionKey,
    current_user: CurrentUser,
    db: Database,
) -> MissingInformationDetailResponse:
    snapshot = await MissingInformationService(db).dismiss_optional_question(
        current_user, application_id, question_key
    )
    return _detail(snapshot)


@router.post(
    "/{application_id}/conflicts/{question_key}/resolve",
    response_model=MissingInformationDetailResponse,
    summary="Resolve a stale or conflicting reusable answer",
    description=(
        "Re-confirms the stored value, adopts the verified profile value or "
        "stores a corrected value, then recalculates the queue."
    ),
    responses=ERROR_RESPONSES,
)
async def resolve_conflict(
    application_id: ApplicationId,
    question_key: QuestionKey,
    payload: AnswerConflictResolution,
    current_user: CurrentUser,
    db: Database,
) -> MissingInformationDetailResponse:
    snapshot = await MissingInformationService(db).resolve_conflict(
        current_user, application_id, question_key, payload
    )
    return _detail(snapshot)


__all__ = ["router"]
