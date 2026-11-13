"""Explainable matching of application questions to reusable answers.

The matcher is deliberately deterministic: the same question, profile and answer
bank always produce the same decision. It never turns a resume extraction or an
AI draft into a verified fact, never invents a value, and refuses to reuse
passwords, one-time codes, authentication cookies, payment data or legal
declarations.

Matching works in three layers:

1. A canonical question key (``contact.phone``) is an exact match.
2. A normalised question label maps onto a canonical field category using
   conservative phrase rules and a small synonym set (``mobile number`` and
   ``contact number`` both mean ``contact.phone``).
3. Token similarity is used only to rank candidates *within* the same category,
   never to bridge two different meanings. A question about a desired company
   is therefore never answered with the candidate's current company.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from pydantic import ValidationError
from sqlalchemy import inspect as sa_inspect

from app.models.application import AnswerSource, QuestionStatus, QuestionType
from app.models.application_answer import ApplicationAnswer, VerificationStatus
from app.models.candidate import Candidate
from app.models.preference import RelocationPreference
from app.models.resume import Resume
from app.schemas.resume import ParsedResumeData
from app.services.answer_safety import is_forbidden_answer_storage

#: Default period after which a confirmed answer should be reconfirmed.
DEFAULT_FRESHNESS_DAYS = 365

#: Some facts change faster than others. Values are maximum ages in days.
FRESHNESS_DAYS_BY_CATEGORY: dict[str, int] = {
    "personal.full_name": 1095,
    "personal.first_name": 1095,
    "personal.last_name": 1095,
    "personal.preferred_name": 730,
    "contact.email": 730,
    "contact.phone": 365,
    "contact.linkedin": 365,
    "contact.github": 365,
    "contact.portfolio": 365,
    "address.line1": 365,
    "address.line2": 365,
    "address.city": 365,
    "address.region": 365,
    "address.postal_code": 365,
    "address.country": 730,
    "education.summary": 1825,
    "education.institution": 1825,
    "education.degree": 1825,
    "education.field_of_study": 1825,
    "experience.summary": 365,
    "experience.current_company": 180,
    "experience.current_title": 180,
    "skills.primary": 365,
    "projects.summary": 365,
    "certifications.summary": 730,
    "compensation.expected_salary": 90,
    "availability.notice_period": 120,
    "availability.available_from": 90,
    "availability.relocation": 180,
    "availability.work_mode": 180,
    "authorization.status": 180,
    "authorization.sponsorship": 180,
    "documents.resume": 365,
    "company.cover_letter": 30,
}

SENSITIVE_CATEGORIES: frozenset[str] = frozenset(
    {
        "compensation.expected_salary",
        "availability.notice_period",
        "availability.relocation",
        "authorization.status",
        "authorization.sponsorship",
    }
)

LEGAL_CATEGORIES: frozenset[str] = frozenset(
    {"declarations.accuracy", "declarations.eeo"}
)

# Keywords are matched as complete tokens. More specific categories are listed
# first so a tie on score keeps the safer meaning.
_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "personal.preferred_name": ("preferred name", "nickname"),
    "personal.first_name": ("first name", "given name", "forename"),
    "personal.last_name": ("last name", "family name", "surname"),
    "personal.full_name": ("full name", "legal name", "name"),
    "contact.email": ("email address", "email", "e mail", "mail address"),
    "contact.phone": (
        "phone number",
        "contact number",
        "mobile number",
        "phone",
        "country code",
    ),
    "contact.linkedin": ("linkedin profile", "linkedin url", "linkedin"),
    "contact.github": ("github profile", "github url", "github"),
    "contact.portfolio": ("portfolio url", "personal website", "portfolio"),
    "address.line1": ("address line 1", "street address", "mailing address", "address"),
    "address.line2": ("address line 2", "apartment", "suite", "unit"),
    "address.city": ("city", "town", "municipality"),
    "address.region": ("state province", "state", "province", "region", "county"),
    "address.postal_code": ("postal code", "zip code", "postcode", "zip"),
    "address.country": ("country", "nation"),
    "education.field_of_study": ("field of study", "major", "specialization", "discipline"),
    "education.degree": (
        "highest qualification",
        "highest degree",
        "education level",
        "academic qualification",
        "educational qualification",
        "qualification",
        "degree",
    ),
    "education.institution": (
        "university name",
        "college name",
        "school name",
        "institution name",
        "university",
        "college",
        "school",
        "institution",
        "alma mater",
    ),
    "education.summary": ("education history", "education background", "academic background"),
    "experience.current_company": (
        "company name",
        "employer name",
        "current company",
        "current employer",
        "present employer",
        "current organization",
    ),
    "experience.current_title": (
        "current job title",
        "job title",
        "position title",
        "current title",
        "current role",
        "current designation",
        "present designation",
    ),
    "experience.summary": ("work experience", "employment history", "professional experience"),
    "compensation.expected_salary": (
        "expected salary",
        "salary expectation",
        "salary requirement",
        "desired salary",
        "expected compensation",
        "compensation expectation",
        "expected pay",
    ),
    "availability.notice_period": ("notice period", "notice"),
    "availability.available_from": (
        "available from",
        "availability date",
        "earliest start date",
        "start date",
        "when can you start",
    ),
    "availability.work_mode": (
        "work mode",
        "work arrangement",
        "preferred work arrangement",
        "remote hybrid onsite",
    ),
    "availability.relocation": ("willing to relocate", "relocate", "relocation"),
    "authorization.sponsorship": (
        "visa sponsorship",
        "require sponsorship",
        "need sponsorship",
        "sponsorship",
    ),
    "authorization.status": (
        "work authorization",
        "work authorisation",
        "authorized to work",
        "authorised to work",
        "right to work",
        "legally eligible to work",
        "employment authorization",
        "work permit",
    ),
    "skills.primary": ("technical skills", "key skills", "skills", "areas of expertise"),
    "projects.summary": ("portfolio projects", "projects"),
    "certifications.summary": ("certifications", "certificates", "licenses"),
    "documents.resume": ("resume", "curriculum vitae", "cv"),
    "company.cover_letter": (
        "cover letter",
        "additional information",
        "anything else you want us to know",
    ),
    "declarations.eeo": (
        "sexual orientation",
        "gender identity",
        "ethnicity",
        "veteran status",
        "disability status",
        "gender",
        "race",
        "veteran",
        "disability",
    ),
    "declarations.accuracy": (
        "true and complete",
        "privacy policy",
        "terms and conditions",
        "certify",
        "declaration",
        "consent",
    ),
}

_KNOWN_CATEGORIES: tuple[str, ...] = tuple(_CATEGORY_KEYWORDS)

_TOKEN_SYNONYMS: dict[str, str] = {
    "mobile": "phone",
    "cell": "phone",
    "cellphone": "phone",
    "telephone": "phone",
    "authorisation": "authorization",
    "authorised": "authorized",
    "authorise": "authorize",
}

_SINGULAR_EXCEPTIONS = frozenset(
    {"status", "address", "business", "analysis", "process", "access"}
)

_BOILERPLATE = re.compile(
    r"^(?:please\s+)?(?:provide|enter|tell\s+us|kindly\s+provide|kindly\s+enter|"
    r"what\s+is|what\s+are|what's|enter|select|choose|describe|list)\b[\s:]*"
)

# These patterns reject a superficial word overlap when the meaning differs.
_EXCLUSION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\brelocation\s+(assistance|package|reimbursement|stipend|support|expenses)\b"),
    re.compile(r"\b(phone|mobile|cell)\s+(type|model|brand|carrier|plan|bill|number\s+of)\b"),
    re.compile(r"\b(desired|preferred|dream|ideal|target)\s+(company|employer)\b"),
    re.compile(r"\b(previous|prior|former)\s+(company|employer)\b"),
    re.compile(r"\b(company|employer)\s+(size|revenue|culture|website|mission|product|founded)\b"),
    re.compile(r"\bnotice\s+(pay|payment)\b"),
    re.compile(r"\bdriver'?s?\s+licen[cs]e\b"),
    re.compile(r"\b(unwilling|not\s+willing|refuse)\s+to\s+relocate\b"),
    re.compile(r"\b(offer|provide|available|include)\b.{0,30}\bsponsor"),
    re.compile(r"\bsponsor.{0,30}\b(offer|provide|available|include)\b"),
)


def _normalise(text: str) -> str:
    """Casefold, strip punctuation and collapse whitespace."""
    value = (text or "").casefold().replace("&", " and ")
    value = value.replace("e-mail", "email")
    value = re.sub(r"[^a-z0-9+#']+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    value = _BOILERPLATE.sub("", value).strip()
    value = re.sub(r"^your\s+", "", value).strip()
    return value


def _singular(token: str) -> str:
    if token in _SINGULAR_EXCEPTIONS or len(token) <= 3:
        return token
    if token.endswith("ies"):
        return f"{token[:-3]}y"
    if token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _tokens(text: str) -> set[str]:
    return {_singular(_TOKEN_SYNONYMS.get(token, token)) for token in _normalise(text).split()}


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _decimal_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return f"{value.normalize():f}"


def _date_text(value: date | datetime | None) -> str | None:
    return value.isoformat()[:10] if value else None


def _split_name(full_name: str | None) -> tuple[str | None, str | None]:
    parts = (full_name or "").split()
    if not parts:
        return None, None
    if len(parts) == 1:
        return parts[0], None
    return parts[0], " ".join(parts[1:])


def _join_lines(lines: list[str]) -> str | None:
    cleaned = [line for line in (item.strip() for item in lines) if line]
    return "\n".join(cleaned) if cleaned else None


def _comparable(value: str | None) -> str:
    """Normalise values for equality checks without inventing meaning."""
    return re.sub(r"[^a-z0-9]+", "", (value or "").casefold())


def _has_excluded_meaning(normalised_text: str) -> bool:
    return any(pattern.search(normalised_text) for pattern in _EXCLUSION_PATTERNS)


def _match_category(normalised_text: str) -> str | None:
    """Pick the most specific category whose phrase tokens all appear."""
    if not normalised_text:
        return None
    text_tokens = _tokens(normalised_text)
    normalised_tokens = set(normalised_text.split())
    best_category: str | None = None
    best_score = 0.0
    for category, phrases in _CATEGORY_KEYWORDS.items():
        for phrase in phrases:
            phrase_tokens = phrase.split()
            if not phrase_tokens or not set(phrase_tokens) <= text_tokens:
                continue
            score = float(len(phrase_tokens))
            if phrase in normalised_text or set(phrase_tokens) <= normalised_tokens:
                score += 0.5
            if score > best_score:
                best_category = category
                best_score = score
    return best_category


@dataclass(frozen=True)
class QuestionDescriptor:
    """Structured metadata for one form question."""

    question_key: str = ""
    question_text: str = ""
    field_identifier: str | None = None
    help_text: str | None = None
    field_type: QuestionType = QuestionType.TEXT
    options: tuple[str, ...] = ()
    required: bool = False
    is_sensitive: bool = False
    is_legal: bool = False

    @property
    def label(self) -> str:
        return self.question_text or self.field_identifier or self.question_key


@dataclass(frozen=True)
class MatchedAnswer:
    """One sourced answer, with everything needed to judge its safety."""

    question_key: str
    question_text: str
    value: str | None
    source: AnswerSource
    confidence: float
    status: QuestionStatus
    requires_confirmation: bool
    is_sensitive: bool = False
    is_legal: bool = False
    source_record_id: str | None = None
    last_confirmed_at: datetime | None = None
    explanation: str = ""
    stale: bool = False
    conflicts: tuple[str, ...] = ()

    @property
    def verified(self) -> bool:
        """Only a confirmed, non-stale answer may be reused as a fact."""
        return (
            self.status is QuestionStatus.CONFIRMED
            and not self.requires_confirmation
            and bool(self.value)
            and not self.stale
        )


@dataclass(frozen=True)
class MissingField:
    """A required question that still needs the user's attention."""

    question_key: str
    question_text: str
    reason: str
    is_sensitive: bool = False
    is_legal: bool = False


