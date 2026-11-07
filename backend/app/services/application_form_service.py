"""Phase 2E Stage B: analyze, persist and retrieve application form fields.

The service bridges the Stage A live browser session and the pure Stage B
analyzer. It reads the current page through the narrow ``content()`` contract,
reuses the Phase 2B/2C candidate profile, resume and answer bank for source
suggestions, and stores only non-secret detection metadata.

It never fills, clicks or submits anything. An analysis is an immutable version
per application so a re-analysis of a multi-step or dynamic form cannot
overwrite what the user was previously shown.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.exceptions import AppError, InvalidOperationError, ResourceNotFoundError
from app.core.logging import get_logger
from app.models.application import QuestionStatus
from app.models.application_answer import VerificationStatus
from app.models.application_form_analysis import ApplicationFormAnalysis
from app.models.user import User
from app.repositories.candidate_repository import CandidateRepository
from app.schemas.application_form import (
    ApplicationFormAnalysisResponse,
    ApplicationFormFieldsResponse,
    DetectedFormField,
)
from app.services.application_form_analyzer import (
    ApplicationFormAnalyzer,
    FormAnalysisResult,
    FormPageUnavailableError,
)
from app.services.application_service import ApplicationService
from app.services.application_url_security import sanitize_url_for_display
from app.services.browser_session_service import (
    FINALIZED_APPLICATION_STATES,
    BrowserSessionService,
)
from app.services.candidate_service import CandidateService

logger = get_logger(__name__)


class FormAnalysisNotFoundError(ResourceNotFoundError):
    """No form analysis exists for this application and user."""

    code = "form_analysis_not_found"
    message = "No form analysis exists for this application yet."


class FormAnalysisUnavailableError(AppError):
    """The live employer page could not be read safely."""

    status_code = 503
    code = "form_analysis_unavailable"
    message = "The employer's application page could not be read safely."


class FormAnalysisConflictError(AppError):
    """Another analysis version was stored concurrently."""

    status_code = 409
    code = "form_analysis_conflict"
    message = "Another form analysis was completed at the same time. Reload the latest analysis."


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ApplicationFormService:
    """Analyze the current employer page for one owned application."""

    def __init__(
        self,
        session: Any,
        *,
        analyzer: ApplicationFormAnalyzer | None = None,
        browser_sessions: BrowserSessionService | None = None,
    ) -> None:
        self.session = session
        self.applications = ApplicationService(session)
        self.browser_sessions = browser_sessions or BrowserSessionService(session)
        self.analyzer = analyzer or ApplicationFormAnalyzer(
            max_fields=settings.APPLICATION_MAX_FORM_FIELDS
        )
        self.candidates = CandidateService(CandidateRepository(session))

    # --- Analysis ------------------------------------------------------------
    async def analyze(
        self, user: User, application_id: uuid.UUID
    ) -> ApplicationFormAnalysisResponse:
        """Inspect the current page and store a new immutable version."""
        application = await self.applications.get_application(user, application_id)
        if application.status in FINALIZED_APPLICATION_STATES:
            raise InvalidOperationError(
                "A form analysis cannot be run for a finished application.",
                code="application_finalized",
            )

        session_row, handle = await self.browser_sessions.require_live_handle(
            user, application_id
        )
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

        try:
            result = await self.analyzer.analyze_page(
                handle,
                candidate=profile,
                resume=application.resume,
                reusable_answers=reusable_answers,
                user_answers=user_answers,
            )
        except FormPageUnavailableError as exc:
            raise FormAnalysisUnavailableError() from exc
        except Exception as exc:  # pragma: no cover - depends on a live page
            logger.warning(
                "Application form analysis failed",
                extra={
                    "stage": "application_form_analyze",
                    "application_id": str(application.id),
                    "error_class": type(exc).__name__,
                },
            )
            raise FormAnalysisUnavailableError() from exc

        page_url = sanitize_url_for_display(await handle.current_url())
        now = _utcnow()
        version = await self._next_version(application.id)
        fields = [field.model_dump(mode="json") for field in result.fields]
        summary = self._summary(result)
        row = ApplicationFormAnalysis(
            id=uuid.uuid4(),
            user_id=user.id,
            application_id=application.id,
            browser_session_id=session_row.id,
            version=version,
            page_url=page_url or session_row.current_url,
            form_count=result.form_count,
            field_count=len(fields),
            summary=summary,
            fields=fields,
            analyzed_at=now,
        )
        self.session.add(row)

        # The checkpoint is a non-secret hand-off for later stages; no values,
        # HTML, cookies or secrets are written to it.
        session_row.checkpoint = {
            **(session_row.checkpoint or {}),
            "form_analysis": {
                "analysis_id": str(row.id),
                "version": version,
                "field_count": len(fields),
                "analyzed_at": now.isoformat(),
            },
        }
        session_row.last_activity_at = now
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise FormAnalysisConflictError() from exc

        await self.session.refresh(row)
        logger.info(
            "Application form analyzed",
            extra={
                "stage": "application_form_analyze",
                "user_id": str(user.id),
                "application_id": str(application.id),
                "version": version,
                "field_count": row.field_count,
                "human_fields": summary["requires_human_input_count"],
                "automation_eligible": summary["automation_eligible_count"],
            },
        )
        return self._response(row)

    # --- Reads ---------------------------------------------------------------
    async def latest(
        self, user: User, application_id: uuid.UUID
    ) -> ApplicationFormAnalysisResponse:
        """Return the newest stored analysis for one owned application."""
        application = await self.applications.get_application(user, application_id)
        row = await self._latest_row(application.id, user.id)
        if row is None:
            raise FormAnalysisNotFoundError()
        return self._response(row)

    async def latest_fields(
        self, user: User, application_id: uuid.UUID
    ) -> ApplicationFormFieldsResponse:
        """Return only the detected fields from the newest analysis."""
        application = await self.applications.get_application(user, application_id)
        row = await self._latest_row(application.id, user.id)
        if row is None:
            raise FormAnalysisNotFoundError()
        fields = [DetectedFormField.model_validate(item) for item in (row.fields or [])]
        return ApplicationFormFieldsResponse(
            analysis_id=row.id,
            version=row.version,
            page_url=row.page_url,
            analyzed_at=row.analyzed_at,
            total=len(fields),
            fields=fields,
        )

    # --- Helpers -------------------------------------------------------------
    async def _next_version(self, application_id: uuid.UUID) -> int:
        current = await self.session.scalar(
            select(func.max(ApplicationFormAnalysis.version)).where(
                ApplicationFormAnalysis.application_id == application_id
            )
        )
        return int(current or 0) + 1

    async def _latest_row(
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

    @staticmethod
    def _summary(result: FormAnalysisResult) -> dict[str, Any]:
        return {
            "visible_field_count": sum(1 for item in result.fields if item.visible),
            "requires_human_input_count": result.requires_human_input_count,
            "automation_eligible_count": result.automation_eligible_count,
            "captcha_detected": result.captcha_detected,
            "otp_detected": result.otp_detected,
            "mfa_detected": result.mfa_detected,
            "legal_declaration_count": result.legal_declaration_count,
            "sensitive_field_count": result.sensitive_field_count,
            "warnings": list(result.warnings),
        }

    @staticmethod
    def _response(row: ApplicationFormAnalysis) -> ApplicationFormAnalysisResponse:
        summary = dict(row.summary or {})
        fields = [DetectedFormField.model_validate(item) for item in (row.fields or [])]
        return ApplicationFormAnalysisResponse(
            analysis_id=row.id,
            application_id=row.application_id,
            browser_session_id=row.browser_session_id,
            version=row.version,
            page_url=row.page_url,
            analyzed_at=row.analyzed_at,
            form_count=row.form_count,
            field_count=row.field_count,
            visible_field_count=int(summary.get("visible_field_count", 0)),
            requires_human_input_count=int(
                summary.get("requires_human_input_count", 0)
            ),
            automation_eligible_count=int(summary.get("automation_eligible_count", 0)),
            captcha_detected=bool(summary.get("captcha_detected", False)),
            otp_detected=bool(summary.get("otp_detected", False)),
            mfa_detected=bool(summary.get("mfa_detected", False)),
            legal_declaration_count=int(summary.get("legal_declaration_count", 0)),
            sensitive_field_count=int(summary.get("sensitive_field_count", 0)),
            warnings=[str(item) for item in summary.get("warnings", [])],
            fields=fields,
        )


__all__ = [
    "ApplicationFormService",
    "FormAnalysisConflictError",
    "FormAnalysisNotFoundError",
    "FormAnalysisUnavailableError",
]
