"""Application workflow: analyse, prepare, approve, submit and track.

The service enforces the guarantees of Phase 2:

* nothing is submitted without an explicit, current user approval;
* a change to any answer or to the selected resume invalidates that approval;
* unsupported platforms never pretend to have submitted anything;
* passwords, OTPs, MFA codes, CAPTCHA answers and session cookies are never
  read, stored or logged. When a human step is required the application moves
  to ``manual_required`` instead.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import tempfile
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.exceptions import AppError, ResourceNotFoundError
from app.core.logging import get_logger
from app.integrations.browser.playwright_driver import PlaywrightBrowser
from app.integrations.browser.session import BrowserUnavailableError, ManualInterventionRequired
from app.models.application import (
    AnswerSource,
    Application,
    ApplicationQuestion,
    ApplicationStage,
    ApplicationStatus,
    AutomationMode,
    QuestionStatus,
    QuestionType,
)
from app.models.application_answer import AnswerType, VerificationStatus
from app.models.candidate import Candidate
from app.models.job import Job
from app.models.resume import Resume
from app.models.user import User
from app.repositories.candidate_repository import CandidateRepository
from app.schemas.application_answer import ApplicationAnswerUpdate
from app.schemas.candidate import CandidateUpdate
from app.services.application_adapters import (
    ApplicationPlatformAdapter,
    adapter_for_url,
)
from app.services.application_questionnaire import (
    SECTION_LABELS,
    SECTION_ORDER,
    QuestionDefinition,
    apply_resume_selection,
    build_questionnaire,
)
from app.services.candidate_service import CandidateService
from app.services.job_service import JobService
from app.services.matching_service import MatchingService
from app.services.resume_service import ResumeService
from app.services.resume_storage import get_resume_storage

logger = get_logger(__name__)

TERMINAL_STATUSES = {ApplicationStatus.SUBMITTED}
EDITABLE_STATUSES = {
    ApplicationStatus.DRAFT,
    ApplicationStatus.NEEDS_INFORMATION,
    ApplicationStatus.READY_FOR_REVIEW,
    ApplicationStatus.APPROVED,
    ApplicationStatus.FAILED,
}

MANUAL_STEPS: tuple[str, ...] = (
    "Open the employer's application page in your browser.",
    "Sign in or create the company account yourself. ApplyPilot never stores or "
    "types your company password.",
    "Attach the resume selected on this page. ApplyPilot never modifies the PDF.",
    "Copy each confirmed answer from the preview into the form.",
    "Complete any CAPTCHA, OTP, email verification or MFA step yourself.",
    "Submit the form, then mark this application as submitted so its status is tracked.",
)


class ApplicationNotFoundError(ResourceNotFoundError):
    """The application does not exist for this user."""

    code = "application_not_found"
    message = "Application not found."


class ApplicationStateError(AppError):
    """The requested action conflicts with the application's current state."""

    status_code = 409
    code = "application_state_conflict"
    message = "This action is not allowed for the application's current state."


@dataclass(frozen=True)
class SubmissionOutcome:
    """Result returned to the API after a submission attempt."""

    submitted: bool
    manual_required: bool
    message: str
    reference: str | None = None
    steps: tuple[str, ...] = ()


