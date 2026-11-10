"""Phase 2E Stage C: the intelligent application field filling engine.

The service composes the existing Stage A persistent browser session, the Stage
B immutable form analysis, the Phase 2C answer matcher, the candidate profile,
the resume library and the Phase 2D eligibility guards. It does not open a new
browser when a session expires, does not submit anything, and never fills a
field the planner did not mark ready.

Execution is deliberately synchronous for this project's request/response
architecture: starting a run fills the currently ready fields and returns
honest progress. A field that needs confirmation, a CAPTCHA/OTP/MFA step, a
legal declaration or missing information pauses the run in
``needs_user_input`` while the browser stays alive for the user to act.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import AppError, InvalidOperationError, ResourceNotFoundError
from app.core.logging import get_logger
from app.integrations.browser.field_actions import FieldActionResult
from app.integrations.browser.persistent import PersistentBrowserHandle
from app.models.application import Application, QuestionStatus
from app.models.application_answer import VerificationStatus
from app.models.application_field_execution import (
    ApplicationFieldExecution,
    FieldExecutionStatus,
)
from app.models.application_form_analysis import ApplicationFormAnalysis
from app.models.user import User
from app.repositories.candidate_repository import CandidateRepository
from app.schemas.application_fill import (
    FillAction,
    FillExecutionResponse,
    FillFieldResult,
    FillFieldStatus,
    FillPlanField,
    FillPlanResponse,
    FillResumeSelection,
)
from app.schemas.application_form import DetectedFormField
from app.services.application_field_planner import (
    FieldExecutionPlanner,
    FillPlan,
    PlannedField,
)
from app.services.application_form_service import ApplicationFormService
from app.services.application_service import ApplicationService
from app.services.browser_session_service import (
    FINALIZED_APPLICATION_STATES,
    BrowserSessionService,
)
from app.services.candidate_service import CandidateService
from app.services.field_value_verifier import FieldValueVerifier
from app.services.playwright_field_interactor import PlaywrightFieldInteractor
from app.services.resume_upload_handler import ResumeUploadHandler

logger = get_logger(__name__)

FINAL_SUBMISSION_NOTE = (
    "ApplyPilot never clicks Submit, Apply or Finish. The final review and "
    "submission remain under your control."
)


class FieldFillNotFoundError(ResourceNotFoundError):
    """No execution has been planned for this application yet."""

    code = "field_fill_not_found"
    message = "No field-fill execution exists for this application yet."


class FieldFillConflictError(AppError):
    """The execution cannot proceed in its current state."""

    status_code = 409
    code = "field_fill_conflict"
    message = "The field-fill execution cannot proceed in its current state."


class FieldFillUnavailableError(AppError):
    """The live page could not be interacted with safely."""

    status_code = 503
    code = "field_fill_unavailable"
    message = "The employer's application page could not be filled safely."


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


_ACTIVE_RUN_STATES = frozenset(
    {FieldExecutionStatus.RUNNING, FieldExecutionStatus.PLANNED}
)

_RESUMABLE_RUN_STATES = frozenset(
    {
        FieldExecutionStatus.PAUSED,
        FieldExecutionStatus.NEEDS_USER_INPUT,
        FieldExecutionStatus.WAITING_FOR_CONFIRMATION,
    }
)


def _coordination_pause(
    handle: PersistentBrowserHandle, navigation_epoch: int
) -> str | None:
    """Return a pause reason when the user or a navigation interrupted filling."""
    current_epoch = getattr(handle, "navigation_epoch", navigation_epoch)
    if current_epoch != navigation_epoch:
        return (
            "The employer's page moved to a new URL while filling. Re-analyze "
            "the form, then resume; the stored analysis is stale."
        )
    active = getattr(handle, "user_input_active", None)
    if callable(active) and active(1.5):
        return (
            "You interacted with the employer's page, so ApplyPilot paused "
            "before touching more fields. Re-analyze when you are ready to "
            "continue."
        )
    return None


def _upsert_result(
    results: list[FillFieldResult], result: FillFieldResult
) -> None:
    """Keep exactly one outcome per field so a retried field cannot double-count."""
    for index, existing in enumerate(results):
        if existing.field_id == result.field_id:
            results[index] = result
            return
    results.append(result)


class ApplicationFieldFillService:
    """Plan and execute safe field filling for one owned application."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        planner: FieldExecutionPlanner | None = None,
        interactor: PlaywrightFieldInteractor | None = None,
        verifier: FieldValueVerifier | None = None,
        uploads: ResumeUploadHandler | None = None,
        browser_sessions: BrowserSessionService | None = None,
    ) -> None:
        self.session = session
        self.applications = ApplicationService(session)
        self.forms = ApplicationFormService(session)
        self.browser_sessions = browser_sessions or BrowserSessionService(session)
        self.candidates = CandidateService(CandidateRepository(session))
        self.planner = planner or FieldExecutionPlanner()
        self.interactor = interactor or PlaywrightFieldInteractor()
        self.verifier = verifier or FieldValueVerifier()
        self.uploads = uploads or ResumeUploadHandler()

    # --- Plan ---------------------------------------------------------------
    async def plan(self, user: User, application_id: uuid.UUID) -> FillPlanResponse:
        """Return a preview without writing anything or touching the page."""
        application = await self._owned_application(user, application_id)
        session_row, _handle = await self.browser_sessions.require_live_handle(
            user, application_id
        )
        analysis = await self._latest_analysis(user, application, session_row.id)
        plan = await self._build_plan(user, application, analysis)
        return self._plan_response(application, session_row.id, analysis, plan)

    # --- Start --------------------------------------------------------------
    async def start(
        self,
        user: User,
        application_id: uuid.UUID,
        *,
        restart: bool = False,
    ) -> FillExecutionResponse:
        """Fill the ready fields once, idempotently for the same analysis."""
        application = await self._owned_application(user, application_id)
        session_row, handle = await self.browser_sessions.require_live_handle(
            user, application_id
        )
        analysis = await self._latest_analysis(user, application, session_row.id)
        plan = await self._build_plan(user, application, analysis)

        row = await self._row_for_application(application.id, user.id, for_update=True)
        if row is not None and not restart:
            if row.status is FieldExecutionStatus.RUNNING:
                raise FieldFillConflictError(
                    "A field-fill run is already in progress for this application.",
                    code="field_fill_in_progress",
                )
            if row.analysis_id == analysis.id:
                if row.status is FieldExecutionStatus.PAUSED:
                    raise FieldFillConflictError(
                        "This run is paused. Resume it instead of starting a new one.",
                        code="field_fill_paused",
                    )
                if row.status in {
                    FieldExecutionStatus.NEEDS_USER_INPUT,
                    FieldExecutionStatus.WAITING_FOR_CONFIRMATION,
                }:
                    # The user has already seen this plan; do not re-type into the
                    # employer's form just because the start button was clicked twice.
                    return self._execution_response(row, analysis.version)
                if row.status in {
                    FieldExecutionStatus.READY_FOR_FINAL_REVIEW,
                    FieldExecutionStatus.COMPLETED,
                }:
                    raise FieldFillConflictError(
                        "This fill run is already complete and ready for your review.",
                        code="field_fill_already_complete",
                    )

        now = _utcnow()
        if row is None:
            row = ApplicationFieldExecution(
                id=uuid.uuid4(),
                user_id=user.id,
                application_id=application.id,
                browser_session_id=session_row.id,
                analysis_id=analysis.id,
                analysis_version=analysis.version,
                run_number=1,
                plan=[],
                results=[],
                summary={},
            )
            self.session.add(row)
        else:
            row.run_number += 1
        row.browser_session_id = session_row.id
        row.analysis_id = analysis.id
        row.analysis_version = analysis.version
        row.status = FieldExecutionStatus.RUNNING
        row.plan = [item.persisted for item in plan.fields]
        row.results = []
        row.summary = self._plan_summary(plan)
        row.selected_resume_id = plan.resume.resume_id
        row.selected_resume_filename = plan.resume.filename
        row.pause_reason = None
        row.failure_code = None
        row.failure_reason = None
        row.paused_at = None
        row.completed_at = None
        row.started_at = now
        row.last_activity_at = now
        await self.session.commit()

        await self._run(user, application, handle, plan, row)
        logger.info(
            "Field fill run finished",
            extra={
                "stage": "field_fill",
                "user_id": str(user.id),
                "application_id": str(application.id),
                "run_number": row.run_number,
                "status": row.status.value,
                "succeeded": row.summary.get("succeeded_count", 0),
                "failed": row.summary.get("failed_count", 0),
            },
        )
        return self._execution_response(row, analysis.version)

    # --- Progress -----------------------------------------------------------
    async def get_execution(
        self, user: User, application_id: uuid.UUID
    ) -> FillExecutionResponse:
        application = await self._owned_application(user, application_id)
        # Reconcile the browser honestly; a dead browser must not look active.
        session_row = await self.browser_sessions.get_session(user, application_id)
        row = await self._row_for_application(application.id, user.id)
        if row is None:
            raise FieldFillNotFoundError()
        if (
            row.status in (_ACTIVE_RUN_STATES | _RESUMABLE_RUN_STATES)
            and session_row.browser_status.value != "open"
        ):
            row.status = FieldExecutionStatus.SESSION_EXPIRED
            row.failure_code = "browser_session_expired"
            row.failure_reason = (
                "The browser session expired before the fill run finished. "
                "ApplyPilot did not reopen the employer's page."
            )
            row.last_activity_at = _utcnow()
            await self.session.commit()
        return self._execution_response(row, row.analysis_version)

    # --- Controls -----------------------------------------------------------
    async def pause(
        self, user: User, application_id: uuid.UUID, *, reason: str | None = None
    ) -> FillExecutionResponse:
        application = await self._owned_application(user, application_id)
        await self.browser_sessions.require_live_handle(user, application_id)
        row = await self._row_for_application(application.id, user.id)
        if row is None:
            raise FieldFillNotFoundError()
        if row.status is FieldExecutionStatus.PAUSED:
            return self._execution_response(row, row.analysis_version)
        if row.status not in {
            FieldExecutionStatus.RUNNING,
            FieldExecutionStatus.NEEDS_USER_INPUT,
            FieldExecutionStatus.WAITING_FOR_CONFIRMATION,
            FieldExecutionStatus.PLANNED,
        }:
            raise FieldFillConflictError(
                "This field-fill run cannot be paused.", code="field_fill_not_pausable"
            )
        row.status = FieldExecutionStatus.PAUSED
        row.paused_at = _utcnow()
        row.pause_reason = (reason or "Paused by you.").strip()[:500]
        row.last_activity_at = row.paused_at
        await self.session.commit()
        return self._execution_response(row, row.analysis_version)

    async def resume(
        self, user: User, application_id: uuid.UUID
    ) -> FillExecutionResponse:
        application = await self._owned_application(user, application_id)
        session_row, handle = await self.browser_sessions.require_live_handle(
            user, application_id
        )
        analysis = await self._latest_analysis(user, application, session_row.id)
        row = await self._row_for_application(application.id, user.id, for_update=True)
        if row is None:
            raise FieldFillNotFoundError()
        if row.status not in _RESUMABLE_RUN_STATES:
            raise FieldFillConflictError(
                "Only a run paused for the user can be resumed.",
                code="field_fill_not_paused",
            )
        plan = await self._build_plan(user, application, analysis)
        if row.analysis_id != analysis.id:
            # A newer analysis was produced after human intervention. Rebuild the
            # plan from it instead of reusing stale field coordinates, but keep
            # successful results that still refer to fields on the current form.
            current_ids = {item.field_id for item in plan.fields}
            row.results = [
                result
                for result in (row.results or [])
                if result.get("field_id") in current_ids
                and result.get("status") == FillFieldStatus.SUCCEEDED.value
            ]
            row.analysis_id = analysis.id
            row.analysis_version = analysis.version
            row.plan = [item.persisted for item in plan.fields]
            row.summary = self._result_summary(self._result_models(row), plan)
        row.status = FieldExecutionStatus.RUNNING
        row.pause_reason = None
        row.paused_at = None
        row.last_activity_at = _utcnow()
        await self.session.commit()
        await self._run(user, application, handle, plan, row)
        return self._execution_response(row, analysis.version)

    async def cancel(
        self, user: User, application_id: uuid.UUID
    ) -> FillExecutionResponse:
        application = await self._owned_application(user, application_id)
        row = await self._row_for_application(application.id, user.id)
        if row is None:
            raise FieldFillNotFoundError()
        if row.status is FieldExecutionStatus.CANCELLED:
            return self._execution_response(row, row.analysis_version)
        if row.status is FieldExecutionStatus.COMPLETED:
            raise FieldFillConflictError(
                "A completed fill run cannot be cancelled.",
                code="field_fill_completed",
            )
        row.status = FieldExecutionStatus.CANCELLED
        row.pause_reason = None
        row.paused_at = None
        row.completed_at = _utcnow()
        row.last_activity_at = row.completed_at
        await self.session.commit()
        return self._execution_response(row, row.analysis_version)

    # --- Execution ----------------------------------------------------------
    async def _run(
        self,
        user: User,
        application: Application,
        handle: PersistentBrowserHandle,
        plan: FillPlan,
        row: ApplicationFieldExecution,
    ) -> None:
        completed = {
            item.field_id
            for item in self._result_models(row)
            if item.status is FillFieldStatus.SUCCEEDED
        }
        results: list[FillFieldResult] = list(self._result_models(row))
        navigation_epoch = int(getattr(handle, "navigation_epoch", 0) or 0)

        # Process ordinary fields first; a resume upload is last so a validation
        # error on the form does not waste the file interaction.
        ordered = [
            item
            for item in plan.ready_fields
            if item.action is not FillAction.UPLOAD and item.field_id not in completed
        ]
        uploads = [
            item
            for item in plan.ready_fields
            if item.action is FillAction.UPLOAD and item.field_id not in completed
        ]

        for item in ordered:
            pause_reason = _coordination_pause(handle, navigation_epoch)
            if pause_reason:
                row.status = FieldExecutionStatus.NEEDS_USER_INPUT
                row.pause_reason = pause_reason
                await self._persist_progress(row, results, plan)
                return
            _upsert_result(results, await self._execute_field(handle, item))
            await self._persist_progress(row, results, plan)
            if row.status is FieldExecutionStatus.CANCELLED:
                return

        if uploads and plan.resume.will_upload:
            try:
                async with self.uploads.materialize(user, application.resume) as path:
                    for item in uploads:
                        pause_reason = _coordination_pause(handle, navigation_epoch)
                        if pause_reason:
                            row.status = FieldExecutionStatus.NEEDS_USER_INPUT
                            row.pause_reason = pause_reason
                            await self._persist_progress(row, results, plan)
                            return
                        _upsert_result(
                            results,
                            await self._execute_field(handle, item, upload_path=path),
                        )
                        await self._persist_progress(row, results, plan)
                        if row.status is FieldExecutionStatus.CANCELLED:
                            return
            except Exception as exc:
                logger.warning(
                    "Resume upload for field fill failed",
                    extra={
                        "stage": "field_fill_upload",
                        "error_class": type(exc).__name__,
                    },
                )
                for item in uploads:
                    _upsert_result(
                        results,
                        FillFieldResult(
                            field_id=item.field_id,
                            label=item.label,
                            action=item.action,
                            status=FillFieldStatus.FAILED,
                            verified=False,
                            reason="The selected resume could not be prepared for upload.",
                            source=item.source.value,
                        ),
                    )

        pause_reason = _coordination_pause(handle, navigation_epoch)
        if pause_reason:
            row.status = FieldExecutionStatus.NEEDS_USER_INPUT
            row.pause_reason = pause_reason
            await self._persist_progress(row, results, plan)
            return

        # Fields that were never candidates for execution are reported so the
        # UI can show confirmations and human steps together with outcomes.
        planned_ids = {item.field_id for item in results}
        for item in plan.fields:
            if item.field_id in planned_ids:
                continue
            results.append(self._planned_result(item))

        self._finalise(row, results, plan)
        await self.session.commit()

    async def _execute_field(
        self,
        handle: PersistentBrowserHandle,
        item: PlannedField,
        *,
        upload_path: str | None = None,
    ) -> FillFieldResult:
        try:
            outcome: FieldActionResult = await self.interactor.execute(
                handle, item, upload_path=upload_path
            )
        except Exception as exc:  # pragma: no cover - live page failures
            logger.warning(
                "Field interaction raised",
                extra={"stage": "field_fill", "error_class": type(exc).__name__},
            )
            return FillFieldResult(
                field_id=item.field_id,
                label=item.label,
                action=item.action,
                status=FillFieldStatus.FAILED,
                verified=False,
                reason="The employer's page could not be interacted with.",
                source=item.source.value,
            )
        verified, reason = self.verifier.verify(item, outcome)
        if not outcome.ok:
            return FillFieldResult(
                field_id=item.field_id,
                label=item.label,
                action=item.action,
                status=FillFieldStatus.FAILED,
                verified=False,
                reason=outcome.reason or reason,
                source=item.source.value,
            )
        return FillFieldResult(
            field_id=item.field_id,
            label=item.label,
            action=item.action,
            status=FillFieldStatus.SUCCEEDED if verified else FillFieldStatus.FAILED,
            verified=verified,
            reason=reason,
            source=item.source.value,
        )

    def _planned_result(self, item: PlannedField) -> FillFieldResult:
        status = {
            FillFieldStatus.NEEDS_CONFIRMATION: FillFieldStatus.PENDING_CONFIRMATION,
            FillFieldStatus.NEEDS_HUMAN_INPUT: FillFieldStatus.PENDING_HUMAN_INPUT,
            FillFieldStatus.ALREADY_FILLED: FillFieldStatus.SKIPPED,
        }.get(item.status, FillFieldStatus.SKIPPED)
        return FillFieldResult(
            field_id=item.field_id,
            label=item.label,
            action=item.action,
            status=status,
            verified=False,
            reason=item.reason,
            source=item.source.value,
        )

    async def _persist_progress(
        self,
        row: ApplicationFieldExecution,
        results: list[FillFieldResult],
        plan: FillPlan,
    ) -> None:
        row.results = [item.model_dump(mode="json") for item in results]
        row.summary = self._result_summary(results, plan)
        row.last_activity_at = _utcnow()
        await self.session.commit()

    def _finalise(
        self,
        row: ApplicationFieldExecution,
        results: list[FillFieldResult],
        plan: FillPlan,
    ) -> None:
        row.results = [item.model_dump(mode="json") for item in results]
        summary = self._result_summary(results, plan)
        row.summary = summary
        row.last_activity_at = _utcnow()
        row.pause_reason = None
        if summary["pending_human_input_count"]:
            row.status = FieldExecutionStatus.NEEDS_USER_INPUT
            row.pause_reason = (
                "Some fields need your input in the employer's form. "
                "ApplyPilot stopped before touching them."
            )
        elif summary["pending_confirmation_count"]:
            row.status = FieldExecutionStatus.WAITING_FOR_CONFIRMATION
            row.pause_reason = (
                "Some fields need your confirmation before they can be filled."
            )
        elif summary["failed_count"]:
            row.status = FieldExecutionStatus.NEEDS_USER_INPUT
            row.pause_reason = (
                "Some fields could not be verified. Review the employer's form "
                "before continuing."
            )
        else:
            row.status = FieldExecutionStatus.READY_FOR_FINAL_REVIEW
            row.completed_at = _utcnow()

    # --- Context ------------------------------------------------------------
    async def _owned_application(
        self, user: User, application_id: uuid.UUID
    ) -> Application:
        application = await self.applications.get_application(user, application_id)
        if application.status in FINALIZED_APPLICATION_STATES:
            raise InvalidOperationError(
                "A field-fill run cannot start for a finished application.",
                code="application_finalized",
            )
        return application

    async def _latest_analysis(
        self,
        user: User,
        application: Application,
        browser_session_id: uuid.UUID,
    ) -> ApplicationFormAnalysis:
        row = await self.session.scalar(
            select(ApplicationFormAnalysis)
            .where(
                ApplicationFormAnalysis.application_id == application.id,
                ApplicationFormAnalysis.user_id == user.id,
            )
            .order_by(ApplicationFormAnalysis.version.desc())
            .limit(1)
        )
        if row is None:
            raise FieldFillConflictError(
                "Analyze the current application form before planning a fill.",
                code="form_analysis_required",
            )
        if row.browser_session_id != browser_session_id:
            raise FieldFillConflictError(
                "The latest form analysis belongs to a different browser session. "
                "Analyze the current page again before filling it.",
                code="analysis_session_mismatch",
            )
        return row

    async def _build_plan(
        self,
        user: User,
        application: Application,
        analysis: ApplicationFormAnalysis,
    ) -> FillPlan:
        profile = await self.candidates.ensure_profile_for_user(user)
        reusable_answers = [
            answer
            for answer in (profile.application_answers or [])
            if answer.verification_status is VerificationStatus.VERIFIED and answer.answer
        ]
        user_answers = {
            question.question_key: question.answer
            for question in application.questions
            if question.status is QuestionStatus.CONFIRMED and question.answer
        }
        fields = [
            DetectedFormField.model_validate(item) for item in (analysis.fields or [])
        ]
        resume = application.resume
        return self.planner.build(
            fields,
            candidate=profile,
            resume=resume,
            reusable_answers=reusable_answers,
            user_answers=user_answers,
            resume_owned=resume is not None and resume.user_id == user.id,
        )

    # --- Serialisation ------------------------------------------------------
    def _plan_response(
        self,
        application: Application,
        browser_session_id: uuid.UUID,
        analysis: ApplicationFormAnalysis,
        plan: FillPlan,
    ) -> FillPlanResponse:
        return FillPlanResponse(
            application_id=application.id,
            analysis_id=analysis.id,
            analysis_version=analysis.version,
            browser_session_id=browser_session_id,
            generated_at=_utcnow(),
            total_fields=len(plan.fields),
            eligible_count=plan.eligible_count,
            confirmation_count=plan.confirmation_count,
            human_input_count=plan.human_input_count,
            already_filled_count=plan.already_filled_count,
            skipped_count=plan.skipped_count,
            resume=FillResumeSelection(
                resume_id=plan.resume.resume_id,
                filename=plan.resume.filename,
                will_upload=plan.resume.will_upload,
                file_field_ids=list(plan.resume.file_field_ids),
                reason=plan.resume.reason,
            ),
            fields=[self._plan_field(item) for item in plan.fields],
            warnings=list(plan.warnings),
            executable=plan.executable,
            final_submission_note=FINAL_SUBMISSION_NOTE,
        )

    @staticmethod
    def _plan_field(item: PlannedField) -> FillPlanField:
        return FillPlanField(
            field_id=item.field_id,
            label=item.label,
            field_type=item.field_type.value,
            classification=item.classification.value,
            required=item.required,
            action=item.action,
            status=item.status,
            proposed_value=item.proposed_value if item.shows_value else None,
            source=item.source.value,
            confidence=item.confidence,
            options=list(item.options),
            reason=item.reason,
        )

    @staticmethod
    def _plan_summary(plan: FillPlan) -> dict[str, Any]:
        return {
            "total_fields": len(plan.fields),
            "ready_count": plan.eligible_count,
            "confirmation_count": plan.confirmation_count,
            "human_input_count": plan.human_input_count,
            "already_filled_count": plan.already_filled_count,
            "skipped_count": plan.skipped_count,
        }

    @staticmethod
    def _result_summary(
        results: list[FillFieldResult], plan: FillPlan
    ) -> dict[str, Any]:
        return {
            "total_fields": len(plan.fields),
            "ready_count": plan.eligible_count,
            "succeeded_count": sum(
                1 for item in results if item.status is FillFieldStatus.SUCCEEDED
            ),
            "failed_count": sum(
                1 for item in results if item.status is FillFieldStatus.FAILED
            ),
            "skipped_count": sum(
                1
                for item in results
                if item.status
                in {FillFieldStatus.SKIPPED, FillFieldStatus.PENDING_HUMAN_INPUT}
            ),
            "verified_count": sum(1 for item in results if item.verified),
            "pending_confirmation_count": sum(
                1
                for item in results
                if item.status is FillFieldStatus.PENDING_CONFIRMATION
            ),
            "pending_human_input_count": sum(
                1
                for item in results
                if item.status is FillFieldStatus.PENDING_HUMAN_INPUT
            ),
        }

    @staticmethod
    def _result_models(row: ApplicationFieldExecution) -> list[FillFieldResult]:
        return [
            FillFieldResult.model_validate(item) for item in (row.results or [])
        ]

    def _execution_response(
        self, row: ApplicationFieldExecution, analysis_version: int
    ) -> FillExecutionResponse:
        summary = dict(row.summary or {})
        results = self._result_models(row)
        can_resume = row.status in _RESUMABLE_RUN_STATES
        can_pause = row.status in {
            FieldExecutionStatus.RUNNING,
            FieldExecutionStatus.NEEDS_USER_INPUT,
            FieldExecutionStatus.WAITING_FOR_CONFIRMATION,
        }
        can_cancel = row.status not in {
            FieldExecutionStatus.CANCELLED,
            FieldExecutionStatus.COMPLETED,
            FieldExecutionStatus.FAILED,
            FieldExecutionStatus.SESSION_EXPIRED,
        }
        return FillExecutionResponse(
            execution_id=row.id,
            application_id=row.application_id,
            analysis_id=row.analysis_id,
            analysis_version=analysis_version or row.analysis_version,
            browser_session_id=row.browser_session_id,
            run_number=row.run_number,
            status=row.status.value,
            total_fields=int(summary.get("total_fields", 0)),
            ready_count=int(summary.get("ready_count", 0)),
            succeeded_count=int(summary.get("succeeded_count", 0)),
            failed_count=int(summary.get("failed_count", 0)),
            skipped_count=int(summary.get("skipped_count", 0)),
            verified_count=int(summary.get("verified_count", 0)),
            pending_confirmation_count=int(
                summary.get("pending_confirmation_count", 0)
            ),
            pending_human_input_count=int(
                summary.get("pending_human_input_count", 0)
            ),
            selected_resume_id=row.selected_resume_id,
            selected_resume_filename=row.selected_resume_filename,
            results=results,
            warnings=[],
            pause_reason=row.pause_reason,
            failure_code=row.failure_code,
            failure_reason=row.failure_reason,
            can_pause=can_pause,
            can_resume=can_resume,
            can_cancel=can_cancel,
            started_at=row.started_at,
            paused_at=row.paused_at,
            completed_at=row.completed_at,
            last_activity_at=row.last_activity_at,
            final_submission_note=FINAL_SUBMISSION_NOTE,
        )

    async def _row_for_application(
        self,
        application_id: uuid.UUID,
        user_id: uuid.UUID,
        *,
        for_update: bool = False,
    ) -> ApplicationFieldExecution | None:
        statement = (
            select(ApplicationFieldExecution)
            .where(
                ApplicationFieldExecution.application_id == application_id,
                ApplicationFieldExecution.user_id == user_id,
            )
            .options(selectinload(ApplicationFieldExecution.application))
        )
        if for_update:
            statement = statement.with_for_update()
        return await self.session.scalar(statement)


__all__ = [
    "FINAL_SUBMISSION_NOTE",
    "ApplicationFieldFillService",
    "FieldFillConflictError",
    "FieldFillNotFoundError",
    "FieldFillUnavailableError",
]