@dataclass(frozen=True)
class FreshnessAssessment:
    """Whether a stored answer is still recent enough to reuse."""

    fresh: bool
    age_days: int | None
    max_age_days: int
    reason: str | None = None
    needs_reconfirmation: bool = False


@dataclass(frozen=True)
class StaleAnswer:
    """A stored answer whose confirmation has expired."""

    question_key: str
    question_text: str
    last_confirmed_at: datetime | None
    age_days: int | None
    max_age_days: int
    reason: str


@dataclass(frozen=True)
class AnswerConflict:
    """A stored answer that disagrees with a verified source."""

    question_key: str
    question_text: str
    category: str | None
    stored_answer: str
    conflicting_answer: str
    conflicting_source: str
    reason: str
    resolution_options: tuple[str, ...] = ("keep_stored", "use_profile", "edited")


@dataclass(frozen=True)
class AnswerBankReview:
    """Freshness and conflict summary for one candidate's answer bank."""

    total: int
    verified: int
    stale: tuple[StaleAnswer, ...] = ()
    conflicts: tuple[AnswerConflict, ...] = ()
    unverified: tuple[str, ...] = ()


@dataclass(frozen=True)
class ApplicationAnswerMap:
    """The complete answer map for one application form."""

    answers: Mapping[str, MatchedAnswer]
    missing_required: tuple[MissingField, ...] = ()
    optional_unanswered: tuple[str, ...] = ()
    stale: tuple[StaleAnswer, ...] = ()
    conflicts: tuple[AnswerConflict, ...] = ()

    def get(self, question_key: str) -> MatchedAnswer | None:
        return self.answers.get(question_key)


