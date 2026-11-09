"""Phase 2E Stage C: decide exactly which fields may be filled, and how.

The planner is deterministic and browser-free, so a plan can be previewed and
tested without launching Chromium. It consumes the immutable Stage B analysis
and re-resolves values through the existing Phase 2C ``ApplicationAnswerMatcher``
rather than trusting a stale suggestion in the analysis record.

Never planned for execution:

* CAPTCHA, OTP, MFA and password controls;
* legal declarations, demographic questions and work-authorization questions;
* ambiguous or unclassified fields;
* stale or conflicting answers, and anything needing confirmation;
* fields that already contain a value the user (or employer) entered;
* resume-derived information that was never confirmed.

A resume upload is planned only for an explicit resume file field and only when
the application's selected resume is owned by the same user.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.models.application import AnswerSource, QuestionStatus, QuestionType
from app.models.application_answer import ApplicationAnswer
from app.models.candidate import Candidate
from app.models.resume import Resume
from app.schemas.application_fill import FillAction, FillFieldStatus
from app.schemas.application_form import (
    DetectedFormField,
    FieldCategory,
    FieldSource,
    FormFieldType,
)
from app.services.application_answer_matching import (
    ApplicationAnswerMatcher,
    MatchedAnswer,
    QuestionDescriptor,
)

#: Reuse the Stage B confidence floor; never fill a weakly classified field.
MINIMUM_CONFIDENCE = 0.75

_FORM_TO_QUESTION: dict[FormFieldType, QuestionType] = {
    FormFieldType.EMAIL: QuestionType.EMAIL,
    FormFieldType.PHONE: QuestionType.PHONE,
    FormFieldType.NUMBER: QuestionType.NUMBER,
    FormFieldType.DATE: QuestionType.DATE,
    FormFieldType.URL: QuestionType.URL,
    FormFieldType.TEXTAREA: QuestionType.TEXTAREA,
    FormFieldType.SELECT: QuestionType.SINGLE_CHOICE,
    FormFieldType.RADIO_GROUP: QuestionType.RADIO,
    FormFieldType.CHECKBOX_GROUP: QuestionType.MULTI_CHOICE,
    FormFieldType.CHECKBOX: QuestionType.BOOLEAN,
    FormFieldType.FILE: QuestionType.FILE,
    FormFieldType.TEXT: QuestionType.TEXT,
}

_NEVER_AUTOMATE: frozenset[FieldCategory] = frozenset(
    {
        FieldCategory.CAPTCHA,
        FieldCategory.OTP_VERIFICATION,
        FieldCategory.MFA_VERIFICATION,
        FieldCategory.LEGAL_DECLARATION,
        FieldCategory.DEMOGRAPHIC_INFORMATION,
        FieldCategory.WORK_AUTHORIZATION,
        FieldCategory.OTHER,
        FieldCategory.UNKNOWN,
    }
)

_SENSITIVE_CATEGORIES: frozenset[FieldCategory] = frozenset(
    {
        FieldCategory.CURRENT_SALARY,
        FieldCategory.EXPECTED_SALARY,
        FieldCategory.NOTICE_PERIOD,
        FieldCategory.RELOCATION,
        FieldCategory.WORK_AUTHORIZATION,
    }
)

_ALLOWED_SOURCES: frozenset[FieldSource] = frozenset(
    {
        FieldSource.VERIFIED_PROFILE,
        FieldSource.CONFIRMED_REUSABLE_ANSWER,
        FieldSource.USER_PROVIDED,
    }
)


@dataclass
class PlannedField:
    """One plan row. ``text`` is never persisted by the execution record."""

    field_id: str
    label: str
    field_type: FormFieldType
    classification: FieldCategory
    required: bool
    action: FillAction
    status: FillFieldStatus
    proposed_value: str | None = None
    source: FieldSource = FieldSource.NONE
    confidence: float = 0.0
    options: list[str] = field(default_factory=list)
    reason: str = ""
    html_id: str | None = None
    name: str | None = None

    @property
    def persisted(self) -> dict[str, Any]:
        """Plan metadata without the proposed value."""
        return {
            "field_id": self.field_id,
            "label": self.label,
            "field_type": self.field_type.value,
            "classification": self.classification.value,
            "required": self.required,
            "action": self.action.value,
            "status": self.status.value,
            "source": self.source.value,
            "confidence": self.confidence,
            "reason": self.reason,
        }

    @property
    def shows_value(self) -> bool:
        return (
            self.status
            in {FillFieldStatus.READY, FillFieldStatus.NEEDS_CONFIRMATION}
            and self.source is not FieldSource.NONE
            and not self.is_sensitive
            and self.classification is not FieldCategory.LEGAL_DECLARATION
        )

    @property
    def is_sensitive(self) -> bool:
        return self.classification in _SENSITIVE_CATEGORIES


@dataclass(frozen=True)
class PlannedResume:
    resume_id: Any | None = None
    filename: str | None = None
    will_upload: bool = False
    file_field_ids: tuple[str, ...] = ()
    reason: str = ""


@dataclass
class FillPlan:
    fields: list[PlannedField]
    resume: PlannedResume
    warnings: tuple[str, ...] = ()

    @property
    def eligible_count(self) -> int:
        return sum(1 for item in self.fields if item.status is FillFieldStatus.READY)

    @property
    def confirmation_count(self) -> int:
        return sum(
            1
            for item in self.fields
            if item.status is FillFieldStatus.NEEDS_CONFIRMATION
        )

    @property
    def human_input_count(self) -> int:
        return sum(
            1
            for item in self.fields
            if item.status is FillFieldStatus.NEEDS_HUMAN_INPUT
        )

    @property
    def already_filled_count(self) -> int:
        return sum(
            1 for item in self.fields if item.status is FillFieldStatus.ALREADY_FILLED
        )

    @property
    def skipped_count(self) -> int:
        return sum(1 for item in self.fields if item.status is FillFieldStatus.SKIPPED)

    @property
    def executable(self) -> bool:
        return self.eligible_count > 0 or self.resume.will_upload

    @property
    def ready_fields(self) -> list[PlannedField]:
        return [item for item in self.fields if item.status is FillFieldStatus.READY]


def _normalise(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def _descriptor(planned: DetectedFormField) -> QuestionDescriptor:
    return QuestionDescriptor(
        question_text=planned.label,
        field_identifier=planned.html_id or planned.name,
        help_text=planned.help_text,
        field_type=_FORM_TO_QUESTION.get(planned.field_type, QuestionType.TEXT),
        options=tuple(planned.options),
        required=planned.required,
        is_sensitive=planned.is_sensitive,
        is_legal=planned.is_legal,
    )


def _action_for(planned: DetectedFormField) -> FillAction:
    if planned.field_type is FormFieldType.FILE:
        return FillAction.UPLOAD
    if planned.field_type in {FormFieldType.SELECT, FormFieldType.RADIO_GROUP}:
        return FillAction.SELECT
    if planned.field_type in {FormFieldType.CHECKBOX, FormFieldType.CHECKBOX_GROUP}:
        return FillAction.CHECK
    return FillAction.FILL


def _option_match(planned: DetectedFormField, value: str) -> str | None:
    wanted = _normalise(value).casefold()
    for option in planned.options:
        if _normalise(option).casefold() == wanted:
            return option
    return None


def _checkbox_values(planned: DetectedFormField, value: str) -> list[str] | None:
    parts = [_normalise(part) for part in value.split(",") if _normalise(part)]
    if not parts:
        return None
    matched: list[str] = []
    for part in parts:
        option = _option_match(planned, part)
        if option is None:
            return None
        matched.append(option)
    return matched


def _valid_for_shape(planned: DetectedFormField, value: str) -> str | None:
    """Return the cleaned value, or ``None`` when it does not fit the control."""
    cleaned = _normalise(value)[:5000]
    if not cleaned:
        return None
    if planned.field_type is FormFieldType.NUMBER:
        try:
            float(cleaned.replace(",", ""))
        except ValueError:
            return None
    if planned.field_type is FormFieldType.DATE:
        try:
            date.fromisoformat(cleaned[:10])
        except ValueError:
            return None
    if planned.field_type is FormFieldType.EMAIL and "@" not in cleaned:
        return None
    if planned.field_type is FormFieldType.URL and not cleaned.startswith(
        ("http://", "https://")
    ):
        return None
    if planned.field_type in {FormFieldType.SELECT, FormFieldType.RADIO_GROUP}:
        return _option_match(planned, cleaned)
    if planned.field_type is FormFieldType.CHECKBOX_GROUP:
        matches = _checkbox_values(planned, cleaned)
        return ", ".join(matches) if matches else None
    if planned.field_type is FormFieldType.CHECKBOX:
        lowered = cleaned.casefold()
        if lowered in {"yes", "true", "1", "on"}:
            return "yes"
        if lowered in {"no", "false", "0", "off"}:
            return "no"
        return None
    return cleaned


class FieldExecutionPlanner:
    """Turn one Stage B analysis into a safe, previewable fill plan."""

    def __init__(self, matcher: ApplicationAnswerMatcher | None = None) -> None:
        self.matcher = matcher or ApplicationAnswerMatcher()

    def build(
        self,
        fields: list[DetectedFormField],
        *,
        candidate: Candidate | None,
        resume: Resume | None,
        reusable_answers: list[ApplicationAnswer] | None = None,
        user_answers: dict[str, str] | None = None,
        resume_owned: bool = True,
    ) -> FillPlan:
        rows: list[PlannedField] = []
        file_fields: list[PlannedField] = []
        warnings: list[str] = []

        for item in fields:
            row = self._plan_field(
                item,
                candidate=candidate,
                reusable_answers=reusable_answers or [],
                user_answers=user_answers or {},
            )
            rows.append(row)
            if row.action is FillAction.UPLOAD:
                file_fields.append(row)

        planned_resume = self._plan_resume(
            resume,
            resume_owned=resume_owned,
            file_fields=file_fields,
        )
        if planned_resume.will_upload:
            for row in file_fields:
                if row.status is FillFieldStatus.NEEDS_HUMAN_INPUT:
                    row.status = FillFieldStatus.READY
                    row.reason = "The selected resume will be attached to this field."

        legal = sum(
            1
            for item in rows
            if item.classification is FieldCategory.LEGAL_DECLARATION
        )
        captcha = sum(
            1 for item in rows if item.classification is FieldCategory.CAPTCHA
        )
        otp = sum(
            1
            for item in rows
            if item.classification
            in {FieldCategory.OTP_VERIFICATION, FieldCategory.MFA_VERIFICATION}
        )
        if legal:
            warnings.append(
                f"{legal} legal declaration{'s' if legal != 1 else ''} will never be "
                "filled or accepted automatically. Confirm it yourself."
            )
        if captcha:
            warnings.append(
                "A CAPTCHA is present. ApplyPilot never solves it; complete it yourself."
            )
        if otp:
            warnings.append(
                "A one-time or multi-factor code is required. ApplyPilot never reads "
                "or enters it."
            )
        if file_fields and not planned_resume.will_upload:
            warnings.append(planned_resume.reason or "No owned resume is available.")
        confirmations = sum(
            1 for item in rows if item.status is FillFieldStatus.NEEDS_CONFIRMATION
        )
        if confirmations:
            warnings.append(
                f"{confirmations} field{'s' if confirmations != 1 else ''} need your "
                "confirmation before anything is typed into the employer's page."
            )
        return FillPlan(
            fields=rows,
            resume=planned_resume,
            warnings=tuple(warnings),
        )

    # --- One field -----------------------------------------------------------
    def _plan_field(
        self,
        item: DetectedFormField,
        *,
        candidate: Candidate | None,
        reusable_answers: list[ApplicationAnswer],
        user_answers: dict[str, str],
    ) -> PlannedField:
        row = PlannedField(
            field_id=item.field_id,
            label=item.label,
            field_type=item.field_type,
            classification=item.classification,
            required=item.required,
            action=_action_for(item),
            status=FillFieldStatus.SKIPPED,
            confidence=item.confidence,
            options=list(item.options),
            html_id=item.html_id,
            name=item.name,
        )

        # Verification, legal, demographic and ambiguous controls first.
        if item.field_type is FormFieldType.PASSWORD:
            return self._human(
                row, "Passwords are entered by you and never handled by ApplyPilot."
            )
        if item.classification in _NEVER_AUTOMATE:
            return self._human(row, self._human_reason(item))
        if item.classification is FieldCategory.RESUME_UPLOAD or (
            item.field_type is FormFieldType.FILE
        ):
            if item.classification is not FieldCategory.RESUME_UPLOAD:
                return self._human(
                    row, "Only the selected resume PDF may be uploaded automatically."
                )
            row.action = FillAction.UPLOAD
            return self._human(row, "Waiting for the selected resume.")

        if not item.visible:
            row.reason = "The field is not visible on the current page."
            return row
        if item.current_value is not None and _normalise(item.current_value):
            row.status = FillFieldStatus.ALREADY_FILLED
            row.reason = "The field already has a value; it was not overwritten."
            return row
        if item.confidence < MINIMUM_CONFIDENCE:
            return self._human(
                row, "The field classification is not confident enough to fill."
            )

        matched = self._resolve(
            item,
            candidate=candidate,
            reusable_answers=reusable_answers,
            user_answers=user_answers,
        )
        if matched is None:
            if item.required:
                return self._human(
                    row,
                    "No verified profile value, confirmed answer or safe source matched "
                    "this required field.",
                )
            row.reason = "No safe value matched this optional field."
            return row

        value = _valid_for_shape(item, matched.value or "")
        if value is None:
            return self._human(
                row,
                "The stored answer does not fit this field's type or options.",
            )

        source = self._source_for(matched)
        if source is FieldSource.NONE:
            return self._human(row, matched.explanation or "No safe source was found.")

        row.proposed_value = value
        row.source = source
        row.confidence = max(row.confidence, matched.confidence)

        if matched.stale:
            row.status = FillFieldStatus.NEEDS_CONFIRMATION
            row.reason = matched.explanation or (
                "The stored answer is stale and must be confirmed again."
            )
            return row
        if matched.requires_confirmation or matched.stale:
            row.status = FillFieldStatus.NEEDS_CONFIRMATION
            row.reason = (
                "This answer is sensitive or was not confirmed for this application."
            )
            return row
        if source is FieldSource.RESUME_SUGGESTION:
            row.status = FillFieldStatus.NEEDS_CONFIRMATION
            row.reason = "Resume-derived information must be confirmed before use."
            return row

        row.status = FillFieldStatus.READY
        row.reason = "Verified information is ready to fill."
        return row

    def _resolve(
        self,
        item: DetectedFormField,
        *,
        candidate: Candidate | None,
        reusable_answers: list[ApplicationAnswer],
        user_answers: dict[str, str],
    ) -> MatchedAnswer | None:
        descriptor = _descriptor(item)

        # A value the user explicitly confirmed for this application wins.
        if user_answers:
            matched = self.matcher.match_reusable_answers(descriptor, user_answers)
            if matched is not None and matched.value:
                return matched

        matched = self.matcher.match_candidate_profile(descriptor, candidate)
        if matched is not None and matched.value:
            return matched

        if reusable_answers:
            matched = self.matcher.match_reusable_answers(descriptor, reusable_answers)
            if matched is not None and matched.value:
                return matched

        # Last resort: a Stage B suggestion from an allowed source. A resume
        # suggestion is intentionally excluded: it is not a verified fact.
        suggestion = item.source
        if (
            suggestion.source in _ALLOWED_SOURCES
            and suggestion.suggested_value
            and not suggestion.stale
            and suggestion.confidence >= MINIMUM_CONFIDENCE
        ):
            return MatchedAnswer(
                question_key="",
                question_text=item.label,
                value=suggestion.suggested_value,
                source=(
                    AnswerSource.VERIFIED_PROFILE
                    if suggestion.source is FieldSource.VERIFIED_PROFILE
                    else AnswerSource.CONFIRMED_REUSABLE_ANSWER
                    if suggestion.source is FieldSource.CONFIRMED_REUSABLE_ANSWER
                    else AnswerSource.USER_ENTERED
                ),
                confidence=suggestion.confidence,
                status=(
                    QuestionStatus.SUGGESTED
                    if item.requires_confirmation
                    else QuestionStatus.CONFIRMED
                ),
                requires_confirmation=item.requires_confirmation,
                is_sensitive=item.is_sensitive,
                is_legal=item.is_legal,
                explanation=suggestion.explanation,
                stale=suggestion.stale,
            )
        return None

    @staticmethod
    def _source_for(matched: MatchedAnswer) -> FieldSource:
        return {
            AnswerSource.VERIFIED_PROFILE: FieldSource.VERIFIED_PROFILE,
            AnswerSource.PROFILE: FieldSource.VERIFIED_PROFILE,
            AnswerSource.CONFIRMED_REUSABLE_ANSWER: FieldSource.CONFIRMED_REUSABLE_ANSWER,
            AnswerSource.PREVIOUS_ANSWER: FieldSource.CONFIRMED_REUSABLE_ANSWER,
            AnswerSource.USER_ENTERED: FieldSource.USER_PROVIDED,
            AnswerSource.USER_INPUT: FieldSource.USER_PROVIDED,
        }.get(matched.source, FieldSource.NONE)

    @staticmethod
    def _human(row: PlannedField, reason: str) -> PlannedField:
        row.status = FillFieldStatus.NEEDS_HUMAN_INPUT
        row.action = FillAction.NONE if row.action is not FillAction.UPLOAD else row.action
        row.reason = reason
        row.proposed_value = None
        return row

    @staticmethod
    def _human_reason(item: DetectedFormField) -> str:
        return {
            FieldCategory.CAPTCHA: "CAPTCHA challenges must be completed by you.",
            FieldCategory.OTP_VERIFICATION: (
                "One-time verification codes are never read or entered."
            ),
            FieldCategory.MFA_VERIFICATION: (
                "Multi-factor verification must be completed by you."
            ),
            FieldCategory.LEGAL_DECLARATION: (
                "Legal declarations are never accepted automatically."
            ),
            FieldCategory.DEMOGRAPHIC_INFORMATION: (
                "Demographic information is optional and must be answered by you."
            ),
            FieldCategory.WORK_AUTHORIZATION: (
                "Work-authorization declarations must be confirmed by you."
            ),
            FieldCategory.OTHER: "This control is not a safely automatable question.",
            FieldCategory.UNKNOWN: "This field could not be classified safely.",
        }.get(item.classification, "This field requires your input.")

    @staticmethod
    def _plan_resume(
        resume: Resume | None,
        *,
        resume_owned: bool,
        file_fields: list[PlannedField],
    ) -> PlannedResume:
        if not file_fields:
            return PlannedResume(
                resume_id=resume.id if resume else None,
                filename=resume.original_filename if resume else None,
                reason="",
            )
        if resume is None:
            for row in file_fields:
                row.status = FillFieldStatus.NEEDS_HUMAN_INPUT
                row.reason = "No resume is selected for this application."
            return PlannedResume(
                will_upload=False,
                reason=(
                    "No resume is selected. Choose a resume before the file field "
                    "can be filled."
                ),
            )
        if not resume_owned:
            for row in file_fields:
                row.status = FillFieldStatus.NEEDS_HUMAN_INPUT
                row.reason = "The selected resume is not owned by this user."
            return PlannedResume(
                resume_id=resume.id,
                filename=resume.original_filename,
                reason="The selected resume is not owned by this user.",
            )
        return PlannedResume(
            resume_id=resume.id,
            filename=resume.original_filename,
            will_upload=True,
            file_field_ids=tuple(row.field_id for row in file_fields),
            reason="The selected resume will be attached to the employer's file field.",
        )


__all__ = [
    "MINIMUM_CONFIDENCE",
    "FieldExecutionPlanner",
    "FillPlan",
    "PlannedField",
    "PlannedResume",
]
