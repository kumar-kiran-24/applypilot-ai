"""Phase 2E Stage B: detect and classify an application form without filling it.

The analyzer is deliberately pure: it receives HTML already fetched from the
owned browser session and returns structured fields. It never launches a
browser, clicks a control, submits anything, or reads a secret value.

Source suggestions reuse :class:`ApplicationAnswerMatcher`; there is no second
answer-matching engine here. The matcher only suggests a value and provenance;
Stage B never writes it into the employer's form.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.models.application import AnswerSource, QuestionType
from app.models.application_answer import ApplicationAnswer
from app.models.candidate import Candidate
from app.models.resume import Resume
from app.schemas.application_form import (
    DetectedFormField,
    FieldCategory,
    FieldSource,
    FieldSourceMatch,
    FormFieldType,
)
from app.services.application_answer_matching import (
    ApplicationAnswerMatcher,
    MatchedAnswer,
    QuestionDescriptor,
)
from app.services.application_form_classification import (
    HUMAN_ONLY_CATEGORIES,
    VALUE_HIDDEN_CATEGORIES,
    FieldClassification,
    FieldClassifierFacade,
    field_type_for,
)
from app.services.application_form_extraction import (
    FormPageContent,
    RawFormField,
    extract_form_snapshot,
)

#: Below this classification confidence a field is never considered automated.
AUTOMATION_CONFIDENCE_THRESHOLD = 0.75

#: Categories whose answer must be a specific shape, used only for descriptors.
_QUESTION_TYPES: dict[FormFieldType, QuestionType] = {
    FormFieldType.EMAIL: QuestionType.EMAIL,
    FormFieldType.PHONE: QuestionType.PHONE,
    FormFieldType.NUMBER: QuestionType.NUMBER,
    FormFieldType.DATE: QuestionType.DATE,
    FormFieldType.URL: QuestionType.URL,
    FormFieldType.TEXTAREA: QuestionType.TEXTAREA,
    FormFieldType.SELECT: QuestionType.SINGLE_CHOICE,
    FormFieldType.RADIO_GROUP: QuestionType.RADIO,
    FormFieldType.CHECKBOX_GROUP: QuestionType.MULTI_CHOICE,
    FormFieldType.CHECKBOX: QuestionType.CHECKBOX,
    FormFieldType.FILE: QuestionType.FILE,
    FormFieldType.PASSWORD: QuestionType.TEXT,
    FormFieldType.HIDDEN: QuestionType.TEXT,
    FormFieldType.CAPTCHA: QuestionType.TEXT,
}

#: Fallback canonical keys for source matching when text classification is vague.
_CATEGORY_KEYS: dict[FieldCategory, str] = {
    FieldCategory.PERSONAL_NAME: "personal.full_name",
    FieldCategory.EMAIL: "contact.email",
    FieldCategory.PHONE: "contact.phone",
    FieldCategory.LOCATION: "address.city",
    FieldCategory.ADDRESS: "address.line1",
    FieldCategory.EDUCATION: "education.summary",
    FieldCategory.DEGREE: "education.degree",
    FieldCategory.COLLEGE: "education.institution",
    FieldCategory.SKILLS: "skills.primary",
    FieldCategory.EXPERIENCE: "experience.summary",
    FieldCategory.EMPLOYMENT_HISTORY: "experience.summary",
    FieldCategory.PROJECTS: "projects.summary",
    FieldCategory.LINKEDIN: "contact.linkedin",
    FieldCategory.GITHUB: "contact.github",
    FieldCategory.PORTFOLIO: "contact.portfolio",
    FieldCategory.EXPECTED_SALARY: "compensation.expected_salary",
    FieldCategory.NOTICE_PERIOD: "availability.notice_period",
    FieldCategory.RELOCATION: "availability.relocation",
    FieldCategory.WORK_AUTHORIZATION: "authorization.status",
    FieldCategory.RESUME_UPLOAD: "documents.resume",
    FieldCategory.COVER_LETTER: "company.cover_letter",
    FieldCategory.LEGAL_DECLARATION: "declarations.accuracy",
    FieldCategory.DEMOGRAPHIC_INFORMATION: "declarations.eeo",
}

_NEVER_SUGGEST = frozenset(
    {
        FieldCategory.CAPTCHA,
        FieldCategory.OTP_VERIFICATION,
        FieldCategory.MFA_VERIFICATION,
        FieldCategory.LEGAL_DECLARATION,
        FieldCategory.DEMOGRAPHIC_INFORMATION,
        FieldCategory.OTHER,
        FieldCategory.UNKNOWN,
    }
)

_SOURCE_MAP: dict[AnswerSource, FieldSource] = {
    AnswerSource.VERIFIED_PROFILE: FieldSource.VERIFIED_PROFILE,
    AnswerSource.PROFILE: FieldSource.VERIFIED_PROFILE,
    AnswerSource.CONFIRMED_REUSABLE_ANSWER: FieldSource.CONFIRMED_REUSABLE_ANSWER,
    AnswerSource.PREVIOUS_ANSWER: FieldSource.CONFIRMED_REUSABLE_ANSWER,
    AnswerSource.USER_ENTERED: FieldSource.USER_PROVIDED,
    AnswerSource.USER_INPUT: FieldSource.USER_PROVIDED,
    AnswerSource.RESUME_SUGGESTION: FieldSource.RESUME_SUGGESTION,
    AnswerSource.RESUME: FieldSource.RESUME_SUGGESTION,
}


class FormPageUnavailableError(RuntimeError):
    """The live page returned no HTML and cannot be analysed safely."""


@dataclass(frozen=True)
class FormAnalysisResult:
    fields: tuple[DetectedFormField, ...]
    form_count: int
    warnings: tuple[str, ...] = ()

    @property
    def captcha_detected(self) -> bool:
        return any(item.classification is FieldCategory.CAPTCHA for item in self.fields)

    @property
    def otp_detected(self) -> bool:
        return any(item.classification is FieldCategory.OTP_VERIFICATION for item in self.fields)

    @property
    def mfa_detected(self) -> bool:
        return any(item.classification is FieldCategory.MFA_VERIFICATION for item in self.fields)

    @property
    def requires_human_input_count(self) -> int:
        return sum(1 for item in self.fields if item.requires_human_input)

    @property
    def automation_eligible_count(self) -> int:
        return sum(1 for item in self.fields if item.automation_eligible)

    @property
    def legal_declaration_count(self) -> int:
        return sum(1 for item in self.fields if item.is_legal)

    @property
    def sensitive_field_count(self) -> int:
        return sum(1 for item in self.fields if item.is_sensitive)


@dataclass
class _FieldInput:
    raw: RawFormField
    classification: FieldClassification
    field_type: FormFieldType
    key: str
    source: FieldSourceMatch = field(default_factory=FieldSourceMatch)


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return cleaned[:80] or "field"


def _stable_id(raw: RawFormField, index: int, seen: dict[str, int]) -> str:
    base = raw.html_id or raw.name or f"{raw.field_type}-{_slug(raw.label)}"
    base = f"form{raw.form_index}-{_slug(base)}"
    count = seen.get(base, 0) + 1
    seen[base] = count
    return base if count == 1 else f"{base}-{count}"


class FieldSourceMatcher:
    """Suggest sources using the existing Phase 2B/2C matcher only."""

    def __init__(self, matcher: ApplicationAnswerMatcher | None = None) -> None:
        self.matcher = matcher or ApplicationAnswerMatcher()

    def _descriptor(self, raw: RawFormField, category: FieldCategory) -> QuestionDescriptor:
        identified = self.matcher.identify_field_category(
            f"{raw.label} {raw.name or ''} {raw.placeholder or ''}"
        )
        key = identified or _CATEGORY_KEYS.get(category, "")
        field_type = field_type_for(raw)
        return QuestionDescriptor(
            question_key=key,
            question_text=raw.label,
            field_identifier=raw.html_id or raw.name,
            help_text=raw.help_text,
            field_type=_QUESTION_TYPES.get(field_type, QuestionType.TEXT),
            options=tuple(option.label for option in raw.options),
            required=raw.required,
            is_sensitive=category
            in {
                FieldCategory.CURRENT_SALARY,
                FieldCategory.EXPECTED_SALARY,
                FieldCategory.NOTICE_PERIOD,
                FieldCategory.RELOCATION,
                FieldCategory.WORK_AUTHORIZATION,
            },
            is_legal=category is FieldCategory.LEGAL_DECLARATION,
        )

    def match(
        self,
        raw: RawFormField,
        category: FieldCategory,
        *,
        candidate: Candidate | None,
        resume: Resume | None,
        reusable_answers: list[ApplicationAnswer] | None,
        user_answers: dict[str, str] | None,
    ) -> tuple[FieldSourceMatch, MatchedAnswer | None]:
        if category in _NEVER_SUGGEST or raw.field_type == "password":
            return (
                FieldSourceMatch(
                    source=FieldSource.NONE,
                    confidence=0.0,
                    explanation="No value may be suggested for this safety-sensitive field.",
                ),
                None,
            )

        descriptor = self._descriptor(raw, category)
        matched = self.matcher.match_candidate_profile(descriptor, candidate)

        if matched is None and user_answers:
            matched = self.matcher.match_reusable_answers(descriptor, user_answers)
            if matched is not None:
                matched = self._as_user_provided(matched)

        if matched is None and reusable_answers:
            matched = self.matcher.match_reusable_answers(descriptor, reusable_answers)

        if matched is None and category is not FieldCategory.WORK_AUTHORIZATION:
            matched = self.matcher.match_resume_data(descriptor, resume)

        if matched is None or not matched.value:
            return (
                FieldSourceMatch(
                    source=FieldSource.NONE,
                    confidence=0.0,
                    explanation=(
                        "No verified profile, confirmed answer or resume suggestion "
                        "matched this field."
                    ),
                ),
                None,
            )

        source = _SOURCE_MAP.get(matched.source, FieldSource.NONE)
        if matched.is_legal:
            source = FieldSource.NONE
        return (
            FieldSourceMatch(
                source=source,
                suggested_value=matched.value if source is not FieldSource.NONE else None,
                confidence=matched.confidence,
                source_record_id=matched.source_record_id,
                last_confirmed_at=matched.last_confirmed_at,
                stale=matched.stale,
                explanation=matched.explanation,
            ),
            matched,
        )

    @staticmethod
    def _as_user_provided(matched: MatchedAnswer) -> MatchedAnswer:
        return MatchedAnswer(
            question_key=matched.question_key,
            question_text=matched.question_text,
            value=matched.value,
            source=AnswerSource.USER_ENTERED,
            confidence=matched.confidence,
            status=matched.status,
            requires_confirmation=False,
            is_sensitive=matched.is_sensitive,
            is_legal=matched.is_legal,
            source_record_id=matched.source_record_id,
            last_confirmed_at=matched.last_confirmed_at,
            explanation="Confirmed by you for this application.",
            stale=False,
        )


class ApplicationFormAnalyzer:
    """Turn serialized page HTML into safety-labelled form fields."""

    def __init__(
        self,
        *,
        classifier: FieldClassifierFacade | None = None,
        source_matcher: FieldSourceMatcher | None = None,
        max_fields: int = 150,
    ) -> None:
        self.classifier = classifier or FieldClassifierFacade()
        self.source_matcher = source_matcher or FieldSourceMatcher()
        self.max_fields = max(1, max_fields)

    async def analyze_page(
        self,
        page: FormPageContent,
        *,
        candidate: Candidate | None = None,
        resume: Resume | None = None,
        reusable_answers: list[ApplicationAnswer] | None = None,
        user_answers: dict[str, str] | None = None,
    ) -> FormAnalysisResult:
        """Analyse a live page through the narrow, read-only content contract."""
        html = await page.content()
        if not (html or "").strip():
            raise FormPageUnavailableError(
                "The employer's page did not return readable HTML."
            )
        return self.analyze(
            html,
            candidate=candidate,
            resume=resume,
            reusable_answers=reusable_answers,
            user_answers=user_answers,
        )

    def analyze(
        self,
        html: str,
        *,
        candidate: Candidate | None = None,
        resume: Resume | None = None,
        reusable_answers: list[ApplicationAnswer] | None = None,
        user_answers: dict[str, str] | None = None,
    ) -> FormAnalysisResult:
        snapshot = extract_form_snapshot(html)
        warnings: list[str] = []
        fields = list(snapshot.fields)
        if len(fields) > self.max_fields:
            warnings.append(
                f"Only the first {self.max_fields} of {len(fields)} fields were "
                "returned; the page may contain a very large or multi-step form."
            )
            fields = fields[: self.max_fields]

        seen: dict[str, int] = {}
        rows: list[_FieldInput] = []
        for index, raw in enumerate(fields):
            classification = self.classifier.classify(raw)
            field_type = field_type_for(raw)
            decision = self.source_matcher.match(
                raw,
                classification.category,
                candidate=candidate,
                resume=resume,
                reusable_answers=reusable_answers,
                user_answers=user_answers,
            )
            rows.append(
                _FieldInput(
                    raw=raw,
                    classification=classification,
                    field_type=field_type,
                    key=_stable_id(raw, index, seen),
                    source=decision[0],
                )
            )

        # A required field with no source needs the user; an optional one may
        # simply be left blank. Verification and legal fields are always human.
        detected: list[DetectedFormField] = []
        for row in rows:
            classification = row.classification
            source = row.source
            has_source = (
                source.source is not FieldSource.NONE and bool(source.suggested_value)
            )
            requires_human = classification.requires_human_input
            requires_confirmation = classification.requires_confirmation
            if row.raw.required and not has_source:
                requires_human = True
                requires_confirmation = True
            if source.stale:
                requires_confirmation = True
            if source.source is FieldSource.RESUME_SUGGESTION:
                requires_confirmation = True
            if classification.confidence < AUTOMATION_CONFIDENCE_THRESHOLD:
                requires_human = classification.category not in {
                    FieldCategory.OTHER,
                    FieldCategory.UNKNOWN,
                } or requires_human
            if classification.category in HUMAN_ONLY_CATEGORIES:
                requires_human = True
                requires_confirmation = True

            current_value = row.raw.current_value
            if (
                current_value is None
                or not row.raw.visible
                or classification.category in VALUE_HIDDEN_CATEGORIES
                or classification.is_sensitive
                or classification.is_legal
            ):
                current_value = None

            automation_eligible = bool(
                has_source
                and not requires_human
                and not requires_confirmation
                and classification.category not in HUMAN_ONLY_CATEGORIES
                and classification.confidence >= AUTOMATION_CONFIDENCE_THRESHOLD
                and source.source
                in {
                    FieldSource.VERIFIED_PROFILE,
                    FieldSource.CONFIRMED_REUSABLE_ANSWER,
                    FieldSource.USER_PROVIDED,
                }
            )

            detected.append(
                DetectedFormField(
                    field_id=row.key,
                    form_index=row.raw.form_index,
                    label=row.raw.label[:500],
                    field_type=row.field_type,
                    classification=classification.category,
                    required=row.raw.required,
                    current_value=current_value,
                    options=[option.label[:250] for option in row.raw.options][:100],
                    placeholder=row.raw.placeholder,
                    help_text=row.raw.help_text,
                    name=row.raw.name,
                    html_id=row.raw.html_id,
                    visible=row.raw.visible,
                    confidence=classification.confidence,
                    requires_human_input=requires_human,
                    requires_confirmation=requires_confirmation,
                    is_sensitive=classification.is_sensitive,
                    is_legal=classification.is_legal,
                    automation_eligible=automation_eligible,
                    classification_reason=classification.reason[:1000],
                    source=source,
                )
            )

        if snapshot.form_count > 1:
            warnings.append(
                f"{snapshot.form_count} forms were detected on this page. "
                "Fields are grouped by form_index."
            )
        if any(item.classification is FieldCategory.CAPTCHA for item in detected):
            warnings.append(
                "A CAPTCHA was detected. ApplyPilot never solves it; complete it "
                "yourself in the browser."
            )
        if any(item.classification is FieldCategory.OTP_VERIFICATION for item in detected):
            warnings.append(
                "A one-time code field was detected. ApplyPilot never reads or "
                "enters verification codes."
            )
        if any(item.classification is FieldCategory.MFA_VERIFICATION for item in detected):
            warnings.append(
                "An MFA field was detected. Complete multi-factor verification yourself."
            )
        if any(item.is_legal for item in detected):
            warnings.append(
                "Legal declarations were detected. They are never auto-confirmed."
            )
        unknowns = sum(1 for item in detected if item.classification is FieldCategory.UNKNOWN)
        if unknowns:
            warnings.append(
                f"{unknowns} field{'s' if unknowns != 1 else ''} could not be "
                "classified confidently and require human review."
            )
        return FormAnalysisResult(
            fields=tuple(detected),
            form_count=snapshot.form_count,
            warnings=tuple(warnings),
        )


__all__ = [
    "AUTOMATION_CONFIDENCE_THRESHOLD",
    "ApplicationFormAnalyzer",
    "FieldSourceMatcher",
    "FormAnalysisResult",
    "FormPageUnavailableError",
]