@dataclass(frozen=True)
class _ProfileValue:
    value: str
    record_id: str | None = None


@dataclass(frozen=True)
class _ReusableEntry:
    question_key: str
    question_text: str
    value: str
    is_sensitive: bool
    confidence: float
    last_confirmed_at: datetime | None
    record_id: str | None
    freshness_days: int | None
    is_legal: bool = False


def _coerce_question(question: Any) -> QuestionDescriptor:
    if isinstance(question, QuestionDescriptor):
        return question
    if isinstance(question, str):
        return QuestionDescriptor(question_text=question, field_identifier=question)
    return QuestionDescriptor(
        question_key=str(getattr(question, "question_key", "") or ""),
        question_text=str(
            getattr(question, "question_text", "")
            or getattr(question, "label", "")
            or ""
        ),
        field_identifier=getattr(question, "field_identifier", None)
        or getattr(question, "identifier", None),
        help_text=getattr(question, "help_text", None),
        field_type=getattr(question, "field_type", QuestionType.TEXT) or QuestionType.TEXT,
        options=tuple(getattr(question, "options", ()) or ()),
        required=bool(getattr(question, "required", False)),
        is_sensitive=bool(getattr(question, "is_sensitive", False)),
        is_legal=bool(getattr(question, "is_legal", False)),
    )


def _loaded(instance: Any, attribute: str, default: Any = None) -> Any:
    """Read an attribute without triggering an async lazy load."""
    try:
        state = sa_inspect(instance)
        if attribute in state.unloaded:
            return default
    except Exception:  # pragma: no cover - defensive for detached instances
        pass
    try:
        return getattr(instance, attribute)
    except Exception:  # pragma: no cover - defensive for detached instances
        return default


def _related(instance: Any, attribute: str) -> list[Any]:
    """Read a loaded relationship without triggering an async lazy load."""
    return list(_loaded(instance, attribute, []) or [])


