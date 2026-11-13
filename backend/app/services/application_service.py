"""Application workflow: analyse, prepare, approve, hand off and track.

The service enforces the guarantees of Phase 2 and Stage G:

* ApplyPilot never performs the final submission and has no click/submit
  browser primitive; the user submits on the employer's page;
* an approved application only reaches a manual handoff, never ``SUBMITTED``;
* ``SUBMITTED`` is recorded solely by an explicit ``confirmed=true`` user
  confirmation with optional sanitised evidence;
* a change to any answer or to the selected resume invalidates that approval;
* passwords, OTPs, MFA codes, CAPTCHA answers and session cookies are never
  read, stored or logged. When a human step is required the application moves
  to ``needs_user_action`` instead.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.exceptions import AppError, InvalidOperationError, ResourceNotFoundError
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
    RedirectStatus,
)
from app.models.application_answer import (
    AnswerType,
    ApplicationAnswer,
    VerificationStatus,
)
from app.models.candidate import Candidate
from app.models.job import Job
from app.models.resume import Resume
from app.models.user import User
from app.repositories.candidate_repository import CandidateRepository
from app.schemas.application_answer import ApplicationAnswerUpdate
from app.schemas.candidate import CandidateUpdate
from app.schemas.preference import JobPreferenceUpdate
from app.services.answer_library import AnswerLibrary, ReuseSuggestion
from app.services.application_adapters import (
    ApplicationPlatformAdapter,
    adapter_for_url,
)
from app.services.application_platforms import (
    ApplicationUrlError,
    detect_platform,
    is_job_board_url,
    normalize_application_url,
    suggest_employer_application_url,
)
from app.services.application_questionnaire import (
    SECTION_LABELS,
    SECTION_ORDER,
    QuestionDefinition,
    apply_resume_selection,
    build_questionnaire,
    profile_answers,
)
from app.services.application_state import (
    EDITABLE_STATUSES,
    PROGRESSABLE_STATUSES,
    UNREANALYZABLE_STATUSES,
    ApplicationStateError,
    transition,
)
from app.services.application_url_security import (
    sanitize_url_for_display,
    validate_application_destination,
)
from app.services.candidate_service import CandidateService, is_forbidden_answer_storage
from app.services.job_service import JobService
from app.services.job_understanding_service import JobUnderstandingService
from app.services.resume_comparison_service import (
    RESUME_READY,
    ResumeComparisonService,
)
from app.services.resume_recommendation_service import RecommendationOutcome
from app.services.resume_service import ResumeService
from app.services.resume_storage import get_resume_storage

logger = get_logger(__name__)

#: Persisted ``resume_reason`` marker meaning the user explicitly chose the
#: resume. It is compared exactly so a later analysis never replaces that choice.
MANUAL_RESUME_SELECTION_REASON = (
    "Resume selected manually by you for this application."
)

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


@dataclass(frozen=True)
class SubmissionOutcome:
    """Result returned to the API after a submission attempt."""

    submitted: bool
    manual_required: bool
    message: str
    reference: str | None = None
    steps: tuple[str, ...] = ()


@dataclass(frozen=True)
class PipelineCounts:
    """Backend-computed application pipeline totals for the dashboard."""

    total: int
    in_progress: int
    submitted: int
    failed: int
    cancelled: int
    by_stage: dict[str, int]


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
        QuestionType.RADIO: AnswerType.SINGLE_CHOICE,
        QuestionType.MULTI_CHOICE: AnswerType.MULTI_CHOICE,
        QuestionType.CHECKBOX: AnswerType.MULTI_CHOICE,
    }.get(field_type, AnswerType.TEXT)


class ApplicationService:
    """Coordinates the application lifecycle for one user."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.jobs = JobService(session)
        self.resumes = ResumeService(session, get_resume_storage())
        self.candidates = CandidateService(CandidateRepository(session))

    # --- Reads ---------------------------------------------------------------
    async def pipeline_counts(self, user: User) -> PipelineCounts:
        """Aggregate the user's applications by stored status and stage.

        One grouped query per column keeps the dashboard free of N+1 reads and
        makes the stored columns the single source of truth for the pipeline.
        """
        status_rows = await self.session.execute(
            select(Application.status, func.count(Application.id))
            .where(Application.user_id == user.id)
            .group_by(Application.status)
        )
        by_status = {
            (status.value if hasattr(status, "value") else str(status)): int(count)
            for status, count in status_rows.all()
        }

        stage_rows = await self.session.execute(
            select(Application.stage, func.count(Application.id))
            .where(Application.user_id == user.id, Application.stage.is_not(None))
            .group_by(Application.stage)
        )
        by_stage = {stage.value: 0 for stage in ApplicationStage}
        for stage, count in stage_rows.all():
            by_stage[stage.value if hasattr(stage, "value") else str(stage)] = int(count)

        pre_submission = {
            ApplicationStatus.DRAFT,
            ApplicationStatus.ANALYZING,
            ApplicationStatus.AWAITING_USER_INFORMATION,
            ApplicationStatus.READY_FOR_REVIEW,
            ApplicationStatus.AWAITING_APPROVAL,
            ApplicationStatus.SUBMITTING,
            ApplicationStatus.NEEDS_USER_ACTION,
        }
        return PipelineCounts(
            total=sum(by_status.values()),
            in_progress=sum(
                count
                for status, count in by_status.items()
                if status in {value.value for value in pre_submission}
            ),
            submitted=by_status.get(ApplicationStatus.SUBMITTED.value, 0),
            failed=by_status.get(ApplicationStatus.FAILED.value, 0),
            cancelled=by_status.get(ApplicationStatus.CANCELLED.value, 0),
            by_stage=by_stage,
        )

    async def recent_applications(
        self, user: User, *, limit: int = 5
    ) -> list[Application]:
        """Most recently updated applications, with the rows `_summary` needs."""
        rows = await self.session.scalars(
            select(Application)
            .where(Application.user_id == user.id)
            .options(
                selectinload(Application.job),
                selectinload(Application.questions),
                selectinload(Application.resume),
            )
            .order_by(Application.updated_at.desc(), Application.created_at.desc())
            .limit(limit)
        )
        return list(rows.all())

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

    async def get_application(
        self, user: User, application_id: UUID, *, lock: bool = False
    ) -> Application:
        statement = (
            select(Application)
            .where(Application.id == application_id, Application.user_id == user.id)
            .options(
                selectinload(Application.job),
                selectinload(Application.questions),
                selectinload(Application.resume),
                selectinload(Application.candidate),
            )
        )
        if lock:
            # Serialises concurrent submissions of the same application.
            statement = statement.with_for_update()
        application = await self.session.scalar(statement)
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
        if existing is not None and existing.status in UNREANALYZABLE_STATUSES:
            raise ApplicationStateError(
                "This application was already submitted or handed over for manual "
                "submission and cannot be re-analysed. Cancel it and prepare a new "
                "application if you need to start over.",
                code="application_finalized",
            )

        profile = await self.candidates.ensure_profile_for_user(user)
        target_url = application_url or (existing.application_url if existing else job.job_url)
        adapter, detection = adapter_for_url(target_url)
        # A listing (for example Glassdoor) is a supported starting page for an
        # Easy Apply hand-off. Its original URL is never replaced by the form.
        listing_flow = detection.is_job_board_link
        keep_validated_redirect = bool(
            existing is not None and listing_flow and existing.destination_validated
        )

        if existing is None:
            application = Application(
                id=uuid.uuid4(),
                user_id=user.id,
                job_id=job.id,
                platform=detection.platform.value,
                platform_name=detection.display_name,
                application_url=detection.application_url,
                # Preserve the original discovered listing separately so a later
                # employer URL never destroys the reference link.
                job_listing_url=job.job_url,
                candidate_id=profile.id,
                status=ApplicationStatus.DRAFT,
                redirect_status=RedirectStatus.NOT_STARTED,
            )
            self.session.add(application)
            current_questions: list[ApplicationQuestion] = []
        else:
            application = existing
            application.candidate_id = profile.id
            if keep_validated_redirect:
                # Re-analysis must not demote a validated Indeed SmartApply
                # destination back to the job listing.
                application.application_url = application.application_url or (
                    detection.application_url
                )
            else:
                application.platform = detection.platform.value
                application.platform_name = detection.display_name
                application.application_url = detection.application_url
            if not application.job_listing_url:
                application.job_listing_url = job.job_url
            if not listing_flow:
                # A direct employer form replaces any earlier listing hand-off.
                application.application_destination_url = None
                application.destination_platform = None
                application.destination_validated = False
                application.redirect_status = RedirectStatus.NOT_STARTED
                application.redirect_detected_at = None
            current_questions = list(existing.questions)

        # Publish the analysis state before doing the work: inspection can take
        # seconds, and an interrupted run leaves a recoverable ANALYZING row
        # instead of a half-updated review.
        transition(application, ApplicationStatus.ANALYZING)
        try:
            await self.session.commit()
        except IntegrityError:
            # Two concurrent requests can both observe "no application yet" and
            # race on the unique (user_id, job_id) constraint. The winner has
            # already committed; adopt its row instead of returning a 500 or
            # creating a duplicate application.
            await self.session.rollback()
            existing = await self._find_for_job(user, job_id)
            if existing is None:  # pragma: no cover - defensive
                raise
            logger.info(
                "Concurrent application preparation detected",
                extra={
                    "stage": "application_analyze_conflict",
                    "user_id": str(user.id),
                    "application_id": str(existing.id),
                },
            )
            return await self.get_application(user, existing.id)

        inspection, inspection_note = await self._inspect(adapter, detection, user)

        if self._keeps_manual_resume(existing, resume_id=resume_id):
            # Re-analysis must never silently replace a resume the user chose.
            assert existing is not None and existing.resume_id is not None
            resume = await self.resumes.get_resume(user, existing.resume_id)
            resume_reason = MANUAL_RESUME_SELECTION_REASON
            resume_score = existing.resume_score
        else:
            resume, resume_reason, resume_score = await self._select_resume(
                user, job, resume_id=resume_id
            )
        reusable_answers = [
            answer
            for answer in (profile.application_answers or [])
            if answer.verification_status is VerificationStatus.VERIFIED and answer.answer
        ]
        approved = {
            answer.question_key.lower(): answer.answer for answer in reusable_answers
        }
        definitions = build_questionnaire(
            candidate=profile,
            user=user,
            resume=resume,
            approved_answers=approved,
            reusable_answers=reusable_answers,
            inspection=inspection,
        )
        definitions = apply_resume_selection(
            definitions,
            resume=resume,
            reason=resume_reason,
            score=resume_score,
        )

        automation_available = self._automation_available(adapter, detection)
        if listing_flow:
            mode = (
                AutomationMode.ASSISTED
                if settings.APPLICATION_AUTOMATION_ENABLED
                else AutomationMode.MANUAL
            )
        else:
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
        if listing_flow:
            source = detection.listing_source or "job board"
            notes.append(
                f"This is a {source} job listing with Easy Apply. Open the live "
                "browser, click Easy Apply yourself, and ApplyPilot will detect "
                "and validate where it goes. The listing URL stays unchanged."
            )
            if application.destination_validated:
                notes.append(
                    "Application destination already validated: "
                    f"{application.platform_name}."
                )
        if adapter.supports_automation and not automation_available:
            notes.append(
                "Browser automation is disabled, so this application is prepared and "
                "submitted by you through the guided workflow."
            )

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
        # Reload the collection so the caller (and the preview) sees questions
        # created in this run even though the relationship was cached earlier.
        await self.session.refresh(application, attribute_names=["questions"])

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
        self,
        user: User,
        application_id: UUID,
        application_url: str,
        *,
        employer_careers_url: str | None = None,
    ) -> Application:
        """Store the employer's own application URL and re-inspect the form.

        The URL passes the same static validation and DNS/public-address check
        the browser session uses, and a job-board link is rejected with a
        specific error instead of being stored as the application form.
        """
        application = await self.get_application(user, application_id)
        if application.status in UNREANALYZABLE_STATUSES:
            raise ApplicationStateError(
                "This application was already submitted or handed over for manual "
                "submission and its URL cannot be changed.",
                code="application_finalized",
            )

        normalized = normalize_application_url(application_url)
        if is_job_board_url(normalized):
            raise InvalidOperationError(
                "This link points to a job board. Add the employer's own application "
                "URL before starting a browser session.",
                code="job_board_url_not_supported",
            )
        # Resolve and reject private/loopback destinations before the URL is
        # stored or inspected by a browser. The session start repeats this and
        # pins the validated address into Chromium's resolver.
        normalized, _ = await validate_application_destination(normalized)

        careers = application.employer_careers_url
        if employer_careers_url is not None:
            raw_careers = employer_careers_url.strip()
            if raw_careers:
                careers = normalize_application_url(raw_careers)
                if is_job_board_url(careers):
                    raise InvalidOperationError(
                        "The employer careers URL must be the employer's own page, "
                        "not a job board.",
                        code="job_board_url_not_supported",
                    )
            else:
                careers = None

        if not application.job_listing_url:
            application.job_listing_url = application.job.job_url or application.application_url
        application.employer_careers_url = careers
        await self.session.commit()

        return await self.analyze_job(
            user,
            application.job_id,
            application_url=normalized,
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

    @staticmethod
    def _keeps_manual_resume(
        existing: Application | None, *, resume_id: UUID | None
    ) -> bool:
        """Whether a stored explicit selection must survive this analysis."""
        return bool(
            resume_id is None
            and existing is not None
            and existing.resume_id is not None
            and existing.resume_reason == MANUAL_RESUME_SELECTION_REASON
        )

    async def _select_resume(
        self, user: User, job: Job, *, resume_id: UUID | None
    ) -> tuple[Resume | None, str, float | None]:
        """Pick the recommended resume, unless the user chose one explicitly.

        The comparison/recommendation service is the same one exposed by the
        jobs API, so application preparation cannot drift from the comparison
        shown elsewhere. When no resume has enough evidence for a confident
        recommendation, the existing ``rows[0]`` fallback is preserved and its
        reason says explicitly that it is not a confident recommendation.
        """
        if resume_id is not None:
            resume = await self.resumes.get_resume(user, resume_id)
            return resume, MANUAL_RESUME_SELECTION_REASON, None

        outcome = await ResumeComparisonService(self.session).compare(user, job.id)
        recommendation = outcome.recommendation
        if recommendation is not None and recommendation.recommended is not None:
            best = recommendation.recommended
            # Re-check ownership through the existing resume service.
            resume = await self.resumes.get_resume(user, best.resume_id)
            return (
                resume,
                self._recommendation_reason(best, recommendation),
                best.overall_score,
            )

        ranked = [entry for entry in outcome.entries if entry.row is not None]
        if ranked:
            entry = ranked[0]
            resume = await self.resumes.get_resume(user, entry.resume_id)
            reason = (
                "Selected as the highest stored compatibility result, but no "
                "resume had enough usable evidence for a confident recommendation. "
                "Review this selection before continuing."
            )
            return resume, reason, entry.overall_score
        return (
            None,
            "No PDF resume is available. Upload one to include it in this application.",
            None,
        )

    @staticmethod
    def _recommendation_reason(best: Any, recommendation: RecommendationOutcome) -> str:
        """Summarise the recommendation for the persisted preview reason."""
        parts = [
            "Recommended by resume comparison: "
            f"{(best.overall_score or 0.0):.1f}% compatibility for "
            f"{best.resume_title}."
        ]
        if recommendation.confidence:
            parts.append(f"Recommendation confidence: {recommendation.confidence}.")
        if recommendation.close_call:
            parts.append(
                "Another resume is within the close-call threshold, so this is a "
                "preference rather than a clear winner."
            )
        if best.state != RESUME_READY:
            parts.append(
                "Resume analysis is not current for every resume, so this selection "
                "uses the stored parsed resume data; run resume analysis to improve "
                "confidence."
            )
        if recommendation.alternatives:
            alternative = recommendation.alternatives[0]
            parts.append(
                f"Close alternative: {alternative.resume_title} "
                f"({alternative.score_difference:g} points lower)."
            )
        return " ".join(parts)

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
                question.last_confirmed_at = question.last_confirmed_at or _utcnow()
                continue

            question.suggested_answer = definition.answer
            question.answer = definition.answer
            question.answer_source = definition.answer_source
            question.confidence = definition.confidence
            question.requires_confirmation = definition.requires_confirmation
            question.status = definition.status
            question.source_record_id = definition.source_record_id
            question.last_confirmed_at = definition.last_confirmed_at
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
        if application.status not in PROGRESSABLE_STATUSES:
            return
        transition(
            application,
            ApplicationStatus.READY_FOR_REVIEW
            if not application.missing_fields
            else ApplicationStatus.AWAITING_USER_INFORMATION,
        )

    # --- Reusable answers ----------------------------------------------------
    async def reusable_suggestions(
        self, user: User, application_id: UUID, question_id: UUID
    ) -> tuple[Application, ApplicationQuestion, tuple[ReuseSuggestion, ...]]:
        """Rank saved answers for one question in this application.

        The user's own saved answers are matched against the question wording
        and category; the job title and extracted required skills only reorder
        genuine matches. Conflicts with verified profile data are returned so
        the UI can refuse to insert them safely. Nothing is written here.
        """
        application = await self.get_application(user, application_id)
        question = next(
            (item for item in application.questions if item.id == question_id), None
        )
        if question is None:
            raise ResourceNotFoundError("Application question was not found.")
        if question.is_legal or application.candidate_id is None:
            return application, question, ()

        answers = await self.candidates.list_answers(application.candidate_id)
        # The repository loads the candidate's nested records, which the
        # deterministic profile mapping reads without triggering lazy I/O.
        profile = await self.candidates.ensure_profile_for_user(user)
        if profile is None:
            return application, question, ()

        required_skills: list[str] = []
        job = application.job
        if job is not None:
            understanding = await JobUnderstandingService(
                self.session
            ).intelligence_for(user, job)
            required_skills = list(understanding.fields.required_skills)

        suggestions = AnswerLibrary().reuse_suggestions(
            question_key=question.question_key,
            question_text=question.question_text,
            answers=answers,
            candidate=profile,
            profile_values=profile_answers(profile, user),
            job_title=job.job_title if job is not None else None,
            job_skills=required_skills,
        )
        return application, question, suggestions[:8]

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
        store_reusable: bool = True,
        allow_sensitive_reuse: bool = True,
        confirmed_source: AnswerSource | None = None,
        reused_answer_key: str | None = None,
    ) -> Application:
        """Save or confirm one answer.

        Phase 2C passes ``store_reusable`` and ``allow_sensitive_reuse`` so an
        answer confirmed only for this application is not added to the
        reusable bank, and ``confirmed_source`` so confirming a suggestion
        keeps its provenance instead of pretending the user typed it.

        ``reused_answer_key`` records that the value came from a saved reusable
        answer. The saved answer is looked up for the owning candidate, so a
        client cannot claim provenance for somebody else's answer, and the
        provenance survives this application's own confirmation.
        """
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

        reused: ApplicationAnswer | None = None
        if reused_answer_key:
            normalised_reuse = reused_answer_key.strip().lower()
            if application.candidate_id is None:
                raise ResourceNotFoundError(
                    f"No reusable answer is stored for '{normalised_reuse}'."
                )
            reused = next(
                (
                    item
                    for item in await self.candidates.list_answers(
                        application.candidate_id
                    )
                    if item.question_key == normalised_reuse
                ),
                None,
            )
            if reused is None:
                raise ResourceNotFoundError(
                    f"No reusable answer is stored for '{normalised_reuse}'."
                )
            if reused.is_legal or is_forbidden_answer_storage(
                reused.question_key, reused.question_text
            ):
                raise InvalidOperationError(
                    "That saved answer can never be reused for an application.",
                    code="forbidden_answer",
                )

        cleaned = (answer or "").strip()
        self._validate_answer(question, cleaned)
        if note is not None:
            question.note = note.strip()[:2000] or None
        question.answer = cleaned or None
        question.answered_at = _utcnow() if cleaned and confirmed else None
        if cleaned and confirmed:
            question.status = QuestionStatus.CONFIRMED
            question.requires_confirmation = False
            question.answer_source = (
                confirmed_source
                or (
                    AnswerSource.CONFIRMED_REUSABLE_ANSWER
                    if reused is not None
                    else AnswerSource.USER_INPUT
                )
            )
            question.confidence = 1.0
            question.last_confirmed_at = _utcnow()
            if reused is not None:
                question.source_record_id = (
                    f"application_answer:{reused.question_key}"
                )
            elif confirmed_source is None:
                question.source_record_id = None
        elif cleaned:
            question.status = QuestionStatus.SUGGESTED
            question.requires_confirmation = True
            if reused is not None:
                # Reused but not confirmed for this application: keep the
                # provenance instead of pretending the user typed it.
                question.answer_source = AnswerSource.CONFIRMED_REUSABLE_ANSWER
                question.confidence = 0.85
                question.source_record_id = (
                    f"application_answer:{reused.question_key}"
                )
            elif cleaned != (question.suggested_answer or ""):
                # A draft the user rewrote is their input, not the source value.
                question.answer_source = AnswerSource.USER_INPUT
                question.confidence = 0.5
        else:
            question.status = QuestionStatus.MISSING
            question.requires_confirmation = True

        self._invalidate_approval(application)
        self._refresh_completion(application)

        if cleaned and confirmed and store_reusable:
            await self._remember_answer(
                user,
                application,
                question,
                cleaned,
                allow_sensitive_reuse=allow_sensitive_reuse,
            )
        elif cleaned and confirmed:
            # The value still belongs on the profile when it is unambiguous,
            # but it must not enter the reusable answer bank.
            await self._persist_profile_value(
                user, application, question.question_key, cleaned
            )
        await self.session.commit()
        await self.session.refresh(application)
        return await self.get_application(user, application.id)

    def _validate_answer(self, question: ApplicationQuestion, value: str) -> None:
        """Reject answers that cannot be placed in the employer's field."""
        if is_forbidden_answer_storage(question.question_key, question.question_text):
            raise InvalidOperationError(
                "ApplyPilot never collects passwords, one-time codes, "
                "authentication cookies or payment details. Complete this field "
                "yourself on the employer's site.",
                code="forbidden_answer",
            )
        if not value:
            return
        options = [str(option) for option in (question.options or [])]
        choice_types = {
            QuestionType.SINGLE_CHOICE,
            QuestionType.RADIO,
            QuestionType.BOOLEAN,
            QuestionType.DECLARATION,
        }
        if options and question.field_type in choice_types and value not in options:
            raise InvalidOperationError(
                f"Choose one of: {', '.join(options)}.", code="invalid_answer"
            )
        if options and question.field_type in {
            QuestionType.MULTI_CHOICE,
            QuestionType.CHECKBOX,
        }:
            selected = [part.strip() for part in value.split(",") if part.strip()]
            invalid = [part for part in selected if part not in options]
            if invalid:
                raise InvalidOperationError(
                    f"Choose from: {', '.join(options)}.", code="invalid_answer"
                )
        if question.field_type is QuestionType.NUMBER:
            try:
                float(value)
            except ValueError as exc:
                raise InvalidOperationError(
                    "Enter a number for this question.", code="invalid_answer"
                ) from exc
        if question.field_type is QuestionType.DATE:
            try:
                date.fromisoformat(value[:10])
            except ValueError as exc:
                raise InvalidOperationError(
                    "Enter a valid date (YYYY-MM-DD).", code="invalid_answer"
                ) from exc
        if question.field_type is QuestionType.EMAIL and "@" not in value:
            raise InvalidOperationError(
                "Enter a valid email address.", code="invalid_answer"
            )
        if question.field_type is QuestionType.URL and not value.startswith(
            ("http://", "https://")
        ):
            raise InvalidOperationError(
                "Enter a URL starting with http:// or https://.", code="invalid_answer"
            )
        # A declaration must be an affirmative, explicit acceptance.
        if question.field_type is QuestionType.DECLARATION and value.casefold() not in {
            "yes",
            "true",
            "i agree",
            "accepted",
        }:
            raise InvalidOperationError(
                "The declaration must be accepted explicitly.", code="invalid_answer"
            )

    async def _remember_answer(
        self,
        user: User,
        application: Application,
        question: ApplicationQuestion,
        value: str,
        *,
        allow_sensitive_reuse: bool = True,
    ) -> None:
        """Save a confirmed answer for reuse and update safe profile fields."""
        if application.candidate_id is None:
            return
        if (
            not question.is_legal
            and question.field_type is not QuestionType.FILE
            and (allow_sensitive_reuse or not question.is_sensitive)
            and not is_forbidden_answer_storage(
                question.question_key, question.question_text
            )
        ):
            payload = ApplicationAnswerUpdate(
                question_text=question.question_text,
                answer=value,
                answer_type=_answer_type_for(question.field_type),
                verification_status=VerificationStatus.VERIFIED,
                is_sensitive=question.is_sensitive,
                is_legal=question.is_legal,
                answer_source=AnswerSource.CONFIRMED_REUSABLE_ANSWER,
                confidence=1.0,
                source_record_id=(
                    f"application:{application.id}:question:{question.question_key}"
                ),
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
        elif key == "skills.primary":
            skills = [
                item.strip()
                for item in value.replace("\n", ",").split(",")
                if item.strip()
            ][:100]
            updates["skills"] = skills
        if key == "availability.work_mode":
            work_modes = {
                "on-site": "onsite",
                "onsite": "onsite",
                "hybrid": "hybrid",
                "remote": "remote",
            }
            mapped = work_modes.get(value.strip().casefold())
            if mapped:
                await self.candidates.update_preferences_for_user(
                    user,
                    JobPreferenceUpdate(preferred_work_modes=[mapped]),
                )
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
        application.resume_reason = MANUAL_RESUME_SELECTION_REASON
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
            question.last_confirmed_at = _utcnow()
            question.source_record_id = f"resume:{resume.id}"

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
        ready_to_approve = not missing and application.resume_id is not None
        return {
            "application_id": application.id,
            "job_id": application.job_id,
            "job_title": application.job.job_title if application.job else "",
            "company": application.job.company if application.job else None,
            "platform": application.platform,
            "platform_name": application.platform_name,
            "application_url": application.application_url,
            "job_listing_url": application.job_listing_url
            or (application.job.job_url if application.job else None),
            "employer_careers_url": application.employer_careers_url,
            "application_destination_url": application.application_destination_url,
            "destination_platform": application.destination_platform,
            "destination_validated": application.destination_validated,
            "redirect_status": application.redirect_status.value,
            "redirect_flow": self._application_url_is_job_board(
                application.application_url
            ),
            "application_url_is_job_board": self._application_url_is_job_board(
                application.application_url
            ),
            "suggested_application_url": self._suggest_application_url(application),
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
                application.status is ApplicationStatus.AWAITING_APPROVAL
                and application.approval_fingerprint
                and application.approval_fingerprint
                == approval_fingerprint(application, questions)
            ),
            "submission_mode": "manual",
            "manual_steps": list(MANUAL_STEPS),
            "warnings": self._warnings(application, missing),
        }

    @staticmethod
    def _application_url_is_job_board(url: str | None) -> bool:
        if not url:
            return False
        try:
            return detect_platform(url).is_job_board_link
        except ApplicationUrlError:
            return False

    @staticmethod
    def _suggest_application_url(application: Application) -> str | None:
        """Offer a URL found in trusted listing text; it still needs validation."""
        if not ApplicationService._application_url_is_job_board(application.application_url):
            return None
        job = application.job
        if job is None:
            return None
        return suggest_employer_application_url(
            job.job_description, listing_url=job.job_url
        )

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
            "source_record_id": question.source_record_id,
            "last_confirmed_at": question.last_confirmed_at,
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
        if application.status is ApplicationStatus.CANCELLED:
            warnings.append("This application was cancelled and will not be submitted.")
        if application.requires_account:
            warnings.append(
                "This employer may require an account. Create it yourself; ApplyPilot "
                "never stores company passwords or completes verification for you."
            )
        warnings.append(
            "ApplyPilot never clicks Submit, Apply or Finish. You complete the "
            "final submission on the employer's site."
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
        if self._application_url_is_job_board(application.application_url):
            if application.destination_validated:
                warnings.append(
                    "The application form was detected and validated as "
                    f"{application.destination_platform or application.platform_name}. "
                    "The original listing URL is preserved."
                )
            else:
                warnings.append(
                    "This application starts from a job listing. Open the live "
                    "browser, click Easy Apply yourself, then detect and validate "
                    "the destination before analysing the form."
                )
        return warnings

    async def approve(self, user: User, application_id: UUID, *, approved: bool) -> Application:
        application = await self.get_application(user, application_id)
        if application.status not in {
            ApplicationStatus.AWAITING_USER_INFORMATION,
            ApplicationStatus.READY_FOR_REVIEW,
            ApplicationStatus.AWAITING_APPROVAL,
        }:
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

        application.approved_at = _utcnow()
        application.approval_fingerprint = approval_fingerprint(
            application, list(application.questions)
        )
        transition(application, ApplicationStatus.AWAITING_APPROVAL)
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
        # Lock the row so two concurrent requests cannot both start a
        # submission; the winner persists SUBMITTING before any browser work.
        application = await self.get_application(user, application_id, lock=True)
        if application.status is not ApplicationStatus.AWAITING_APPROVAL:
            if application.status is ApplicationStatus.SUBMITTING:
                # Legacy records from the removed automated flow; still treated
                # as mid-submission so a retry can never submit twice.
                raise ApplicationStateError(
                    "This application is already being processed. A retry cannot "
                    "submit it twice.",
                    code="submission_in_progress",
                )
            if application.status in {
                ApplicationStatus.SUBMITTED,
                ApplicationStatus.NEEDS_USER_ACTION,
            }:
                raise ApplicationStateError(
                    "This application has already been processed or is awaiting "
                    "manual action."
                )
            if application.status is ApplicationStatus.CANCELLED:
                raise ApplicationStateError(
                    "This application was cancelled and cannot be submitted. "
                    "Prepare it again to restart.",
                    code="application_cancelled",
                )
            if application.status is ApplicationStatus.FAILED:
                raise ApplicationStateError(
                    "The previous attempt failed. Complete the application "
                    "manually or prepare it again.",
                    code="submission_failed",
                )
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

        if application.resume_id is None:
            raise ApplicationStateError(
                "Select a resume before submitting the application.",
                code="resume_required",
            )

        # Stage G: the approved application is handed off, never submitted.
        # There is intentionally no browser fill/click path here. The user
        # completes the employer's form and records it with /mark-submitted.
        logger.info(
            "Application handed off for manual submission",
            extra={
                "stage": "application_submit_handoff",
                "application_id": str(application.id),
            },
        )
        return await self._finish_manual(application, reason=None)

    async def _finish_manual(
        self, application: Application, *, reason: str | None
    ) -> SubmissionOutcome:
        transition(application, ApplicationStatus.NEEDS_USER_ACTION)
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
        self,
        user: User,
        application_id: UUID,
        *,
        reference: str | None,
        confirmed: bool = False,
        confirmation_url: str | None = None,
        confirmation_message: str | None = None,
    ) -> Application:
        """Record a user-confirmed manual submission with explicit evidence.

        A status change, navigation or a form that disappeared is never enough.
        The caller must send ``confirmed=true``. Optional evidence (reference,
        sanitised confirmation URL, short employer message) is stored so the
        user can distinguish "I confirmed it" from "the employer confirmed it".
        """
        if confirmed is not True:
            raise ApplicationStateError(
                "Confirm that you completed the submission on the employer's "
                "site before recording it.",
                code="submission_not_confirmed",
            )
        application = await self.get_application(user, application_id)
        if application.status not in {
            ApplicationStatus.READY_FOR_REVIEW,
            ApplicationStatus.AWAITING_APPROVAL,
            ApplicationStatus.NEEDS_USER_ACTION,
            ApplicationStatus.FAILED,
        }:
            raise ApplicationStateError(
                "This application cannot be confirmed as submitted in its "
                "current state.",
                code="submission_confirmation_not_allowed",
            )

        confirmed_at = _utcnow()
        cleaned_reference = (reference or "").strip()[:255] or None
        # Query strings can carry one-time tokens, so only origin + path is kept.
        safe_url = sanitize_url_for_display((confirmation_url or "").strip()) or None
        message = (confirmation_message or "").strip()[:2000] or None

        transition(application, ApplicationStatus.SUBMITTED)
        application.stage = ApplicationStage.APPLIED
        application.submitted_at = confirmed_at
        application.submission_reference = cleaned_reference
        application.failure_reason = None
        details = dict(application.automation_details or {})
        details["submission_confirmation"] = {
            "confirmed_by_user": True,
            "confirmed_at": confirmed_at.isoformat(),
            "reference": cleaned_reference,
            "confirmation_url": safe_url,
            "confirmation_message": message,
        }
        application.automation_details = details
        await self.session.commit()
        logger.info(
            "Manual submission confirmed by user",
            extra={
                "stage": "application_submission_confirmed",
                "user_id": str(user.id),
                "application_id": str(application.id),
                "has_reference": cleaned_reference is not None,
                "has_confirmation_url": safe_url is not None,
                "has_confirmation_message": message is not None,
            },
        )
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

    async def cancel(self, user: User, application_id: UUID) -> Application:
        """Cancel an unfinished application without touching the employer's site."""
        application = await self.get_application(user, application_id)
        if application.status is ApplicationStatus.CANCELLED:
            return application
        if application.status in {
            ApplicationStatus.SUBMITTING,
            ApplicationStatus.SUBMITTED,
        }:
            raise ApplicationStateError(
                "An application that was submitted, or is being submitted, cannot be "
                "cancelled.",
                code="application_finalized",
            )
        transition(application, ApplicationStatus.CANCELLED)
        self._invalidate_approval(application)
        await self.session.commit()
        logger.info(
            "Application cancelled",
            extra={
                "stage": "application_cancel",
                "user_id": str(user.id),
                "application_id": str(application.id),
            },
        )
        return await self.get_application(user, application.id)


__all__ = [
    "MANUAL_STEPS",
    "ApplicationNotFoundError",
    "ApplicationService",
    "ApplicationStateError",
    "SubmissionOutcome",
    "approval_fingerprint",
]
