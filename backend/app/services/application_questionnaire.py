"""Build the dynamic application questionnaire for one posting.

Every answer is derived from a named source and carries a confidence:

1. ``profile`` - data the user already verified on their candidate profile.
2. ``resume`` - data parsed from the selected resume (a suggestion, not a fact).
3. ``previously_approved`` - an answer the user confirmed for a previous form.
4. ``ai_suggestion`` - reserved for model-generated drafts. It is never treated
   as verified and always requires explicit confirmation.
5. ``user_input`` - nothing reliable was available; the user must answer.

Sensitive and legal questions are always flagged and always require the user's
confirmation, even when the profile has a value.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from pydantic import ValidationError

from app.models.application import AnswerSource, QuestionStatus, QuestionType
from app.models.candidate import Candidate
from app.models.preference import RelocationPreference
from app.schemas.resume import ParsedResumeData
from app.services.application_adapters import (
    SECTION_ADDRESS,
    SECTION_AUTHORIZATION,
    SECTION_AVAILABILITY,
    SECTION_CERTIFICATIONS,
    SECTION_COMPANY,
    SECTION_COMPENSATION,
    SECTION_CONTACT,
    SECTION_DECLARATIONS,
    SECTION_DOCUMENTS,
    SECTION_EDUCATION,
    SECTION_EXPERIENCE,
    SECTION_PERSONAL,
    SECTION_PROJECTS,
    SECTION_SKILLS,
    ApplicationInspection,
)

if TYPE_CHECKING:
    from app.models.resume import Resume
    from app.models.user import User

SECTION_LABELS: dict[str, str] = {
    SECTION_PERSONAL: "Personal information",
    SECTION_CONTACT: "Contact details",
    SECTION_ADDRESS: "Address",
    SECTION_EDUCATION: "Education",
    SECTION_EXPERIENCE: "Work experience",
    SECTION_SKILLS: "Technical skills",
    SECTION_PROJECTS: "Projects",
    SECTION_CERTIFICATIONS: "Certifications",
    SECTION_COMPENSATION: "Compensation",
    SECTION_AVAILABILITY: "Availability",
    SECTION_AUTHORIZATION: "Work authorization",
    SECTION_COMPANY: "Company questions",
    SECTION_DECLARATIONS: "Declarations",
    SECTION_DOCUMENTS: "Documents",
}

# Section display order used by the API and the UI.
SECTION_ORDER: tuple[str, ...] = tuple(SECTION_LABELS)


@dataclass(frozen=True)
class QuestionDefinition:
    """Static definition of a canonical application question."""

    question_key: str
    question_text: str
    field_type: QuestionType
    section: str
    required: bool = False
    options: tuple[str, ...] = ()
    is_sensitive: bool = False
    is_legal: bool = False
    help_text: str | None = None
    answer: str | None = None
    answer_source: AnswerSource = AnswerSource.USER_INPUT
    confidence: float = 0.0
    requires_confirmation: bool = True
    status: QuestionStatus = QuestionStatus.MISSING
    field_identifier: str | None = None


def _definition(
    key: str,
    text: str,
    field_type: QuestionType,
    section: str,
    *,
    required: bool = False,
    sensitive: bool = False,
    legal: bool = False,
    options: tuple[str, ...] = (),
    help_text: str | None = None,
) -> QuestionDefinition:
    return QuestionDefinition(
        question_key=key,
        question_text=text,
        field_type=field_type,
        section=section,
        required=required,
        options=options,
        is_sensitive=sensitive,
        is_legal=legal,
        help_text=help_text,
    )


CORE_QUESTIONS: tuple[QuestionDefinition, ...] = (
    _definition(
        "personal.full_name",
        "Full legal name",
        QuestionType.TEXT,
        SECTION_PERSONAL,
        required=True,
        help_text="Use the name that appears on your official documents.",
    ),
    _definition(
        "personal.preferred_name",
        "Preferred name",
        QuestionType.TEXT,
        SECTION_PERSONAL,
    ),
    _definition(
        "personal.first_name",
        "First name",
        QuestionType.TEXT,
        SECTION_PERSONAL,
        required=True,
    ),
    _definition(
        "personal.last_name",
        "Last name",
        QuestionType.TEXT,
        SECTION_PERSONAL,
        required=True,
    ),
    _definition(
        "contact.email",
        "Email address",
        QuestionType.EMAIL,
        SECTION_CONTACT,
        required=True,
    ),
    _definition(
        "contact.phone",
        "Phone number",
        QuestionType.PHONE,
        SECTION_CONTACT,
        required=True,
        help_text="Include the country code, e.g. +1 555 0100.",
    ),
    _definition("contact.linkedin", "LinkedIn profile", QuestionType.URL, SECTION_CONTACT),
    _definition("contact.github", "GitHub profile", QuestionType.URL, SECTION_CONTACT),
    _definition(
        "contact.portfolio",
        "Portfolio or personal website",
        QuestionType.URL,
        SECTION_CONTACT,
    ),
    _definition("address.line1", "Address line 1", QuestionType.TEXT, SECTION_ADDRESS),
    _definition("address.line2", "Address line 2", QuestionType.TEXT, SECTION_ADDRESS),
    _definition("address.city", "City", QuestionType.TEXT, SECTION_ADDRESS),
    _definition(
        "address.region",
        "State / province / region",
        QuestionType.TEXT,
        SECTION_ADDRESS,
    ),
    _definition("address.postal_code", "Postal / ZIP code", QuestionType.TEXT, SECTION_ADDRESS),
    _definition("address.country", "Country", QuestionType.TEXT, SECTION_ADDRESS),
    _definition(
        "education.summary",
        "Education history",
        QuestionType.TEXTAREA,
        SECTION_EDUCATION,
    ),
    _definition(
        "education.institution",
        "Most recent institution",
        QuestionType.TEXT,
        SECTION_EDUCATION,
    ),
    _definition("education.degree", "Highest degree", QuestionType.TEXT, SECTION_EDUCATION),
    _definition(
        "experience.summary",
        "Work experience",
        QuestionType.TEXTAREA,
        SECTION_EXPERIENCE,
    ),
    _definition(
        "experience.current_company",
        "Current company",
        QuestionType.TEXT,
        SECTION_EXPERIENCE,
    ),
    _definition(
        "experience.current_title",
        "Current job title",
        QuestionType.TEXT,
        SECTION_EXPERIENCE,
    ),
    _definition("skills.primary", "Technical skills", QuestionType.TEXTAREA, SECTION_SKILLS),
    _definition("projects.summary", "Projects", QuestionType.TEXTAREA, SECTION_PROJECTS),
    _definition(
        "certifications.summary",
        "Certifications",
        QuestionType.TEXTAREA,
        SECTION_CERTIFICATIONS,
    ),
    _definition(
        "compensation.expected_salary",
        "Expected salary",
        QuestionType.TEXT,
        SECTION_COMPENSATION,
        sensitive=True,
        help_text="Include the currency, e.g. 85000 USD.",
    ),
    _definition(
        "availability.notice_period",
        "Notice period (days)",
        QuestionType.NUMBER,
        SECTION_AVAILABILITY,
        sensitive=True,
    ),
    _definition(
        "availability.available_from",
        "Available from",
        QuestionType.DATE,
        SECTION_AVAILABILITY,
    ),
    _definition(
        "availability.relocation",
        "Willing to relocate?",
        QuestionType.SINGLE_CHOICE,
        SECTION_AVAILABILITY,
        sensitive=True,
        options=("Yes", "No", "Negotiable"),
    ),
    _definition(
        "authorization.status",
        "Work authorization",
        QuestionType.TEXT,
        SECTION_AUTHORIZATION,
        required=True,
        sensitive=True,
        help_text="The legal right you hold to work in the role's country.",
    ),
    _definition(
        "authorization.sponsorship",
        "Will you require visa sponsorship?",
        QuestionType.BOOLEAN,
        SECTION_AUTHORIZATION,
        required=True,
        sensitive=True,
        options=("Yes", "No"),
    ),
    _definition(
        "documents.resume",
        "Resume",
        QuestionType.FILE,
        SECTION_DOCUMENTS,
        required=True,
        help_text="The resume ApplyPilot selected for this application.",
    ),
    _definition(
        "company.cover_letter",
        "Cover letter or additional information",
        QuestionType.TEXTAREA,
        SECTION_COMPANY,
        help_text="Optional. ApplyPilot does not invent experience for you.",
    ),
    _definition(
        "declarations.accuracy",
        "I confirm the information in this application is accurate and complete.",
        QuestionType.DECLARATION,
        SECTION_DECLARATIONS,
        required=True,
        sensitive=True,
        legal=True,
        options=("Yes",),
    ),
    _definition(
        "declarations.eeo",
        "Voluntary equal-employment information",
        QuestionType.TEXTAREA,
        SECTION_DECLARATIONS,
        sensitive=True,
        legal=True,
        help_text="Optional. Only answer what you are comfortable sharing.",
    ),
)

_CORE_BY_KEY = {definition.question_key: definition for definition in CORE_QUESTIONS}

_STRING_FIELDS = (
    "full_name",
    "email",
    "phone",
    "phone_country_code",
    "location",
    "linkedin_url",
    "github_url",
    "portfolio_url",
    "address_line1",
    "address_line2",
    "city",
    "region",
    "postal_code",
    "country",
    "work_authorization",
    "preferred_name",
)


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _decimal_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    number = value.normalize()
    return f"{number:f}"


def _date_text(value: date | datetime | None) -> str | None:
    return value.isoformat()[:10] if value else None


def _split_name(full_name: str | None) -> tuple[str | None, str | None]:
    """Split a name only at whitespace; never guess beyond what is written."""
    parts = (full_name or "").split()
    if not parts:
        return None, None
    if len(parts) == 1:
        return parts[0], None
    return parts[0], " ".join(parts[1:])


def _join_lines(lines: list[str]) -> str | None:
    cleaned = [line for line in (item.strip() for item in lines) if line]
    return "\n".join(cleaned) if cleaned else None


def profile_answers(candidate: Candidate | None, user: User) -> dict[str, str]:
    """Values already verified on the candidate profile, keyed canonically."""
    values: dict[str, str] = {}
    if candidate is not None:
        for name in _STRING_FIELDS:
            value = _clean(getattr(candidate, name, None))
            if value:
                values[name] = value

        if candidate.phone and candidate.phone_country_code:
            values["contact.phone"] = f"{candidate.phone_country_code} {candidate.phone}".strip()

        first, last = _split_name(candidate.full_name)
        if first:
            values["personal.first_name"] = first
        if last:
            values["personal.last_name"] = last

        mapping = {
            "full_name": "personal.full_name",
            "preferred_name": "personal.preferred_name",
            "email": "contact.email",
            "phone": "contact.phone",
            "linkedin_url": "contact.linkedin",
            "github_url": "contact.github",
            "portfolio_url": "contact.portfolio",
            "address_line1": "address.line1",
            "address_line2": "address.line2",
            "city": "address.city",
            "region": "address.region",
            "postal_code": "address.postal_code",
            "country": "address.country",
            "work_authorization": "authorization.status",
        }
        for attribute, key in mapping.items():
            if values.get(attribute):
                values[key] = values[attribute]

        education = [
            " — ".join(part for part in (entry.degree, entry.institution) if part)
            for entry in (candidate.education or [])
        ]
        if education:
            values["education.summary"] = _join_lines(education) or ""
            latest = (candidate.education or [None])[0]
            if latest is not None:
                if latest.institution:
                    values["education.institution"] = latest.institution
                if latest.degree:
                    values["education.degree"] = latest.degree

        experience = [
            " — ".join(part for part in (entry.designation, entry.company) if part)
            for entry in (candidate.experiences or [])
        ]
        if experience:
            values["experience.summary"] = _join_lines(experience) or ""
            current = next(
                (entry for entry in candidate.experiences or [] if entry.is_current), None
            )
            if current is not None:
                if current.company:
                    values["experience.current_company"] = current.company
                if current.designation:
                    values["experience.current_title"] = current.designation

        projects = [
            entry.name for entry in (candidate.projects or []) if entry.name
        ]
        if projects:
            values["projects.summary"] = _join_lines(projects) or ""

        certifications = [
            " — ".join(part for part in (entry.name, entry.issuer) if part)
            for entry in (candidate.certifications or [])
        ]
        if certifications:
            values["certifications.summary"] = _join_lines(certifications) or ""

        if candidate.requires_sponsorship is not None:
            values["authorization.sponsorship"] = (
                "Yes" if candidate.requires_sponsorship else "No"
            )
        if candidate.notice_period_days is not None:
            values["availability.notice_period"] = str(candidate.notice_period_days)
        if candidate.available_from is not None:
            values["availability.available_from"] = _date_text(candidate.available_from)

        preference = candidate.preference
        if preference is not None:
            salary = _decimal_text(preference.expected_salary)
            if salary:
                values["compensation.expected_salary"] = (
                    f"{salary} {preference.currency}".strip()
                )
            relocation = getattr(preference.relocation_preference, "value", None)
            if relocation and relocation != RelocationPreference.UNKNOWN.value:
                values["availability.relocation"] = {
                    RelocationPreference.YES.value: "Yes",
                    RelocationPreference.NO.value: "No",
                    RelocationPreference.NEGOTIABLE.value: "Negotiable",
                }.get(relocation, relocation)

    if not values.get("contact.email") and user.email:
        values["contact.email"] = user.email
    if not values.get("personal.full_name") and user.full_name:
        values["personal.full_name"] = user.full_name
        first, last = _split_name(user.full_name)
        if first:
            values.setdefault("personal.first_name", first)
        if last:
            values.setdefault("personal.last_name", last)

    return {key: value for key, value in values.items() if value}


def resume_answers(resume: Resume | None) -> dict[str, str]:
    """Suggestions parsed from the selected resume (unverified)."""
    if resume is None:
        return {}
    try:
        data = ParsedResumeData.model_validate(resume.parsed_data or {})
    except ValidationError:
        data = ParsedResumeData()

    values: dict[str, str] = {}
    if _clean(data.full_name):
        values["personal.full_name"] = data.full_name.strip()
        first, last = _split_name(data.full_name)
        if first:
            values["personal.first_name"] = first
        if last:
            values["personal.last_name"] = last
    for attribute, key in (
        ("email", "contact.email"),
        ("phone", "contact.phone"),
        ("professional_summary", None),
    ):
        value = _clean(getattr(data, attribute, None))
        if value and key:
            values[key] = value

    for link in data.links:
        lowered = link.casefold()
        if "linkedin.com" in lowered:
            values.setdefault("contact.linkedin", link)
        elif "github.com" in lowered:
            values.setdefault("contact.github", link)
        else:
            values.setdefault("contact.portfolio", link)

    education = [
        " — ".join(part for part in (entry.degree, entry.institution) if part)
        for entry in data.education
    ]
    if education:
        values["education.summary"] = _join_lines(education) or ""
        if data.education[0].institution:
            values["education.institution"] = data.education[0].institution
        if data.education[0].degree:
            values["education.degree"] = data.education[0].degree

    experience = [
        " — ".join(part for part in (entry.title, entry.company) if part)
        for entry in data.experience
    ]
    if experience:
        values["experience.summary"] = _join_lines(experience) or ""
        current = next((entry for entry in data.experience if entry.is_current), None)
        if current is not None:
            if current.company:
                values["experience.current_company"] = current.company
            if current.title:
                values["experience.current_title"] = current.title

    if resume.skills:
        values["skills.primary"] = ", ".join(resume.skills)
    if data.projects:
        values["projects.summary"] = _join_lines([project.name for project in data.projects]) or ""
    if data.certifications:
        values["certifications.summary"] = _join_lines(data.certifications) or ""

    return {key: value for key, value in values.items() if value}


def _merge_fields(
    inspection: ApplicationInspection | None,
) -> tuple[QuestionDefinition, ...]:
    """Merge inspected/declared fields into the core set by canonical key."""
    if inspection is None:
        return ()
    merged: list[QuestionDefinition] = []
    for item in inspection.fields:
        core = _CORE_BY_KEY.get(item.question_key)
        merged.append(
            QuestionDefinition(
                question_key=item.question_key,
                question_text=core.question_text if core else item.question_text,
                field_type=item.field_type,
                section=item.section,
                required=bool(core.required if core else item.required),
                options=item.options or (core.options if core else ()),
                is_sensitive=item.is_sensitive or bool(core and core.is_sensitive),
                is_legal=item.is_legal or bool(core and core.is_legal),
                help_text=item.help_text or (core.help_text if core else None),
                field_identifier=item.field_identifier,
            )
        )
    return tuple(merged)


def build_questionnaire(
    *,
    candidate: Candidate | None,
    user: User,
    resume: Resume | None,
    approved_answers: dict[str, str] | None = None,
    inspection: ApplicationInspection | None = None,
) -> list[QuestionDefinition]:
    """Return the ordered question list with sourced, confidence-scored answers."""
    approved_answers = {
        key: value
        for key, value in (approved_answers or {}).items()
        if _clean(value)
    }
    from_profile = profile_answers(candidate, user)
    from_resume = resume_answers(resume)

    definitions: dict[str, QuestionDefinition] = {
        definition.question_key: definition for definition in CORE_QUESTIONS
    }
    # Declared/inspected fields extend the set but never remove a core question.
    declared = _merge_fields(inspection)
    for definition in declared:
        existing = definitions.get(definition.question_key)
        if existing is None:
            definitions[definition.question_key] = definition
            continue
        definitions[definition.question_key] = QuestionDefinition(
            **{
                **existing.__dict__,
                "required": existing.required or definition.required,
                "field_identifier": definition.field_identifier,
                "options": existing.options or definition.options,
            }
        )

    resolved: list[QuestionDefinition] = []
    for definition in definitions.values():
        value: str | None = None
        source = AnswerSource.USER_INPUT
        confidence = 0.0
        status = QuestionStatus.MISSING

        if definition.question_key in from_profile:
            value = from_profile[definition.question_key]
            source = AnswerSource.PROFILE
            confidence = 0.95
            status = QuestionStatus.CONFIRMED
        elif definition.question_key in from_resume:
            value = from_resume[definition.question_key]
            source = AnswerSource.RESUME
            confidence = 0.7
            status = QuestionStatus.SUGGESTED
        elif definition.question_key in approved_answers:
            value = approved_answers[definition.question_key]
            source = AnswerSource.PREVIOUS_ANSWER
            confidence = 1.0
            status = QuestionStatus.CONFIRMED

        requires_confirmation = True
        if source is AnswerSource.PROFILE and not definition.is_sensitive:
            requires_confirmation = False
        if source is AnswerSource.PREVIOUS_ANSWER:
            # Already approved for an earlier application, so reuse is safe
            # unless the field is a legal declaration.
            requires_confirmation = False
        if definition.is_legal or definition.question_key.startswith("declarations."):
            # Legal declarations are never pre-confirmed.
            requires_confirmation = True
        if value is not None and requires_confirmation:
            status = QuestionStatus.SUGGESTED

        resolved.append(
            QuestionDefinition(
                question_key=definition.question_key,
                question_text=definition.question_text,
                field_type=definition.field_type,
                section=definition.section,
                required=definition.required,
                options=definition.options,
                is_sensitive=definition.is_sensitive,
                is_legal=definition.is_legal,
                help_text=definition.help_text,
                answer=value,
                answer_source=source,
                confidence=confidence,
                requires_confirmation=requires_confirmation,
                status=status,
                field_identifier=definition.field_identifier,
            )
        )

    # Stable, human-friendly order: section order, then the core order.
    core_order = {
        definition.question_key: index for index, definition in enumerate(CORE_QUESTIONS)
    }
    resolved.sort(
        key=lambda item: (
            SECTION_ORDER.index(item.section)
            if item.section in SECTION_ORDER
            else len(SECTION_ORDER),
            core_order.get(item.question_key, len(core_order)),
            item.question_key,
        )
    )
    return resolved


def apply_resume_selection(
    definitions: list[QuestionDefinition],
    *,
    resume: Resume | None,
    reason: str,
    score: float | None,
) -> list[QuestionDefinition]:
    """Attach the selected resume to the questionnaire as a documents answer."""
    if resume is None:
        return definitions
    updated: list[QuestionDefinition] = []
    for item in definitions:
        if item.question_key != "documents.resume":
            updated.append(item)
            continue
        updated.append(
            replace(
                item,
                answer=resume.original_filename,
                answer_source=AnswerSource.RESUME,
                confidence=min(1.0, max(0.0, (score or 0) / 100)),
                requires_confirmation=False,
                status=QuestionStatus.CONFIRMED,
                help_text=reason,
                field_type=QuestionType.FILE,
            )
        )
    return updated


def slugify_question_key(value: str) -> str:
    """Normalise an arbitrary question key (used for reused answers)."""
    cleaned = re.sub(r"[^a-z0-9_.-]+", "-", value.strip().casefold()).strip("-")
    return cleaned[:100] or "question"


__all__ = [
    "CORE_QUESTIONS",
    "SECTION_LABELS",
    "SECTION_ORDER",
    "QuestionDefinition",
    "apply_resume_selection",
    "build_questionnaire",
    "profile_answers",
    "resume_answers",
    "slugify_question_key",
]
