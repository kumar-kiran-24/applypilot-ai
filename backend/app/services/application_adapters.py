"""Modular application-platform adapters.

Each supported platform gets its own adapter instance (not one generic scraper).
An adapter knows how to classify its URLs, what its forms normally look like,
which selector to use and which extra questions to expect. It never invents a
successful submission: when browser automation is unavailable, inspection
reports ``declared`` questions and submission reports that manual completion is
required.

Browser interactions are delegated to an ``AutomationSession`` (see
``app.integrations.browser``). Passwords, OTPs, MFA codes and CAPTCHAs are never
handled here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from app.models.application import InspectionMode, QuestionType
from app.services.application_platforms import (
    PLATFORM_SPECS,
    ApplicationPlatform,
    DetectionConfidence,
    PlatformDetection,
    PlatformSpec,
    detect_platform,
    normalize_application_url,
    platform_catalog,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from app.integrations.browser.session import AutomationSession, RawFormField

SECTION_PERSONAL = "personal"
SECTION_CONTACT = "contact"
SECTION_ADDRESS = "address"
SECTION_EDUCATION = "education"
SECTION_EXPERIENCE = "experience"
SECTION_SKILLS = "skills"
SECTION_PROJECTS = "projects"
SECTION_CERTIFICATIONS = "certifications"
SECTION_COMPENSATION = "compensation"
SECTION_AVAILABILITY = "availability"
SECTION_AUTHORIZATION = "authorization"
SECTION_COMPANY = "company"
SECTION_DECLARATIONS = "declarations"
SECTION_DOCUMENTS = "documents"


@dataclass(frozen=True)
class FormFieldSpec:
    """One expected/known form field, keyed by its canonical concept."""

    question_key: str
    question_text: str
    field_type: QuestionType
    section: str
    required: bool = False
    options: tuple[str, ...] = ()
    is_sensitive: bool = False
    is_legal: bool = False
    help_text: str | None = None
    field_identifier: str | None = None


@dataclass(frozen=True)
class ApplicationInspection:
    """What an adapter could learn about one application form."""

    platform: ApplicationPlatform
    mode: InspectionMode
    fields: tuple[FormFieldSpec, ...]
    requires_account: bool | None
    account_reason: str | None
    notes: tuple[str, ...]
    form_found: bool


@dataclass(frozen=True)
class FillReport:
    """Result of filling a form; skipped fields are reported, never guessed."""

    filled: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    needs_user_action: tuple[str, ...] = ()


@dataclass(frozen=True)
class UploadReport:
    """Result of attaching the approved resume."""

    uploaded: bool
    filename: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class ValidationReport:
    """Client-side validation outcome before submission is attempted."""

    valid: bool
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class FormSelectors:
    """Platform-specific selectors used by the optional browser driver."""

    form: str = "form"
    resume_input: str = "input[type=file]"
    login_marker: str = (
        "input[type=password], a[href*='login'], a[href*='signin'], "
        "button:has-text('Sign in'), button:has-text('Create account')"
    )
    account_marker: str = (
        "a[href*='register'], a[href*='signup'], a[href*='create-account'], "
        "button:has-text('Create account'), button:has-text('Register')"
    )


# Label heuristics: map an inspected label/id onto the canonical question key.
_LABEL_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bfirst\s*name\b|\bgiven\s*name\b", re.I), "personal.first_name"),
    (re.compile(r"\blast\s*name\b|\bfamily\s*name\b|\bsurname\b", re.I), "personal.last_name"),
    (re.compile(r"\bpreferred\s*name\b", re.I), "personal.preferred_name"),
    (re.compile(r"\bfull\s*name\b|\bname\b", re.I), "personal.full_name"),
    (re.compile(r"\be-?mail\b", re.I), "contact.email"),
    (re.compile(r"\bphone\b|\bmobile\b|\btelephone\b", re.I), "contact.phone"),
    (re.compile(r"linkedin", re.I), "contact.linkedin"),
    (re.compile(r"github", re.I), "contact.github"),
    (re.compile(r"portfolio|website|personal\s*site", re.I), "contact.portfolio"),
    (re.compile(r"address\s*line\s*1|street\s*address|\baddress\b", re.I), "address.line1"),
    (re.compile(r"address\s*line\s*2", re.I), "address.line2"),
    (re.compile(r"\bcity\b|\btown\b", re.I), "address.city"),
    (re.compile(r"\bstate\b|\bprovince\b|\bregion\b", re.I), "address.region"),
    (re.compile(r"postal|zip", re.I), "address.postal_code"),
    (re.compile(r"\bcountry\b", re.I), "address.country"),
    (re.compile(r"current\s*company|present\s*employer", re.I), "experience.current_company"),
    (re.compile(r"current\s*(job\s*)?title|current\s*role", re.I), "experience.current_title"),
    (re.compile(r"university|college|school|institution", re.I), "education.institution"),
    (re.compile(r"\bdegree\b|qualification", re.I), "education.degree"),
    (re.compile(r"\bmajor\b|field\s*of\s*study", re.I), "education.field_of_study"),
    (re.compile(r"\bskills?\b", re.I), "skills.primary"),
    (re.compile(r"certification|certificate", re.I), "certifications.summary"),
    (
        re.compile(r"expected\s*(salary|compensation)|desired\s*salary", re.I),
        "compensation.expected_salary",
    ),
    (re.compile(r"notice\s*period", re.I), "availability.notice_period"),
    (
        re.compile(r"start\s*date|available\s*from|availability", re.I),
        "availability.available_from",
    ),
    (
        re.compile(r"authoriz|authoris|eligible\s*to\s*work|right\s*to\s*work", re.I),
        "authorization.status",
    ),
    (re.compile(r"sponsor|visa", re.I), "authorization.sponsorship"),
    (re.compile(r"relocat", re.I), "availability.relocation"),
    (re.compile(r"resume|\bcv\b", re.I), "documents.resume"),
    (re.compile(r"cover\s*letter", re.I), "company.cover_letter"),
    (
        re.compile(r"gender|race|ethnicity|veteran|disability", re.I),
        "declarations.eeo",
    ),
    (
        re.compile(r"consent|privacy|terms|certif|accurate|true\s*and\s*complete", re.I),
        "declarations.accuracy",
    ),
)

_EEO_KEYS = {"declarations.eeo"}
_SENSITIVE_KEYS = {
    "compensation.expected_salary",
    "availability.notice_period",
    "authorization.status",
    "authorization.sponsorship",
    "availability.relocation",
}
_LEGAL_KEYS = {"declarations.accuracy", "declarations.eeo"}

_SPEC_BY_PLATFORM = {spec.platform: spec for spec in PLATFORM_SPECS}


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.strip().casefold()).strip("-")
    return cleaned[:60] or "question"


def canonical_key_for_label(label: str) -> str | None:
    """Map a form label onto a canonical concept, or ``None`` when unknown."""
    for pattern, key in _LABEL_RULES:
        if pattern.search(label or ""):
            return key
    return None


def _section_for_key(key: str) -> str:
    if key.startswith("custom."):
        return SECTION_COMPANY
    prefix = key.split(".", 1)[0]
    return {
        "personal": SECTION_PERSONAL,
        "contact": SECTION_CONTACT,
        "address": SECTION_ADDRESS,
        "education": SECTION_EDUCATION,
        "experience": SECTION_EXPERIENCE,
        "skills": SECTION_SKILLS,
        "projects": SECTION_PROJECTS,
        "certifications": SECTION_CERTIFICATIONS,
        "compensation": SECTION_COMPENSATION,
        "availability": SECTION_AVAILABILITY,
        "authorization": SECTION_AUTHORIZATION,
        "company": SECTION_COMPANY,
        "declarations": SECTION_DECLARATIONS,
        "documents": SECTION_DOCUMENTS,
    }.get(prefix, SECTION_COMPANY)


def _field_from_raw(raw: RawFormField) -> FormFieldSpec:
    label = (raw.label or "").strip() or raw.identifier
    key = canonical_key_for_label(f"{label} {raw.identifier}")
    if key is None:
        key = f"custom.{_slug(label)}"
    return FormFieldSpec(
        question_key=key,
        question_text=label,
        field_type=raw.field_type,
        section=_section_for_key(key),
        required=raw.required,
        options=raw.options,
        is_sensitive=key in _SENSITIVE_KEYS,
        is_legal=key in _LEGAL_KEYS or key in _EEO_KEYS,
        field_identifier=raw.identifier,
        help_text="Detected on the employer's form.",
    )


class ApplicationPlatformAdapter:
    """Base adapter contract shared by every platform."""

    spec: ClassVar[PlatformSpec]
    selectors: ClassVar[FormSelectors] = FormSelectors()
    # Extra questions this platform commonly adds on top of the core set.
    declared_fields: ClassVar[tuple[FormFieldSpec, ...]] = ()

    @property
    def platform(self) -> ApplicationPlatform:
        return self.spec.platform

    @property
    def display_name(self) -> str:
        return self.spec.display_name

    @property
    def supports_automation(self) -> bool:
        return self.spec.supports_automation

    @property
    def requires_account(self) -> bool | None:
        return self.spec.requires_account

    def detect_platform(self, url: str) -> PlatformDetection:
        """Classify a URL and confirm it belongs to this adapter."""
        detection = detect_platform(url)
        if detection.platform is not self.platform:
            raise ValueError(
                f"{self.display_name} cannot handle a {detection.platform.value} URL."
            )
        return detection

    def _declared_inspection(
        self, detection: PlatformDetection, note: str
    ) -> ApplicationInspection:
        return ApplicationInspection(
            platform=self.platform,
            mode=InspectionMode.DECLARED,
            fields=self.declared_fields,
            requires_account=self.requires_account,
            account_reason=(
                f"{self.display_name} normally asks candidates to create an account "
                "before applying. ApplyPilot will never create one without your consent."
                if self.requires_account
                else None
            ),
            notes=(note, *detection.notes),
            form_found=False,
        )

    async def inspect_application(
        self,
        url: str,
        session: AutomationSession | None = None,
        *,
        max_fields: int = 150,
    ) -> ApplicationInspection:
        """Inspect the live form, or report honestly that it could not be read."""
        detection = self.detect_platform(url)
        if session is None or not self.supports_automation:
            return self._declared_inspection(
                detection,
                "The live form could not be inspected. The questions below are "
                "ApplyPilot's standard set for this platform; confirm them against "
                "the employer's form before submitting.",
            )

        raw_fields, login_required = await session.inspect(self.selectors)
        fields = tuple(_field_from_raw(raw) for raw in raw_fields[:max_fields])
        notes: list[str] = []
        if login_required:
            notes.append(
                "The form asks you to sign in or register. ApplyPilot never enters "
                "passwords, OTPs or CAPTCHA answers."
            )
        if not fields:
            notes.append(
                "No form fields were detected. The page may load its form in stages; "
                "use the manual checklist instead."
            )
        mode = InspectionMode.AUTOMATED if fields else InspectionMode.MANUAL
        return ApplicationInspection(
            platform=self.platform,
            mode=mode,
            fields=fields if fields else self.declared_fields,
            requires_account=login_required or bool(self.requires_account),
            account_reason=(
                "The employer's form requires an account before an application "
                "can be submitted."
                if login_required or self.requires_account
                else None
            ),
            notes=tuple(notes) or ("The live form was inspected.",),
            form_found=bool(fields),
        )

    def extract_form_fields(
        self, inspection: ApplicationInspection
    ) -> tuple[FormFieldSpec, ...]:
        """All fields known for this form, in a stable order."""
        return tuple(inspection.fields)

    def extract_required_questions(
        self, inspection: ApplicationInspection
    ) -> tuple[FormFieldSpec, ...]:
        """The subset the form marks as required."""
        return tuple(field for field in inspection.fields if field.required)

    async def fill_supported_fields(
        self,
        session: AutomationSession,
        fields: tuple[FormFieldSpec, ...],
        values: Mapping[str, str],
    ) -> FillReport:
        """Fill only fields with a confirmed answer and a known identifier."""
        mapping: dict[str, str] = {}
        skipped: list[str] = []
        key_by_identifier: dict[str, str] = {}
        for item in fields:
            value = (values.get(item.question_key) or "").strip()
            if not item.field_identifier or not value or item.field_type is QuestionType.FILE:
                skipped.append(item.question_key)
                continue
            mapping[item.field_identifier] = value
            key_by_identifier[item.field_identifier] = item.question_key
        identifiers = await session.fill(mapping, self.selectors) if mapping else []
        filled = tuple(key_by_identifier[item] for item in identifiers if item in key_by_identifier)
        return FillReport(
            filled=filled,
            skipped=tuple(key for key in skipped if key not in filled),
            needs_user_action=(),
        )

    async def upload_resume(
        self, session: AutomationSession, resume_path: str | None, filename: str | None
    ) -> UploadReport:
        """Attach the approved PDF; never modifies the original file."""
        if not resume_path or not filename:
            return UploadReport(uploaded=False, reason="No approved resume selected.")
        if not self.supports_automation:
            return UploadReport(
                uploaded=False, reason="This platform is completed manually."
            )
        identifier = await session.upload_file(
            resume_path, self.selectors.resume_input
        )
        if identifier is None:
            return UploadReport(
                uploaded=False,
                reason="The resume field could not be found on the form.",
            )
        return UploadReport(uploaded=True, filename=filename)

    async def validate_application(self, session: AutomationSession) -> ValidationReport:
        """Ask the live page what is still invalid, without guessing."""
        errors = await session.validation_errors(self.selectors)
        return ValidationReport(valid=not errors, errors=tuple(errors))


class GreenhouseAdapter(ApplicationPlatformAdapter):
    """Greenhouse job-board forms."""

    spec = _SPEC_BY_PLATFORM[ApplicationPlatform.GREENHOUSE]
    selectors = FormSelectors(
        form="#application_form, form[action*='applications'], form",
        resume_input="input[type=file][name*='resume'], input[type=file]",
        login_marker="input[type=password]",
        account_marker="a[href*='create_account'], button:has-text('Create account')",
    )
    declared_fields = (
        FormFieldSpec(
            question_key="company.how_did_you_hear",
            question_text="How did you hear about this role?",
            field_type=QuestionType.TEXT,
            section=SECTION_COMPANY,
            required=False,
            help_text="Optional on most Greenhouse forms.",
        ),
    )


class LeverAdapter(ApplicationPlatformAdapter):
    """Lever postings."""

    spec = _SPEC_BY_PLATFORM[ApplicationPlatform.LEVER]
    selectors = FormSelectors(
        form="form#application-form, form.application-form, form",
        resume_input="input[type=file][name*='resume'], input[type=file]",
        login_marker="input[type=password]",
    )
    declared_fields = (
        FormFieldSpec(
            question_key="company.how_did_you_hear",
            question_text="How did you hear about this role?",
            field_type=QuestionType.TEXT,
            section=SECTION_COMPANY,
            required=False,
        ),
    )


class AshbyAdapter(ApplicationPlatformAdapter):
    """Ashby application forms."""

    spec = _SPEC_BY_PLATFORM[ApplicationPlatform.ASHBY]
    selectors = FormSelectors(
        form="form",
        resume_input="input[type=file]",
        login_marker="input[type=password]",
        account_marker="a[href*='signin'], a[href*='signup']",
    )
    declared_fields = ()


class SmartRecruitersAdapter(ApplicationPlatformAdapter):
    """SmartRecruiters application forms."""

    spec = _SPEC_BY_PLATFORM[ApplicationPlatform.SMARTRECRUITERS]
    selectors = FormSelectors(
        form="form",
        resume_input="input[type=file]",
        login_marker="input[type=password]",
        account_marker="a[href*='signup'], button:has-text('Create account')",
    )
    declared_fields = (
        FormFieldSpec(
            question_key="experience.current_company",
            question_text="Current company",
            field_type=QuestionType.TEXT,
            section=SECTION_EXPERIENCE,
            required=False,
        ),
    )


class WorkdayAdapter(ApplicationPlatformAdapter):
    """Workday: multi-step, account-gated; guided manually."""

    spec = _SPEC_BY_PLATFORM[ApplicationPlatform.WORKDAY]
    selectors = FormSelectors()
    declared_fields = (
        FormFieldSpec(
            question_key="company.previous_employment",
            question_text="Have you previously worked for this company?",
            field_type=QuestionType.BOOLEAN,
            section=SECTION_COMPANY,
            required=False,
        ),
    )


class ICIMSAdapter(ApplicationPlatformAdapter):
    """iCIMS career portals: layout varies, so guided manually."""

    spec = _SPEC_BY_PLATFORM[ApplicationPlatform.ICIMS]
    selectors = FormSelectors()
    declared_fields = ()


class CompanyHostedAdapter(ApplicationPlatformAdapter):
    """Unknown employer-hosted portals: preparation plus manual steps only."""

    spec = _SPEC_BY_PLATFORM[ApplicationPlatform.COMPANY_HOSTED]
    selectors = FormSelectors()
    declared_fields = ()


_ADAPTERS: dict[ApplicationPlatform, ApplicationPlatformAdapter] = {
    adapter.spec.platform: adapter
    for adapter in (
        GreenhouseAdapter(),
        LeverAdapter(),
        AshbyAdapter(),
        SmartRecruitersAdapter(),
        WorkdayAdapter(),
        ICIMSAdapter(),
        CompanyHostedAdapter(),
    )
}


def get_adapter(platform: ApplicationPlatform) -> ApplicationPlatformAdapter:
    """Return the adapter registered for a platform."""
    return _ADAPTERS[platform]


def adapter_for_url(url: str) -> tuple[ApplicationPlatformAdapter, PlatformDetection]:
    """Detect a URL and return its adapter plus detection details."""
    normalized = normalize_application_url(url)
    detection = detect_platform(normalized)
    if detection.confidence is DetectionConfidence.NONE and not detection.is_job_board_link:
        # Company-hosted is a valid, manual-only adapter.
        return _ADAPTERS[ApplicationPlatform.COMPANY_HOSTED], detection
    return _ADAPTERS[detection.platform], detection


def adapter_catalog() -> list[dict[str, object]]:
    """Platform catalog enriched with each adapter's declared questions."""
    return [
        {
            **entry,
            "declared_questions": [
                {
                    "question_key": field.question_key,
                    "question_text": field.question_text,
                    "field_type": field.field_type.value,
                    "required": field.required,
                }
                for field in _ADAPTERS[ApplicationPlatform(entry["platform"])].declared_fields
            ],
        }
        for entry in platform_catalog()
    ]


__all__ = [
    "SECTION_ADDRESS",
    "SECTION_AUTHORIZATION",
    "SECTION_CERTIFICATIONS",
    "SECTION_COMPANY",
    "SECTION_COMPENSATION",
    "SECTION_CONTACT",
    "SECTION_DECLARATIONS",
    "SECTION_DOCUMENTS",
    "SECTION_EDUCATION",
    "SECTION_EXPERIENCE",
    "SECTION_PERSONAL",
    "SECTION_PROJECTS",
    "SECTION_SKILLS",
    "ApplicationInspection",
    "ApplicationPlatformAdapter",
    "AshbyAdapter",
    "CompanyHostedAdapter",
    "FillReport",
    "FormFieldSpec",
    "FormSelectors",
    "GreenhouseAdapter",
    "ICIMSAdapter",
    "LeverAdapter",
    "SmartRecruitersAdapter",
    "UploadReport",
    "ValidationReport",
    "WorkdayAdapter",
    "adapter_catalog",
    "adapter_for_url",
    "canonical_key_for_label",
    "get_adapter",
]