def approval_fingerprint(
    application: Application, questions: list[ApplicationQuestion]
) -> str:
    """Digest of everything the user approved; any change invalidates it."""
    payload = {
        "platform": application.platform,
        "application_url": application.application_url,
        "resume_id": str(application.resume_id or ""),
        "resume_filename": application.resume_filename or "",
        "answers": sorted(
            (question.question_key, question.answer or "")
            for question in questions
            if question.status is QuestionStatus.CONFIRMED
        ),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _answer_type_for(field_type: QuestionType) -> AnswerType:
    return {
        QuestionType.URL: AnswerType.URL,
        QuestionType.NUMBER: AnswerType.NUMBER,
        QuestionType.BOOLEAN: AnswerType.BOOLEAN,
        QuestionType.DECLARATION: AnswerType.BOOLEAN,
        QuestionType.DATE: AnswerType.DATE,
        QuestionType.SINGLE_CHOICE: AnswerType.SINGLE_CHOICE,
        QuestionType.MULTI_CHOICE: AnswerType.MULTI_CHOICE,
    }.get(field_type, AnswerType.TEXT)


class ApplicationService:
    """Coordinates the application lifecycle for one user."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.jobs = JobService(session)
        self.matching = MatchingService(session)
        self.resumes = ResumeService(session, get_resume_storage())
        self.candidates = CandidateService(CandidateRepository(session))

    # --- Reads ---------------------------------------------------------------
    async def list_applications(self, user: User) -> list[Application]:
        rows = await self.session.scalars(
            select(Application)
            .where(Application.user_id == user.id)
            .options(
                selectinload(Application.job),
                selectinload(Application.questions),
                selectinload(Application.resume),
            )
            .order_by(Application.created_at.desc())
        )
        return list(rows.all())

    async def get_application(self, user: User, application_id: UUID) -> Application:
        application = await self.session.scalar(
            select(Application)
            .where(Application.id == application_id, Application.user_id == user.id)
            .options(
                selectinload(Application.job),
                selectinload(Application.questions),
                selectinload(Application.resume),
                selectinload(Application.candidate),
            )
        )
        if application is None:
            raise ApplicationNotFoundError()
        return application

    async def _find_for_job(self, user: User, job_id: UUID) -> Application | None:
        return await self.session.scalar(
            select(Application)
            .where(Application.user_id == user.id, Application.job_id == job_id)
            .options(selectinload(Application.questions))
        )

    # --- Preparation ---------------------------------------------------------
    async def analyze_job(
        self,
        user: User,
        job_id: UUID,
        *,
        application_url: str | None = None,
        resume_id: UUID | None = None,
    ) -> Application:
        """Prepare (or refresh) a draft application for one owned job."""
        job = await self.jobs.get_job(user, job_id)
        existing = await self._find_for_job(user, job_id)
        if existing is not None and existing.status in TERMINAL_STATUSES:
            raise ApplicationStateError(
                "This application was already submitted and cannot be re-analysed."
            )

        profile = await self.candidates.ensure_profile_for_user(user)
        target_url = application_url or (existing.application_url if existing else job.job_url)
        adapter, detection = adapter_for_url(target_url)
        inspection, inspection_note = await self._inspect(adapter, detection, user)

        resume, resume_reason, resume_score = await self._select_resume(
            user, job, resume_id=resume_id
        )
        approved = {
            answer.question_key.lower(): answer.answer
            for answer in (profile.application_answers or [])
            if answer.verification_status is VerificationStatus.VERIFIED and answer.answer
        }
        definitions = build_questionnaire(
            candidate=profile,
            user=user,
            resume=resume,
            approved_answers=approved,
            inspection=inspection,
        )
        definitions = apply_resume_selection(
            definitions,
            resume=resume,
            reason=resume_reason,
            score=resume_score,
        )

        automation_available = self._automation_available(adapter, detection)
        mode = (
            AutomationMode.AUTOMATED
            if automation_available
            else AutomationMode.ASSISTED
            if adapter.supports_automation
            else AutomationMode.MANUAL
        )

        notes = list(inspection.notes)
        if inspection_note:
            notes.append(inspection_note)
        if detection.is_job_board_link:
            notes.append(
                "Add the employer's own application URL to prepare this application."
            )
        if adapter.supports_automation and not automation_available:
            notes.append(
                "Browser automation is disabled, so this application is prepared and "
                "submitted by you through the guided workflow."
            )

        if existing is None:
            application = Application(id=uuid.uuid4(), user_id=user.id, job_id=job.id)
            self.session.add(application)
            current_questions: list[ApplicationQuestion] = []
        else:
            application = existing
            current_questions = list(existing.questions)

        application.candidate_id = profile.id
        application.platform = detection.platform.value
        application.platform_name = detection.display_name
        application.application_url = detection.application_url
        application.automation_mode = mode
        application.inspection_mode = inspection.mode
        application.requires_account = bool(inspection.requires_account)
        application.notes = notes
        application.resume_id = resume.id if resume else None
        application.resume_reason = resume_reason
        application.resume_score = resume_score
        application.resume_filename = resume.original_filename if resume else None
        self._invalidate_approval(application)

        questions = await self._sync_questions(application, definitions, current_questions)
        self._refresh_completion(application, questions)
        await self.session.commit()

        logger.info(
            "Application prepared",
            extra={
                "stage": "application_analyze",
                "user_id": str(user.id),
                "application_id": str(application.id),
                "platform": application.platform,
                "automation_mode": application.automation_mode.value,
                "questions": len(questions),
            },
        )
        return await self.get_application(user, application.id)

    async def set_application_url(
        self, user: User, application_id: UUID, application_url: str
    ) -> Application:
        """Re-inspect an application after the user supplies the employer URL."""
        application = await self.get_application(user, application_id)
        return await self.analyze_job(
            user,
            application.job_id,
            application_url=application_url,
        )

    async def _inspect(
        self,
        adapter: ApplicationPlatformAdapter,
        detection: Any,
        user: User,
    ) -> tuple[Any, str | None]:
        """Inspect the live form when allowed, otherwise use declared questions."""
        if not (
            settings.APPLICATION_AUTOMATION_ENABLED
            and adapter.supports_automation
            and not detection.is_job_board_link
        ):
            return await adapter.inspect_application(detection.application_url), None

        try:
            async with PlaywrightBrowser(settings).open(
                detection.application_url, user_id=str(user.id)
            ) as session:
                inspection = await adapter.inspect_application(
                    detection.application_url,
                    session,
                    max_fields=settings.APPLICATION_MAX_FORM_FIELDS,
                )
            return inspection, None
        except BrowserUnavailableError:
            note = (
                "Browser automation could not start. The standard question set was "
                "used instead of claiming the live form was inspected."
            )
        except ManualInterventionRequired as exc:
            note = f"{exc.reason} The standard question set was used instead."
        except Exception as exc:  # pragma: no cover - depends on a live page
            logger.warning(
                "Application inspection failed",
                extra={"stage": "application_inspect", "error_class": type(exc).__name__},
            )
            note = (
                "The live form could not be inspected. The standard question set was "
                "used instead; confirm fields on the employer's page."
            )
        return await adapter.inspect_application(detection.application_url), note

    def _automation_available(self, adapter: ApplicationPlatformAdapter, detection: Any) -> bool:
        return bool(
            settings.APPLICATION_AUTOMATION_ENABLED
            and adapter.supports_automation
            and not detection.is_job_board_link
        )

    async def _select_resume(
        self, user: User, job: Job, *, resume_id: UUID | None
    ) -> tuple[Resume | None, str, float | None]:
        """Pick the best-matching resume, unless the user chose one explicitly."""
        if resume_id is not None:
            resume = await self.resumes.get_resume(user, resume_id)
            return resume, "Resume selected manually by you for this application.", None

        await self.matching.ensure_job_matches(user, job)
        rows = await self.matching.get_job_matches(user, job)
        if not rows:
            return (
                None,
                "No PDF resume is available. Upload one to include it in this application.",
                None,
            )
        best = rows[0]
        filename = best.resume.original_filename if best.resume else "resume.pdf"
        reason = (
            f"Recommended by the matching engine: {best.overall_score}% compatibility "
            f"for {filename}. {best.explanation}"
        ).strip()
        return best.resume, reason, best.overall_score

    async def _sync_questions(
        self,
        application: Application,
        definitions: list[QuestionDefinition],
        current_questions: list[ApplicationQuestion],
    ) -> list[ApplicationQuestion]:
        existing = {question.question_key: question for question in current_questions}
        rows = list(current_questions)
        keep: set[str] = set()
        for index, definition in enumerate(definitions):
            keep.add(definition.question_key)
            question = existing.get(definition.question_key)
            if question is None:
                question = ApplicationQuestion(
                    id=uuid.uuid4(),
                    application_id=application.id,
                    question_key=definition.question_key,
                )
                self.session.add(question)
                rows.append(question)

            question.field_identifier = definition.field_identifier
            question.section = definition.section
            question.question_text = definition.question_text
            question.help_text = definition.help_text
            question.field_type = definition.field_type
            question.required = definition.required
            question.options = list(definition.options)
            question.is_sensitive = definition.is_sensitive
            question.is_legal = definition.is_legal
            question.order_index = index

            user_confirmed = (
                existing.get(definition.question_key) is question
                and question.status is QuestionStatus.CONFIRMED
                and bool(question.answer)
                and definition.question_key != "documents.resume"
            )
            if user_confirmed:
                # Keep the user's own confirmed answer across re-analysis.
                question.answer_source = AnswerSource.USER_INPUT
                question.confidence = 1.0
                question.requires_confirmation = False
                continue

            question.suggested_answer = definition.answer
            question.answer = definition.answer
            question.answer_source = definition.answer_source
            question.confidence = definition.confidence
            question.requires_confirmation = definition.requires_confirmation
            question.status = definition.status
            question.answered_at = (
                _utcnow() if definition.status is QuestionStatus.CONFIRMED else None
            )

        for key, question in existing.items():
            if key not in keep:
                await self.session.delete(question)
                rows.remove(question)
        return rows

    def _invalidate_approval(self, application: Application) -> None:
        application.approval_fingerprint = None
        application.approved_at = None

    def _refresh_completion(
        self,
        application: Application,
        questions: list[ApplicationQuestion] | None = None,
    ) -> None:
        questions = questions if questions is not None else list(application.questions)
        missing = [
            question.question_key
            for question in questions
            if question.required
            and (question.status is not QuestionStatus.CONFIRMED or not question.answer)
        ]
        if application.resume_id is None and "documents.resume" not in missing:
            missing.append("documents.resume")
        application.missing_fields = sorted(set(missing))
        if application.status in {ApplicationStatus.SUBMITTED, ApplicationStatus.MANUAL_REQUIRED}:
            return
        application.status = (
            ApplicationStatus.READY_FOR_REVIEW
            if not application.missing_fields
            else ApplicationStatus.NEEDS_INFORMATION
        )

    # --- Answering -----------------------------------------------------------
    async def update_question(
        self,
        user: User,
        application_id: UUID,
        question_id: UUID,
        *,
        answer: str | None,
        confirmed: bool,
        note: str | None = None,
    ) -> Application:
        application = await self.get_application(user, application_id)
        if application.status not in EDITABLE_STATUSES:
            raise ApplicationStateError(
                "This application has already been submitted and can no longer be edited."
            )
        question = next(
            (item for item in application.questions if item.id == question_id), None
        )
        if question is None:
            raise ResourceNotFoundError("Application question was not found.")

        cleaned = (answer or "").strip()
        if note is not None:
            question.note = note.strip()[:2000] or None
        question.answer = cleaned or None
        question.answered_at = _utcnow() if cleaned and confirmed else None
        if cleaned and confirmed:
            question.status = QuestionStatus.CONFIRMED
            question.requires_confirmation = False
            question.answer_source = AnswerSource.USER_INPUT
            question.confidence = 1.0
        elif cleaned:
            question.status = QuestionStatus.SUGGESTED
            question.requires_confirmation = True
        else:
            question.status = QuestionStatus.MISSING
            question.requires_confirmation = True

        self._invalidate_approval(application)
        self._refresh_completion(application)

        if cleaned and confirmed:
            await self._remember_answer(user, application, question, cleaned)
        await self.session.commit()
        await self.session.refresh(application)
        return await self.get_application(user, application.id)

    async def _remember_answer(
        self,
        user: User,
        application: Application,
        question: ApplicationQuestion,
        value: str,
    ) -> None:
        """Save a confirmed answer for reuse and update safe profile fields."""
        if application.candidate_id is None:
            return
        if not question.is_legal and question.field_type is not QuestionType.FILE:
            payload = ApplicationAnswerUpdate(
                question_text=question.question_text,
                answer=value,
                answer_type=_answer_type_for(question.field_type),
                verification_status=VerificationStatus.VERIFIED,
            )
            try:
                await self.candidates.upsert_answer(
                    application.candidate_id, question.question_key, payload
                )
            except AppError:
                logger.warning(
                    "Reusable answer could not be stored",
                    extra={"stage": "application_answer_reuse"},
                )
        await self._persist_profile_value(user, application, question.question_key, value)

    async def _persist_profile_value(
        self, user: User, application: Application, key: str, value: str
    ) -> None:
        """Write confirmed, unambiguous values back to the candidate profile.

        Only fields whose stored shape matches the answer are updated; salary,
        phone and address composition are intentionally left to the approved
        answer store to avoid corrupting curated profile data.
        """
        profile = await self.session.get(Candidate, application.candidate_id)
        if profile is None:
            return
        updates: dict[str, Any] = {}
        simple = {
            "personal.full_name": "full_name",
            "personal.preferred_name": "preferred_name",
            "address.line1": "address_line1",
            "address.line2": "address_line2",
            "address.city": "city",
            "address.region": "region",
            "address.postal_code": "postal_code",
            "address.country": "country",
            "authorization.status": "work_authorization",
        }
        if key in simple:
            updates[simple[key]] = value[:500]
        elif key == "authorization.sponsorship":
            lowered = value.strip().casefold()
            if lowered in {"yes", "true", "1"}:
                updates["requires_sponsorship"] = True
            elif lowered in {"no", "false", "0"}:
                updates["requires_sponsorship"] = False
        elif key == "availability.notice_period":
            if value.isdigit():
                updates["notice_period_days"] = min(int(value), 365)
        elif key == "availability.available_from":
            try:
                updates["available_from"] = date.fromisoformat(value[:10])
            except ValueError:
                return
        if updates:
            await self.candidates.update_profile_for_user(
                user=user, payload=CandidateUpdate(**updates)
            )

    async def select_resume(
        self, user: User, application_id: UUID, resume_id: UUID
    ) -> Application:
        application = await self.get_application(user, application_id)
        if application.status not in EDITABLE_STATUSES:
            raise ApplicationStateError(
                "The resume cannot be changed after the application is submitted."
            )
        resume = await self.resumes.get_resume(user, resume_id)
        application.resume_id = resume.id
        application.resume_filename = resume.original_filename
        application.resume_reason = "Resume selected manually by you for this application."
        application.resume_score = None

        for question in application.questions:
            if question.question_key != "documents.resume":
                continue
            question.answer = resume.original_filename
            question.suggested_answer = resume.original_filename
            question.answer_source = AnswerSource.RESUME
            question.confidence = 1.0
            question.status = QuestionStatus.CONFIRMED
            question.requires_confirmation = False
            question.answered_at = _utcnow()

        self._invalidate_approval(application)
        self._refresh_completion(application)
        await self.session.commit()
        return await self.get_application(user, application.id)

    # --- Preview / approval --------------------------------------------------
    def preview_payload(self, application: Application) -> dict[str, Any]:
        questions = list(application.questions)
        confirmed = [item for item in questions if item.status is QuestionStatus.CONFIRMED]
        required = [item for item in questions if item.required]
        missing = [item for item in required if item not in confirmed or not item.answer]
        sections: list[dict[str, Any]] = []
        for section in SECTION_ORDER:
            rows = [item for item in questions if item.section == section]
            if not rows:
                continue
            sections.append(
                {
                    "section": section,
                    "label": SECTION_LABELS.get(section, section.title()),
                    "questions": [self._question_payload(item) for item in rows],
                }
            )
        manual = application.automation_mode is not AutomationMode.AUTOMATED
        ready_to_approve = not missing and application.resume_id is not None
        return {
            "application_id": application.id,
            "job_id": application.job_id,
            "job_title": application.job.job_title if application.job else "",
            "company": application.job.company if application.job else None,
            "platform": application.platform,
            "platform_name": application.platform_name,
            "application_url": application.application_url,
            "automation_mode": application.automation_mode.value,
            "inspection_mode": application.inspection_mode.value,
            "status": application.status.value,
            "stage": application.stage.value if application.stage else None,
            "approved_at": application.approved_at,
            "submitted_at": application.submitted_at,
            "submission_reference": application.submission_reference,
            "failure_reason": application.failure_reason,
            "requires_account": application.requires_account,
            "notes": list(application.notes or []),
            "resume": {
                "id": application.resume_id,
                "filename": application.resume_filename,
                "reason": application.resume_reason,
                "score": application.resume_score,
            },
            "sections": sections,
            "missing_required": [
                {"question_key": item.question_key, "question_text": item.question_text}
                for item in missing
            ],
            "progress": {
                "total": len(questions),
                "confirmed": len(confirmed),
                "required_total": len(required),
                "required_confirmed": len(required) - len(missing),
                "percent": round((len(confirmed) / len(questions)) * 100) if questions else 0,
            },
            "ready_to_approve": ready_to_approve,
            "can_submit": bool(
                application.status is ApplicationStatus.APPROVED
                and application.approval_fingerprint
                and application.approval_fingerprint
                == approval_fingerprint(application, questions)
            ),
            "submission_mode": "manual" if manual else "automated",
            "manual_steps": list(MANUAL_STEPS) if manual else [],
            "warnings": self._warnings(application, missing),
        }

    def _question_payload(self, question: ApplicationQuestion) -> dict[str, Any]:
        return {
            "id": question.id,
            "question_key": question.question_key,
            "field_identifier": question.field_identifier,
            "section": question.section,
            "question_text": question.question_text,
            "help_text": question.help_text,
            "field_type": question.field_type.value,
            "required": question.required,
            "options": list(question.options or []),
            "suggested_answer": question.suggested_answer,
            "answer": question.answer,
            "answer_source": question.answer_source.value,
            "confidence": question.confidence,
            "requires_confirmation": question.requires_confirmation,
            "is_sensitive": question.is_sensitive,
            "is_legal": question.is_legal,
            "status": question.status.value,
            "note": question.note,
        }

    def _warnings(
        self, application: Application, missing: list[ApplicationQuestion]
    ) -> list[str]:
        warnings: list[str] = []
        if application.requires_account:
            warnings.append(
                "This employer may require an account. Create it yourself; ApplyPilot "
                "never stores company passwords or completes verification for you."
            )
        if application.automation_mode is not AutomationMode.AUTOMATED:
            warnings.append(
                "ApplyPilot will not submit this form. You will complete the final "
                "submission on the employer's site."
            )
        if missing:
            warnings.append(
                f"{len(missing)} required question"
                f"{'s are' if len(missing) != 1 else ' is'} still unanswered."
            )
        if any(question.is_legal for question in application.questions):
            warnings.append(
                "Legal declarations must be read and confirmed by you; they are never "
                "auto-filled."
            )
        return warnings

    async def approve(self, user: User, application_id: UUID, *, approved: bool) -> Application:
        application = await self.get_application(user, application_id)
        if application.status in {ApplicationStatus.SUBMITTED, ApplicationStatus.MANUAL_REQUIRED}:
            raise ApplicationStateError("This application can no longer be approved.")
        if not approved:
            self._invalidate_approval(application)
            self._refresh_completion(application)
            await self.session.commit()
            return await self.get_application(user, application.id)

        self._refresh_completion(application)
        if application.missing_fields:
            raise ApplicationStateError(
                "Answer every required question and select a resume before approving."
            )
        if application.resume_id is None:
            raise ApplicationStateError("Select a resume before approving the application.")
        if application.status is ApplicationStatus.FAILED:
            raise ApplicationStateError("A failed application cannot be approved.")

        application.approved_at = _utcnow()
        application.approval_fingerprint = approval_fingerprint(
            application, list(application.questions)
        )
        application.status = ApplicationStatus.APPROVED
        await self.session.commit()
        logger.info(
            "Application approved",
            extra={
                "stage": "application_approve",
                "user_id": str(user.id),
                "application_id": str(application.id),
            },
        )
        return await self.get_application(user, application.id)

    # --- Submission ----------------------------------------------------------
    async def submit(self, user: User, application_id: UUID) -> SubmissionOutcome:
        application = await self.get_application(user, application_id)
        if application.status in {ApplicationStatus.SUBMITTED, ApplicationStatus.MANUAL_REQUIRED}:
            raise ApplicationStateError("This application has already been processed.")
        if application.status is not ApplicationStatus.APPROVED:
            raise ApplicationStateError(
                "Explicit approval is required before an application can be submitted.",
                code="approval_required",
            )
        current = approval_fingerprint(application, list(application.questions))
        if application.approval_fingerprint != current:
            self._invalidate_approval(application)
            self._refresh_completion(application)
            await self.session.commit()
            raise ApplicationStateError(
                "The application changed after it was approved. Review and approve it again.",
                code="approval_stale",
            )

        if application.automation_mode is not AutomationMode.AUTOMATED:
            return await self._finish_manual(application, reason=None)

        try:
            return await self._submit_automated(user, application)
        except BrowserUnavailableError as exc:
            return await self._finish_manual(application, reason=str(exc))
        except ManualInterventionRequired as exc:
            logger.info(
                "Manual intervention required during submission",
                extra={
                    "stage": "application_submit",
                    "application_id": str(application.id),
                    "reason_code": exc.step,
                },
            )
            return await self._finish_manual(application, reason=exc.reason)
        except ApplicationStateError:
            raise
        except Exception as exc:  # pragma: no cover - live browser failures
            logger.warning(
                "Application submission failed",
                extra={
                    "stage": "application_submit",
                    "application_id": str(application.id),
                    "error_class": type(exc).__name__,
                },
            )
            application.status = ApplicationStatus.FAILED
            application.failure_reason = (
                "Automation could not complete the submission. No application was "
                "confirmed by the employer; finish it manually."
            )
            application.automation_details = {"error_class": type(exc).__name__}
            await self.session.commit()
            raise ApplicationStateError(
                "The automated submission failed. Complete the application manually.",
                code="submission_failed",
            ) from exc

    async def _submit_automated(
        self, user: User, application: Application
    ) -> SubmissionOutcome:
        adapter, detection = adapter_for_url(application.application_url)
        if not adapter.supports_automation:
            return await self._finish_manual(application, reason=None)

        _, data = await self.resumes.read_file(user, application.resume_id)
        extension = ".pdf"
        handle, temp_path = tempfile.mkstemp(prefix="applypilot-", suffix=extension)
        os.close(handle)
        try:
            await asyncio.to_thread(Path(temp_path).write_bytes, data)
            async with PlaywrightBrowser(settings).open(
                detection.application_url, user_id=str(user.id)
            ) as session:
                inspection = await adapter.inspect_application(
                    detection.application_url,
                    session,
                    max_fields=settings.APPLICATION_MAX_FORM_FIELDS,
                )
                fields = adapter.extract_form_fields(inspection)
                values = {
                    question.question_key: question.answer or ""
                    for question in application.questions
                    if question.status is QuestionStatus.CONFIRMED and question.answer
                }
                fill = await adapter.fill_supported_fields(session, fields, values)
                upload = await adapter.upload_resume(
                    session, temp_path, application.resume_filename
                )
                validation = await adapter.validate_application(session)
                if not validation.valid:
                    application.status = ApplicationStatus.FAILED
                    application.failure_reason = (
                        "The employer's form reported validation errors. Review and "
                        "submit it manually."
                    )
                    application.automation_details = {
                        "validation_errors": list(validation.errors)[:5],
                        "filled_fields": len(fill.filled),
                    }
                    await self.session.commit()
                    raise ApplicationStateError(
                        "The form reported validation errors. Complete it manually.",
                        code="validation_failed",
                    )
                report = await adapter.submit_application(session)
        finally:
            with contextlib.suppress(OSError):
                os.unlink(temp_path)

        if not report.submitted:
            return await self._finish_manual(application, reason=report.reason)

        application.status = ApplicationStatus.SUBMITTED
        application.stage = ApplicationStage.APPLIED
        application.submitted_at = _utcnow()
        application.submission_reference = report.reference
        application.failure_reason = None
        application.automation_details = {
            "filled_fields": len(fill.filled),
            "resume_uploaded": upload.uploaded,
            "evidence": report.evidence,
        }
        await self.session.commit()
        return SubmissionOutcome(
            submitted=True,
            manual_required=False,
            message="The employer's confirmation was detected. Application submitted.",
            reference=report.reference,
        )

    async def _finish_manual(
        self, application: Application, *, reason: str | None
    ) -> SubmissionOutcome:
        application.status = ApplicationStatus.MANUAL_REQUIRED
        application.submitted_at = None
        application.submission_reference = None
        application.stage = None
        application.failure_reason = None
        if reason:
            application.automation_details = {"manual_reason": reason}
            notes = list(application.notes or [])
            if reason not in notes:
                notes.append(reason)
            application.notes = notes
        await self.session.commit()
        return SubmissionOutcome(
            submitted=False,
            manual_required=True,
            message=(
                "No application was submitted by ApplyPilot. Complete the final "
                "submission yourself, then mark it as submitted here."
            ),
            steps=MANUAL_STEPS,
        )

    async def confirm_manual_submission(
        self, user: User, application_id: UUID, *, reference: str | None
    ) -> Application:
        application = await self.get_application(user, application_id)
        if application.status is not ApplicationStatus.MANUAL_REQUIRED:
            raise ApplicationStateError(
                "Only an application awaiting manual submission can be confirmed."
            )
        application.status = ApplicationStatus.SUBMITTED
        application.stage = ApplicationStage.APPLIED
        application.submitted_at = _utcnow()
        application.submission_reference = (reference or "").strip()[:255] or None
        await self.session.commit()
        return await self.get_application(user, application.id)

    async def update_stage(
        self, user: User, application_id: UUID, stage: ApplicationStage
    ) -> Application:
        application = await self.get_application(user, application_id)
        if application.submitted_at is None:
            raise ApplicationStateError(
                "Track an application only after it has actually been submitted."
            )
        application.stage = stage
        await self.session.commit()
        return await self.get_application(user, application.id)


__all__ = [
    "MANUAL_STEPS",
    "ApplicationNotFoundError",
    "ApplicationService",
    "ApplicationStateError",
    "SubmissionOutcome",
    "approval_fingerprint",
]
