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

import hmac
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
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
from app.services.application_field_normalizer import structure_fingerprint
from app.services.application_field_planner import (
    FieldExecutionPlanner,
    FillPlan,
    PlannedField,
)
from app.services.application_form_analyzer import ApplicationFormAnalyzer
from app.services.application_form_service import ApplicationFormService
from app.services.application_service import ApplicationService
from app.services.application_url_security import sanitize_url_for_display
from app.services.browser_session_service import (
    FINALIZED_APPLICATION_STATES,
    BrowserSessionService,
)
from app.services.candidate_service import CandidateService
from app.services.field_value_verifier import FieldValueVerifier
from app.services.fill_approval import APPROVAL_STALE_MESSAGE, approval_fingerprint
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


class FieldFillStaleApprovalError(AppError):
    """The approved plan no longer describes the live form."""

    status_code = 409
    code = "approval_stale"
    message = APPROVAL_STALE_MESSAGE


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# Namespace for the transaction-scoped advisory lock that serialises the very
# first execution insert for one application. "APFU" = ApplyPilot Field-fill
# Uniqueness; the value stays below 2**31 so it fits PostgreSQL's int4.
_FIELD_FILL_ADVISORY_LOCK_NAMESPACE = 0x41504655


def _advisory_lock_key(application_id: uuid.UUID) -> int:
    """Stable, positive 64-bit advisory-lock key for one application."""
    return (_FIELD_FILL_ADVISORY_LOCK_NAMESPACE << 32) | int.from_bytes(
        application_id.bytes[:4], "big"
    )


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


@dataclass(frozen=True)
class LiveForm:
    """The form as it is on the page right now, read once before typing."""

    structure: str
    fields: tuple[Any, ...]

    def values(self) -> dict[str, str]:
        """Non-empty values currently present, keyed by field id."""
        populated: dict[str, str] = {}
        for field in self.fields:
            current = str(getattr(field, "current_value", "") or "").strip()
            if current:
                populated[str(field.field_id)] = current
        return populated


def _normalise_value(value: str) -> str:
    return " ".join(value.split()).casefold()


