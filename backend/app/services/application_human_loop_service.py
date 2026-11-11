"""Phase 2E Stage D: human-in-the-loop orchestration.

This service connects the Stage B analysis, Stage C execution, Phase 2C
missing-information queue and the persistent browser session. It records only
safe intervention metadata, pauses honestly, re-analyses the live page when
asked, reconciles fields the user completed, and builds a final review
checklist.

It never accepts a legal declaration, solves a CAPTCHA, supplies an OTP/MFA
code, fills a password, marks an intervention resolved because the frontend
asked, or submits an application. When the deployment has no interactive
browser view, it says so instead of pretending the backend Playwright page is
visible to the user.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError, ResourceNotFoundError
from app.core.logging import get_logger
from app.integrations.browser.view import browser_view_capability
from app.models.application import Application
from app.models.application_field_execution import (
    ApplicationFieldExecution,
    FieldExecutionStatus,
)
from app.models.application_form_analysis import ApplicationFormAnalysis
from app.models.application_intervention import (
    ApplicationIntervention,
    InterventionStatus,
    InterventionType,
)
from app.models.browser_session import BrowserSession, BrowserSessionStatus
from app.models.candidate import Candidate
from app.models.user import User
from app.repositories.candidate_repository import CandidateRepository
from app.schemas.application_fill import (
    FillExecutionResponse,
    FillFieldStatus,
    FillPlanField,
    FillResumeSelection,
)
from app.schemas.application_form import ApplicationFormAnalysisResponse, DetectedFormField
from app.schemas.application_human_loop import (
    FinalReviewResponse,
    FormFieldChange,
    HumanLoopReconcileResponse,
    InterventionListResponse,
    InterventionResponse,
    ReviewItem,
    ReviewSection,
)
from app.schemas.browser_session import BrowserViewInfo
from app.services.application_field_fill_service import (
    FINAL_SUBMISSION_NOTE,
    ApplicationFieldFillService,
    FieldFillNotFoundError,
)
from app.services.application_form_service import ApplicationFormService
from app.services.application_service import ApplicationService
from app.services.browser_session_service import (
    BrowserSessionNotFoundError,
    BrowserSessionService,
)
from app.services.candidate_service import CandidateService

logger = get_logger(__name__)

_ACTIVE_STATUSES = (InterventionStatus.PENDING, InterventionStatus.USER_WORKING)
_FINAL_CONFIRMATION_TYPES = frozenset(
    {
        InterventionType.USER_CONFIRMATION,
        InterventionType.SENSITIVE_INFORMATION,
        InterventionType.UNSUPPORTED_FIELD,
    }
)
_BLOCKING_TYPES = frozenset(
    {
        InterventionType.MISSING_INFORMATION,
        InterventionType.STALE_INFORMATION,
        InterventionType.CONFLICTING_INFORMATION,
        InterventionType.BROWSER_SESSION_EXPIRED,
    }
)
_SENSITIVE_CLASSIFICATIONS = frozenset(
    {
        "legal_declaration",
        "demographic_information",
        "work_authorization",
        "current_salary",
        "expected_salary",
        "notice_period",
        "relocation",
    }
)
_UNSUPPORTED_CLASSIFICATIONS = frozenset(
    {"captcha", "otp_verification", "mfa_verification"}
)
#: Interventions that only a verified user action or a fresh structural check can
#: close. A stage-C run outcome must never resolve them implicitly.
_PROTECTED_TYPES = frozenset(
    {
        InterventionType.SENSITIVE_INFORMATION,
        InterventionType.UNSUPPORTED_FIELD,
        InterventionType.BROWSER_SESSION_EXPIRED,
        InterventionType.FORM_CHANGED,
    }
)

_MANUAL_HANDOFF_NOTE = (
    "Live browser streaming is disabled on this deployment, so the Playwright "
    "page cannot be shown in this interface and external changes cannot be "
    "observed. Enable it on the backend to view and complete the page here; no "
    "submission is automated."
)

_STREAM_HANDOFF_NOTE = (
    "The employer's page is available in the live browser workspace, which is "
    "the same Playwright page used for analysis and filling. Complete CAPTCHA, "
    "OTP, MFA and legal declarations there; no submission is automated."
)


class InterventionNotFoundError(ResourceNotFoundError):
    code = "intervention_not_found"
    message = "This intervention was not found for the application."


class InterventionConflictError(AppError):
    status_code = 409
    code = "intervention_conflict"
    message = "The intervention cannot be changed in its current state."


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _view() -> BrowserViewInfo:
    return BrowserViewInfo(**browser_view_capability().to_payload())


def _has_value(field: DetectedFormField | FillPlanField | None) -> bool:
    if field is None:
        return False
    if isinstance(field, DetectedFormField):
        return bool((field.current_value or "").strip())
    return field.status in {FillFieldStatus.ALREADY_FILLED, FillFieldStatus.SUCCEEDED}


class ApplicationHumanLoopService:
    """Own the intervention queue, reconciliation and final review."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        browser_sessions: BrowserSessionService | None = None,
        forms: ApplicationFormService | None = None,
        fill: ApplicationFieldFillService | None = None,
    ) -> None:
        self.session = session
        self.applications = ApplicationService(session)
        self.browser_sessions = browser_sessions or BrowserSessionService(session)
        self.forms = forms or ApplicationFormService(session)
        self.fill = fill or ApplicationFieldFillService(
            session, browser_sessions=self.browser_sessions
        )
        self.candidates = CandidateService(CandidateRepository(session))

    # --- Reads ---------------------------------------------------------------
    async def list_interventions(
        self, user: User, application_id: uuid.UUID
    ) -> InterventionListResponse:
        application = await self._owned_application(user, application_id)
        session_row = await self._maybe_session(user, application.id)
        execution = await self._execution_row(application.id, user.id)
        latest = await self._latest_analysis_row(application.id, user.id)
        if execution is not None and execution.status is FieldExecutionStatus.CANCELLED:
            await self._cancel_active_for_run(user, application)
        elif execution is not None and (
            latest is None or latest.id == execution.analysis_id
        ):
            await self._sync_from_execution(user, application, session_row, execution)
        if session_row is not None:
            await self._sync_session_state(user, application, session_row, execution)
        await self.session.commit()
        items = await self._all_items(application.id, user.id)
        active = [item for item in items if item.status in _ACTIVE_STATUSES]
        blocking = [item for item in active if self._is_blocking(item)]
        return InterventionListResponse(
            application_id=application.id,
            application_url=application.application_url,
            browser_session_id=session_row.id if session_row else None,
            view=_view(),
            total=len(items),
            pending_count=sum(
                1 for item in active if item.status is InterventionStatus.PENDING
            ),
            user_working_count=sum(
                1 for item in active if item.status is InterventionStatus.USER_WORKING
            ),
            blocking_count=len(blocking),
            interventions=[self._response(item) for item in items],
            final_submission_note=FINAL_SUBMISSION_NOTE,
        )

    async def get_intervention(
        self, user: User, application_id: uuid.UUID, intervention_id: uuid.UUID
    ) -> InterventionResponse:
        application = await self._owned_application(user, application_id)
        item = await self._item(application.id, user.id, intervention_id)
        return self._response(item)

    # --- User controls -------------------------------------------------------
    async def mark_working(
        self, user: User, application_id: uuid.UUID, intervention_id: uuid.UUID
    ) -> InterventionResponse:
        application = await self._owned_application(user, application_id)
        item = await self._item(application.id, user.id, intervention_id)
        self._require_active(item)
        if item.intervention_type is InterventionType.BROWSER_SESSION_EXPIRED:
            raise InterventionConflictError(
                "Reopen the employer page instead of marking this item as working.",
                code="browser_session_expired",
            )
        if item.status is InterventionStatus.USER_WORKING:
            return self._response(item)
        item.status = InterventionStatus.USER_WORKING
        item.user_working_at = _utcnow()
        await self.session.commit()
        await self.session.refresh(item)
        return self._response(item)

    async def cancel_intervention(
        self, user: User, application_id: uuid.UUID, intervention_id: uuid.UUID
    ) -> InterventionResponse:
        application = await self._owned_application(user, application_id)
        item = await self._item(application.id, user.id, intervention_id)
        if item.status is InterventionStatus.CANCELLED:
            return self._response(item)
        if item.status is InterventionStatus.RESOLVED:
            raise InterventionConflictError(
                "A resolved intervention cannot be cancelled.",
                code="intervention_already_resolved",
            )
        item.status = InterventionStatus.CANCELLED
        item.cancelled_at = _utcnow()
        item.resolution_reason = "Cancelled by the user."
        await self.session.commit()
        await self.session.refresh(item)
        return self._response(item)

    async def reanalyze(
        self, user: User, application_id: uuid.UUID
    ) -> HumanLoopReconcileResponse:
        """Re-read the live page, diff it, then reconcile the queue safely."""
        application = await self._owned_application(user, application_id)
        execution = await self._execution_row(application.id, user.id)
        if execution is not None:
            if execution.status in {
                FieldExecutionStatus.RUNNING,
                FieldExecutionStatus.PLANNED,
            }:
                raise InterventionConflictError(
                    "A field-fill run is in progress. Let it finish before "
                    "re-analyzing the form.",
                    code="field_fill_in_progress",
                )
            if execution.status is FieldExecutionStatus.CANCELLED:
                raise InterventionConflictError(
                    "This fill run was cancelled. Start a new fill run before "
                    "re-analyzing the form.",
                    code="field_fill_cancelled",
                )
        previous = await self._latest_analysis_row(application.id, user.id)
        previous_fields = (
            [
                DetectedFormField.model_validate(item)
                for item in (previous.fields or [])
            ]
            if previous
            else []
        )

        analyzed = await self.forms.analyze(user, application.id)
        plan = await self.fill.plan(user, application.id)
        session_row = await self._maybe_session(user, application.id)
        current_fields = [DetectedFormField.model_validate(item) for item in analyzed.fields]

        await self._sync_from_plan(
            user,
            application,
            session_row,
            execution,
            plan.fields,
            current_fields,
            analyzed.analysis_id,
            analyzed.version,
        )
        changes = self._diff(previous_fields, current_fields)
        newly_completed = self._newly_completed(previous_fields, current_fields)
        await self._sync_form_changed(
            user,
            application,
            session_row,
            execution,
            analyzed,
            changes,
        )
        if session_row is not None:
            await self._sync_session_state(user, application, session_row, execution)
        await self.session.commit()

        items = await self._all_items(application.id, user.id)
        active = [item for item in items if item.status in _ACTIVE_STATUSES]
        execution_response = (
            await self.fill.get_execution(user, application.id) if execution else None
        )
        view = _view()
        logger.info(
            "Application form re-analyzed for human reconciliation",
            extra={
                "stage": "human_loop_reanalyze",
                "user_id": str(user.id),
                "application_id": str(application.id),
                "version": analyzed.version,
                "changed_fields": len(changes),
                "interventions": len(active),
            },
        )
        return HumanLoopReconcileResponse(
            application_id=application.id,
            analysis_id=analyzed.analysis_id,
            analysis_version=analyzed.version,
            previous_analysis_id=previous.id if previous else None,
            previous_analysis_version=previous.version if previous else None,
            page_url=analyzed.page_url,
            field_count=analyzed.field_count,
            changes=changes,
            newly_completed_fields=newly_completed,
            blocked_count=sum(1 for item in active if self._is_blocking(item)),
            interventions=[self._response(item) for item in items],
            execution=execution_response,
            view=view,
            manual_handoff_required=not view.user_can_interact,
            verification_note=(
                _STREAM_HANDOFF_NOTE
                if view.user_can_interact
                else _MANUAL_HANDOFF_NOTE
            ),
            final_submission_note=FINAL_SUBMISSION_NOTE,
        )

    async def resolve_intervention(
        self, user: User, application_id: uuid.UUID, intervention_id: uuid.UUID
    ) -> InterventionResponse:
        """Resolve only after a fresh analysis proves the condition is gone."""
        application = await self._owned_application(user, application_id)
        item = await self._item(application.id, user.id, intervention_id)
        if item.status is InterventionStatus.RESOLVED:
            return self._response(item)
        self._require_active(item)
        if item.intervention_type in _FINAL_CONFIRMATION_TYPES:
            raise InterventionConflictError(
                "This field must be confirmed by you on the employer's page. "
                "ApplyPilot cannot resolve it on your behalf.",
                code="intervention_requires_user_action",
            )
        if item.field_id is None or item.field_id == "__form__":
            raise InterventionConflictError(
                "This item is resolved by reopening and re-analyzing the form.",
                code="intervention_not_verifiable",
            )
        try:
            await self.reanalyze(user, application.id)
        except AppError:
            raise
        await self.session.refresh(item)
        if item.status is not InterventionStatus.RESOLVED:
            raise InterventionConflictError(
                "The latest form analysis does not yet satisfy this intervention.",
                code="intervention_not_satisfied",
            )
        return self._response(item)

    async def resume(
        self, user: User, application_id: uuid.UUID
    ) -> FillExecutionResponse:
        """Resume Stage C using the newest analysis, then refresh the queue."""
        application = await self._owned_application(user, application_id)
        execution = await self._execution_row(application.id, user.id)
        if execution is None:
            raise FieldFillNotFoundError()
        result = await self.fill.resume(user, application.id)
        session_row = await self._maybe_session(user, application.id)
        await self._sync_from_execution(user, application, session_row, execution)
        if session_row is not None:
            await self._sync_session_state(user, application, session_row, execution)
        await self.session.commit()
        return result

    # --- Final review --------------------------------------------------------
    async def final_review(
        self, user: User, application_id: uuid.UUID
    ) -> FinalReviewResponse:
        application = await self._owned_application(user, application_id)
        execution = await self._execution_row(application.id, user.id)
        latest = await self._latest_analysis_row(application.id, user.id)
        candidate = await self.candidates.ensure_profile_for_user(user)
        if execution is not None:
            session_row = await self._maybe_session(user, application.id)
            if execution.status is FieldExecutionStatus.CANCELLED:
                await self._cancel_active_for_run(user, application)
            elif latest is None or latest.id == execution.analysis_id:
                await self._sync_from_execution(
                    user, application, session_row, execution
                )
            if session_row is not None:
                await self._sync_session_state(user, application, session_row, execution)
        await self.session.commit()

        items = await self._all_items(application.id, user.id)
        active = [item for item in items if item.status in _ACTIVE_STATUSES]
        blockers = [item for item in active if self._is_blocking(item)]
        confirmations = [
            item for item in active if item.intervention_type in _FINAL_CONFIRMATION_TYPES
        ]
        pending_optional = [
            item for item in active if not item.required and not self._is_blocking(item)
        ]
        results = self._results(execution)
        plan_fields = self._plan_fields(execution)
        by_field = {item.field_id: item for item in plan_fields}

        filled = [
            result.label
            for result in results
            if result.status is FillFieldStatus.SUCCEEDED
        ]
        verified = [result.label for result in results if result.verified]
        skipped = [
            (by_field.get(result.field_id).label if by_field.get(result.field_id) else result.label)
            for result in results
            if result.status
            in {
                FillFieldStatus.SKIPPED,
                FillFieldStatus.PENDING_HUMAN_INPUT,
                FillFieldStatus.PENDING_CONFIRMATION,
            }
        ]
        sections = self._review_sections(candidate, application, execution, active)
        terminal_failure = (
            execution is not None
            and execution.status
            in {
                FieldExecutionStatus.FAILED,
                FieldExecutionStatus.CANCELLED,
                FieldExecutionStatus.SESSION_EXPIRED,
            }
        )
        ready = (
            execution is not None
            and not blockers
            and not terminal_failure
            and execution.status
            in {
                FieldExecutionStatus.NEEDS_USER_INPUT,
                FieldExecutionStatus.WAITING_FOR_CONFIRMATION,
                FieldExecutionStatus.READY_FOR_FINAL_REVIEW,
                FieldExecutionStatus.COMPLETED,
            }
        )
        if (
            ready
            and execution is not None
            and execution.status
            in {
                FieldExecutionStatus.NEEDS_USER_INPUT,
                FieldExecutionStatus.WAITING_FOR_CONFIRMATION,
            }
        ):
            # All blocking information is resolved; the remaining items are
            # final confirmations the user handles on the employer's page.
            execution.status = FieldExecutionStatus.READY_FOR_FINAL_REVIEW
            execution.pause_reason = (
                "Safe fields are filled. Review the checklist, then complete any "
                "legal/CAPTCHA/OTP step and submit the application yourself."
            )
            execution.completed_at = _utcnow()
            await self.session.commit()

        return FinalReviewResponse(
            application_id=application.id,
            execution_id=execution.id if execution else None,
            execution_status=(
                execution.status.value if execution else "not_started"
            ),
            analysis_version=(
                execution.analysis_version
                if execution
                else latest.version
                if latest
                else None
            ),
            ready_for_final_review=ready,
            generated_at=_utcnow(),
            resume=self._resume(application, execution),
            sections=sections,
            filled_field_labels=filled,
            verified_field_labels=verified,
            skipped_field_labels=skipped,
            confirmation_fields=[item.field_label for item in confirmations],
            blocking_interventions=[
                f"{item.field_label}: {item.reason}" for item in blockers
            ],
            missing_optional_fields=list(
                dict.fromkeys(
                    [
                        item.field_label
                        for item in pending_optional
                    ]
                    + [
                        field.label
                        for field in plan_fields
                        if not field.required
                        and field.status
                        in {
                            FillFieldStatus.SKIPPED,
                            FillFieldStatus.NEEDS_HUMAN_INPUT,
                            FillFieldStatus.NEEDS_CONFIRMATION,
                        }
                    ]
                )
            ),
            filled_count=len(filled),
            verified_count=len(verified),
            skipped_count=len(skipped),
            blocker_count=len(blockers),
            view=_view(),
            manual_submission_required=True,
            final_submission_note=FINAL_SUBMISSION_NOTE,
        )

    # --- Synchronisation -----------------------------------------------------
    async def _sync_from_execution(
        self,
        user: User,
        application: Application,
        session_row: BrowserSession | None,
        execution: ApplicationFieldExecution,
    ) -> None:
        fields = [
            FillPlanField.model_validate(item) for item in (execution.plan or [])
        ]
        latest = await self._latest_analysis_row(application.id, user.id)
        current_by_id = {
            item.get("field_id"): DetectedFormField.model_validate(item)
            for item in ((latest.fields or []) if latest is not None else [])
        }
        results = self._results(execution)
        result_by_field = {item.field_id: item for item in results}
        active = await self._active_items(application.id, user.id)
        active_by_field = {
            item.field_id: item for item in active if item.field_id is not None
        }
        kept: set[str] = set()

        for field in fields:
            result = result_by_field.get(field.field_id)
            info = self._intervention_for(field, result)
            current = current_by_id.get(field.field_id)
            if (
                info is not None
                and info[0] is InterventionType.MISSING_INFORMATION
                and self._has_user_value(field, current)
            ):
                # A newer analysis shows the user completed this control. The
                # stage-C plan is older than that evidence, so it must not
                # reopen the requirement.
                existing = active_by_field.get(field.field_id)
                if existing is not None:
                    self._resolve(
                        existing,
                        "The current page shows the value you entered for this "
                        "field.",
                    )
                continue
            if info is None:
                existing = active_by_field.get(field.field_id)
                if (
                    existing is not None
                    and existing.intervention_type not in _PROTECTED_TYPES
                ):
                    self._resolve(
                        existing,
                        "The latest run no longer requires this intervention.",
                    )
                continue
            kept.add(field.field_id)
            existing = active_by_field.get(field.field_id)
            if existing is None:
                self._create(
                    user,
                    application,
                    session_row,
                    execution,
                    field,
                    info,
                )
            else:
                self._update(existing, field, info, execution)

        for item in active:
            if item.field_id in {None, "__form__", "__session__"}:
                continue
            if item.field_id not in kept:
                item.status = InterventionStatus.CANCELLED
                item.cancelled_at = _utcnow()
                item.resolution_reason = "The field is no longer part of this run."

    async def _sync_from_plan(
        self,
        user: User,
        application: Application,
        session_row: BrowserSession | None,
        execution: ApplicationFieldExecution | None,
        plan_fields: list[FillPlanField],
        current_fields: list[DetectedFormField],
        analysis_id: uuid.UUID,
        analysis_version: int,
    ) -> None:
        active = await self._active_items(application.id, user.id)
        active_by_field = {
            item.field_id: item for item in active if item.field_id is not None
        }
        current_by_id = {field.field_id: field for field in current_fields}
        kept: set[str] = set()

        for field in plan_fields:
            current = current_by_id.get(field.field_id)
            info = self._intervention_for(field, None)
            existing = active_by_field.get(field.field_id)
            if existing is not None and self._user_value_satisfies(
                field, current, existing
            ):
                # An unknown free-text control is never auto-filled, but a
                # value the user typed on the same page does satisfy the
                # missing-information requirement. Legal, sensitive and
                # confirmation items are explicitly excluded.
                self._resolve(
                    existing,
                    "The current page shows the value you entered for this "
                    "field.",
                )
                continue
            if info is None:
                if existing is not None and self._safe_to_resolve(
                    field, current, existing
                ):
                    self._resolve(
                        existing,
                        "The latest form analysis confirms this field is resolved.",
                    )
                continue
            kept.add(field.field_id)
            if existing is None:
                self._create(
                    user,
                    application,
                    session_row,
                    execution,
                    field,
                    info,
                    analysis_id=analysis_id,
                    analysis_version=analysis_version,
                )
            else:
                self._update(
                    existing,
                    field,
                    info,
                    execution,
                    analysis_id=analysis_id,
                    analysis_version=analysis_version,
                )

        for item in active:
            if item.field_id in {None, "__form__", "__session__"}:
                continue
            if item.field_id not in kept:
                if item.intervention_type is InterventionType.FORM_CHANGED:
                    continue
                if item.field_id in current_by_id:
                    # Still on the page but no longer in the plan; do not invent
                    # a resolution without a verified condition.
                    continue
                item.status = InterventionStatus.CANCELLED
                item.cancelled_at = _utcnow()
                item.resolution_reason = (
                    "The field is no longer present on the current form."
                )

    async def _sync_form_changed(
        self,
        user: User,
        application: Application,
        session_row: BrowserSession | None,
        execution: ApplicationFieldExecution | None,
        analyzed: ApplicationFormAnalysisResponse,
        changes: list[FormFieldChange],
    ) -> None:
        active = await self._active_items(application.id, user.id)
        existing = next(
            (
                item
                for item in active
                if item.intervention_type is InterventionType.FORM_CHANGED
            ),
            None,
        )
        if not changes:
            if existing is not None:
                existing.status = InterventionStatus.RESOLVED
                existing.resolved_at = _utcnow()
                existing.resolution_reason = (
                    "A newer analysis found no structural form changes."
                )
            return
        structural = [
            change
            for change in changes
            if change.change in {"added", "removed"}
            or change.previous_label is not None
            or change.previous_field_type is not None
            or change.previous_required is not None
            or change.options_changed
        ]
        if not structural:
            if existing is not None:
                existing.status = InterventionStatus.RESOLVED
                existing.resolved_at = _utcnow()
                existing.resolution_reason = (
                    "Only field values changed; the form structure is unchanged."
                )
            return
        labels = ", ".join(
            dict.fromkeys(change.label for change in structural)
        )[:1000]
        reason = (
            f"The employer's form changed ({len(structural)} field(s)): {labels}."
        )
        if existing is None:
            self.session.add(
                ApplicationIntervention(
                    id=uuid.uuid4(),
                    user_id=user.id,
                    application_id=application.id,
                    browser_session_id=session_row.id if session_row else None,
                    application_field_execution_id=execution.id if execution else None,
                    analysis_id=analyzed.analysis_id,
                    analysis_version=analyzed.version,
                    field_id="__form__",
                    field_label="Application form changed",
                    field_category="form",
                    required=False,
                    reason=reason,
                    intervention_type=InterventionType.FORM_CHANGED,
                    status=InterventionStatus.PENDING,
                )
            )
        else:
            existing.reason = reason
            existing.analysis_id = analyzed.analysis_id
            existing.analysis_version = analyzed.version
            existing.status = InterventionStatus.PENDING
            existing.resolved_at = None
            existing.cancelled_at = None

    async def _cancel_active_for_run(
        self, user: User, application: Application
    ) -> None:
        """Close the queue when its run is cancelled; never mark it resolved."""
        for item in await self._active_items(application.id, user.id):
            item.status = InterventionStatus.CANCELLED
            item.cancelled_at = _utcnow()
            item.resolution_reason = (
                "The field-fill run was cancelled. Start a new run to re-check "
                "the employer's form."
            )

    async def _sync_session_state(
        self,
        user: User,
        application: Application,
        session_row: BrowserSession,
        execution: ApplicationFieldExecution | None,
    ) -> None:
        active = await self._active_items(application.id, user.id)
        existing = next(
            (
                item
                for item in active
                if item.intervention_type is InterventionType.BROWSER_SESSION_EXPIRED
            ),
            None,
        )
        if session_row.browser_status is BrowserSessionStatus.OPEN:
            if existing is not None:
                existing.status = InterventionStatus.CANCELLED
                existing.cancelled_at = _utcnow()
                existing.resolution_reason = (
                    "The browser session was reopened. Re-analyze the form before "
                    "resuming."
                )
            return
        if existing is not None:
            return
        self.session.add(
            ApplicationIntervention(
                id=uuid.uuid4(),
                user_id=user.id,
                application_id=application.id,
                browser_session_id=session_row.id,
                application_field_execution_id=execution.id if execution else None,
                analysis_id=execution.analysis_id if execution else None,
                analysis_version=execution.analysis_version if execution else None,
                field_id="__session__",
                field_label="Browser session ended",
                field_category="browser_session",
                required=True,
                reason=(
                    "The employer's browser session expired. ApplyPilot did not "
                    "open a replacement. Reopen the employer application to "
                    "continue; do not create a new session silently."
                ),
                intervention_type=InterventionType.BROWSER_SESSION_EXPIRED,
                status=InterventionStatus.PENDING,
            )
        )

    # --- Helpers -------------------------------------------------------------
    def _intervention_for(
        self,
        field: FillPlanField,
        result: Any | None,
    ) -> tuple[InterventionType, str] | None:
        status = result.status if result is not None else field.status
        if status in {
            FillFieldStatus.SUCCEEDED,
            FillFieldStatus.READY,
            FillFieldStatus.ALREADY_FILLED,
            FillFieldStatus.SKIPPED,
        }:
            return None
        if status not in {
            FillFieldStatus.NEEDS_CONFIRMATION,
            FillFieldStatus.NEEDS_HUMAN_INPUT,
            FillFieldStatus.PENDING_CONFIRMATION,
            FillFieldStatus.PENDING_HUMAN_INPUT,
            FillFieldStatus.FAILED,
        }:
            return None

        classification = field.classification
        if (
            classification == "legal_declaration"
            or classification in _SENSITIVE_CLASSIFICATIONS
        ):
            return (
                InterventionType.SENSITIVE_INFORMATION,
                "This field involves a legal, authorization or sensitive "
                "declaration. ApplyPilot leaves it entirely to you.",
            )
        if classification in _UNSUPPORTED_CLASSIFICATIONS:
            return (
                InterventionType.UNSUPPORTED_FIELD,
                "CAPTCHA, OTP and MFA steps cannot be automated. Complete this "
                "step yourself on the employer's page.",
            )
        if status is FillFieldStatus.FAILED:
            return (
                InterventionType.UNSUPPORTED_FIELD,
                result.reason
                if result is not None and result.reason
                else "The employer's page rejected this field.",
            )
        if status in {
            FillFieldStatus.NEEDS_CONFIRMATION,
            FillFieldStatus.PENDING_CONFIRMATION,
        }:
            return (
                InterventionType.USER_CONFIRMATION,
                "This value needs your confirmation before ApplyPilot uses it.",
            )
        return (
            InterventionType.MISSING_INFORMATION,
            field.reason or "Required information is missing for this field.",
        )

    @staticmethod
    def _has_user_value(
        field: FillPlanField, current: DetectedFormField | None
    ) -> bool:
        """Whether the live page shows a value the user supplied here."""
        if field.classification in _SENSITIVE_CLASSIFICATIONS:
            return False
        if field.classification in _UNSUPPORTED_CLASSIFICATIONS:
            return False
        if field.classification == "legal_declaration":
            return False
        if current is None:
            return False
        return bool((current.current_value or "").strip())

    @classmethod
    def _user_value_satisfies(
        cls,
        field: FillPlanField,
        current: DetectedFormField | None,
        existing: ApplicationIntervention,
    ) -> bool:
        """A user-entered non-empty value resolves a missing-info item once."""
        if existing.intervention_type is not InterventionType.MISSING_INFORMATION:
            return False
        return cls._has_user_value(field, current)

    def _safe_to_resolve(
        self,
        field: FillPlanField,
        current: DetectedFormField | None,
        existing: ApplicationIntervention,
    ) -> bool:
        if field.classification in _SENSITIVE_CLASSIFICATIONS:
            return False
        if field.classification in _UNSUPPORTED_CLASSIFICATIONS:
            return False
        if field.classification in {"unknown", "other"}:
            # A non-empty unknown control is not proof that it was completed
            # correctly; leave it for the user.
            return False
        if field.status not in {
            FillFieldStatus.READY,
            FillFieldStatus.ALREADY_FILLED,
        }:
            return False
        if existing.intervention_type in _PROTECTED_TYPES:
            return False
        del current
        return True

    def _create(
        self,
        user: User,
        application: Application,
        session_row: BrowserSession | None,
        execution: ApplicationFieldExecution | None,
        field: FillPlanField,
        info: tuple[InterventionType, str],
        *,
        analysis_id: uuid.UUID | None = None,
        analysis_version: int | None = None,
    ) -> ApplicationIntervention:
        intervention_type, reason = info
        item = ApplicationIntervention(
            id=uuid.uuid4(),
            user_id=user.id,
            application_id=application.id,
            browser_session_id=session_row.id if session_row else None,
            application_field_execution_id=execution.id if execution else None,
            analysis_id=(
                analysis_id
                if analysis_id is not None
                else execution.analysis_id
                if execution
                else None
            ),
            analysis_version=(
                analysis_version
                if analysis_version is not None
                else execution.analysis_version
                if execution
                else None
            ),
            field_id=field.field_id,
            field_label=field.label[:500],
            field_category=field.classification,
            required=field.required,
            reason=reason[:2000],
            intervention_type=intervention_type,
            status=InterventionStatus.PENDING,
        )
        self.session.add(item)
        return item

    def _update(
        self,
        item: ApplicationIntervention,
        field: FillPlanField,
        info: tuple[InterventionType, str],
        execution: ApplicationFieldExecution | None,
        *,
        analysis_id: uuid.UUID | None = None,
        analysis_version: int | None = None,
    ) -> None:
        intervention_type, reason = info
        item.intervention_type = intervention_type
        item.field_label = field.label[:500]
        item.field_category = field.classification
        item.required = field.required
        item.reason = reason[:2000]
        item.analysis_id = (
            analysis_id
            if analysis_id is not None
            else execution.analysis_id
            if execution
            else item.analysis_id
        )
        item.analysis_version = (
            analysis_version
            if analysis_version is not None
            else execution.analysis_version
            if execution
            else item.analysis_version
        )

    @staticmethod
    def _resolve(item: ApplicationIntervention, reason: str) -> None:
        if item.status is InterventionStatus.RESOLVED:
            return
        item.status = InterventionStatus.RESOLVED
        item.resolved_at = _utcnow()
        item.resolution_reason = reason[:2000]

    @staticmethod
    def _require_active(item: ApplicationIntervention) -> None:
        if item.status not in _ACTIVE_STATUSES:
            raise InterventionConflictError(
                "This intervention is no longer active.",
                code="intervention_not_active",
            )

    def _is_blocking(self, item: ApplicationIntervention) -> bool:
        if not item.required:
            return False
        return item.intervention_type in _BLOCKING_TYPES

    @staticmethod
    def _diff(
        previous: list[DetectedFormField], current: list[DetectedFormField]
    ) -> list[FormFieldChange]:
        old = {field.field_id: field for field in previous}
        new = {field.field_id: field for field in current}
        changes: list[FormFieldChange] = []
        for field_id, field in new.items():
            before = old.get(field_id)
            if before is None:
                changes.append(
                    FormFieldChange(
                        change="added",
                        field_id=field_id,
                        label=field.label,
                        field_type=field.field_type.value,
                        required=field.required,
                        now_has_value=bool((field.current_value or "").strip()),
                    )
                )
                continue
            changed = (
                before.label != field.label
                or before.field_type is not field.field_type
                or before.required != field.required
                or tuple(before.options) != tuple(field.options)
                or (before.current_value or "") != (field.current_value or "")
            )
            if not changed:
                continue
            changes.append(
                FormFieldChange(
                    change="changed",
                    field_id=field_id,
                    label=field.label,
                    previous_label=before.label if before.label != field.label else None,
                    field_type=field.field_type.value,
                    previous_field_type=(
                        before.field_type.value
                        if before.field_type is not field.field_type
                        else None
                    ),
                    required=field.required,
                    previous_required=(
                        before.required if before.required != field.required else None
                    ),
                    options_changed=tuple(before.options) != tuple(field.options),
                    now_has_value=bool((field.current_value or "").strip()),
                    previously_had_value=bool((before.current_value or "").strip()),
                )
            )
        for field_id, field in old.items():
            if field_id in new:
                continue
            changes.append(
                FormFieldChange(
                    change="removed",
                    field_id=field_id,
                    label=field.label,
                    previous_label=field.label,
                    previous_field_type=field.field_type.value,
                    previous_required=field.required,
                    previously_had_value=bool((field.current_value or "").strip()),
                )
            )
        return changes[:100]

    @staticmethod
    def _newly_completed(
        previous: list[DetectedFormField], current: list[DetectedFormField]
    ) -> list[str]:
        old = {field.field_id: field for field in previous}
        completed: list[str] = []
        for field in current:
            if field.is_sensitive or field.is_legal:
                continue
            if not (field.current_value or "").strip():
                continue
            before = old.get(field.field_id)
            if before is None or not (before.current_value or "").strip():
                completed.append(field.label)
        return completed

    def _review_sections(
        self,
        candidate: Candidate,
        application: Application,
        execution: ApplicationFieldExecution | None,
        active: list[ApplicationIntervention],
    ) -> list[ReviewSection]:
        personal = [
            ReviewItem(label="Full name", present=bool(candidate.full_name)),
            ReviewItem(
                label="Preferred name",
                present=bool(candidate.preferred_name),
                optional=True,
            ),
            ReviewItem(label="Email", present=bool(candidate.email)),
            ReviewItem(
                label="Phone", present=bool(candidate.phone), optional=True
            ),
            ReviewItem(
                label="Location",
                present=bool(candidate.location or candidate.city),
                optional=True,
            ),
            ReviewItem(
                label="LinkedIn",
                present=bool(candidate.linkedin_url),
                optional=True,
            ),
        ]
        education = [
            ReviewItem(
                label=entry.institution,
                present=True,
                detail=" ".join(                        part
                        for part in (
                            getattr(entry, "degree", None),
                            getattr(entry, "specialization", None),
                        )
                        if part
                )
                or None,
            )
            for entry in (candidate.education or [])
        ]
        experience = [
            ReviewItem(
                label=entry.company,
                present=True,
                detail=" ".join(
                    part
                    for part in (
                        getattr(entry, "designation", None),
                        getattr(entry, "employment_type", None),
                    )
                    if part
                )
                or None,
            )
            for entry in (candidate.experiences or [])
        ]
        skills = [
            ReviewItem(label=skill, present=True) for skill in (candidate.skills or [])
        ]
        external = [
            ReviewItem(
                label=item.field_label,
                present=False,
                optional=not item.required,
                detail=item.reason,
            )
            for item in active
            if item.intervention_type in _FINAL_CONFIRMATION_TYPES
        ]
        resume_name = (
            execution.selected_resume_filename
            if execution and execution.selected_resume_filename
            else application.resume_filename
            or (application.resume.original_filename if application.resume else None)
        )
        application_items = [
            ReviewItem(
                label="Selected resume",
                present=bool(resume_name),
                detail=resume_name,
            )
        ]
        return [
            ReviewSection(
                key="personal",
                label="Personal and contact information",
                status="complete"
                if all(item.present or item.optional for item in personal)
                else "incomplete",
                items=personal,
            ),
            ReviewSection(
                key="education",
                label="Education",
                status="complete" if education else "not_provided",
                items=education,
            ),
            ReviewSection(
                key="experience",
                label="Experience",
                status="complete" if experience else "not_provided",
                items=experience,
            ),
            ReviewSection(
                key="skills",
                label="Skills",
                status="complete" if skills else "not_provided",
                items=skills,
            ),
            ReviewSection(
                key="application",
                label="Application",
                status="complete" if resume_name else "incomplete",
                items=application_items,
            ),
            ReviewSection(
                key="confirmations",
                label="Final confirmations and external steps",
                status="action_required" if external else "complete",
                items=external,
            ),
        ]

    @staticmethod
    def _results(execution: ApplicationFieldExecution | None) -> list[Any]:
        if execution is None:
            return []
        from app.schemas.application_fill import FillFieldResult

        return [
            FillFieldResult.model_validate(item) for item in (execution.results or [])
        ]

    @staticmethod
    def _plan_fields(execution: ApplicationFieldExecution | None) -> list[FillPlanField]:
        if execution is None:
            return []
        return [
            FillPlanField.model_validate(item) for item in (execution.plan or [])
        ]

    @staticmethod
    def _resume(
        application: Application, execution: ApplicationFieldExecution | None
    ) -> FillResumeSelection:
        if execution and execution.selected_resume_filename:
            return FillResumeSelection(
                resume_id=execution.selected_resume_id,
                filename=execution.selected_resume_filename,
                will_upload=False,
                reason="Selected for this application.",
            )
        resume = application.resume
        return FillResumeSelection(
            resume_id=resume.id if resume else application.resume_id,
            filename=resume.original_filename if resume else application.resume_filename,
            will_upload=False,
            reason="No resume was uploaded by the fill run.",
        )

    def _response(self, item: ApplicationIntervention) -> InterventionResponse:
        return InterventionResponse(
            intervention_id=item.id,
            application_id=item.application_id,
            browser_session_id=item.browser_session_id,
            execution_id=item.application_field_execution_id,
            analysis_id=item.analysis_id,
            analysis_version=item.analysis_version,
            field_id=item.field_id,
            field_label=item.field_label,
            field_category=item.field_category,
            required=item.required,
            reason=item.reason,
            intervention_type=item.intervention_type,
            status=item.status,
            resolution_reason=item.resolution_reason,
            created_at=item.created_at,
            updated_at=item.updated_at,
            user_working_at=item.user_working_at,
            resolved_at=item.resolved_at,
            cancelled_at=item.cancelled_at,
            expired_at=item.expired_at,
        )

    async def _owned_application(
        self, user: User, application_id: uuid.UUID
    ) -> Application:
        return await self.applications.get_application(user, application_id)

    async def _execution_row(
        self, application_id: uuid.UUID, user_id: uuid.UUID
    ) -> ApplicationFieldExecution | None:
        return await self.session.scalar(
            select(ApplicationFieldExecution).where(
                ApplicationFieldExecution.application_id == application_id,
                ApplicationFieldExecution.user_id == user_id,
            )
        )

    async def _latest_analysis_row(
        self, application_id: uuid.UUID, user_id: uuid.UUID
    ) -> ApplicationFormAnalysis | None:
        return await self.session.scalar(
            select(ApplicationFormAnalysis)
            .where(
                ApplicationFormAnalysis.application_id == application_id,
                ApplicationFormAnalysis.user_id == user_id,
            )
            .order_by(ApplicationFormAnalysis.version.desc())
            .limit(1)
        )

    async def _maybe_session(
        self, user: User, application_id: uuid.UUID
    ) -> BrowserSession | None:
        try:
            return await self.browser_sessions.get_session(user, application_id)
        except BrowserSessionNotFoundError:
            return None

    async def _item(
        self, application_id: uuid.UUID, user_id: uuid.UUID, intervention_id: uuid.UUID
    ) -> ApplicationIntervention:
        item = await self.session.scalar(
            select(ApplicationIntervention).where(
                ApplicationIntervention.id == intervention_id,
                ApplicationIntervention.application_id == application_id,
                ApplicationIntervention.user_id == user_id,
            )
        )
        if item is None:
            raise InterventionNotFoundError()
        return item

    async def _all_items(
        self, application_id: uuid.UUID, user_id: uuid.UUID
    ) -> list[ApplicationIntervention]:
        rows = await self.session.scalars(
            select(ApplicationIntervention)
            .where(
                ApplicationIntervention.application_id == application_id,
                ApplicationIntervention.user_id == user_id,
            )
            .order_by(
                ApplicationIntervention.created_at.asc(),
                ApplicationIntervention.id.asc(),
            )
        )
        return list(rows)

    async def _active_items(
        self, application_id: uuid.UUID, user_id: uuid.UUID
    ) -> list[ApplicationIntervention]:
        rows = await self.session.scalars(
            select(ApplicationIntervention).where(
                ApplicationIntervention.application_id == application_id,
                ApplicationIntervention.user_id == user_id,
                ApplicationIntervention.status.in_(_ACTIVE_STATUSES),
            )
        )
        return list(rows)


__all__ = [
    "ApplicationHumanLoopService",
    "InterventionConflictError",
    "InterventionNotFoundError",
]