class ApplicationAnswerMatcher:
    """Match form questions to verified candidate information."""

    # --- Normalisation and classification ---------------------------------
    def normalize_question(self, text: str) -> str:
        """Return the canonical comparison form of a question label."""
        return _normalise(text)

    def identify_field_category(self, question: Any) -> str | None:
        """Map a question onto a canonical field category, or ``None``.

        A canonical key wins immediately. Otherwise the label, help text and
        field identifier are classified with conservative phrase rules.
        """
        descriptor = _coerce_question(question)
        raw_key = (descriptor.question_key or "").strip().casefold()
        if raw_key in _CATEGORY_KEYWORDS:
            return raw_key
        for category in _KNOWN_CATEGORIES:
            if raw_key.startswith(f"{category}.") or raw_key.startswith(f"{category}_"):
                return category

        text = _normalise(
            " ".join(
                part
                for part in (
                    descriptor.question_text,
                    descriptor.help_text,
                    descriptor.field_identifier,
                )
                if part
            )
        )
        if not text or _has_excluded_meaning(text):
            return None
        return _match_category(text)

    def semantic_similarity(self, left: str, right: str) -> float:
        """Return a 0..1 similarity, but only within the same field category.

        Two questions about different things never receive a positive score,
        however many words they share.
        """
        left_text = _coerce_question(left)
        right_text = _coerce_question(right)
        left_category = self.identify_field_category(left_text)
        right_category = self.identify_field_category(right_text)
        if left_category is None or left_category != right_category:
            return 0.0
        left_tokens = _tokens(left_text.label)
        right_tokens = _tokens(right_text.label)
        if not left_tokens or not right_tokens:
            return 0.7
        jaccard = len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
        return round(min(1.0, 0.7 + (0.3 * jaccard)), 3)

    # --- Profile and resume values ----------------------------------------
    def _candidate_values(self, candidate: Candidate | None) -> dict[str, _ProfileValue]:
        values: dict[str, _ProfileValue] = {}
        if candidate is None:
            return values

        def put(key: str, value: str | None, record_id: str | None = None) -> None:
            if value:
                values[key] = _ProfileValue(value=value, record_id=record_id)

        put("personal.full_name", _clean(candidate.full_name), "candidate.full_name")
        put(
            "personal.preferred_name",
            _clean(candidate.preferred_name),
            "candidate.preferred_name",
        )
        first, last = _split_name(candidate.full_name)
        put("personal.first_name", first, "candidate.full_name")
        put("personal.last_name", last, "candidate.full_name")
        put("contact.email", _clean(candidate.email), "candidate.email")
        phone = _clean(candidate.phone)
        if phone and candidate.phone_country_code:
            phone = f"{candidate.phone_country_code} {phone}".strip()
        put("contact.phone", phone, "candidate.phone")
        put("contact.linkedin", _clean(candidate.linkedin_url), "candidate.linkedin_url")
        put("contact.github", _clean(candidate.github_url), "candidate.github_url")
        put("contact.portfolio", _clean(candidate.portfolio_url), "candidate.portfolio_url")
        for key, attribute in (
            ("address.line1", "address_line1"),
            ("address.line2", "address_line2"),
            ("address.city", "city"),
            ("address.region", "region"),
            ("address.postal_code", "postal_code"),
            ("address.country", "country"),
        ):
            put(key, _clean(getattr(candidate, attribute, None)), f"candidate.{attribute}")

        education = _related(candidate, "education")
        if education:
            latest = education[0]
            record_id = f"candidate.education:{latest.id}" if latest.id else "candidate.education"
            put("education.institution", _clean(latest.institution), record_id)
            put("education.degree", _clean(latest.degree), record_id)
            put("education.field_of_study", _clean(latest.specialization), record_id)
            put(
                "education.summary",
                _join_lines(
                    [
                        " - ".join(
                            part for part in (entry.degree, entry.institution) if part
                        )
                        for entry in education
                    ]
                ),
                record_id,
            )

        experiences = _related(candidate, "experiences")
        if experiences:
            put(
                "experience.summary",
                _join_lines(
                    [
                        " - ".join(
                            part
                            for part in (entry.designation, entry.company)
                            if part
                        )
                        for entry in experiences
                    ]
                ),
                "candidate.experiences",
            )
            current = next(
                (entry for entry in experiences if getattr(entry, "is_current", False)),
                None,
            )
            if current is not None:
                record_id = (
                    f"candidate.experience:{current.id}"
                    if current.id
                    else "candidate.experiences"
                )
                put("experience.current_company", _clean(current.company), record_id)
                put("experience.current_title", _clean(current.designation), record_id)

        if candidate.skills:
            put("skills.primary", ", ".join(candidate.skills), "candidate.skills")
        projects = [
            entry.name for entry in _related(candidate, "projects") if entry.name
        ]
        if projects:
            put("projects.summary", _join_lines(projects), "candidate.projects")
        certifications = [
            " - ".join(part for part in (entry.name, entry.issuer) if part)
            for entry in _related(candidate, "certifications")
        ]
        if certifications:
            put(
                "certifications.summary",
                _join_lines(certifications),
                "candidate.certifications",
            )

        put(
            "authorization.status",
            _clean(candidate.work_authorization),
            "candidate.work_authorization",
        )
        if candidate.requires_sponsorship is not None:
            put(
                "authorization.sponsorship",
                "Yes" if candidate.requires_sponsorship else "No",
                "candidate.requires_sponsorship",
            )
        if candidate.notice_period_days is not None:
            put(
                "availability.notice_period",
                str(candidate.notice_period_days),
                "candidate.notice_period_days",
            )
        put(
            "availability.available_from",
            _date_text(candidate.available_from),
            "candidate.available_from",
        )

        preference = _loaded(candidate, "preference")
        if preference is not None:
            salary = _decimal_text(preference.expected_salary)
            if salary:
                put(
                    "compensation.expected_salary",
                    f"{salary} {preference.currency}".strip(),
                    "candidate.preference.expected_salary",
                )
            relocation = getattr(preference.relocation_preference, "value", None)
            if relocation and relocation != RelocationPreference.UNKNOWN.value:
                put(
                    "availability.relocation",
                    {
                        RelocationPreference.YES.value: "Yes",
                        RelocationPreference.NO.value: "No",
                        RelocationPreference.NEGOTIABLE.value: "Negotiable",
                    }.get(relocation, relocation),
                    "candidate.preference.relocation_preference",
                )
            work_modes = list(preference.preferred_work_modes or [])
            if work_modes:
                put(
                    "availability.work_mode",
                    {
                        "onsite": "On-site",
                        "hybrid": "Hybrid",
                        "remote": "Remote",
                    }.get(str(work_modes[0]).casefold(), str(work_modes[0])),
                    "candidate.preference.preferred_work_modes",
                )
        return values

    def _merge_profile_values(
        self, candidate: Candidate | None, profile_values: Mapping[str, str] | None
    ) -> dict[str, _ProfileValue]:
        matcher_values: dict[str, _ProfileValue] = {}
        if profile_values:
            for key, value in profile_values.items():
                cleaned = _clean(value)
                if cleaned:
                    matcher_values[key] = _ProfileValue(
                        value=cleaned, record_id=f"candidate:{key}"
                    )
        if candidate is not None:
            matcher_values.update(self._candidate_values(candidate))
        # Explicitly supplied values win over derived ones.
        if profile_values:
            for key, value in profile_values.items():
                cleaned = _clean(value)
                if cleaned:
                    matcher_values[key] = _ProfileValue(
                        value=cleaned,
                        record_id=matcher_values.get(key, _ProfileValue("", None)).record_id,
                    )
        return matcher_values

    def _resume_values(
        self, resume: Resume | None, resume_values: Mapping[str, str] | None
    ) -> dict[str, _ProfileValue]:
        if resume_values:
            return {
                key: _ProfileValue(value=_clean(value) or "", record_id=None)
                for key, value in resume_values.items()
                if _clean(value)
            }
        if resume is None:
            return {}
        try:
            data = ParsedResumeData.model_validate(resume.parsed_data or {})
        except ValidationError:
            data = ParsedResumeData()

        values: dict[str, _ProfileValue] = {}

        def put(key: str, value: str | None) -> None:
            if value:
                values[key] = _ProfileValue(value=value, record_id=f"resume:{resume.id}")

        put("personal.full_name", _clean(data.full_name))
        first, last = _split_name(data.full_name)
        put("personal.first_name", first)
        put("personal.last_name", last)
        put("contact.email", _clean(data.email))
        put("contact.phone", _clean(data.phone))
        for link in data.links:
            lowered = link.casefold()
            if "linkedin.com" in lowered:
                put("contact.linkedin", link)
            elif "github.com" in lowered:
                put("contact.github", link)
            else:
                put("contact.portfolio", link)
        if data.education:
            put("education.institution", _clean(data.education[0].institution))
            put("education.degree", _clean(data.education[0].degree))
            put(
                "education.summary",
                _join_lines(
                    [
                        " - ".join(
                            part for part in (entry.degree, entry.institution) if part
                        )
                        for entry in data.education
                    ]
                ),
            )
        if data.experience:
            put(
                "experience.summary",
                _join_lines(
                    [
                        " - ".join(part for part in (entry.title, entry.company) if part)
                        for entry in data.experience
                    ]
                ),
            )
            current = next((entry for entry in data.experience if entry.is_current), None)
            if current is not None:
                put("experience.current_company", _clean(current.company))
                put("experience.current_title", _clean(current.title))
        if resume.skills:
            put("skills.primary", ", ".join(resume.skills))
        if data.projects:
            put("projects.summary", _join_lines([item.name for item in data.projects]))
        if data.certifications:
            put("certifications.summary", _join_lines(list(data.certifications)))
        return values

    # --- Matching ----------------------------------------------------------
    def _answer(
        self,
        descriptor: QuestionDescriptor,
        value: str,
        *,
        source: AnswerSource,
        base_confidence: float,
        explanation: str,
        record_id: str | None,
        last_confirmed_at: datetime | None = None,
        sensitive: bool = False,
        stale: bool = False,
        conflicts: tuple[str, ...] = (),
        needs_confirmation: bool | None = None,
    ) -> MatchedAnswer:
        category = self.identify_field_category(descriptor)
        is_sensitive = bool(
            descriptor.is_sensitive
            or sensitive
            or (category and category in SENSITIVE_CATEGORIES)
        )
        is_legal = bool(
            descriptor.is_legal or (category and category in LEGAL_CATEGORIES)
        )
        requires_confirmation = (
            needs_confirmation
            if needs_confirmation is not None
            else bool(sensitive or is_sensitive or stale)
        )
        status = (
            QuestionStatus.CONFIRMED
            if not requires_confirmation and not stale
            else QuestionStatus.SUGGESTED
        )
        confidence = self.assess_answer_confidence(
            descriptor,
            source,
            base=base_confidence,
            stale=stale,
            conflicting=bool(conflicts),
            sensitive=is_sensitive,
            legal=is_legal,
        )
        return MatchedAnswer(
            question_key=descriptor.question_key,
            question_text=descriptor.label,
            value=value,
            source=source,
            confidence=confidence,
            status=status,
            requires_confirmation=requires_confirmation,
            is_sensitive=is_sensitive,
            is_legal=is_legal,
            source_record_id=record_id,
            last_confirmed_at=last_confirmed_at,
            explanation=explanation,
            stale=stale,
            conflicts=conflicts,
        )

    def match_candidate_profile(
        self,
        question: Any,
        candidate: Candidate | None,
        *,
        profile_values: Mapping[str, str] | None = None,
    ) -> MatchedAnswer | None:
        """Answer from verified profile data, exact key first then category."""
        descriptor = _coerce_question(question)
        category = self.identify_field_category(descriptor)
        values = self._merge_profile_values(candidate, profile_values)
        entry = values.get(descriptor.question_key.casefold() if descriptor.question_key else "")
        if entry is None and category is not None:
            entry = values.get(category)
        if entry is None or not entry.value:
            return None
        if descriptor.is_legal or (category in LEGAL_CATEGORIES):
            return None
        return self._answer(
            descriptor,
            entry.value,
            source=AnswerSource.VERIFIED_PROFILE,
            base_confidence=0.97,
            explanation=(
                f"Matched the verified profile field for "
                f"{category or descriptor.question_key}."
            ),
            record_id=entry.record_id,
            last_confirmed_at=getattr(candidate, "updated_at", None),
        )

    def match_resume_data(
        self,
        question: Any,
        resume: Resume | None,
        *,
        resume_values: Mapping[str, str] | None = None,
    ) -> MatchedAnswer | None:
        """Suggest a value parsed from a resume. It is never verified."""
        descriptor = _coerce_question(question)
        category = self.identify_field_category(descriptor)
        values = self._resume_values(resume, resume_values)
        entry = values.get(descriptor.question_key.casefold() if descriptor.question_key else "")
        if entry is None and category is not None:
            entry = values.get(category)
        if entry is None or not entry.value:
            return None
        if descriptor.is_legal or (category in LEGAL_CATEGORIES):
            return None
        return self._answer(
            descriptor,
            entry.value,
            source=AnswerSource.RESUME_SUGGESTION,
            base_confidence=0.6,
            explanation=(
                "Suggested from the parsed resume. Confirm it before it is used "
                "as a fact."
            ),
            record_id=entry.record_id,
            last_confirmed_at=None,
            needs_confirmation=True,
        )

    @staticmethod
    def _reusable_entries(
        reusable_answers: Sequence[ApplicationAnswer] | Mapping[str, str] | None,
    ) -> list[_ReusableEntry]:
        if reusable_answers is None:
            return []
        if isinstance(reusable_answers, Mapping):
            supplied_at = datetime.now(timezone.utc)
            return [
                _ReusableEntry(
                    question_key=str(key).strip().lower(),
                    question_text="",
                    value=str(value).strip(),
                    is_sensitive=False,
                    confidence=0.97,
                    last_confirmed_at=supplied_at,
                    record_id=f"application_answer:{str(key).strip().lower()}",
                    freshness_days=None,
                )
                for key, value in reusable_answers.items()
                if _clean(value)
            ]
        entries: list[_ReusableEntry] = []
        for answer in reusable_answers:
            if answer.verification_status is not VerificationStatus.VERIFIED:
                continue
            if answer.is_legal or not _clean(answer.answer):
                continue
            if is_forbidden_answer_storage(answer.question_key, answer.question_text):
                continue
            entries.append(
                _ReusableEntry(
                    question_key=answer.question_key.strip().lower(),
                    question_text=answer.question_text,
                    value=str(answer.answer).strip(),
                    is_sensitive=bool(answer.is_sensitive),
                    confidence=float(answer.confidence or 0.0),
                    last_confirmed_at=answer.last_confirmed_at or answer.verified_at,
                    record_id=f"application_answer:{answer.question_key}",
                    freshness_days=answer.freshness_days,
                    is_legal=bool(answer.is_legal),
                )
            )
        return entries

    def match_reusable_answers(
        self,
        question: Any,
        reusable_answers: Sequence[ApplicationAnswer] | Mapping[str, str] | None,
        *,
        now: datetime | None = None,
    ) -> MatchedAnswer | None:
        """Reuse only explicitly confirmed, non-legal, non-forbidden answers."""
        descriptor = _coerce_question(question)
        category = self.identify_field_category(descriptor)
        if descriptor.is_legal or category in LEGAL_CATEGORIES:
            return None

        best: tuple[float, _ReusableEntry] | None = None
        normalised_text = _normalise(descriptor.label)
        for entry in self._reusable_entries(reusable_answers):
            exact_key = bool(descriptor.question_key) and (
                entry.question_key == descriptor.question_key.strip().lower()
            )
            exact_text = bool(normalised_text) and (
                _normalise(entry.question_text) == normalised_text
            )
            entry_category = category or self.identify_field_category(
                f"{entry.question_key} {entry.question_text}"
            )
            if exact_key or exact_text:
                score = 2.0
            elif entry_category is not None and entry_category == category:
                score = self.semantic_similarity(descriptor, entry.question_text)
                if score < 0.7:
                    continue
            else:
                continue
            if best is None or score > best[0]:
                best = (score, entry)
        if best is None:
            return None

        score, entry = best
        exact = score >= 2.0
        freshness = self.validate_answer_freshness(
            {
                "question_key": entry.question_key,
                "question_text": entry.question_text,
                "last_confirmed_at": entry.last_confirmed_at,
                "freshness_days": entry.freshness_days,
            },
            category=category,
            now=now,
        )
        if not freshness.fresh:
            return self._answer(
                descriptor,
                entry.value,
                source=AnswerSource.NEEDS_CONFIRMATION,
                base_confidence=0.4,
                explanation=(
                    f"A previously approved answer exists, but {freshness.reason}"
                ),
                record_id=entry.record_id,
                last_confirmed_at=entry.last_confirmed_at,
                sensitive=entry.is_sensitive,
                stale=True,
                needs_confirmation=True,
            )
        return self._answer(
            descriptor,
            entry.value,
            source=AnswerSource.CONFIRMED_REUSABLE_ANSWER,
            base_confidence=0.96 if exact else 0.84,
            explanation=(
                "Reused an answer you confirmed for a previous application."
                if exact
                else "Semantically matched an answer you confirmed previously."
            ),
            record_id=entry.record_id,
            last_confirmed_at=entry.last_confirmed_at,
            sensitive=entry.is_sensitive,
        )

    # --- Confidence and freshness -----------------------------------------
    def assess_answer_confidence(
        self,
        question: Any,
        source: AnswerSource,
        *,
        base: float | None = None,
        stale: bool = False,
        conflicting: bool = False,
        sensitive: bool = False,
        legal: bool = False,
    ) -> float:
        """Combine source reliability with freshness and conflict penalties."""
        del question  # reserved for future per-question rules
        defaults = {
            AnswerSource.VERIFIED_PROFILE: 0.97,
            AnswerSource.CONFIRMED_REUSABLE_ANSWER: 0.95,
            AnswerSource.RESUME_SUGGESTION: 0.6,
            AnswerSource.AI_DRAFT: 0.35,
            AnswerSource.USER_ENTERED: 1.0,
            AnswerSource.NEEDS_CONFIRMATION: 0.4,
        }
        score = base if base is not None else defaults.get(source, 0.5)
        if stale:
            score -= 0.3
        if conflicting:
            score -= 0.4
        if sensitive:
            score -= 0.03
        if legal:
            return 0.0
        if source in {AnswerSource.RESUME_SUGGESTION, AnswerSource.AI_DRAFT}:
            score = min(score, 0.75)
        return max(0.0, min(1.0, round(score, 3)))

    def validate_answer_freshness(
        self,
        answer: Any,
        *,
        category: str | None = None,
        now: datetime | None = None,
    ) -> FreshnessAssessment:
        """Check whether an answer's last confirmation is still recent enough."""
        if isinstance(answer, Mapping):
            question_key = str(answer.get("question_key") or "")
            question_text = str(answer.get("question_text") or "")
            last_confirmed_at = answer.get("last_confirmed_at") or answer.get("verified_at")
            freshness_days = answer.get("freshness_days")
        else:
            question_key = str(getattr(answer, "question_key", "") or "")
            question_text = str(getattr(answer, "question_text", "") or "")
            last_confirmed_at = getattr(answer, "last_confirmed_at", None) or getattr(
                answer, "verified_at", None
            )
            freshness_days = getattr(answer, "freshness_days", None)

        category = category or self.identify_field_category(
            f"{question_key} {question_text}"
        )
        max_age = int(freshness_days) if freshness_days else FRESHNESS_DAYS_BY_CATEGORY.get(
            category or "", DEFAULT_FRESHNESS_DAYS
        )
        if last_confirmed_at is None:
            reason = "It has no recorded confirmation date."
            return FreshnessAssessment(
                fresh=False,
                age_days=None,
                max_age_days=max_age,
                reason=reason,
                needs_reconfirmation=True,
            )
        reference = now or datetime.now(timezone.utc)
        last = last_confirmed_at
        if isinstance(last, str):
            try:
                last = datetime.fromisoformat(last)
            except ValueError:
                return FreshnessAssessment(
                    fresh=False,
                    age_days=None,
                    max_age_days=max_age,
                    reason="Its confirmation date could not be read.",
                    needs_reconfirmation=True,
                )
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        age_days = max(0, (reference - last).days)
        if age_days > max_age:
            reason = (
                f"It was last confirmed {age_days} days ago; answers of this type "
                f"are reconfirmed every {max_age} days."
            )
            return FreshnessAssessment(
                fresh=False,
                age_days=age_days,
                max_age_days=max_age,
                reason=reason,
                needs_reconfirmation=True,
            )
        return FreshnessAssessment(
            fresh=True,
            age_days=age_days,
            max_age_days=max_age,
        )

    # --- Missing fields ----------------------------------------------------
    def identify_missing_required_fields(
        self,
        questions: Sequence[Any],
        answer_map: Mapping[str, MatchedAnswer] | ApplicationAnswerMap,
    ) -> tuple[MissingField, ...]:
        """Return every required question that is unanswered or unconfirmed."""
        answers = (
            answer_map.answers
            if isinstance(answer_map, ApplicationAnswerMap)
            else answer_map
        )
        missing: list[MissingField] = []
        for question in questions:
            descriptor = _coerce_question(question)
            if not descriptor.required:
                continue
            matched = answers.get(descriptor.question_key)
            if matched is not None and matched.verified:
                continue
            if matched is not None and matched.is_legal:
                reason = "Legal declarations must be confirmed for this application."
            elif matched is None or not matched.value:
                reason = "Required information is missing."
            elif matched.stale:
                reason = matched.explanation or "This answer is outdated."
            elif matched.requires_confirmation:
                reason = "This answer needs your confirmation before it can be used."
            else:
                reason = "This answer is not verified."
            missing.append(
                MissingField(
                    question_key=descriptor.question_key,
                    question_text=descriptor.label,
                    reason=reason,
                    is_sensitive=descriptor.is_sensitive
                    or bool(matched and matched.is_sensitive),
                    is_legal=descriptor.is_legal
                    or bool(matched and matched.is_legal),
                )
            )
        return tuple(missing)

    # --- Conflict and stale detection -------------------------------------
    def detect_stale_answers(
        self,
        answers: Sequence[ApplicationAnswer],
        *,
        now: datetime | None = None,
    ) -> tuple[StaleAnswer, ...]:
        """Return verified answers that should be reconfirmed before reuse."""
        stale: list[StaleAnswer] = []
        for answer in answers:
            if answer.verification_status is not VerificationStatus.VERIFIED:
                continue
            if answer.is_legal or not _clean(answer.answer):
                continue
            if is_forbidden_answer_storage(answer.question_key, answer.question_text):
                continue
            assessment = self.validate_answer_freshness(answer, now=now)
            if assessment.fresh:
                continue
            stale.append(
                StaleAnswer(
                    question_key=answer.question_key,
                    question_text=answer.question_text,
                    last_confirmed_at=answer.last_confirmed_at or answer.verified_at,
                    age_days=assessment.age_days,
                    max_age_days=assessment.max_age_days,
                    reason=assessment.reason or "This answer is outdated.",
                )
            )
        return tuple(stale)

    def detect_answer_conflicts(
        self,
        candidate: Candidate | None,
        answers: Sequence[ApplicationAnswer],
        *,
        profile_values: Mapping[str, str] | None = None,
    ) -> tuple[AnswerConflict, ...]:
        """Find stored answers that disagree with the profile or each other."""
        verified = [
            answer
            for answer in answers
            if answer.verification_status is VerificationStatus.VERIFIED
            and not answer.is_legal
            and _clean(answer.answer)
            and not is_forbidden_answer_storage(answer.question_key, answer.question_text)
        ]
        if not verified:
            return ()

        profile = self._merge_profile_values(candidate, profile_values)
        conflicts: list[AnswerConflict] = []
        seen: set[tuple[str, str]] = set()
        for answer in verified:
            category = self.identify_field_category(
                f"{answer.question_key} {answer.question_text}"
            )
            if category is None:
                continue
            profile_value = profile.get(category)
            if profile_value is None or not profile_value.value:
                continue
            if _comparable(answer.answer) == _comparable(profile_value.value):
                continue
            key = (answer.question_key, "profile")
            if key in seen:
                continue
            seen.add(key)
            conflicts.append(
                AnswerConflict(
                    question_key=answer.question_key,
                    question_text=answer.question_text,
                    category=category,
                    stored_answer=str(answer.answer),
                    conflicting_answer=profile_value.value,
                    conflicting_source="profile",
                    reason=(
                        "Your verified profile now says something different from "
                        "this stored answer."
                    ),
                )
            )

        by_category: dict[str, list[ApplicationAnswer]] = {}
        for answer in verified:
            category = self.identify_field_category(
                f"{answer.question_key} {answer.question_text}"
            )
            if category is not None:
                by_category.setdefault(category, []).append(answer)
        for category, group in by_category.items():
            if len(group) < 2:
                continue
            for index, answer in enumerate(group):
                for other in group[index + 1 :]:
                    if _comparable(answer.answer) == _comparable(other.answer):
                        continue
                    key = (answer.question_key, other.question_key)
                    if key in seen:
                        continue
                    seen.add(key)
                    conflicts.append(
                        AnswerConflict(
                            question_key=answer.question_key,
                            question_text=answer.question_text,
                            category=category,
                            stored_answer=str(answer.answer),
                            conflicting_answer=str(other.answer),
                            conflicting_source=other.question_key,
                            reason=(
                                "Two reusable answers describe the same field with "
                                "different values."
                            ),
                        )
                    )
        return tuple(conflicts)

    # --- Whole-form mapping -------------------------------------------------
    @staticmethod
    def _ai_value(
        descriptor: QuestionDescriptor, ai_suggestions: Mapping[str, str] | None
    ) -> str | None:
        if not ai_suggestions:
            return None
        if descriptor.question_key:
            for key, value in ai_suggestions.items():
                if key.strip().lower() == descriptor.question_key.strip().lower():
                    return _clean(value)
        target = _normalise(descriptor.label)
        for key, value in ai_suggestions.items():
            if target and _normalise(key) == target:
                return _clean(value)
        return None

    def build_application_answer_map(
        self,
        questions: Sequence[Any],
        *,
        candidate: Candidate | None,
        resume: Resume | None = None,
        reusable_answers: Sequence[ApplicationAnswer] | Mapping[str, str] | None = None,
        profile_values: Mapping[str, str] | None = None,
        resume_values: Mapping[str, str] | None = None,
        ai_suggestions: Mapping[str, str] | None = None,
        now: datetime | None = None,
    ) -> ApplicationAnswerMap:
        """Build a sourced answer for every question, never inventing values.

        Priority is verified profile, confirmed reusable answer, resume
        suggestion, then AI draft. Legal declarations are never pre-filled.
        """
        answers: dict[str, MatchedAnswer] = {}
        stale: list[StaleAnswer] = []
        optional_unanswered: list[str] = []

        for index, question in enumerate(questions):
            descriptor = _coerce_question(question)
            key = descriptor.question_key or (
                self.identify_field_category(descriptor) or f"question.{index}"
            )
            category = self.identify_field_category(descriptor)
            is_legal = descriptor.is_legal or category in LEGAL_CATEGORIES

            if is_legal:
                answers[key] = MatchedAnswer(
                    question_key=key,
                    question_text=descriptor.label,
                    value=None,
                    source=AnswerSource.NEEDS_CONFIRMATION,
                    confidence=0.0,
                    status=QuestionStatus.SUGGESTED,
                    requires_confirmation=True,
                    is_sensitive=descriptor.is_sensitive,
                    is_legal=True,
                    explanation=(
                        "Legal declarations must be confirmed for each application "
                        "and are never reused."
                    ),
                )
                continue

            matched = self.match_candidate_profile(
                descriptor, candidate, profile_values=profile_values
            )
            if matched is None:
                matched = self.match_reusable_answers(
                    descriptor, reusable_answers, now=now
                )
            if matched is None:
                matched = self.match_resume_data(
                    descriptor, resume, resume_values=resume_values
                )
            if matched is None:
                ai_value = self._ai_value(descriptor, ai_suggestions)
                if ai_value:
                    matched = self._answer(
                        descriptor,
                        ai_value,
                        source=AnswerSource.AI_DRAFT,
                        base_confidence=0.35,
                        explanation=(
                            "An AI draft that must be reviewed and confirmed; it is "
                            "never treated as a fact."
                        ),
                        record_id=None,
                        needs_confirmation=True,
                    )

            if matched is None:
                matched = MatchedAnswer(
                    question_key=key,
                    question_text=descriptor.label,
                    value=None,
                    source=AnswerSource.NEEDS_CONFIRMATION,
                    confidence=0.0,
                    status=QuestionStatus.MISSING,
                    requires_confirmation=descriptor.required,
                    is_sensitive=descriptor.is_sensitive,
                    is_legal=False,
                    explanation=(
                        "Required information is missing."
                        if descriptor.required
                        else "Optional question left unanswered."
                    ),
                )
                if not descriptor.required:
                    optional_unanswered.append(key)

            answers[key] = matched
            if matched.stale:
                assessment = self.validate_answer_freshness(
                    {
                        "question_key": key,
                        "question_text": descriptor.label,
                        "last_confirmed_at": matched.last_confirmed_at,
                    },
                    category=category,
                    now=now,
                )
                stale.append(
                    StaleAnswer(
                        question_key=key,
                        question_text=descriptor.label,
                        last_confirmed_at=matched.last_confirmed_at,
                        age_days=assessment.age_days,
                        max_age_days=assessment.max_age_days,
                        reason=assessment.reason or "This answer is outdated.",
                    )
                )

        answer_map = ApplicationAnswerMap(
            answers=answers,
            missing_required=self.identify_missing_required_fields(questions, answers),
            optional_unanswered=tuple(optional_unanswered),
            stale=tuple(stale),
            conflicts=self.detect_answer_conflicts(
                candidate,
                [
                    answer
                    for answer in (reusable_answers or [])
                    if isinstance(answer, ApplicationAnswer)
                ],
                profile_values=profile_values,
            ),
        )
        return answer_map


__all__ = [
    "DEFAULT_FRESHNESS_DAYS",
    "FRESHNESS_DAYS_BY_CATEGORY",
    "LEGAL_CATEGORIES",
    "SENSITIVE_CATEGORIES",
    "AnswerBankReview",
    "AnswerConflict",
    "ApplicationAnswerMap",
    "ApplicationAnswerMatcher",
    "FreshnessAssessment",
    "MatchedAnswer",
    "MissingField",
    "QuestionDescriptor",
    "StaleAnswer",
]
