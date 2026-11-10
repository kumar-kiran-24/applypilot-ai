"""Phase 2E Stage F: one derived, consistent view of the application workflow.

This service is deliberately **read-only**. It composes the records that Stage
A-E already own (application status, browser session, form analysis, fill
execution, interventions) into a single snapshot and recommends the next safe
step. It does not drive the browser, fill fields, create sessions or submit
anything, so it cannot become a second orchestration engine or a second source
of truth.

The snapshot makes the end-to-end hand-off explicit:

    discovered -> matched -> application created -> information -> approval
    -> browser ready -> form analysis -> filling -> paused for user
    -> final review -> awaiting user submission -> submitted

``workflow_stage`` and ``submission_phase`` are derived on every request. They
are never persisted, which is why an invalid transition cannot be "written" by
this module: the owning state machines still validate every change.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.models.application import Application, ApplicationStatus
from app.models.application_field_execution import (
    ApplicationFieldExecution,
    FieldExecutionStatus,
)
from app.models.application_form_analysis import ApplicationFormAnalysis
from app.models.application_intervention import (
    ApplicationIntervention,
    InterventionStatus,
)
from app.models.browser_session import BrowserSession, BrowserSessionStatus
from app.models.user import User
from app.schemas.application_workflow import (
    ApplicationWorkflowResponse,
    SubmissionEvidence,
    WorkflowAction,
    WorkflowAnalysisSummary,
    WorkflowBrowserSummary,
    WorkflowExecutionSummary,
    WorkflowInterventionSummary,
)
from app.services.application_service import ApplicationService
from app.services.browser_session_service import FINALIZED_APPLICATION_STATES

logger = get_logger(__name__)

FINAL_SUBMISSION_NOTE = (
    "ApplyPilot never clicks Submit, Apply or Finish. You review the employer's "
    "page and submit it yourself; a submission is recorded only after you "
    "explicitly confirm it."
)

_TERMINAL_STAGES = {
    "submitted",
    "cancelled",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


class ApplicationWorkflowService:
    """Build the derived end-to-end snapshot for one owned application."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.applications = ApplicationService(session)

    async def snapshot(
        self, user: User, application_id: uuid.UUID
    ) -> ApplicationWorkflowResponse:
        application = await self.applications.get_application(user, application_id)
        browser = await self._latest_browser(user.id, application.id)
        analysis = await self._latest_analysis(user.id, application.id)
        execution = await self._latest_execution(user.id, application.id)
        interventions = await self._interventions(user.id, application.id)

        evidence = self._submission_evidence(application)
        stage = self._stage(application, browser, analysis, execution)
        execution_summary = self._execution_summary(execution)
        blocking = self._blocking_items(
            application, execution, interventions, browser
        )
        ready = bool(
            execution is not None
            and execution.status
            in {
                FieldExecutionStatus.READY_FOR_FINAL_REVIEW,
                FieldExecutionStatus.COMPLETED,
            }
            and not blocking
        )

        snapshot = ApplicationWorkflowResponse(
            application_id=application.id,
            job_id=application.job_id,
            job_title=application.job.job_title if application.job else "",
            company=application.job.company if application.job else None,
            application_url=application.application_url,
            status=application.status.value,
            workflow_stage=stage,
            submission_phase=self._submission_phase(application, stage, evidence),
            ready_for_final_review=ready,
            missing_required_count=len(application.missing_fields or []),
            blocking_items=blocking,
            next_actions=self._next_actions(
                application=application,
                browser=browser,
                analysis=analysis,
                execution=execution,
                ready=ready,
                blocking=blocking,
            ),
            browser=self._browser_summary(browser),
            analysis=(
                WorkflowAnalysisSummary(
                    analysis_id=analysis.id,
                    version=analysis.version,
                    field_count=analysis.field_count,
                    page_url=analysis.page_url,
                    analyzed_at=analysis.analyzed_at,
                )
                if analysis
                else WorkflowAnalysisSummary()
            ),
            execution=execution_summary,
            interventions=interventions,
            submission=evidence,
            recovery=self._recovery(application, browser, analysis),
            as_of=_utcnow(),
            final_submission_note=FINAL_SUBMISSION_NOTE,
        )
        logger.info(
            "Application workflow snapshot",
            extra={
                "stage": "application_workflow_snapshot",
                "user_id": str(user.id),
                "application_id": str(application.id),
                "job_id": str(application.job_id),
                "workflow_stage": stage,
                "submission_phase": snapshot.submission_phase,
                "browser_session_id": (
                    str(browser.id) if browser is not None else None
                ),
                "execution_id": (
                    str(execution.id) if execution is not None else None
                ),
            },
        )
        return snapshot

    # --- Reads ---------------------------------------------------------------
    async def _latest_browser(
        self, user_id: uuid.UUID, application_id: uuid.UUID
    ) -> BrowserSession | None:
        return await self.session.scalar(
            select(BrowserSession).where(
                BrowserSession.user_id == user_id,
                BrowserSession.application_id == application_id,
            )
        )

    async def _latest_analysis(
        self, user_id: uuid.UUID, application_id: uuid.UUID
    ) -> ApplicationFormAnalysis | None:
        return await self.session.scalar(
            select(ApplicationFormAnalysis)
            .where(
                ApplicationFormAnalysis.user_id == user_id,
                ApplicationFormAnalysis.application_id == application_id,
            )
            .order_by(ApplicationFormAnalysis.version.desc())
            .limit(1)
        )

    async def _latest_execution(
        self, user_id: uuid.UUID, application_id: uuid.UUID
    ) -> ApplicationFieldExecution | None:
        return await self.session.scalar(
            select(ApplicationFieldExecution).where(
                ApplicationFieldExecution.user_id == user_id,
                ApplicationFieldExecution.application_id == application_id,
            )
        )

    async def _interventions(
        self, user_id: uuid.UUID, application_id: uuid.UUID
    ) -> WorkflowInterventionSummary:
        rows = list(
            (
                await self.session.scalars(
                    select(ApplicationIntervention).where(
                        ApplicationIntervention.user_id == user_id,
                        ApplicationIntervention.application_id == application_id,
                    )
                )
            ).all()
        )
        pending = sum(
            1 for row in rows if row.status is InterventionStatus.PENDING
        )
        working = sum(
            1 for row in rows if row.status is InterventionStatus.USER_WORKING
        )
        blocking = sum(
            1
            for row in rows
            if row.required
            and row.status
            in {InterventionStatus.PENDING, InterventionStatus.USER_WORKING}
        )
        return WorkflowInterventionSummary(
            total=len(rows),
            pending_count=pending,
            user_working_count=working,
            blocking_count=blocking,
        )

    # --- Derivation ----------------------------------------------------------
    def _stage(
        self,
        application: Application,
        browser: BrowserSession | None,
        analysis: ApplicationFormAnalysis | None,
        execution: ApplicationFieldExecution | None,
    ) -> str:
        """Derive the user-facing stage. Later records win over earlier ones."""
        status = application.status
        if status is ApplicationStatus.CANCELLED:
            return "cancelled"
        if status is ApplicationStatus.SUBMITTED:
            return "submitted"
        if browser is not None and browser.browser_status is BrowserSessionStatus.EXPIRED:
            return "session_expired"
        if execution is not None:
            if execution.status is FieldExecutionStatus.NEEDS_USER_INPUT:
                return "paused_for_user"
            if execution.status is FieldExecutionStatus.WAITING_FOR_CONFIRMATION:
                return "waiting_for_confirmation"
            if execution.status in {
                FieldExecutionStatus.READY_FOR_FINAL_REVIEW,
                FieldExecutionStatus.COMPLETED,
            }:
                return "ready_for_final_review"
            if execution.status is FieldExecutionStatus.SESSION_EXPIRED:
                return "session_expired"
            if execution.status is FieldExecutionStatus.FAILED:
                return "failed"
            if execution.status is FieldExecutionStatus.RUNNING:
                return "filling"
            if execution.status is FieldExecutionStatus.CANCELLED:
                return "application_created"
        if status is ApplicationStatus.NEEDS_USER_ACTION:
            return "awaiting_user_submission"
        if status is ApplicationStatus.FAILED:
            return "failed"
        if status is ApplicationStatus.SUBMITTING:
            return "awaiting_user_submission"
        if browser is not None and browser.browser_status is BrowserSessionStatus.OPEN:
            if analysis is None:
                return "form_analysis_pending"
            return "browser_session_ready"
        if status is ApplicationStatus.AWAITING_APPROVAL:
            return "approved"
        if status is ApplicationStatus.READY_FOR_REVIEW:
            return "needs_approval"
        if status is ApplicationStatus.AWAITING_USER_INFORMATION:
            return "needs_information"
        if status is ApplicationStatus.ANALYZING:
            return "form_analysis_pending"
        if status is ApplicationStatus.DRAFT:
            return "application_created"
        return "application_created"

    def _submission_phase(
        self,
        application: Application,
        stage: str,
        evidence: SubmissionEvidence,
    ) -> str | None:
        if application.status is ApplicationStatus.SUBMITTED:
            return (
                "submission_confirmed"
                if evidence.employer_confirmation_detected
                else "user_confirmed_submitted"
            )
        if stage in _TERMINAL_STAGES:
            return None
        if stage == "ready_for_final_review":
            return "ready_for_final_review"
        if stage in {"awaiting_user_submission", "waiting_for_confirmation"}:
            return "awaiting_user_submission"
        return None

    def _submission_evidence(self, application: Application) -> SubmissionEvidence:
        details = application.automation_details or {}
        raw = details.get("submission_confirmation")
        if isinstance(raw, dict):
            reference = raw.get("reference")
            url = raw.get("confirmation_url")
            message = raw.get("confirmation_message")
            return SubmissionEvidence(
                confirmed_by_user=bool(raw.get("confirmed_by_user")),
                confirmed_at=_parse_dt(raw.get("confirmed_at"))
                or application.submitted_at,
                reference=(
                    reference[:255] if isinstance(reference, str) else None
                ),
                confirmation_url=url if isinstance(url, str) else None,
                confirmation_message=(
                    message[:2000] if isinstance(message, str) else None
                ),
                employer_confirmation_detected=bool(
                    reference or url or message
                ),
            )
        # Legacy rows submitted before evidence existed: the user did confirm,
        # but there is no employer evidence.
        return SubmissionEvidence(
            confirmed_by_user=application.submitted_at is not None,
            confirmed_at=application.submitted_at,
            reference=application.submission_reference,
            employer_confirmation_detected=bool(
                application.submission_reference
            ),
        )

    def _execution_summary(
        self, execution: ApplicationFieldExecution | None
    ) -> WorkflowExecutionSummary:
        if execution is None:
            return WorkflowExecutionSummary()
        summary = execution.summary or {}
        return WorkflowExecutionSummary(
            execution_id=execution.id,
            execution_status=execution.status.value,
            run_number=execution.run_number,
            analysis_version=execution.analysis_version,
            succeeded_count=int(summary.get("succeeded_count", 0)),
            verified_count=int(summary.get("verified_count", 0)),
            failed_count=int(summary.get("failed_count", 0)),
            skipped_count=int(summary.get("skipped_count", 0)),
            pending_confirmation_count=int(
                summary.get("pending_confirmation_count", 0)
            ),
            can_pause=execution.status
            in {
                FieldExecutionStatus.PLANNED,
                FieldExecutionStatus.RUNNING,
                FieldExecutionStatus.NEEDS_USER_INPUT,
                FieldExecutionStatus.WAITING_FOR_CONFIRMATION,
            },
            can_resume=execution.status
            in {
                FieldExecutionStatus.PAUSED,
                FieldExecutionStatus.NEEDS_USER_INPUT,
                FieldExecutionStatus.WAITING_FOR_CONFIRMATION,
            },
        )

    def _browser_summary(
        self, browser: BrowserSession | None
    ) -> WorkflowBrowserSummary:
        if browser is None:
            return WorkflowBrowserSummary(
                view_available=settings.APPLICATION_AUTOMATION_ENABLED
                and settings.APPLICATION_BROWSER_STREAMING_ENABLED
            )
        return WorkflowBrowserSummary(
            session_id=browser.id,
            run_number=browser.run_number,
            state=browser.state.value,
            browser_status=browser.browser_status.value,
            current_url=browser.current_url,
            live=browser.browser_status is BrowserSessionStatus.OPEN,
            expired=browser.browser_status is BrowserSessionStatus.EXPIRED,
            view_available=settings.APPLICATION_AUTOMATION_ENABLED
            and settings.APPLICATION_BROWSER_STREAMING_ENABLED,
        )

    def _blocking_items(
        self,
        application: Application,
        execution: ApplicationFieldExecution | None,
        interventions: WorkflowInterventionSummary,
        browser: BrowserSession | None,
    ) -> list[str]:
        items: list[str] = []
        if application.missing_fields:
            items.append(
                f"{len(application.missing_fields)} required question(s) still "
                "need an answer."
            )
        if interventions.blocking_count:
            items.append(
                f"{interventions.blocking_count} intervention(s) need your "
                "attention."
            )
        if execution is not None:
            summary = execution.summary or {}
            if summary.get("pending_confirmation_count"):
                items.append("Some fields still need your confirmation.")
            if execution.status is FieldExecutionStatus.SESSION_EXPIRED:
                items.append("The browser session expired before the run finished.")
            if execution.status is FieldExecutionStatus.FAILED:
                items.append("The last field-fill run failed.")
        if browser is not None and browser.browser_status is BrowserSessionStatus.EXPIRED:
            items.append("The browser session expired; reopen the employer page.")
        return items

    def _next_actions(
        self,
        *,
        application: Application,
        browser: BrowserSession | None,
        analysis: ApplicationFormAnalysis | None,
        execution: ApplicationFieldExecution | None,
        ready: bool,
        blocking: list[str],
    ) -> list[WorkflowAction]:
        actions: list[WorkflowAction] = []
        status = application.status
        finalized = status in FINALIZED_APPLICATION_STATES
        live = bool(
            browser is not None
            and browser.browser_status is BrowserSessionStatus.OPEN
        )
        automation = bool(settings.APPLICATION_AUTOMATION_ENABLED)

        if status is ApplicationStatus.CANCELLED:
            actions.append(
                WorkflowAction(
                    action="restart_application",
                    label="Prepare the application again",
                    enabled=True,
                )
            )
            return actions
        if status is ApplicationStatus.SUBMITTED:
            actions.append(
                WorkflowAction(
                    action="track_status",
                    label="Track the application stage",
                    enabled=True,
                )
            )
            return actions

        if application.missing_fields:
            actions.append(
                WorkflowAction(
                    action="complete_missing_information",
                    label="Answer the remaining questions",
                    enabled=True,
                )
            )
        elif status in {
            ApplicationStatus.AWAITING_USER_INFORMATION,
            ApplicationStatus.READY_FOR_REVIEW,
        }:
            actions.append(
                WorkflowAction(
                    action="approve",
                    label="Review and approve the application",
                    enabled=True,
                )
            )

        if not live and not finalized:
            if automation:
                actions.append(
                    WorkflowAction(
                        action="start_browser",
                        label="Open the employer's page",
                        enabled=True,
                    )
                )
            else:
                actions.append(
                    WorkflowAction(
                        action="start_browser",
                        label="Open the employer's page",
                        enabled=False,
                        reason=(
                            "Live browser automation is disabled on this "
                            "deployment; complete the application manually."
                        ),
                    )
                )
        if live and analysis is None:
            actions.append(
                WorkflowAction(
                    action="analyze_form",
                    label="Analyze the application form",
                    enabled=True,
                )
            )
        if live and analysis is not None and not ready:
            actions.append(
                WorkflowAction(
                    action="fill_fields",
                    label="Fill the safe fields",
                    enabled=not blocking,
                    reason=(
                        "Resolve the blocking items first."
                        if blocking
                        else None
                    ),
                )
            )
        if live:
            actions.append(
                WorkflowAction(
                    action="open_browser_view",
                    label="Open the live browser",
                    enabled=True,
                )
            )

        if execution is not None and execution.status in {
            FieldExecutionStatus.NEEDS_USER_INPUT,
            FieldExecutionStatus.WAITING_FOR_CONFIRMATION,
            FieldExecutionStatus.PAUSED,
        }:
            actions.append(
                WorkflowAction(
                    action="reanalyze_and_resume",
                    label="Re-analyze after your changes",
                    enabled=live,
                    reason=(
                        None
                        if live
                        else "Reopen the browser session to re-analyze."
                    ),
                )
            )

        actions.append(
            WorkflowAction(
                action="final_review",
                label="Open the final review checklist",
                enabled=True,
            )
        )
        actions.append(
            WorkflowAction(
                action="confirm_submission",
                label="Confirm you submitted on the employer's site",
                enabled=ready or status is ApplicationStatus.NEEDS_USER_ACTION,
                reason=(
                    None
                    if ready or status is ApplicationStatus.NEEDS_USER_ACTION
                    else "Finish the review checklist before confirming."
                ),
            )
        )
        return actions

    def _recovery(
        self,
        application: Application,
        browser: BrowserSession | None,
        analysis: ApplicationFormAnalysis | None,
    ) -> list[str]:
        hints: list[str] = []
        if browser is not None and browser.browser_status is BrowserSessionStatus.EXPIRED:
            hints.append(
                "The browser session expired and the employer's page was not "
                "preserved. Start a new run; completed answers and verified "
                "fields stay stored."
            )
        if application.status is ApplicationStatus.FAILED:
            hints.append(
                "The last automated step failed. Open the employer's page, "
                "review it, then retry or finish the application manually."
            )
        if (
            browser is not None
            and browser.browser_status is BrowserSessionStatus.OPEN
            and analysis is not None
            and analysis.version < browser.run_number
        ):
            hints.append(
                "The form analysis is older than the current browser run. "
                "Re-analyze before resuming automation."
            )
        return hints


__all__ = ["FINAL_SUBMISSION_NOTE", "ApplicationWorkflowService"]
