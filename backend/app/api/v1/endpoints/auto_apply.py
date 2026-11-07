"""Authenticated, user-scoped auto-apply settings and eligibility endpoints.

Every route resolves records through the authenticated user; a client-supplied
application id can never reach another user's data. No endpoint starts browser
automation, stores credentials or submits anything in Phase 2D.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_authenticated_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.auto_apply import (
    ApproveApplicationEligibilityRequest,
    AutoApplySettingsResponse,
    AutoApplySettingsUpdate,
    EligibilityApprovalResponse,
    EligibilityCheckResponse,
    EligibilityListResponse,
    EligibilityOverviewResponse,
    EligibilityReportResponse,
    EligibilitySummaryResponse,
)
from app.schemas.common import ErrorResponse
from app.services.auto_apply_eligibility import (
    AutoApplyEligibilityService,
    EligibilityCheck,
    EligibilityReport,
    EligibilityStatus,
)
from app.services.auto_apply_settings import AutoApplySettingsService

router = APIRouter(prefix="/auto-apply", tags=["Auto-apply"])

CurrentUser = Annotated[User, Depends(get_authenticated_user)]
Database = Annotated[AsyncSession, Depends(get_db)]
ApplicationId = Annotated[UUID, Path(description="Application UUID")]

ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    400: {"model": ErrorResponse},
    401: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}


def _check(check: EligibilityCheck) -> EligibilityCheckResponse:
    return EligibilityCheckResponse.model_validate(check)


def _report(report: EligibilityReport) -> EligibilityReportResponse:
    return EligibilityReportResponse(
        application_id=report.application_id,
        job_id=report.job_id,
        job_title=report.job_title,
        company=report.company,
        platform=report.platform,
        platform_name=report.platform_name,
        application_url=report.application_url,
        application_status=report.application_status.value,
        resume_selected=report.resume_id is not None,
        resume_id=report.resume_id,
        resume_filename=report.resume_filename,
        resume_match_score=report.resume_match_score,
        overall_decision=report.overall_decision.value,
        readiness_percentage=report.readiness_percentage,
        message=report.message,
        blocking_issues=[_check(check) for check in report.blocking_issues],
        warnings=[_check(check) for check in report.warnings],
        required_user_actions=list(report.required_user_actions),
        passed_checks=[_check(check) for check in report.passed_checks],
        failed_checks=[_check(check) for check in report.failed_checks],
        checks=[_check(check) for check in report.checks],
        automation_enabled=report.automation_enabled,
        require_user_approval=report.require_user_approval,
        require_user_review=report.require_user_review,
        approval=EligibilityApprovalResponse.from_record(
            report.approval, current=report.approval_current
        ),
        evaluation_timestamp=report.evaluation_timestamp,
        report_version=report.report_version,
    )


def _summary(report: EligibilityReport) -> EligibilitySummaryResponse:
    return EligibilitySummaryResponse(
        application_id=report.application_id,
        job_id=report.job_id,
        job_title=report.job_title,
        company=report.company,
        application_status=report.application_status.value,
        platform_name=report.platform_name,
        resume_filename=report.resume_filename,
        resume_match_score=report.resume_match_score,
        overall_decision=report.overall_decision.value,
        readiness_percentage=report.readiness_percentage,
        blocking_count=len(report.blocking_issues),
        warning_count=len(report.warnings),
        blocking_issue_names=[check.name for check in report.blocking_issues],
        next_action=(
            report.required_user_actions[0] if report.required_user_actions else None
        ),
        automation_enabled=report.automation_enabled,
        approved=report.approved,
        evaluation_timestamp=report.evaluation_timestamp,
        report_version=report.report_version,
    )


def _primary_status(report: EligibilityReport) -> EligibilityStatus:
    """One bucket per application so dashboard counts never double-count."""
    if report.blocking_issues:
        return report.blocking_issues[0].status
    if report.warnings:
        return report.warnings[0].status
    return EligibilityStatus.ELIGIBLE


def _overview(
    reports: list[EligibilityReport], *, automation_enabled: bool
) -> EligibilityOverviewResponse:
    overview = EligibilityOverviewResponse(
        total=len(reports), automation_enabled=automation_enabled
    )
    for report in reports:
        if report.overall_decision.value == "ready_to_prepare":
            overview.ready_to_prepare += 1
        elif report.overall_decision.value == "ready_for_user_review":
            overview.ready_for_user_review += 1
        else:
            overview.blocked += 1
        status = _primary_status(report)
        if status is EligibilityStatus.NEEDS_INFORMATION:
            overview.needs_information += 1
        elif status is EligibilityStatus.NEEDS_CONFIRMATION:
            overview.needs_confirmation += 1
        elif status is EligibilityStatus.NEEDS_USER_APPROVAL:
            overview.needs_user_approval += 1
        elif status is EligibilityStatus.INELIGIBLE:
            overview.ineligible += 1
        elif status is EligibilityStatus.DUPLICATE:
            overview.duplicate += 1
        elif status is EligibilityStatus.ALREADY_APPLIED:
            overview.already_applied += 1
        elif status is EligibilityStatus.UNSUPPORTED_PLATFORM:
            overview.unsupported_platform += 1
        elif status is EligibilityStatus.FAILED:
            overview.failed += 1
    return overview


@router.get(
    "/settings",
    response_model=AutoApplySettingsResponse,
    summary="Get auto-apply settings",
    description=(
        "Returns the authenticated user's automation settings. Missing settings "
        "are created once with conservative defaults: automation off, approval "
        "and review required, and no salary or relocation values assumed."
    ),
    responses=ERROR_RESPONSES,
)
async def get_settings(
    current_user: CurrentUser, db: Database
) -> AutoApplySettingsResponse:
    settings = await AutoApplySettingsService(db).get_settings(current_user)
    return AutoApplySettingsResponse.model_validate(settings)


@router.put(
    "/settings",
    response_model=AutoApplySettingsResponse,
    summary="Update auto-apply settings",
    description=(
        "Partial update of job targeting, salary bounds, relocation willingness, "
        "daily limits and approval requirements. Existing job preferences are "
        "left untouched."
    ),
    responses=ERROR_RESPONSES,
)
async def update_settings(
    payload: AutoApplySettingsUpdate, current_user: CurrentUser, db: Database
) -> AutoApplySettingsResponse:
    settings = await AutoApplySettingsService(db).update_settings(current_user, payload)
    return AutoApplySettingsResponse.model_validate(settings)


@router.get(
    "/eligibility",
    response_model=EligibilityListResponse,
    summary="List eligibility for every application",
    description=(
        "Evaluates every application owned by the authenticated user and reports "
        "which are ready to prepare, waiting for review or blocked."
    ),
    responses=ERROR_RESPONSES,
)
async def list_eligibility(
    current_user: CurrentUser, db: Database
) -> EligibilityListResponse:
    service = AutoApplyEligibilityService(db)
    reports = await service.list_reports(current_user)
    settings = await AutoApplySettingsService(db).get_settings(current_user)
    return EligibilityListResponse(
        overview=_overview(
            reports, automation_enabled=settings.automation_enabled
        ),
        applications=[_summary(report) for report in reports],
    )


@router.get(
    "/eligibility/{application_id}",
    response_model=EligibilityReportResponse,
    summary="Get one application's eligibility report",
    description=(
        "Returns every check with its status, explanation, evidence source and "
        "suggested resolution. The application must belong to the caller."
    ),
    responses=ERROR_RESPONSES,
)
async def get_eligibility(
    application_id: ApplicationId, current_user: CurrentUser, db: Database
) -> EligibilityReportResponse:
    report = await AutoApplyEligibilityService(db).evaluate_application(
        current_user, application_id
    )
    return _report(report)


@router.post(
    "/eligibility/{application_id}/evaluate",
    response_model=EligibilityReportResponse,
    summary="Re-evaluate one application",
    description=(
        "Recalculates the report from the current profile, resume, answer queue, "
        "job posting and automation settings. It never prepares or submits a form."
    ),
    responses=ERROR_RESPONSES,
)
async def evaluate_eligibility(
    application_id: ApplicationId, current_user: CurrentUser, db: Database
) -> EligibilityReportResponse:
    report = await AutoApplyEligibilityService(db).evaluate_application(
        current_user, application_id
    )
    return _report(report)


@router.post(
    "/eligibility/{application_id}/approve",
    response_model=EligibilityReportResponse,
    summary="Approve or withdraw preparation of one application",
    description=(
        "Records the authenticated user, timestamp and exact report version. A "
        "blocked report cannot be approved, and a changed report invalidates the "
        "previous approval."
    ),
    responses=ERROR_RESPONSES,
)
async def approve_eligibility(
    application_id: ApplicationId,
    payload: ApproveApplicationEligibilityRequest,
    current_user: CurrentUser,
    db: Database,
) -> EligibilityReportResponse:
    report = await AutoApplyEligibilityService(db).approve_application(
        current_user,
        application_id,
        report_version=payload.report_version,
        approved=payload.approved,
    )
    return _report(report)


__all__ = ["router"]
