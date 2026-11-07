"""Derive and persist the missing-information queue for applications.

This service is a thin orchestration layer over the Phase 2C
:class:`~app.services.application_answer_matching.ApplicationAnswerMatcher`:

* every application question is re-evaluated against the verified profile,
  confirmed reusable answers and the selected resume;
* only *required* unresolved questions block an application, so optional
  questions can stay unanswered or be dismissed;
* stale reusable answers, conflicting answers and sensitive/legal answers that
  need a fresh confirmation are surfaced separately;
* the result is summarised on one persisted queue row per application, so
  progress and dismissed optional questions survive a reload.

Nothing here stores an answer that the safety rules forbid. Passwords,
one-time codes, authentication cookies and payment details remain rejected by
the shared answer validation before a value reaches the database.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import select

from app.core.exceptions import InvalidOperationError, ResourceNotFoundError
from app.models.application import (
    AnswerSource,
    Application,
    ApplicationStatus,
    QuestionStatus,
    QuestionType,
)
from app.models.application_answer import ApplicationAnswer
from app.models.candidate import Candidate
from app.models.missing_information import (
    MissingInformationItemStatus,
    MissingInformationQueue,
    MissingInformationStatus,
)
from app.models.user import User
from app.schemas.application_answer import AnswerConflictResolution
from app.services.application_answer_matching import (
    LEGAL_CATEGORIES,
    SENSITIVE_CATEGORIES,
    AnswerConflict,
    ApplicationAnswerMatcher,
    MatchedAnswer,
    QuestionDescriptor,
)
from app.services.application_questionnaire import profile_answers
from app.services.application_service import ApplicationService
from app.services.application_state import (
    PROGRESSABLE_STATUSES,
    can_transition,
    transition,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

#: Applications whose information queue is still actionable.
_QUEUE_STATUSES: frozenset[ApplicationStatus] = frozenset(
    {
        ApplicationStatus.AWAITING_USER_INFORMATION,
        ApplicationStatus.READY_FOR_REVIEW,
        ApplicationStatus.AWAITING_APPROVAL,
        ApplicationStatus.FAILED,
    }
)

#: Answer sources that count as "previously matched" rather than user input.
_AUTOMATIC_SOURCES: frozenset[AnswerSource] = frozenset(
    {
        AnswerSource.VERIFIED_PROFILE,
        AnswerSource.RESUME_SUGGESTION,
        AnswerSource.CONFIRMED_REUSABLE_ANSWER,
    }
)

_UNRESOLVED_STATUSES: frozenset[MissingInformationItemStatus] = frozenset(
    {
        MissingInformationItemStatus.MISSING,
        MissingInformationItemStatus.SUGGESTED,
        MissingInformationItemStatus.STALE,
        MissingInformationItemStatus.SENSITIVE_CONFIRMATION,
        MissingInformationItemStatus.LEGAL_CONFIRMATION,
    }
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _text(value: str | None) -> str:
    return (value or "").strip()


@dataclass(frozen=True)
class QueueItem:
    """One evaluated question, ready to be serialised by the API."""

    question_key: str
    question_text: str
    section: str
    field_type: QuestionType
    required: bool
    status: MissingInformationItemStatus
    reason: str
    answer: str | None = None
    answer_source: AnswerSource | None = None
    suggested_answer: str | None = None
    suggested_source: AnswerSource | None = None
    confidence: float = 0.0
    requires_confirmation: bool = True
    is_sensitive: bool = False
    is_legal: bool = False
    blocks_application: bool = False
    source_record_id: str | None = None
    last_confirmed_at: datetime | None = None
    options: tuple[str, ...] = ()


@dataclass(frozen=True)
class QueueConflict:
    """A reusable-answer conflict together with its blocking effect."""

    conflict: AnswerConflict
    blocks_application: bool = False


@dataclass(frozen=True)
class QueueSnapshot:
    """The persisted queue row plus the freshly derived items."""

    application: Application
    queue: MissingInformationQueue
    items: tuple[QueueItem, ...]
    conflicts: tuple[QueueConflict, ...]

    @property
    def ready_to_complete(self) -> bool:
        return self.queue.status is MissingInformationStatus.READY_TO_COMPLETE

    @property
    def required_questions(self) -> tuple[QueueItem, ...]:
        return tuple(
            item for item in self.items if item.required and item.status in _UNRESOLVED_STATUSES
        )

    @property
    def optional_questions(self) -> tuple[QueueItem, ...]:
        return tuple(
            item for item in self.items if not item.required and item.status in _UNRESOLVED_STATUSES
        )

    @property
    def matched_questions(self) -> tuple[QueueItem, ...]:
        return tuple(
            item
            for item in self.items
            if item.status is MissingInformationItemStatus.RESOLVED
            and item.answer
            and item.answer_source in _AUTOMATIC_SOURCES
        )

    @property
    def stale_answers(self) -> tuple[QueueItem, ...]:
        return tuple(
            item for item in self.items if item.status is MissingInformationItemStatus.STALE
        )

    @property
    def sensitive_confirmations(self) -> tuple[QueueItem, ...]:
        return tuple(
            item
            for item in self.items
            if item.status
            in {
                MissingInformationItemStatus.SENSITIVE_CONFIRMATION,
                MissingInformationItemStatus.LEGAL_CONFIRMATION,
            }
        )

    @property
    def dismissed_questions(self) -> tuple[QueueItem, ...]:
        return tuple(
            item for item in self.items if item.status is MissingInformationItemStatus.DISMISSED
        )


class MissingInformationService:
    """Build, persist and resolve the missing-information queue."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.applications = ApplicationService(session)
        self.candidates = self.applications.candidates
        self.matcher = ApplicationAnswerMatcher()

    # --- Reads ---------------------------------------------------------------
    async def list_queues(self, user: User) -> list[QueueSnapshot]:
        """Recalculate and return every actionable application's queue."""
        applications = await self.applications.list_applications(user)
        candidate = await self.candidates.ensure_profile_for_user(user)
        answers = list(await self.candidates.repository.get_answers(candidate.id))
        snapshots: list[QueueSnapshot] = []
        for application in applications:
            if application.status not in _QUEUE_STATUSES:
                continue
            snapshots.append(
                await self._recalculate(
                    user,
                    application,
                    candidate=candidate,
                    answers=answers,
                    commit=False,
                )
            )
        await self.session.commit()
        snapshots.sort(
            key=lambda snapshot: (
                snapshot.ready_to_complete,
                -snapshot.queue.required_remaining,
                snapshot.queue.progress_percent,
            )
        )
        return snapshots

    async def get_queue(self, user: User, application_id: uuid.UUID) -> QueueSnapshot:
        """Recalculate one owned application and return its queue."""
        application = await self.applications.get_application(user, application_id)
        candidate = await self.candidates.ensure_profile_for_user(user)
        answers = list(await self.candidates.repository.get_answers(candidate.id))
        return await self._recalculate(user, application, candidate=candidate, answers=answers)

    async def refresh(self, user: User, application_id: uuid.UUID) -> QueueSnapshot:
        """Explicitly recalculate a queue even when nothing changed."""
        return await self.get_queue(user, application_id)

    # --- Mutations -----------------------------------------------------------
    async def submit_answer(
        self,
        user: User,
        application_id: uuid.UUID,
        question_key: str,
        *,
        answer: str,
        save_for_reuse: bool,
    ) -> QueueSnapshot:
        """Validate and confirm a direct answer, then recalculate the queue."""
        application = await self.applications.get_application(user, application_id)
        question = self._question(application, question_key)
        self._require_text_answerable(question)

        queue = await self._queue_row(application.id)
        if queue is not None and question_key in (queue.dismissed_question_keys or []):
            queue.dismissed_question_keys = [
                key for key in queue.dismissed_question_keys if key != question_key
            ]

        # Sensitive and legal answers are confirmed per application but never
        # made reusable, even when the caller asks to save them. Classification
        # uses the canonical category as well as the stored question flag.
        category = self.matcher.identify_field_category(self._descriptor(question))
        is_sensitive = bool(question.is_sensitive or category in SENSITIVE_CATEGORIES)
        is_legal = bool(question.is_legal or category in LEGAL_CATEGORIES)
        store_reusable = bool(save_for_reuse) and not is_sensitive and not is_legal
        await self.applications.update_question(
            user,
            application_id,
            question.id,
            answer=answer,
            confirmed=True,
            store_reusable=store_reusable,
            allow_sensitive_reuse=False,
        )
        return await self._reload_and_recalculate(user, application_id)

    async def confirm_suggested_answer(
        self,
        user: User,
        application_id: uuid.UUID,
        question_key: str,
        *,
        answer: str | None,
        save_for_reuse: bool,
    ) -> QueueSnapshot:
        """Confirm the matcher's suggestion (or an explicit override)."""
        application = await self.applications.get_application(user, application_id)
        question = self._question(application, question_key)
        self._require_text_answerable(question)
        candidate = await self.candidates.ensure_profile_for_user(user)
        answers = list(await self.candidates.repository.get_answers(candidate.id))
        profile_values = profile_answers(candidate, user)

        descriptor = self._descriptor(question)
        category = self.matcher.identify_field_category(descriptor)
        is_legal = question.is_legal or category in LEGAL_CATEGORIES
        is_sensitive = question.is_sensitive or category in SENSITIVE_CATEGORIES

        supplied = _text(answer)
        source: AnswerSource | None = None
        if supplied:
            match = self._match(descriptor, candidate, answers, profile_values, application)
            if match is not None and _text(match.value) == supplied:
                source = match.source
        else:
            if is_legal:
                raise InvalidOperationError(
                    "Legal declarations must be confirmed explicitly for this "
                    "application and are never suggested automatically.",
                    code="legal_confirmation_required",
                )
            match = self._match(descriptor, candidate, answers, profile_values, application)
            if match is None or not _text(match.value):
                raise InvalidOperationError(
                    "There is no suggested answer for this question. Enter your "
                    "own answer instead.",
                    code="no_suggestion",
                )
            supplied = _text(match.value)
            source = match.source

        store_reusable = bool(save_for_reuse) and not is_sensitive and not is_legal
        await self.applications.update_question(
            user,
            application_id,
            question.id,
            answer=supplied,
            confirmed=True,
            store_reusable=store_reusable,
            allow_sensitive_reuse=False,
            confirmed_source=source,
        )
        return await self._reload_and_recalculate(user, application_id)

    async def dismiss_optional_question(
        self, user: User, application_id: uuid.UUID, question_key: str
    ) -> QueueSnapshot:
        """Skip an optional question without blocking the application."""
        application = await self.applications.get_application(user, application_id)
        question = self._question(application, question_key)
        if question.required:
            raise InvalidOperationError(
                "Required questions cannot be dismissed; answer them or update "
                "the employer's form.",
                code="required_question_not_dismissible",
            )
        if _text(question.answer) or question.status is QuestionStatus.CONFIRMED:
            raise InvalidOperationError(
                "This question already has an answer. Remove the answer before dismissing it.",
                code="question_already_answered",
            )

        queue = await self._ensure_queue_row(application.id)
        queue.dismissed_question_keys = sorted(
            {*(queue.dismissed_question_keys or []), question_key}
        )
        return await self._reload_and_recalculate(user, application_id)

    async def resolve_conflict(
        self,
        user: User,
        application_id: uuid.UUID,
        question_key: str,
        payload: AnswerConflictResolution,
    ) -> QueueSnapshot:
        """Resolve a stale or conflicting reusable answer, then recalculate."""
        await self.applications.get_application(user, application_id)
        await self.candidates.resolve_answer_conflict_for_user(user, question_key, payload)
        return await self._reload_and_recalculate(user, application_id)

    # --- Recalculation -------------------------------------------------------
    async def _reload_and_recalculate(self, user: User, application_id: uuid.UUID) -> QueueSnapshot:
        application = await self.applications.get_application(user, application_id)
        candidate = await self.candidates.ensure_profile_for_user(user)
        answers = list(await self.candidates.repository.get_answers(candidate.id))
        return await self._recalculate(user, application, candidate=candidate, answers=answers)

    async def _recalculate(
        self,
        user: User,
        application: Application,
        *,
        candidate: Candidate,
        answers: list[ApplicationAnswer],
        commit: bool = True,
    ) -> QueueSnapshot:
        now = _utcnow()
        queue = await self._ensure_queue_row(application.id)
        profile_values = profile_answers(candidate, user)
        dismissed = set(queue.dismissed_question_keys or [])

        items: list[QueueItem] = []
        for question in application.questions:
            item = self._evaluate_question(
                application,
                question,
                candidate=candidate,
                answers=answers,
                profile_values=profile_values,
                dismissed=dismissed,
                now=now,
            )
            items.append(item)

        required_total = sum(1 for item in items if item.required)
        required_resolved = sum(
            1
            for item in items
            if item.required and item.status is MissingInformationItemStatus.RESOLVED
        )
        required_remaining = max(0, required_total - required_resolved)
        optional_unanswered = sum(
            1 for item in items if not item.required and item.status in _UNRESOLVED_STATUSES
        )
        stale_count = sum(1 for item in items if item.status is MissingInformationItemStatus.STALE)
        sensitive_count = sum(
            1
            for item in items
            if item.status
            in {
                MissingInformationItemStatus.SENSITIVE_CONFIRMATION,
                MissingInformationItemStatus.LEGAL_CONFIRMATION,
            }
        )
        matched_count = sum(
            1
            for item in items
            if item.status is MissingInformationItemStatus.RESOLVED
            and item.answer
            and item.answer_source in _AUTOMATIC_SOURCES
        )
        progress_percent = (
            100 if required_total == 0 else round((required_resolved / required_total) * 100)
        )

        unresolved_required_keys = {
            item.question_key
            for item in items
            if item.required and item.status in _UNRESOLVED_STATUSES
        }
        unresolved_required_categories = {
            self.matcher.identify_field_category(
                QuestionDescriptor(
                    question_key=item.question_key,
                    question_text=item.question_text,
                )
            )
            for item in items
            if item.required and item.status in _UNRESOLVED_STATUSES
        }
        unresolved_required_categories.discard(None)

        conflicts = self._relevant_conflicts(
            application,
            candidate=candidate,
            answers=answers,
            profile_values=profile_values,
            unresolved_required_keys=unresolved_required_keys,
            unresolved_required_categories=unresolved_required_categories,
        )

        ready = required_remaining == 0 and not any(
            conflict.blocks_application for conflict in conflicts
        )
        queue.status = (
            MissingInformationStatus.READY_TO_COMPLETE
            if ready
            else MissingInformationStatus.PENDING
        )
        queue.required_total = required_total
        queue.required_resolved = required_resolved
        queue.required_remaining = required_remaining
        queue.optional_unanswered = optional_unanswered
        queue.stale_count = stale_count
        queue.conflict_count = len(conflicts)
        queue.sensitive_confirmation_count = sensitive_count
        queue.matched_count = matched_count
        queue.progress_percent = progress_percent
        queue.last_recalculated_at = now
        queue.resolved_at = now if ready else None

        application.missing_fields = sorted(
            item.question_key
            for item in items
            if item.required and item.status in _UNRESOLVED_STATUSES
        )
        self._sync_application_status(application, ready)

        if commit:
            await self.session.commit()
        else:
            await self.session.flush()
        return QueueSnapshot(
            application=application,
            queue=queue,
            items=tuple(items),
            conflicts=tuple(conflicts),
        )

    def _evaluate_question(
        self,
        application: Application,
        question: object,
        *,
        candidate: Candidate,
        answers: list[ApplicationAnswer],
        profile_values: dict[str, str],
        dismissed: set[str],
        now: datetime,
    ) -> QueueItem:
        descriptor = self._descriptor(question)
        category = self.matcher.identify_field_category(descriptor)
        is_legal = bool(getattr(question, "is_legal", False) or category in LEGAL_CATEGORIES)
        is_sensitive = bool(
            getattr(question, "is_sensitive", False) or category in SENSITIVE_CATEGORIES
        )
        stored_answer = _text(getattr(question, "answer", None)) or None
        stored_source = getattr(question, "answer_source", None)
        confirmed = (
            getattr(question, "status", None) is QuestionStatus.CONFIRMED
            and stored_answer is not None
        )

        suggestion = self._match(descriptor, candidate, answers, profile_values, application)
        draft = (
            stored_answer
            if getattr(question, "status", None) is QuestionStatus.SUGGESTED
            and stored_source is AnswerSource.USER_INPUT
            else None
        )
        suggested_answer: str | None = None
        suggested_source: AnswerSource | None = None
        confidence = 0.0
        if is_legal:
            # Legal declarations are never suggested from stored data.
            suggested_answer = stored_answer
            suggested_source = stored_source if stored_answer else None
            confidence = float(getattr(question, "confidence", 0.0) or 0.0)
        elif draft:
            suggested_answer = draft
            suggested_source = AnswerSource.USER_INPUT
            confidence = float(getattr(question, "confidence", 0.0) or 0.0)
        elif suggestion is not None and _text(suggestion.value):
            suggested_answer = _text(suggestion.value)
            suggested_source = suggestion.source
            confidence = suggestion.confidence
        else:
            fallback = _text(getattr(question, "suggested_answer", None)) or stored_answer
            if fallback:
                suggested_answer = fallback
                suggested_source = stored_source
                confidence = float(getattr(question, "confidence", 0.0) or 0.0)

        status: MissingInformationItemStatus
        reason: str
        stale_reason: str | None = None
        if confirmed:
            stale_reason = self._confirmed_staleness(question, now=now)
            if stale_reason:
                # Keep the value visible, but demote it so the rest of the
                # application flow also treats it as needing confirmation.
                question.status = QuestionStatus.SUGGESTED
                question.requires_confirmation = True
                status = MissingInformationItemStatus.STALE
                reason = stale_reason
                suggested_answer = stored_answer
                suggested_source = stored_source
            else:
                status = MissingInformationItemStatus.RESOLVED
                reason = "Confirmed for this application."
                suggested_answer = stored_answer
                suggested_source = stored_source
        elif is_legal:
            status = MissingInformationItemStatus.LEGAL_CONFIRMATION
            reason = (
                "Legal declarations must be confirmed freshly for each "
                "application and are never reused."
            )
        elif descriptor.question_key in dismissed and not descriptor.required:
            status = MissingInformationItemStatus.DISMISSED
            reason = "You dismissed this optional question."
        elif suggestion is not None and suggestion.stale:
            status = MissingInformationItemStatus.STALE
            reason = suggestion.explanation or "The stored answer is outdated."
        elif is_sensitive and suggested_answer:
            status = MissingInformationItemStatus.SENSITIVE_CONFIRMATION
            reason = "Sensitive answers require a fresh confirmation for this application."
        elif suggested_answer:
            status = MissingInformationItemStatus.SUGGESTED
            reason = "A sourced suggestion is available; confirm it to use it."
        else:
            status = MissingInformationItemStatus.MISSING
            reason = (
                "Required information is missing."
                if descriptor.required
                else "Optional question left unanswered."
            )

        return QueueItem(
            question_key=descriptor.question_key,
            question_text=descriptor.label,
            section=str(getattr(question, "section", "") or ""),
            field_type=getattr(question, "field_type", None) or QuestionType.TEXT,
            required=descriptor.required,
            status=status,
            reason=reason,
            answer=stored_answer,
            answer_source=stored_source if stored_answer else None,
            suggested_answer=suggested_answer,
            suggested_source=suggested_source if suggested_answer else None,
            confidence=confidence,
            requires_confirmation=status is not MissingInformationItemStatus.RESOLVED,
            is_sensitive=is_sensitive,
            is_legal=is_legal,
            blocks_application=bool(descriptor.required and status in _UNRESOLVED_STATUSES),
            source_record_id=(
                getattr(question, "source_record_id", None) if stored_answer else None
            ),
            last_confirmed_at=getattr(question, "last_confirmed_at", None),
            options=tuple(getattr(question, "options", None) or ()),
        )

    def _confirmed_staleness(self, question: object, *, now: datetime) -> str | None:
        """Only reusable answers carry a freshness contract."""
        if getattr(question, "answer_source", None) is not AnswerSource.CONFIRMED_REUSABLE_ANSWER:
            return None
        assessment = self.matcher.validate_answer_freshness(question, now=now)
        if assessment.fresh:
            return None
        return assessment.reason or "This answer is outdated."

    def _match(
        self,
        descriptor: QuestionDescriptor,
        candidate: Candidate,
        answers: list[ApplicationAnswer],
        profile_values: dict[str, str],
        application: Application,
    ) -> MatchedAnswer | None:
        match = self.matcher.match_candidate_profile(
            descriptor, candidate, profile_values=profile_values
        )
        if match is None:
            match = self.matcher.match_reusable_answers(descriptor, answers)
        if match is None:
            match = self.matcher.match_resume_data(descriptor, application.resume)
        return match

    def _relevant_conflicts(
        self,
        application: Application,
        *,
        candidate: Candidate,
        answers: list[ApplicationAnswer],
        profile_values: dict[str, str],
        unresolved_required_keys: set[str],
        unresolved_required_categories: set[str],
    ) -> list[QueueConflict]:
        conflicts = self.matcher.detect_answer_conflicts(
            candidate, answers, profile_values=profile_values
        )
        if not conflicts:
            return []

        question_keys = {question.question_key for question in application.questions}
        categories = {
            self.matcher.identify_field_category(self._descriptor(question))
            for question in application.questions
        }
        categories.discard(None)

        relevant: list[QueueConflict] = []
        for conflict in conflicts:
            if not (
                conflict.category in categories
                or conflict.question_key in question_keys
                or conflict.conflicting_source in question_keys
            ):
                continue
            blocks = bool(
                (conflict.category and conflict.category in unresolved_required_categories)
                or conflict.question_key in unresolved_required_keys
                or conflict.conflicting_source in unresolved_required_keys
            )
            relevant.append(QueueConflict(conflict=conflict, blocks_application=blocks))
        return relevant

    def _sync_application_status(self, application: Application, ready: bool) -> None:
        """Keep the application state aligned without overriding approvals."""
        if application.status not in PROGRESSABLE_STATUSES:
            return
        if ready:
            target = ApplicationStatus.READY_FOR_REVIEW
            if application.status in {
                ApplicationStatus.ANALYZING,
                ApplicationStatus.AWAITING_USER_INFORMATION,
                ApplicationStatus.FAILED,
            } and can_transition(application.status, target):
                transition(application, target)
            return
        if application.status is ApplicationStatus.READY_FOR_REVIEW:
            transition(application, ApplicationStatus.AWAITING_USER_INFORMATION)

    # --- Helpers -------------------------------------------------------------
    def _descriptor(self, question: object) -> QuestionDescriptor:
        return QuestionDescriptor(
            question_key=str(getattr(question, "question_key", "") or ""),
            question_text=str(getattr(question, "question_text", "") or ""),
            field_identifier=getattr(question, "field_identifier", None),
            help_text=getattr(question, "help_text", None),
            field_type=getattr(question, "field_type", None) or QuestionType.TEXT,
            options=tuple(getattr(question, "options", None) or ()),
            required=bool(getattr(question, "required", False)),
            is_sensitive=bool(getattr(question, "is_sensitive", False)),
            is_legal=bool(getattr(question, "is_legal", False)),
        )

    def _require_text_answerable(self, question: object) -> None:
        """A document must be selected, never typed, in the queue."""
        if getattr(question, "field_type", None) is QuestionType.FILE:
            raise InvalidOperationError(
                "Select a resume in the application workspace instead of "
                "typing a document answer here.",
                code="file_answer_not_supported",
            )

    def _question(self, application: Application, question_key: str) -> object:
        question = next(
            (item for item in application.questions if item.question_key == question_key),
            None,
        )
        if question is None:
            raise ResourceNotFoundError(
                f"Question '{question_key}' is not part of this application."
            )
        return question

    async def _queue_row(self, application_id: uuid.UUID) -> MissingInformationQueue | None:
        return await self.session.scalar(
            select(MissingInformationQueue).where(
                MissingInformationQueue.application_id == application_id
            )
        )

    async def _ensure_queue_row(self, application_id: uuid.UUID) -> MissingInformationQueue:
        queue = await self._queue_row(application_id)
        if queue is None:
            queue = MissingInformationQueue(
                id=uuid.uuid4(),
                application_id=application_id,
                dismissed_question_keys=[],
            )
            self.session.add(queue)
            await self.session.flush()
        return queue


__all__ = [
    "MissingInformationService",
    "QueueConflict",
    "QueueItem",
    "QueueSnapshot",
]