def _live_value_matches(item: PlannedField, current: str) -> bool:
    """Whether the value already on the page is the one the user approved.

    A wrong answer here can only make ApplyPilot *more* cautious: a missed
    match pauses the field for review, and a false match skips typing a field
    that is already populated. Neither case writes over the user's value.
    """
    approved = str(item.proposed_value or "")
    if not approved:
        return False
    current_norm = _normalise_value(current)
    approved_norm = _normalise_value(approved)
    if item.action is FillAction.CHECK:
        # A single checkbox is described by "yes"/"no" but observed as the
        # presence of the checked attribute.
        if current_norm == "checked":
            return approved_norm in {"yes", "true", "1", "on"}
        if current_norm in {"unchecked", ""}:
            return approved_norm in {"no", "false", "0", "off"}
    if "," in approved or "," in current:
        # Checkbox groups report the selected labels; order is not meaningful.
        return sorted(
            part.strip() for part in approved_norm.split(",")
        ) == sorted(part.strip() for part in current_norm.split(","))
    return approved_norm == current_norm


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
        analyzer: ApplicationFormAnalyzer | None = None,
    ) -> None:
        self.session = session
        self.applications = ApplicationService(session)
        self.forms = ApplicationFormService(session)
        self.browser_sessions = browser_sessions or BrowserSessionService(session)
        self.candidates = CandidateService(CandidateRepository(session))
        self.planner = planner or FieldExecutionPlanner()
        # Used only to compare the approved structure with the live page. It
        # receives HTML from the same owned session, never a new browser.
        self.analyzer = analyzer or ApplicationFormAnalyzer()
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
        structure = structure_fingerprint(analysis.fields or [])
        return self._plan_response(application, session_row.id, analysis, plan, structure)

    # --- Start --------------------------------------------------------------
    async def start(
        self,
        user: User,
        application_id: uuid.UUID,
        *,
        approval_fingerprint: str,
        restart: bool = False,
    ) -> FillExecutionResponse:
        """Fill the ready fields once, idempotently for the same analysis."""
        application = await self._owned_application(user, application_id)
        session_row, handle = await self.browser_sessions.require_live_handle(
            user, application_id
        )
        analysis = await self._latest_analysis(user, application, session_row.id)
        plan = await self._build_plan(user, application, analysis)
        # Nothing is typed until the live page still matches what was approved.
        live = await self._verify_live_approval(
            handle, analysis, plan, approval_fingerprint
        )
        approved = self._approval_state(
            analysis, plan, live.structure, approval_fingerprint
        )

        # A first start has no row for ``SELECT ... FOR UPDATE`` to lock: two
        # simultaneous requests could both read nothing and both insert. The
        # unique constraint kept the data correct but the loser surfaced as a
        # 500. Take an application-scoped transaction lock first so the
        # read-then-insert is atomic; the loser then sees the winner's committed
        # row and reports a structured conflict.
        await self._acquire_first_start_lock(application.id)

        row = await self._row_for_application(application.id, user.id, for_update=True)
        if row is not None and not restart:
            if row.status is FieldExecutionStatus.RUNNING:
                await self.session.rollback()
                raise FieldFillConflictError(
                    "A field-fill run is already in progress for this application.",
                    code="field_fill_in_progress",
                )
            if row.analysis_id == analysis.id:
                if row.status is FieldExecutionStatus.PAUSED:
                    await self.session.rollback()
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
                    replayed = self._execution_response(row, analysis.version)
                    await self.session.rollback()
                    return replayed
                if row.status in {
                    FieldExecutionStatus.READY_FOR_FINAL_REVIEW,
                    FieldExecutionStatus.COMPLETED,
                }:
                    await self.session.rollback()
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
        row.summary = {**self._plan_summary(plan), **approved}
        row.selected_resume_id = plan.resume.resume_id
        row.selected_resume_filename = plan.resume.filename
        row.pause_reason = None
        row.failure_code = None
        row.failure_reason = None
        row.paused_at = None
        row.completed_at = None
        row.started_at = now
        row.last_activity_at = now
        # Captured before the commit: a rollback expires every ORM instance, so
        # reading these after the failure would need IO on a dead transaction.
        user_id = user.id
        application_ref = application.id
        try:
            await self.session.commit()
        except IntegrityError as exc:
            # Backstop for a backend without the advisory lock (for example the
            # SQLite development/test database): the commit that would insert a
            # second execution row is rejected by the unique constraint. Report
            # the conflict instead of letting it reach the caller as a 500.
            await self.session.rollback()
            logger.warning(
                "Field fill start lost the first-insert race",
                extra={
                    "stage": "field_fill",
                    "user_id": str(user_id),
                    "application_id": str(application_ref),
                },
            )
            raise FieldFillConflictError(
                "A field-fill run was just started for this application. "
                "Refresh to see its progress instead of starting another one.",
                code="field_fill_in_progress",
            ) from exc

        await self._run(
            user,
            application,
            handle,
            plan,
            row,
            page_url=analysis.page_url,
            live_values=live.values(),
        )
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
        self,
        user: User,
        application_id: uuid.UUID,
        *,
        approval_fingerprint: str,
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
        stored = (row.summary or {}).get("approval_fingerprint")
        if not stored:
            raise FieldFillConflictError(
                "This run predates the approval fingerprint. Review the current "
                "fill plan and approve it before continuing.",
                code="approval_required",
            )
        if hmac.compare_digest(str(stored), approval_fingerprint):
            # Continue the approval this run was started with. A re-analysis
            # after the user completed fields legitimately changes the plan, so
            # the digest is checked against the analysis it was granted for.
            live = await self._verify_resume_approval(
                user,
                application,
                row,
                session_row.id,
                handle,
                analysis,
                plan,
                approval_fingerprint,
            )
        else:
            # A different fingerprint is a fresh approval: it can only be
            # accepted when it describes exactly the current plan and page, and
            # it then replaces the old binding.
            live = await self._verify_live_approval(
                handle, analysis, plan, approval_fingerprint
            )
            row.summary = {
                **(row.summary or {}),
                **self._approval_state(
                    analysis, plan, live.structure, approval_fingerprint
                ),
            }
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
            row.summary = self._merged_summary(
                row, self._result_models(row), plan
            )
        row.status = FieldExecutionStatus.RUNNING
        row.pause_reason = None
        row.paused_at = None
        row.last_activity_at = _utcnow()
        await self.session.commit()
        # Values are re-read from the page immediately before typing, so a
        # manual edit that never triggered a re-analysis is still respected.
        await self._run(
            user,
            application,
            handle,
            plan,
            row,
            page_url=analysis.page_url,
            live_values=live.values(),
        )
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
        *,
        page_url: str | None,
        live_values: Mapping[str, str] | None = None,
    ) -> None:
        completed = {
            item.field_id
            for item in self._result_models(row)
            if item.status is FillFieldStatus.SUCCEEDED
        }
        results: list[FillFieldResult] = list(self._result_models(row))
        navigation_epoch = int(getattr(handle, "navigation_epoch", 0) or 0)
        values = live_values or {}
        left_untouched: list[str] = []

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
            pause_reason = await self._page_guard(handle, page_url) or _coordination_pause(
                handle, navigation_epoch
            )
            if pause_reason:
                row.status = FieldExecutionStatus.NEEDS_USER_INPUT
                row.pause_reason = pause_reason
                await self._persist_progress(row, results, plan)
                return
            conflict = self._live_value_conflict(item, values)
            if conflict is not None:
                _upsert_result(results, conflict)
                if conflict.status is FillFieldStatus.PENDING_HUMAN_INPUT:
                    left_untouched.append(item.label)
                await self._persist_progress(row, results, plan)
                continue
            outcome = await self._execute_field(handle, item)
            _upsert_result(results, outcome)
            if outcome.status is FillFieldStatus.PENDING_HUMAN_INPUT:
                # The control changed after approval; it is reported for renewed
                # review instead of being typed over.
                left_untouched.append(item.label)
            await self._persist_progress(row, results, plan)
            if row.status is FieldExecutionStatus.CANCELLED:
                return

        if uploads and plan.resume.will_upload:
            try:
                async with self.uploads.materialize(user, application.resume) as path:
                    for item in uploads:
                        pause_reason = await self._page_guard(
                            handle, page_url
                        ) or _coordination_pause(handle, navigation_epoch)
                        if pause_reason:
                            row.status = FieldExecutionStatus.NEEDS_USER_INPUT
                            row.pause_reason = pause_reason
                            await self._persist_progress(row, results, plan)
                            return
                        conflict = self._live_value_conflict(item, values)
                        if conflict is not None:
                            _upsert_result(results, conflict)
                            await self._persist_progress(row, results, plan)
                            continue
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

        pause_reason = await self._page_guard(handle, page_url) or _coordination_pause(
            handle, navigation_epoch
        )
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
        if left_untouched and row.status is FieldExecutionStatus.NEEDS_USER_INPUT:
            named = ", ".join(left_untouched[:5])
            row.pause_reason = (
                "These fields changed on the employer's page after you approved "
                f"the plan, so ApplyPilot left them untouched: {named}. "
                "Re-analyze the form to review the current values."
            )
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
            if outcome.requires_review:
                # The driver refused because the control no longer matches the
                # approved plan. That is not a failure to retry: the field goes
                # back to the user for a fresh look.
                detail = outcome.reason or (
                    "This field changed after you approved the plan."
                )
                return FillFieldResult(
                    field_id=item.field_id,
                    label=item.label,
                    action=item.action,
                    status=FillFieldStatus.PENDING_HUMAN_INPUT,
                    verified=False,
                    reason=f"{detail} Review this field again before it is filled.",
                    source=item.source.value,
                )
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

    def _live_value_conflict(
        self, item: PlannedField, live_values: Mapping[str, str]
    ) -> FillFieldResult | None:
        """Never type over a value that is already on the page.

        The plan was approved against an analysis. If the page now holds a
        value the analysis did not see, the user (or another tab) put it there
        and ApplyPilot must not replace it: an identical value is a no-op, and
        anything else is left for the user to review.
        """
        if item.action is FillAction.UPLOAD:
            # File inputs deliberately expose no value to the analyser.
            return None
        current = (live_values.get(item.field_id) or "").strip()
        if not current:
            return None
        if _live_value_matches(item, current):
            return FillFieldResult(
                field_id=item.field_id,
                label=item.label,
                action=item.action,
                status=FillFieldStatus.SKIPPED,
                verified=False,
                reason=(
                    "The page already contains the approved value; ApplyPilot "
                    "did not retype it."
                ),
                source=item.source.value,
            )
        return FillFieldResult(
            field_id=item.field_id,
            label=item.label,
            action=item.action,
            status=FillFieldStatus.PENDING_HUMAN_INPUT,
            verified=False,
            reason=(
                "This field changed on the employer's page after you reviewed "
                "the plan. ApplyPilot left your value untouched; re-analyze the "
                "form to include it."
            ),
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
        row.summary = self._merged_summary(row, results, plan)
        row.last_activity_at = _utcnow()
        await self.session.commit()

    def _finalise(
        self,
        row: ApplicationFieldExecution,
        results: list[FillFieldResult],
        plan: FillPlan,
    ) -> None:
        row.results = [item.model_dump(mode="json") for item in results]
        summary = self._merged_summary(row, results, plan)
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
            page_url=analysis.page_url,
            application_step=(analysis.summary or {}).get("application_step"),
            analyzed_at=analysis.analyzed_at,
        )

    # --- Serialisation ------------------------------------------------------
    def _plan_response(
        self,
        application: Application,
        browser_session_id: uuid.UUID,
        analysis: ApplicationFormAnalysis,
        plan: FillPlan,
        structure: str,
    ) -> FillPlanResponse:
        approval = self._approval_fingerprint(analysis, plan, structure)
        return FillPlanResponse(
            application_id=application.id,
            analysis_id=analysis.id,
            analysis_version=analysis.version,
            browser_session_id=browser_session_id,
            generated_at=_utcnow(),
            page_url=analysis.page_url,
            application_step=(analysis.summary or {}).get("application_step"),
            analyzed_at=analysis.analyzed_at,
            structure_fingerprint=structure,
            approval_fingerprint=approval,
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
        """Serialize the canonical field plus the planner's decision.

        The preview row is built from the same ``ApplicationField`` the planner
        resolved, so the client cannot be shown a different label, source or
        value from the one execution will use.
        """
        canonical = item.canonical()
        return FillPlanField(
            **canonical.model_dump(),
            action=item.action,
            status=item.status,
            reason=item.reason,
        )

    @staticmethod
    def _approval_fingerprint(
        analysis: ApplicationFormAnalysis, plan: FillPlan, structure: str
    ) -> str:
        """Digest of the exact fields, actions, statuses and values shown."""
        return approval_fingerprint(
            page_url=analysis.page_url,
            application_step=(analysis.summary or {}).get("application_step"),
            structure=structure,
            fields=[
                (
                    item.field_id,
                    item.action.value,
                    item.status.value,
                    item.proposed_value if item.shows_value else None,
                )
                for item in plan.fields
            ],
            resume=[
                str(plan.resume.resume_id or ""),
                plan.resume.filename or "",
                "upload" if plan.resume.will_upload else "no-upload",
            ],
        )

    def _approval_state(
        self,
        analysis: ApplicationFormAnalysis,
        plan: FillPlan,
        structure: str,
        approved: str,
    ) -> dict[str, Any]:
        """Non-secret binding stored with the execution row."""
        return {
            "approval_fingerprint": approved,
            "structure_fingerprint": structure,
            "approved_analysis_id": str(analysis.id),
            "approved_analysis_version": analysis.version,
            "approved_page_url": analysis.page_url,
            "approved_application_step": (analysis.summary or {}).get(
                "application_step"
            ),
        }

    async def _verify_resume_approval(
        self,
        user: User,
        application: Application,
        row: ApplicationFieldExecution,
        browser_session_id: uuid.UUID,
        handle: PersistentBrowserHandle,
        analysis: ApplicationFormAnalysis,
        plan: FillPlan,
        approved: str,
    ) -> LiveForm:
        """Re-verify a stored approval against the analysis it was granted for.

        Human-in-the-loop resume re-analyzes the page after the user completed
        fields themselves, so the newest plan legitimately differs: those fields
        are no longer candidates. Recomputing the approved digest from the newest
        plan would therefore refuse every continuation. Instead the digest is
        checked against the analysis the run was approved for, and the newest
        plan may only execute fields that approval already covered, so resuming
        can never type a value the user did not review.
        """
        approved_id = (row.summary or {}).get("approved_analysis_id")
        anchor = analysis
        if approved_id:
            candidate = await self._analysis_by_id(
                user, application, browser_session_id, approved_id
            )
            if candidate is not None:
                anchor = candidate
        if anchor.id == analysis.id:
            return await self._verify_live_approval(handle, analysis, plan, approved)

        anchor_plan = await self._build_plan(user, application, anchor)
        live = await self._verify_live_approval(handle, anchor, anchor_plan, approved)
        # Only fields the planner marked READY are executed, so that — not the
        # action shape — is the scope the user actually approved.
        approved_fillable = {
            item.field_id
            for item in anchor_plan.fields
            if item.status is FillFieldStatus.READY
        }
        newly_fillable = [
            item.label
            for item in plan.fields
            if item.status is FillFieldStatus.READY
            and item.field_id not in approved_fillable
        ]
        if newly_fillable:
            raise FieldFillStaleApprovalError(
                "The updated analysis would now fill fields you have not reviewed "
                f"({', '.join(newly_fillable[:3])}). Review the current plan and "
                "approve it before filling."
            )
        return live

    async def _analysis_by_id(
        self,
        user: User,
        application: Application,
        browser_session_id: uuid.UUID,
        analysis_id: str,
    ) -> ApplicationFormAnalysis | None:
        """Return an owned analysis of this session, or None if it is not usable."""
        try:
            identifier = uuid.UUID(str(analysis_id))
        except (TypeError, ValueError):
            return None
        row = await self.session.scalar(
            select(ApplicationFormAnalysis).where(
                ApplicationFormAnalysis.id == identifier,
                ApplicationFormAnalysis.application_id == application.id,
                ApplicationFormAnalysis.user_id == user.id,
                ApplicationFormAnalysis.browser_session_id == browser_session_id,
            )
        )
        return row

    async def _verify_live_approval(
        self,
        handle: PersistentBrowserHandle,
        analysis: ApplicationFormAnalysis,
        plan: FillPlan,
        supplied: str,
    ) -> LiveForm:
        """Refuse execution unless the live page still matches the approval.

        Checks, in order: the session's current page, the current application
        step, the live form structure and finally the exact approved values.
        The comparison uses the session's own HTML; no second browser is opened
        and no cookie or storage state is copied anywhere. The live form is
        returned so execution can avoid every control that already holds a
        value.
        """
        try:
            current_url = sanitize_url_for_display(await handle.current_url())
            html = await handle.content()
        except Exception as exc:  # pragma: no cover - live page failures
            logger.warning(
                "Live page could not be read before filling",
                extra={"stage": "field_fill_verify", "error_class": type(exc).__name__},
            )
            raise FieldFillUnavailableError() from exc

        approved_url = analysis.page_url or ""
        if approved_url and current_url and approved_url != current_url:
            raise FieldFillStaleApprovalError(
                "The employer's page changed since the form was analyzed. "
                "Analyze the current page, review the new plan and approve it again."
            )

        try:
            live = self.analyzer.analyze(html, page_url=current_url or None)
        except Exception as exc:  # pragma: no cover - unreadable HTML
            raise FieldFillUnavailableError() from exc

        approved_step = (analysis.summary or {}).get("application_step")
        if live.application_step != approved_step:
            raise FieldFillStaleApprovalError(
                "The application moved to a different step. Analyze this step and "
                "review its fields before filling."
            )

        structure = structure_fingerprint(live.fields)
        if structure != structure_fingerprint(analysis.fields or []):
            raise FieldFillStaleApprovalError(
                "The form changed since it was analyzed. Re-analyze the page and "
                "review the new plan before filling."
            )

        expected = self._approval_fingerprint(analysis, plan, structure)
        if not hmac.compare_digest(expected, supplied):
            raise FieldFillStaleApprovalError()
        return LiveForm(structure=structure, fields=tuple(live.fields))

    async def _page_guard(
        self, handle: PersistentBrowserHandle, approved_url: str | None
    ) -> str | None:
        """Pause filling if the page moved away from the approved step."""
        if not approved_url:
            return None
        try:
            current = sanitize_url_for_display(await handle.current_url())
        except Exception:  # pragma: no cover - live page failure
            return (
                "The employer's page could not be read, so ApplyPilot stopped "
                "before filling more fields."
            )
        if current and current != approved_url:
            return (
                "The employer's page moved to a different URL during filling. "
                "Re-analyze the new page before continuing."
            )
        return None

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

    @classmethod
    def _merged_summary(
        cls,
        row: ApplicationFieldExecution,
        results: list[FillFieldResult],
        plan: FillPlan,
    ) -> dict[str, Any]:
        """Recompute counters without losing the approved-plan binding."""
        return {
            **(row.summary or {}),
            **cls._result_summary(results, plan),
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
            approval_fingerprint=summary.get("approval_fingerprint"),
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

    async def _acquire_first_start_lock(self, application_id: uuid.UUID) -> None:
        """Serialise creation of one application's execution row.

        ``SELECT ... FOR UPDATE`` cannot lock a row that does not exist yet. On
        PostgreSQL a transaction-scoped advisory lock closes that window: it is
        held until the surrounding transaction commits or rolls back, so the
        competing request blocks until the winner's row is visible.

        Other backends (aiosqlite in the default test suite) already serialise
        writers; there the unique constraint and the ``IntegrityError``
        translation in :meth:`start` remain the guarantee.
        """
        bind = self.session.bind
        if bind is None or bind.dialect.name != "postgresql":
            return
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": _advisory_lock_key(application_id)},
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
    "FieldFillStaleApprovalError",
    "FieldFillUnavailableError",
]
