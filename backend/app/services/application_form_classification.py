"""Conservative field classification for Phase 2E Stage B.

Classification is deterministic and independent of Playwright, so it can be
tested against synthetic HTML without launching a browser. A field is only
assigned a known category when its label, name or accessible metadata contains
a recognised phrase. Ambiguous and generic fields stay ``UNKNOWN`` rather than
being forced into a category to look confident.

Sensitive, legal and verification fields are always marked as requiring human
handling. A required field is never assumed safe to fill automatically.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.schemas.application_form import FieldCategory, FormFieldType
from app.services.application_form_extraction import RawFormField

#: Categories whose value must always come from the user in the browser.
HUMAN_ONLY_CATEGORIES: frozenset[FieldCategory] = frozenset(
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

SENSITIVE_CATEGORIES: frozenset[FieldCategory] = frozenset(
    {
        FieldCategory.CURRENT_SALARY,
        FieldCategory.EXPECTED_SALARY,
        FieldCategory.NOTICE_PERIOD,
        FieldCategory.RELOCATION,
        FieldCategory.WORK_AUTHORIZATION,
        FieldCategory.LEGAL_DECLARATION,
        FieldCategory.DEMOGRAPHIC_INFORMATION,
        FieldCategory.CAPTCHA,
        FieldCategory.OTP_VERIFICATION,
        FieldCategory.MFA_VERIFICATION,
    }
)

#: Fields whose current value is never returned, even when markup exposes it.
VALUE_HIDDEN_CATEGORIES: frozenset[FieldCategory] = frozenset(
    {
        FieldCategory.CAPTCHA,
        FieldCategory.OTP_VERIFICATION,
        FieldCategory.MFA_VERIFICATION,
        FieldCategory.LEGAL_DECLARATION,
        FieldCategory.DEMOGRAPHIC_INFORMATION,
        FieldCategory.WORK_AUTHORIZATION,
        FieldCategory.CURRENT_SALARY,
        FieldCategory.EXPECTED_SALARY,
        FieldCategory.NOTICE_PERIOD,
        FieldCategory.RELOCATION,
    }
)

_CATEGORY_PHRASES: dict[FieldCategory, tuple[str, ...]] = {
    FieldCategory.CAPTCHA: (
        "captcha",
        "recaptcha",
        "hcaptcha",
        "turnstile",
        "human verification",
        "verify you are human",
        "robot check",
        "security check",
    ),
    FieldCategory.MFA_VERIFICATION: (
        "multi factor authentication",
        "two factor authentication",
        "two factor code",
        "two factor",
        "authenticator code",
        "authentication code",
        "authenticator",
        "mfa",
        "2fa",
        "verify identity",
        "identity verification",
    ),
    FieldCategory.OTP_VERIFICATION: (
        "one time password",
        "one time code",
        "one time passcode",
        "verification code",
        "security code",
        "code sent",
        "email code",
        "sms code",
        "otp",
    ),
    FieldCategory.LEGAL_DECLARATION: (
        "legal declaration",
        "certification of accuracy",
        "accurate and complete",
        "true and complete",
        "i agree",
        "agree to terms",
        "terms and conditions",
        "privacy policy",
        "background check",
        "declaration",
        "certify",
        "consent",
    ),
    FieldCategory.DEMOGRAPHIC_INFORMATION: (
        "equal employment opportunity",
        "veteran status",
        "disability status",
        "gender identity",
        "sexual orientation",
        "demographic",
        "ethnicity",
        "pronouns",
        "gender",
        "race",
        "veteran",
        "disability",
        "eeo",
    ),
    FieldCategory.WORK_AUTHORIZATION: (
        "work authorization",
        "work authorisation",
        "legally eligible to work",
        "authorized to work",
        "authorised to work",
        "employment authorization",
        "right to work",
        "work permit",
        "visa sponsorship",
        "require sponsorship",
        "need sponsorship",
        "visa status",
        "sponsorship",
    ),
    FieldCategory.CURRENT_SALARY: (
        "current annual salary",
        "current compensation",
        "current salary",
        "present salary",
        "current ctc",
        "current pay",
    ),
    FieldCategory.EXPECTED_SALARY: (
        "compensation expectation",
        "salary expectation",
        "salary expectations",
        "expected compensation",
        "expected salary",
        "salary requirement",
        "desired salary",
        "expected pay",
        "desired pay",
    ),
    FieldCategory.NOTICE_PERIOD: (
        "notice period",
        "period of notice",
        "notice duration",
    ),
    FieldCategory.RELOCATION: (
        "willingness to relocate",
        "willing to relocate",
        "relocation",
        "relocate",
    ),
    FieldCategory.RESUME_UPLOAD: (
        "curriculum vitae",
        "resume upload",
        "upload resume",
        "attach resume",
        "resume file",
        "upload cv",
        "attach cv",
        "resume",
        "cv",
    ),
    FieldCategory.COVER_LETTER: (
        "cover letter",
        "motivation letter",
        "letter of interest",
        "covering letter",
    ),
    FieldCategory.LINKEDIN: ("linkedin profile", "linkedin url", "linkedin"),
    FieldCategory.GITHUB: ("github profile", "github url", "github username", "github"),
    FieldCategory.PORTFOLIO: (
        "portfolio link",
        "portfolio url",
        "personal website",
        "work samples",
        "portfolio",
        "website",
    ),
    FieldCategory.GRADUATION_YEAR: (
        "year of graduation",
        "graduation year",
        "graduation date",
        "year graduated",
        "completion year",
        "passing year",
        "grad year",
    ),
    FieldCategory.DEGREE: (
        "highest qualification",
        "highest degree",
        "education level",
        "academic qualification",
        "educational qualification",
        "field of study",
        "specialization",
        "qualification",
        "major",
        "degree",
    ),
    FieldCategory.COLLEGE: (
        "college name",
        "university name",
        "alma mater",
        "institution",
        "university",
        "college",
        "school",
    ),
    FieldCategory.EDUCATION: (
        "education history",
        "education background",
        "academic background",
        "academics",
        "education",
    ),
    FieldCategory.SKILLS: (
        "technical skills",
        "areas of expertise",
        "key skills",
        "competencies",
        "expertise",
        "skills",
    ),
    FieldCategory.EMPLOYMENT_HISTORY: (
        "employment history",
        "previous employment",
        "current employer",
        "current company",
        "current title",
        "current role",
        "work history",
        "job history",
        "job title",
        "designation",
        "employment",
        "employer",
    ),
    FieldCategory.EXPERIENCE: (
        "years of experience",
        "total experience",
        "work experience",
        "professional experience",
        "relevant experience",
        "experience",
    ),
    FieldCategory.PROJECTS: (
        "project portfolio",
        "personal projects",
        "project experience",
        "projects",
    ),
    FieldCategory.ADDRESS: (
        "street address",
        "mailing address",
        "address line 1",
        "address line 2",
        "address line",
        "postal code",
        "zip code",
        "postcode",
        "apartment",
        "province",
        "country",
        "suite",
        "address",
        "state",
        "zip",
    ),
    FieldCategory.LOCATION: (
        "current location",
        "where are you located",
        "country of residence",
        "based in",
        "location",
        "town",
        "city",
    ),
    FieldCategory.PHONE: (
        "contact number",
        "phone number",
        "mobile number",
        "cell phone",
        "telephone",
        "mobile",
        "phone",
        "cell",
    ),
    FieldCategory.EMAIL: ("email address", "mail address", "e mail", "email"),
    FieldCategory.PERSONAL_NAME: (
        "preferred name",
        "middle name",
        "family name",
        "given name",
        "legal name",
        "full name",
        "first name",
        "last name",
        "surname",
        "name",
    ),
}

_GENERIC_PHRASES = frozenset(
    {
        "name",
        "address",
        "state",
        "country",
        "city",
        "location",
        "experience",
        "education",
        "employment",
        "employer",
        "website",
        "consent",
        "declaration",
        "relocation",
        "sponsorship",
        "gender",
        "race",
        "veteran",
        "disability",
        "projects",
        "skills",
    }
)

#: Words that change a superficially matching phrase into another meaning.
_EXCLUSIONS: tuple[tuple[re.Pattern[str], frozenset[FieldCategory]], ...] = (
    (
        re.compile(
            r"\b(company|employer|organisation|organization|business|username|"
            r"user name|file)\b"
        ),
        frozenset({FieldCategory.PERSONAL_NAME}),
    ),
    (
        re.compile(r"\b(email|e mail|mail)\b"),
        frozenset({FieldCategory.ADDRESS}),
    ),
    (
        re.compile(r"\b(job|office|company|work site)\b"),
        frozenset({FieldCategory.LOCATION}),
    ),
)


def _normalise(text: str) -> str:
    value = (text or "").casefold()
    value = value.replace("résumé", "resume").replace("resume\u0301", "resume")
    value = value.replace("e-mail", "email").replace("e mail", "email")
    value = value.replace("&", " and ")
    value = re.sub(r"[^a-z0-9+#]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _singular(token: str) -> str:
    if len(token) <= 3 or token.endswith("ss"):
        return token
    if token.endswith("ies"):
        return f"{token[:-3]}y"
    if token.endswith("s"):
        return token[:-1]
    return token


def _tokens(normalised: str) -> set[str]:
    return {_singular(token) for token in normalised.split() if token}


@dataclass(frozen=True)
class CategoryDecision:
    category: FieldCategory
    confidence: float
    reason: str


@dataclass(frozen=True)
class SensitivityDecision:
    is_sensitive: bool
    is_legal: bool
    requires_human_input: bool
    requires_confirmation: bool
    reason: str


@dataclass(frozen=True)
class FieldClassification:
    category: FieldCategory
    confidence: float
    reason: str
    is_sensitive: bool
    is_legal: bool
    requires_human_input: bool
    requires_confirmation: bool


def _text_for(field: RawFormField) -> str:
    parts = [field.label, field.name or "", field.html_id or "", field.placeholder or ""]
    parts.extend(option.label for option in field.options[:20])
    return _normalise(" ".join(part for part in parts if part))


def _phrase_score(phrase: str, normalised: str, tokens: set[str]) -> float | None:
    phrase_normalised = _normalise(phrase)
    phrase_tokens = _tokens(phrase_normalised)
    if not phrase_tokens or not phrase_tokens.issubset(tokens):
        return None
    if phrase_tokens == {"name"} and phrase_normalised != "name":
        return None
    coverage = len(phrase_tokens) / max(1, len(tokens))
    base = 0.74 if phrase_normalised in _GENERIC_PHRASES else 0.88
    base += min(0.08, 0.02 * len(phrase_tokens))
    base += min(0.04, 0.03 * coverage)
    if phrase_normalised == normalised:
        base += 0.03
    return round(min(0.99, base), 3)


def _excluded(normalised: str, category: FieldCategory) -> bool:
    return any(
        category in excluded and pattern.search(normalised)
        for pattern, excluded in _EXCLUSIONS
    )


class FieldClassifier:
    """Classify one raw field without any browser or network dependency."""

    def classify_category(self, field: RawFormField) -> CategoryDecision:
        if field.field_type == "captcha":
            return CategoryDecision(
                FieldCategory.CAPTCHA,
                0.99,
                "The control is a CAPTCHA widget.",
            )
        if field.field_type == "password":
            return CategoryDecision(
                FieldCategory.OTHER,
                0.95,
                "Password fields are never read, stored or automated.",
            )
        if field.field_type == "hidden":
            return CategoryDecision(
                FieldCategory.OTHER,
                0.85,
                "Hidden bookkeeping field; it is not a question the user answers.",
            )

        normalised = _text_for(field)
        tokens = _tokens(normalised)
        candidates: list[tuple[float, FieldCategory, str]] = []
        for category, phrases in _CATEGORY_PHRASES.items():
            if _excluded(normalised, category):
                continue
            best_for_category = 0.0
            best_phrase = ""
            for phrase in phrases:
                score = _phrase_score(phrase, normalised, tokens)
                if score is not None and score > best_for_category:
                    best_for_category = score
                    best_phrase = phrase
            if best_for_category:
                candidates.append((best_for_category, category, best_phrase))

        candidates.sort(key=lambda item: item[0], reverse=True)
        if candidates:
            best_score, best_category, best_phrase = candidates[0]
            if (
                len(candidates) > 1
                and candidates[1][1] is not best_category
                and best_score - candidates[1][0] < 0.06
            ):
                return CategoryDecision(
                    FieldCategory.UNKNOWN,
                    round(min(0.4, best_score * 0.45), 3),
                    (
                        "The label could mean more than one thing "
                        f"('{best_phrase}' vs '{candidates[1][2]}'); it needs a human."
                    ),
                )
            if best_score < 0.55:
                return CategoryDecision(
                    FieldCategory.UNKNOWN,
                    best_score,
                    "No confident classification matched the field metadata.",
                )
            return CategoryDecision(
                best_category,
                best_score,
                f"Matched the field label/metadata phrase '{best_phrase}'.",
            )

        if field.field_type == "email":
            return CategoryDecision(
                FieldCategory.EMAIL, 0.8, "The input type is email."
            )
        if field.field_type == "phone":
            return CategoryDecision(
                FieldCategory.PHONE, 0.8, "The input type is telephone."
            )
        if field.field_type == "file":
            return CategoryDecision(
                FieldCategory.OTHER,
                0.55,
                "A generic file upload; its purpose is not clear from the label.",
            )
        return CategoryDecision(
            FieldCategory.UNKNOWN,
            0.0,
            "No recognised phrase described the field.",
        )


class FieldSensitivityClassifier:
    """Apply safety rules independently of classification confidence."""

    def assess(self, field: RawFormField, category: FieldCategory) -> SensitivityDecision:
        is_sensitive = category in SENSITIVE_CATEGORIES or field.field_type == "password"
        is_legal = category is FieldCategory.LEGAL_DECLARATION
        requires_confirmation = bool(is_sensitive or is_legal)

        if category in HUMAN_ONLY_CATEGORIES:
            requires_human_input = True
            if category is FieldCategory.OTHER:
                reason = (
                    "This control is not a safely automatable question; "
                    "the user must handle it."
                )
            else:
                reason = f"{category.value.replace('_', ' ').title()} requires the user."
        elif category in {
            FieldCategory.CURRENT_SALARY,
            FieldCategory.EXPECTED_SALARY,
            FieldCategory.NOTICE_PERIOD,
            FieldCategory.RELOCATION,
        }:
            requires_human_input = False
            requires_confirmation = True
            reason = "The value is sensitive and must be confirmed before use."
        else:
            requires_human_input = False
            reason = "The field shape is safe for a future, confirmed fill."

        if field.field_type == "hidden":
            requires_human_input = False
            requires_confirmation = False
            reason = "Hidden metadata is never filled."
        if field.field_type == "password":
            requires_human_input = True
            requires_confirmation = True
            reason = "Passwords are entered by the user and never handled by ApplyPilot."

        return SensitivityDecision(
            is_sensitive=is_sensitive,
            is_legal=is_legal,
            requires_human_input=requires_human_input,
            requires_confirmation=requires_confirmation,
            reason=reason,
        )


class FieldClassifierFacade:
    """Combine category and safety decisions into one result."""

    def __init__(
        self,
        classifier: FieldClassifier | None = None,
        sensitivity: FieldSensitivityClassifier | None = None,
    ) -> None:
        self.classifier = classifier or FieldClassifier()
        self.sensitivity = sensitivity or FieldSensitivityClassifier()

    def classify(self, field: RawFormField) -> FieldClassification:
        decision = self.classifier.classify_category(field)
        safety = self.sensitivity.assess(field, decision.category)
        reason = f"{decision.reason} {safety.reason}".strip()
        return FieldClassification(
            category=decision.category,
            confidence=decision.confidence,
            reason=reason,
            is_sensitive=safety.is_sensitive,
            is_legal=safety.is_legal,
            requires_human_input=safety.requires_human_input,
            requires_confirmation=safety.requires_confirmation,
        )


def field_type_for(raw: RawFormField) -> FormFieldType:
    """Map extraction types onto the public schema enum."""

    if raw.field_type == "captcha":
        return FormFieldType.CAPTCHA
    try:
        return FormFieldType(raw.field_type)
    except ValueError:
        return FormFieldType.OTHER


__all__ = [
    "HUMAN_ONLY_CATEGORIES",
    "SENSITIVE_CATEGORIES",
    "VALUE_HIDDEN_CATEGORIES",
    "CategoryDecision",
    "FieldClassification",
    "FieldClassifier",
    "FieldClassifierFacade",
    "FieldSensitivityClassifier",
    "SensitivityDecision",
    "field_type_for",
]
