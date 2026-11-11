"""Authenticated application-preparation endpoints.

Every route is scoped by the authenticated user inside ``ApplicationService``.
ApplyPilot never performs the final submission: the user completes it on the
employer's page and records it explicitly with ``/mark-submitted``. The
``/submit`` route only hands off the manual steps after an approval whose
fingerprint still matches the reviewed application.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_authenticated_user
from app.db.session import get_db
from app.models.application import Application, QuestionStatus
from app.models.user import User
from app.schemas.application import (
    AnalyzeApplicationRequest,
    ApplicationListResponse,
    ApplicationPreviewResponse,
    ApplicationSummaryResponse,
    ApproveApplicationRequest,
    ConfirmManualSubmissionRequest,
    InspectApplicationRequest,
    PlatformCatalogResponse,
    PlatformInfo,
    SelectResumeRequest,
    SubmissionResponse,
    UpdateQuestionRequest,
    UpdateStageRequest,
)
from app.schemas.application_workflow import ApplicationWorkflowResponse
from app.schemas.common import ErrorResponse
from app.services.application_adapters import adapter_catalog
from app.services.application_service import ApplicationService
from app.services.application_workflow_service import ApplicationWorkflowService

router = APIRouter(prefix="/applications", tags=["Applications"])

CurrentUser = Annotated[User, Depends(get_authenticated_user)]
Database = Annotated[AsyncSession, Depends(get_db)]
ApplicationId = Annotated[UUID, Path(description="Application UUID")]
QuestionId = Annotated[UUID, Path(description="Application question UUID")]

ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    400: {"model": ErrorResponse},
    401: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}


def _preview(service: ApplicationService, application: Application) -> ApplicationPreviewResponse:
    return ApplicationPreviewResponse.model_validate(service.preview_payload(application))


def _summary(application: Application) -> ApplicationSummaryResponse:
    questions = list(application.questions)
    confirmed = sum(
        1
        for question in questions
        if question.status is QuestionStatus.CONFIRMED and question.answer
    )
    return ApplicationSummaryResponse(
        id=application.id,
        job_id=application.job_id,
        job_title=application.job.job_title if application.job else "",
        company=application.job.company if application.job else None,
        platform=application.platform,
        platform_name=application.platform_name,
        application_url=application.application_url,
        status=application.status.value,
        stage=application.stage.value if application.stage else None,
        automation_mode=application.automation_mode,
        requires_account=application.requires_account,
        resume_filename=application.resume_filename,
        questions_total=len(questions),
        questions_confirmed=confirmed,
        missing_required_count=len(application.missing_fields or []),
        created_at=application.created_at,
        updated_at=application.updated_at,
        approved_at=application.approved_at,
        submitted_at=application.submitted_at,
    )


@router.get(
    "/platforms",
    response_model=PlatformCatalogResponse,
    summary="List supported application platforms",
    description=(
        "Describes how each platform is handled. Platforms without automation "
        "use the guided manual workflow; ApplyPilot never pretends to submit them."
    ),
    responses=ERROR_RESPONSES,
)
async def list_platforms(current_user: CurrentUser) -> PlatformCatalogResponse:
    del current_user  # authentication is the only requirement
    return PlatformCatalogResponse(
        platforms=[PlatformInfo(**entry) for entry in adapter_catalog()]
    )


@router.post(
    "/analyze",
    response_model=ApplicationPreviewResponse,
    summary="Prepare an application for a stored job",
    description=(
        "Detects the employer's platform, builds the dynamic questionnaire from "
        "your verified profile, parsed resume and previously approved answers, "
        "and selects the best-matching resume. Existing drafts are refreshed "
        "without losing answers you already confirmed."
    ),
    responses=ERROR_RESPONSES,
)
async def analyze_application(
    payload: AnalyzeApplicationRequest,
    current_user: CurrentUser,
    db: Database,
) -> ApplicationPreviewResponse:
    service = ApplicationService(db)
    application = await service.analyze_job(
        current_user,
        payload.job_id,
        application_url=payload.application_url,
        resume_id=payload.resume_id,
    )
    return _preview(service, application)


@router.get(
    "",
    response_model=ApplicationListResponse,
    summary="List applications",
    description="Returns every application owned by the authenticated user.",
    responses=ERROR_RESPONSES,
)
async def list_applications(
    current_user: CurrentUser, db: Database
) -> ApplicationListResponse:
    applications = await ApplicationService(db).list_applications(current_user)
    return ApplicationListResponse(
        total=len(applications),
        applications=[_summary(application) for application in applications],
    )


@router.get(
    "/{application_id}",
    response_model=ApplicationPreviewResponse,
    summary="Get one application with its questionnaire",
    responses=ERROR_RESPONSES,
)
async def get_application(
    application_id: ApplicationId, current_user: CurrentUser, db: Database
) -> ApplicationPreviewResponse:
    service = ApplicationService(db)
    application = await service.get_application(current_user, application_id)
    return _preview(service, application)


@router.post(
    "/{application_id}/inspect",
    response_model=ApplicationPreviewResponse,
    summary="Set the employer URL and re-inspect the form",
    description=(
        "Use this when the discovered link points to a job board. Confirmed "
        "answers are preserved where the question still exists."
    ),
    responses=ERROR_RESPONSES,
)
async def inspect_application(
    application_id: ApplicationId,
    payload: InspectApplicationRequest,
    current_user: CurrentUser,
    db: Database,
) -> ApplicationPreviewResponse:
    service = ApplicationService(db)
    application = await service.set_application_url(
        current_user,
        application_id,
        payload.application_url,
        employer_careers_url=payload.employer_careers_url,
    )
    return _preview(service, application)


@router.patch(
    "/{application_id}/questions/{question_id}",
    response_model=ApplicationPreviewResponse,
    summary="Save or confirm one answer",
    description=(
        "Confirming an answer saves it for reuse and invalidates any previous "
        "approval, so submission always reflects what you reviewed last."
    ),
    responses=ERROR_RESPONSES,
)
async def update_question(
    application_id: ApplicationId,
    question_id: QuestionId,
    payload: UpdateQuestionRequest,
    current_user: CurrentUser,
    db: Database,
) -> ApplicationPreviewResponse:
    service = ApplicationService(db)
    application = await service.update_question(
        current_user,
        application_id,
        question_id,
        answer=payload.answer,
        confirmed=payload.confirmed,
        note=payload.note,
    )
    return _preview(service, application)


@router.patch(
    "/{application_id}/resume",
    response_model=ApplicationPreviewResponse,
    summary="Select a different resume",
    description="Overrides the matching engine's recommendation for this application only.",
    responses=ERROR_RESPONSES,
)
async def select_resume(
    application_id: ApplicationId,
    payload: SelectResumeRequest,
    current_user: CurrentUser,
    db: Database,
) -> ApplicationPreviewResponse:
    service = ApplicationService(db)
    application = await service.select_resume(
        current_user, application_id, payload.resume_id
    )
    return _preview(service, application)


@router.get(
    "/{application_id}/preview",
    response_model=ApplicationPreviewResponse,
    summary="Get the full application preview",
    description=(
        "Shows every answer, its source, the selected resume and what still "
        "blocks approval."
    ),
    responses=ERROR_RESPONSES,
)
async def get_preview(
    application_id: ApplicationId, current_user: CurrentUser, db: Database
) -> ApplicationPreviewResponse:
    service = ApplicationService(db)
    application = await service.get_application(current_user, application_id)
    return _preview(service, application)


@router.post(
    "/{application_id}/approve",
    response_model=ApplicationPreviewResponse,
    summary="Approve or withdraw approval",
    description=(
        "Approval is explicit and binds to the exact answers and resume shown in "
        "the preview. Any later edit invalidates it."
    ),
    responses=ERROR_RESPONSES,
)
async def approve_application(
    application_id: ApplicationId,
    payload: ApproveApplicationRequest,
    current_user: CurrentUser,
    db: Database,
) -> ApplicationPreviewResponse:
    service = ApplicationService(db)
    application = await service.approve(
        current_user, application_id, approved=payload.approved
    )
    return _preview(service, application)


@router.get(
    "/{application_id}/workflow",
    response_model=ApplicationWorkflowResponse,
    summary="Get the end-to-end workflow snapshot",
    description=(
        "Derives one consistent workflow stage, submission phase and list of "
        "next actions from the existing application, browser-session, form "
        "analysis, execution and intervention records. It is read-only: it "
        "never fills, submits or opens a browser, and it never marks a "
        "submission without explicit user evidence."
    ),
    responses=ERROR_RESPONSES,
)
async def get_application_workflow(
    application_id: ApplicationId, current_user: CurrentUser, db: Database
) -> ApplicationWorkflowResponse:
    return await ApplicationWorkflowService(db).snapshot(
        current_user, application_id
    )


@router.post(
    "/{application_id}/submit",
    response_model=SubmissionResponse,
    summary="Hand off an approved application for manual submission",
    description=(
        "Requires a current explicit approval. ApplyPilot never clicks the "
        "employer's Submit, Apply or Finish control: this returns the manual "
        "handoff steps and records that you must finish on the employer's page. "
        "Record the result with /mark-submitted and confirmed=true. Credentials, "
        "OTPs and CAPTCHA challenges are never handled."
    ),
    responses=ERROR_RESPONSES,
)
async def submit_application(
    application_id: ApplicationId, current_user: CurrentUser, db: Database
) -> SubmissionResponse:
    service = ApplicationService(db)
    outcome = await service.submit(current_user, application_id)
    application = await service.get_application(current_user, application_id)
    return SubmissionResponse(
        submitted=outcome.submitted,
        manual_required=outcome.manual_required,
        message=outcome.message,
        reference=outcome.reference,
        steps=list(outcome.steps),
        application=_preview(service, application),
    )


@router.post(
    "/{application_id}/mark-submitted",
    response_model=ApplicationPreviewResponse,
    summary="Confirm a manual submission",
    description=(
        "Use this after completing the employer's form yourself. It records the "
        "submission and starts status tracking."
    ),
    responses=ERROR_RESPONSES,
)
async def confirm_manual_submission(
    application_id: ApplicationId,
    payload: ConfirmManualSubmissionRequest,
    current_user: CurrentUser,
    db: Database,
) -> ApplicationPreviewResponse:
    service = ApplicationService(db)
    application = await service.confirm_manual_submission(
        current_user,
        application_id,
        reference=payload.reference,
        confirmed=payload.confirmed,
        confirmation_url=payload.confirmation_url,
        confirmation_message=payload.confirmation_message,
    )
    return _preview(service, application)


@router.post(
    "/{application_id}/cancel",
    response_model=ApplicationPreviewResponse,
    summary="Cancel an unfinished application",
    description=(
        "Marks the application cancelled without contacting the employer. An "
        "application that was submitted, or is mid-submission, cannot be "
        "cancelled. Cancelling twice is harmless."
    ),
    responses=ERROR_RESPONSES,
)
async def cancel_application(
    application_id: ApplicationId, current_user: CurrentUser, db: Database
) -> ApplicationPreviewResponse:
    service = ApplicationService(db)
    application = await service.cancel(current_user, application_id)
    return _preview(service, application)


@router.patch(
    "/{application_id}/stage",
    response_model=ApplicationPreviewResponse,
    summary="Update the application stage",
    description="Track screening, interview, offer, rejection or withdrawal after submission.",
    responses=ERROR_RESPONSES,
)
async def update_stage(
    application_id: ApplicationId,
    payload: UpdateStageRequest,
    current_user: CurrentUser,
    db: Database,
) -> ApplicationPreviewResponse:
    service = ApplicationService(db)
    application = await service.update_stage(
        current_user, application_id, payload.stage
    )
    return _preview(service, application)


__all__ = ["router"]
