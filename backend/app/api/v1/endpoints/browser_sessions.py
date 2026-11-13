"""Authenticated Phase 2E browser-session and form-analysis endpoints.

Every route is scoped to the authenticated user: an application is resolved by
``ApplicationService`` and a session by both its owner and its application's
owner. The API exposes execution state and non-secret form metadata only -
never browser cookies, storage state, a CDP endpoint, passwords, OTP/MFA codes,
CAPTCHA answers or unrestricted browser control.

The Stage B routes detect and classify fields only; they never fill, click a
legal acceptance, submit or create an account.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.dependencies import get_authenticated_user
from app.db.session import get_db
from app.integrations.browser.view import browser_view_capability
from app.models.user import User
from app.schemas.application_form import (
    ApplicationFormAnalysisResponse,
    ApplicationFormFieldsResponse,
)
from app.schemas.browser_session import (
    ApplicationRedirectResponse,
    BrowserSessionCapabilitiesResponse,
    BrowserSessionListResponse,
    BrowserSessionResponse,
    BrowserViewInfo,
    PauseBrowserSessionRequest,
    StartBrowserSessionRequest,
)
from app.schemas.common import ErrorResponse
from app.services.application_form_service import ApplicationFormService
from app.services.application_redirect_service import ApplicationRedirectService
from app.services.browser_session_service import BrowserSessionService

router = APIRouter(prefix="/browser-sessions", tags=["Browser automation"])

CurrentUser = Annotated[User, Depends(get_authenticated_user)]
Database = Annotated[AsyncSession, Depends(get_db)]
ApplicationId = Annotated[UUID, Path(description="Application UUID")]
SessionId = Annotated[UUID, Path(description="Browser session UUID")]

ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    400: {"model": ErrorResponse},
    401: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
    503: {"model": ErrorResponse},
}


def _response(service: BrowserSessionService, row: object) -> BrowserSessionResponse:
    return BrowserSessionResponse.model_validate(service.snapshot(row))


@router.get(
    "/capabilities",
    response_model=BrowserSessionCapabilitiesResponse,
    summary="Describe live-browser capabilities",
    description=(
        "Reports whether this deployment can actually render an interactive "
        "browser. A backend Playwright session alone is not a user-visible "
        "browser; a separate streaming component is required and is reported "
        "as unavailable when it is not deployed."
    ),
    responses=ERROR_RESPONSES,
)
async def get_capabilities(current_user: CurrentUser) -> BrowserSessionCapabilitiesResponse:
    del current_user  # authentication is the only requirement
    return BrowserSessionCapabilitiesResponse(
        view=BrowserViewInfo(**browser_view_capability().to_payload()),
        automation_enabled=settings.APPLICATION_AUTOMATION_ENABLED,
        session_ttl_seconds=settings.APPLICATION_BROWSER_SESSION_TTL_SECONDS,
    )


@router.post(
    "",
    response_model=BrowserSessionResponse,
    summary="Start (or return) the application's browser session",
    description=(
        "Validates and opens the persisted employer URL in an isolated browser "
        "session. Repeated calls while the browser is live return the same "
        "session instead of starting a second one. Job-board links, private "
        "hosts and unsafe schemes are rejected. The browser stops at "
        "browser_ready: form analysis is the next stage."
    ),
    responses=ERROR_RESPONSES,
)
async def start_session(
    payload: StartBrowserSessionRequest,
    current_user: CurrentUser,
    db: Database,
) -> BrowserSessionResponse:
    service = BrowserSessionService(db)
    row = await service.start(
        current_user, payload.application_id, restart=payload.restart
    )
    return _response(service, row)


@router.get(
    "/active",
    response_model=BrowserSessionListResponse,
    summary="List live browser sessions",
    description="Only sessions whose browser is genuinely open are returned.",
    responses=ERROR_RESPONSES,
)
async def list_active_sessions(
    current_user: CurrentUser, db: Database
) -> BrowserSessionListResponse:
    service = BrowserSessionService(db)
    rows = await service.list_active(current_user)
    return BrowserSessionListResponse(
        total=len(rows), sessions=[_response(service, row) for row in rows]
    )


@router.get(
    "/by-application/{application_id}",
    response_model=BrowserSessionResponse,
    summary="Get the browser session for one application",
    responses=ERROR_RESPONSES,
)
async def get_application_session(
    application_id: ApplicationId, current_user: CurrentUser, db: Database
) -> BrowserSessionResponse:
    service = BrowserSessionService(db)
    row = await service.get_session(current_user, application_id)
    return _response(service, row)


@router.post(
    "/by-application/{application_id}/form-analysis",
    response_model=ApplicationFormAnalysisResponse,
    summary="Analyze the current application form",
    description=(
        "Reads the live employer page through the owned browser session and "
        "classifies every detected field without filling, clicking or submitting "
        "anything. Legal declarations, CAPTCHA, OTP/MFA, passwords and sensitive "
        "demographic questions are always marked for human handling. Each call "
        "stores a new immutable analysis version."
    ),
    responses=ERROR_RESPONSES,
)
async def analyze_application_form(
    application_id: ApplicationId, current_user: CurrentUser, db: Database
) -> ApplicationFormAnalysisResponse:
    service = ApplicationFormService(db)
    return await service.analyze(current_user, application_id)


@router.get(
    "/by-application/{application_id}/form-analysis",
    response_model=ApplicationFormAnalysisResponse,
    summary="Get the latest application form analysis",
    description="Returns the newest stored detection result for the owned application.",
    responses=ERROR_RESPONSES,
)
async def get_latest_form_analysis(
    application_id: ApplicationId, current_user: CurrentUser, db: Database
) -> ApplicationFormAnalysisResponse:
    service = ApplicationFormService(db)
    return await service.latest(current_user, application_id)


@router.get(
    "/by-application/{application_id}/form-analysis/fields",
    response_model=ApplicationFormFieldsResponse,
    summary="Get the detected fields from the latest form analysis",
    responses=ERROR_RESPONSES,
)
async def get_latest_form_fields(
    application_id: ApplicationId, current_user: CurrentUser, db: Database
) -> ApplicationFormFieldsResponse:
    service = ApplicationFormService(db)
    return await service.latest_fields(current_user, application_id)


@router.post(
    "/by-application/{application_id}/form-analysis/reanalyze",
    response_model=ApplicationFormAnalysisResponse,
    summary="Re-analyze the current application page",
    description=(
        "Inspects the page again so newly revealed fields (new steps, expanded "
        "sections, radio-triggered fields) are detected. The previous analysis "
        "version is never overwritten."
    ),
    responses=ERROR_RESPONSES,
)
async def reanalyze_application_form(
    application_id: ApplicationId, current_user: CurrentUser, db: Database
) -> ApplicationFormAnalysisResponse:
    service = ApplicationFormService(db)
    return await service.analyze(current_user, application_id)


@router.post(
    "/by-application/{application_id}/detect-destination",
    response_model=ApplicationRedirectResponse,
    summary="Detect where Easy Apply landed",
    description=(
        "Watches the owned live browser after the user starts Easy Apply on a job "
        "listing, then validates the destination. Only recognised application "
        "platforms on public addresses are stored; the original listing URL is "
        "never overwritten. Unsupported, private or look-alike destinations are "
        "reported precisely instead of as a generic browser failure. The button "
        "is never clicked on the user's behalf."
    ),
    responses=ERROR_RESPONSES,
)
async def detect_application_destination(
    application_id: ApplicationId, current_user: CurrentUser, db: Database
) -> ApplicationRedirectResponse:
    service = ApplicationRedirectService(db)
    outcome = await service.check(current_user, application_id)
    return ApplicationRedirectResponse.model_validate(outcome.to_payload())


@router.post(
    "/by-application/{application_id}/pause",
    response_model=BrowserSessionResponse,
    summary="Pause automation without closing the browser",
    description=(
        "Keeps the browser alive so the same page can be resumed. Paused "
        "sessions still expire and are never presented as preserved afterwards."
    ),
    responses=ERROR_RESPONSES,
)
async def pause_session(
    application_id: ApplicationId,
    current_user: CurrentUser,
    db: Database,
    payload: PauseBrowserSessionRequest | None = None,
) -> BrowserSessionResponse:
    service = BrowserSessionService(db)
    row = await service.pause(
        current_user, application_id, reason=payload.reason if payload else None
    )
    return _response(service, row)


@router.post(
    "/by-application/{application_id}/resume",
    response_model=BrowserSessionResponse,
    summary="Resume a paused browser session",
    description=(
        "Fails with browser_session_expired when the browser is gone; the user "
        "must start a new run. The previous page state is never reused."
    ),
    responses=ERROR_RESPONSES,
)
async def resume_session(
    application_id: ApplicationId, current_user: CurrentUser, db: Database
) -> BrowserSessionResponse:
    service = BrowserSessionService(db)
    row = await service.resume(current_user, application_id)
    return _response(service, row)


@router.post(
    "/by-application/{application_id}/cancel",
    response_model=BrowserSessionResponse,
    summary="Cancel automation and close the browser",
    responses=ERROR_RESPONSES,
)
async def cancel_session(
    application_id: ApplicationId, current_user: CurrentUser, db: Database
) -> BrowserSessionResponse:
    service = BrowserSessionService(db)
    row = await service.cancel(current_user, application_id)
    return _response(service, row)


@router.get(
    "/{session_id}",
    response_model=BrowserSessionResponse,
    summary="Get one browser session by id",
    responses=ERROR_RESPONSES,
)
async def get_session(
    session_id: SessionId, current_user: CurrentUser, db: Database
) -> BrowserSessionResponse:
    service = BrowserSessionService(db)
    row = await service.get_session_by_id(current_user, session_id)
    return _response(service, row)


__all__ = ["router"]
