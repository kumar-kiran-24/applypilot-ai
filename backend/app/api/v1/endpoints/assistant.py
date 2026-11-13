"""AI application-answer assistant endpoints.

The assistant drafts one answer from the authenticated user's verified evidence.
It never saves the draft, never submits an application and never answers legal or
credential questions on the user's behalf.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_authenticated_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.answer_assistant import AnswerDraftResponse, DraftAnswerRequest
from app.schemas.common import ErrorResponse
from app.services.answer_assistant import ApplicationAnswerAssistant

router = APIRouter(prefix="/assistant", tags=["Assistant"])

CurrentUser = Annotated[User, Depends(get_authenticated_user)]
Database = Annotated[AsyncSession, Depends(get_db)]

ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    400: {"model": ErrorResponse},
    401: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}


@router.post(
    "/answers/draft",
    response_model=AnswerDraftResponse,
    summary="Draft one application answer from verified evidence",
    description=(
        "Retrieves evidence deterministically from the owned application, verified "
        "profile, resume intelligence, job intelligence and reusable answers, then "
        "asks the configured LLM to draft one answer from that evidence only. The "
        "draft is never saved automatically. Literal facts such as contact details "
        "are answered from the profile, while legal declarations and missing "
        "sensitive facts are returned as an explicit request for information."
    ),
    responses=ERROR_RESPONSES,
)
async def draft_answer(
    payload: DraftAnswerRequest,
    current_user: CurrentUser,
    session: Database,
) -> AnswerDraftResponse:
    assistant = ApplicationAnswerAssistant(session)
    return await assistant.draft(current_user, payload)
