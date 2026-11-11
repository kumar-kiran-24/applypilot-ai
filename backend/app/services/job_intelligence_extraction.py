"""Deterministic job-understanding extraction (Phase 2F.1).

This module is the non-generative half of job understanding. It reads a stored
posting with explicit rules and records where each value came from. It never
invents a requirement: a value exists only when the posting states it, and every
field carries a source and confidence.

LLM enrichment is applied afterwards by
:mod:`app.services.job_understanding_service` through
:func:`merge_llm_understanding`. That merge may only fill fields left empty here,
and only with claims that can be verified against the same posting text.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.models.experience import EmploymentType
from app.models.preference import WorkMode
from app.schemas.job_intelligence import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    SOURCE_DETERMINISTIC,
    SOURCE_JOB_RECORD,
    SOURCE_LLM,
    SOURCE_NOT_STATED,
    ExperienceLevel,
    JobIntelligenceFields,
)
from app.services.job_requirements import (
    build_job_requirements,
    description_sections,
    normalized_description,
)
from app.services.matching_engine import normalize_skill_key
from app.services.resume_parser import TECH_SKILLS, detect_known_skills

if TYPE_CHECKING:
    from app.agents.job_analysis import JobDescriptionAnalysisOutput

#: Bumped whenever extraction or merge rules change; part of the cache key.
EXTRACTOR_VERSION = "2f.1.1"

MAX_SKILLS = 40
MAX_QUALIFICATIONS = 15
MAX_RESPONSIBILITIES = 12
MAX_CERTIFICATIONS = 10
MAX_ITEM_CHARS = 300
MAX_SALARY_CHARS = 300

#: Every field that appears in ``provenance``/``field_confidence``.
FIELD_NAMES: tuple[str, ...] = (
    "job_title",
    "company",
    "description",
    "required_skills",
    "preferred_skills",
    "experience_years",
    "experience_years_max",
    "education_level",
    "education_field",
    "education_requirement",
    "required_qualifications",
    "responsibilities",
    "employment_type",
    "work_location",
    "work_mode",
    "salary_range",
    "experience_level",
    "required_certifications",
    "application_url",
    "application_url_is_listing",
)

_BULLET_RE = re.compile(
    r"^\s*(?:[-\u2013\u2014\u2022*\u00b7\u25aa\u25e6\u25cf]+|\d{1,2}[.)])\s*"
)
_METADATA_LINE_RE = re.compile(
    r"^(?:location|job\s+location|employment\s+type|job\s+type|work\s+(?:arrangement|mode)|"
    r"salary|compensation|pay|benefits|apply|how\s+to\s+apply|certifications?|"
    r"education)\b",
    re.IGNORECASE,
)
_YEARS_RANGE_HIGH_RE = re.compile(
    r"\b\d{1,2}\s*(?:\+|plus)?\s*(?:-|\u2013|\u2014|to)\s*(?P<high>\d{1,2})\s*"
    r"(?:years?|yrs?)\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Work arrangement and employment type
# ---------------------------------------------------------------------------
_NEGATION_RE = re.compile(
    r"\b(?:not|no|non|cannot|without|isn['\u2019]?t|aren['\u2019]?t)\b[\s\w-]{0,20}$",
    re.IGNORECASE,
)

_WORK_MODE_PATTERNS: tuple[tuple[WorkMode, re.Pattern[str]], ...] = (
    (WorkMode.HYBRID, re.compile(r"\bhybrid\b", re.IGNORECASE)),
    (
        WorkMode.REMOTE,
        re.compile(
            r"\b(?:fully\s+)?remote\b|\bwork\s+from\s+home\b|\bwfh\b|\btelecommut\w+",
            re.IGNORECASE,
        ),
    ),
    (
        WorkMode.ONSITE,
        re.compile(r"\b(?:on[\s-]?site|in[\s-]?office|in[\s-]?person)\b", re.IGNORECASE),
    ),
)

_REMOTE_EXCEPTION_RE = re.compile(
    r"\b(?:occasional(?:ly)?|periodic(?:ally)?|quarterly|visits?|travel|"
    r"few\s+times)\b",
    re.IGNORECASE,
)

_EMPLOYMENT_PATTERNS: tuple[tuple[EmploymentType, re.Pattern[str]], ...] = (
    (EmploymentType.FULL_TIME, re.compile(r"\bfull[\s-]?time\b", re.IGNORECASE)),
    (EmploymentType.PART_TIME, re.compile(r"\bpart[\s-]?time\b", re.IGNORECASE)),
    (
        EmploymentType.INTERNSHIP,
        re.compile(r"\b(?:internship|intern)\b", re.IGNORECASE),
    ),
    (
        EmploymentType.CONTRACT,
        re.compile(r"\b(?:contract|contractor|fixed[\s-]term)\b", re.IGNORECASE),
    ),
    (EmploymentType.FREELANCE, re.compile(r"\bfreelance\b", re.IGNORECASE)),
    (EmploymentType.TEMPORARY, re.compile(r"\b(?:temporary|temp)\b", re.IGNORECASE)),
)

# ---------------------------------------------------------------------------
# Seniority
# ---------------------------------------------------------------------------
_LEVEL_PATTERNS: tuple[tuple[ExperienceLevel, re.Pattern[str]], ...] = (
    (ExperienceLevel.DIRECTOR, re.compile(r"\bdirector\b", re.IGNORECASE)),
    (ExperienceLevel.PRINCIPAL, re.compile(r"\bprincipal\b", re.IGNORECASE)),
    (
        ExperienceLevel.LEAD,
        re.compile(r"\b(?:lead|team\s+lead|tech\s+lead)\b", re.IGNORECASE),
    ),
    (ExperienceLevel.SENIOR, re.compile(r"\b(?:senior|sr\.?)\b", re.IGNORECASE)),
    (ExperienceLevel.MID, re.compile(r"\b(?:mid[\s-]?level|intermediate)\b", re.IGNORECASE)),
    (
        ExperienceLevel.JUNIOR,
        re.compile(r"\b(?:junior|jr\.?|entry[\s-]?level|graduate)\b", re.IGNORECASE),
    ),
    (ExperienceLevel.INTERN, re.compile(r"\bintern(?:ship)?\b", re.IGNORECASE)),
)

#: Keywords used to verify an LLM seniority reading against the posting text.
_LEVEL_KEYWORDS: dict[ExperienceLevel, re.Pattern[str]] = dict(_LEVEL_PATTERNS)

# ---------------------------------------------------------------------------
# Salary, location and application URL
# ---------------------------------------------------------------------------
_SALARY_CONTEXT_RE = re.compile(
    r"\bsalar(?:y|ies)\b|\bcompensation\b|\bpay\s+(?:range|rate)\b|\bctc\b|"
    r"\bpackage\b|\bper\s+(?:annum|year|month|hour)\b|\bannual(?:ly)?\b|"
    r"\bhourly\b|\bstipend\b|\blpa\b",
    re.IGNORECASE,
)
_CURRENCY_RE = re.compile(
    r"[$€£₹¥]|\b(?:usd|eur|gbp|inr|cad|aud|sgd)\b",
    re.IGNORECASE,
)
_DIGIT_RE = re.compile(r"\d")

_LOCATION_LINE_RE = re.compile(
    r"^\s*(?:location|job\s+location)\s*[:\-]\s*(?P<value>.+)$",
    re.IGNORECASE,
)
_LOCATION_PHRASE_RE = re.compile(
    r"^\s*(?:based|located)\s+in\s+(?P<value>.+)$",
    re.IGNORECASE,
)

_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)
_ATS_HOST_HINTS: tuple[str, ...] = (
    "greenhouse.io",
    "lever.co",
    "myworkdayjobs.com",
    "workday.com",
    "smartrecruiters.com",
    "icims.com",
    "ashbyhq.com",
    "bamboohr.com",
    "jobvite.com",
    "taleo.net",
    "recruitee.com",
    "breezy.hr",
    "applytojob.com",
)
_APPLY_PATH_RE = re.compile(r"/(?:apply|application|careers?)(?:/|$|\?)", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Certifications and education
# ---------------------------------------------------------------------------
_CERT_HEADING_RE = re.compile(
    r"^\s*(?:required\s+)?(?:certifications?|certificates?|licenses?|credentials?)"
    r"\s*:?\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_CERT_BOUNDARY_RE = re.compile(
    r"^\s*(?:requirements?|qualifications?|preferred|responsibilities|benefits?|"
    r"about|education|experience|skills|compensation|salary|location|"
    r"apply|how\s+to\s+apply)\b",
    re.IGNORECASE,
)
_KNOWN_CERT_RE = re.compile(
    r"\b(?:"
    r"AWS\s+Certified(?:\s+[A-Za-z]+){0,3}"
    r"|Azure\s+(?:Administrator|Developer|Solutions\s+Architect)(?:\s+[A-Za-z]+){0,3}"
    r"|Google\s+Cloud\s+(?:Certified|Professional)(?:\s+[A-Za-z]+){0,3}"
    r"|Certified\s+Kubernetes\s+(?:Administrator|Application\s+Developer|"
    r"Security\s+Specialist)"
    r"|CKA|CKAD|CKS|PMP|CISSP|CISA|CCNA|CCNP"
    r"|CompTIA\s+[A-Za-z+]{2,24}"
    r"|Security\+|CFA|CPA|ITIL|PSM\s?I|Scrum\s+Master"
    r")\b",
    re.IGNORECASE,
)
_CERT_TRAILING_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "apply",
        "are",
        "as",
        "benefits",
        "compensation",
        "experience",
        "have",
        "is",
        "job",
        "location",
        "must",
        "or",
        "pay",
        "plus",
        "preferred",
        "qualifications",
        "required",
        "responsibilities",
        "salary",
        "to",
        "with",
        "years",
    }
)

_DEGREE_LEVEL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("doctorate", re.compile(r"\b(?:ph\.?\s?d|doctorate|doctoral)\b", re.IGNORECASE)),
    (
        "master",
        re.compile(r"\b(?:master(?:['\u2019])?s?|mba|m\.?\s?tech)\b", re.IGNORECASE),
    ),
    (
        "bachelor",
        re.compile(
            r"\b(?:bachelor(?:['\u2019])?s?|undergraduate|b\.?\s?tech|b\.?\s?e\.?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "associate",
        re.compile(r"\b(?:associate(?:['\u2019])?s?|diploma)\b", re.IGNORECASE),
    ),
)

_EDUCATION_LABELS = {
    "doctorate": "Doctorate or PhD",
    "master": "Master's degree",
    "bachelor": "Bachelor's degree",
    "associate": "Associate's degree or diploma",
}


@dataclass(slots=True)
class JobUnderstanding:
    """Deterministic (and then LLM-supplemented) understanding of a posting."""

    fields: JobIntelligenceFields
    provenance: dict[str, str]
    field_confidence: dict[str, str]
    confidence: str = CONFIDENCE_LOW
    contradictions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    dropped_llm_claims: list[str] = field(default_factory=list)
    supplemented_fields: list[str] = field(default_factory=list)


def _append_unique(values: list[str], message: str) -> None:
    if message and message not in values:
        values.append(message)


def _mark(
    provenance: dict[str, str],
    confidence: dict[str, str],
    name: str,
    source: str,
    level: str,
) -> None:
    provenance[name] = source
    confidence[name] = level


def _clean_item(value: str, *, limit: int = MAX_ITEM_CHARS) -> str:
    return " ".join(value.split()).strip(" .;:-")[:limit]


def _dedupe(values: list[str], *, limit: int) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = _clean_item(value)
        key = cleaned.casefold()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
        if len(result) >= limit:
            break
    return result


def _is_negated(text: str, start: int) -> bool:
    return bool(_NEGATION_RE.search(text[max(0, start - 30) : start]))


def _matches_unnegated(text: str, pattern: re.Pattern[str]) -> bool:
    return any(
        not _is_negated(text, match.start()) for match in pattern.finditer(text)
    )


def _detect_work_mode(text: str) -> tuple[WorkMode | None, list[str]]:
    """Return the stated work arrangement and any contradiction message."""
    if not text:
        return None, []
    found: list[WorkMode] = []
    for mode, pattern in _WORK_MODE_PATTERNS:
        if _matches_unnegated(text, pattern):
            found.append(mode)

    if WorkMode.HYBRID in found:
        return WorkMode.HYBRID, []
    if WorkMode.REMOTE in found and WorkMode.ONSITE in found:
        if _REMOTE_EXCEPTION_RE.search(text):
            return WorkMode.REMOTE, []
        return None, [
            "The posting mentions both remote and on-site work without stating "
            "hybrid; the work mode is left unclassified."
        ]
    if WorkMode.REMOTE in found:
        return WorkMode.REMOTE, []
    if WorkMode.ONSITE in found:
        return WorkMode.ONSITE, []
    return None, []


def _detect_employment_type(
    text: str,
) -> tuple[EmploymentType | None, list[str]]:
    found: list[EmploymentType] = []
    for employment_type, pattern in _EMPLOYMENT_PATTERNS:
        if _matches_unnegated(text, pattern):
            found.append(employment_type)
    if not found:
        return None, []
    if len(found) == 1:
        return found[0], []
    labels = ", ".join(item.value.replace("_", " ") for item in found)
    return found[0], [
        f"The posting mentions multiple employment types ({labels}); the first "
        "stated type is recorded."
    ]


_LEVEL_CONTEXT_RE = re.compile(
    r"\b(?:looking\s+for|seeking|hiring|role|position|candidate|level)\b",
    re.IGNORECASE,
)


def _detect_level(
    text: str, *, contextual: bool = False
) -> tuple[ExperienceLevel, str]:
    """Return the seniority explicitly named in ``text``.

    When ``contextual`` is set, only lines that talk about the role itself are
    considered, so "report to the Director" does not become a director-level
    posting.
    """
    lines = text.split("\n") if contextual else [text]
    for line in lines:
        if contextual and not _LEVEL_CONTEXT_RE.search(line):
            continue
        for level, pattern in _LEVEL_PATTERNS:
            if _matches_unnegated(line, pattern):
                return level, CONFIDENCE_MEDIUM if contextual else CONFIDENCE_HIGH
    return ExperienceLevel.UNKNOWN, CONFIDENCE_LOW


def _detect_salary(text: str) -> tuple[str | None, list[str]]:
    """Return the first stated numeric compensation statement, verbatim."""
    warnings: list[str] = []
    candidates: list[str] = []
    context_without_number = False

    for line in text.split("\n"):
        if not _SALARY_CONTEXT_RE.search(line):
            continue
        if not (_CURRENCY_RE.search(line) and _DIGIT_RE.search(line)):
            context_without_number = True
            continue
        match = _CURRENCY_RE.search(line)
        start = max(0, match.start() - 80) if match else 0
        snippet = line[start : start + MAX_SALARY_CHARS]
        candidates.append(_clean_item(snippet, limit=MAX_SALARY_CHARS))

    if not candidates:
        if context_without_number:
            warnings.append(
                "The posting mentions compensation but gives no numeric range."
            )
        return None, warnings
    if len(candidates) > 1:
        warnings.append(
            "Multiple compensation statements were found; the first one is recorded."
        )
    return candidates[0], warnings


def _detect_location(text: str) -> str | None:
    for line in text.split("\n"):
        for pattern in (_LOCATION_LINE_RE, _LOCATION_PHRASE_RE):
            match = pattern.match(line)
            if not match:
                continue
            value = re.split(r"[.;|]", match.group("value"))[0].strip(" ,-")
            if value:
                return value[:200]
    return None


def _is_skill_only_line(line: str) -> bool:
    chunks = [chunk.strip() for chunk in re.split(r"[,;/|]|\band\b", line) if chunk.strip()]
    if not chunks:
        return False
    known = {normalize_skill_key(skill) for skill in TECH_SKILLS}
    return {normalize_skill_key(chunk) for chunk in chunks} <= known


def _qualification_lines(section_lines: list[str]) -> list[str]:
    values: list[str] = []
    for line in section_lines:
        value = _BULLET_RE.sub("", line).strip()
        if not value or len(value) < 3:
            continue
        if _is_skill_only_line(value):
            continue
        values.append(value)
    return _dedupe(values, limit=MAX_QUALIFICATIONS)


def _clean_certification(value: str) -> str:
    words = value.split()
    while words and words[-1].casefold().strip(".,;:") in _CERT_TRAILING_STOPWORDS:
        words.pop()
    return _clean_item(" ".join(words), limit=120)


def _detect_certifications(description: str, text: str) -> list[str]:
    values: list[str] = []

    raw_lines = description.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    collecting = False
    collected = 0
    for raw_line in raw_lines:
        line = raw_line.strip()
        if _CERT_HEADING_RE.match(line):
            collecting = True
            collected = 0
            continue
        if not collecting:
            continue
        if not line:
            continue
        if _CERT_BOUNDARY_RE.match(line) or (line.endswith(":") and len(line.split()) <= 6):
            collecting = False
            continue
        if collected >= MAX_CERTIFICATIONS:
            break
        value = _clean_certification(_BULLET_RE.sub("", line))
        if value and not _is_skill_only_line(value):
            values.append(value)
        collected += 1

    for match in _KNOWN_CERT_RE.finditer(text):
        value = _clean_certification(match.group(0))
        if value:
            values.append(value)

    return _dedupe(values, limit=MAX_CERTIFICATIONS)


def _detect_application_url(
    description: str | None, fallback: str | None
) -> tuple[str | None, bool]:
    """Return ``(url, is_listing_page)``.

    A direct ATS/careers link in the posting wins over the discovered listing;
    otherwise the stored listing URL is returned and explicitly flagged as such.
    """
    for match in _URL_RE.finditer(description or ""):
        url = match.group(0).rstrip(".,;:")
        host_lower = url.casefold()
        if any(hint in host_lower for hint in _ATS_HOST_HINTS) or _APPLY_PATH_RE.search(url):
            return url[:1000], False
    return (fallback[:1000] if fallback else None), True


def _education_requirement(level: str | None, field_name: str | None) -> str | None:
    if not level:
        return None
    label = _EDUCATION_LABELS.get(level, f"{level.title()} degree")
    return f"{label} in {field_name}" if field_name else label


def _detect_education_conflict(text: str) -> list[str]:
    mentioned = [level for level, pattern in _DEGREE_LEVEL_PATTERNS if pattern.search(text)]
    if len(mentioned) > 1:
        return [
            "The posting mentions more than one education level "
            f"({', '.join(mentioned)}); the highest stated level is recorded."
        ]
    return []


def _detect_entry_level_conflict(
    job_title: str, experience_years: float | None
) -> list[str]:
    if experience_years is None or experience_years < 5:
        return []
    entry_pattern = re.compile(
        r"\b(?:intern|internship|junior|jr\.?|entry[\s-]?level|graduate)\b",
        re.IGNORECASE,
    )
    if entry_pattern.search(job_title):
        return [
            "The job title indicates an entry-level role while the posting asks "
            f"for {experience_years:g}+ years of experience."
        ]
    return []


def _detect_required_preferred_overlap(sections: dict[str, list[str]]) -> list[str]:
    required_text = "\n".join(sections.get("required", []))
    preferred_text = "\n".join(sections.get("preferred", []))
    if not required_text or not preferred_text:
        return []
    required = {skill.casefold() for skill in detect_known_skills(required_text)}
    preferred = {skill.casefold() for skill in detect_known_skills(preferred_text)}
    overlap = sorted(required & preferred)
    if not overlap:
        return []
    return [
        "Listed as both required and preferred: "
        f"{', '.join(overlap)}; these are recorded as required."
    ]


def extract_job_understanding(
    *,
    job_title: str,
    company: str | None,
    location: str | None,
    description: str | None,
    job_url: str | None,
) -> JobUnderstanding:
    """Extract structured evidence from a stored posting, deterministically."""
    requirements = build_job_requirements(
        job_title=job_title,
        company=company,
        location=location,
        description=description,
    )
    text = normalized_description(description)
    sections = description_sections(description)

    provenance = dict.fromkeys(FIELD_NAMES, SOURCE_NOT_STATED)
    confidence_map = dict.fromkeys(FIELD_NAMES, CONFIDENCE_LOW)
    contradictions: list[str] = []
    warnings: list[str] = []

    title = _clean_item(requirements.job_title or job_title, limit=300)
    _mark(provenance, confidence_map, "job_title", SOURCE_JOB_RECORD, CONFIDENCE_HIGH)

    if company and company.strip():
        _mark(provenance, confidence_map, "company", SOURCE_JOB_RECORD, CONFIDENCE_HIGH)

    raw_description = description if description and description.strip() else None
    if raw_description:
        _mark(
            provenance,
            confidence_map,
            "description",
            SOURCE_JOB_RECORD,
            CONFIDENCE_HIGH,
        )
    else:
        _append_unique(
            warnings,
            "The posting has no description; only the stored title, company and "
            "location were used.",
        )

    skill_level = {
        "explicit_sections": CONFIDENCE_HIGH,
        "description_mentions": CONFIDENCE_MEDIUM,
    }.get(requirements.skills_source, CONFIDENCE_LOW)
    if requirements.required_skills:
        _mark(
            provenance,
            confidence_map,
            "required_skills",
            SOURCE_DETERMINISTIC,
            skill_level,
        )
    if requirements.preferred_skills:
        _mark(
            provenance,
            confidence_map,
            "preferred_skills",
            SOURCE_DETERMINISTIC,
            skill_level,
        )

    experience_years = requirements.experience_years
    if experience_years is not None:
        _mark(
            provenance,
            confidence_map,
            "experience_years",
            SOURCE_DETERMINISTIC,
            CONFIDENCE_HIGH,
        )

    experience_years_max: float | None = None
    if experience_years is not None and text:
        for match in _YEARS_RANGE_HIGH_RE.finditer(text):
            high = float(match.group("high"))
            if experience_years <= high <= 40:
                experience_years_max = high
                _mark(
                    provenance,
                    confidence_map,
                    "experience_years_max",
                    SOURCE_DETERMINISTIC,
                    CONFIDENCE_HIGH,
                )
                break

    if requirements.education_level:
        _mark(
            provenance,
            confidence_map,
            "education_level",
            SOURCE_DETERMINISTIC,
            CONFIDENCE_HIGH,
        )
        _mark(
            provenance,
            confidence_map,
            "education_requirement",
            SOURCE_DETERMINISTIC,
            CONFIDENCE_HIGH,
        )
        if requirements.education_field:
            _mark(
                provenance,
                confidence_map,
                "education_field",
                SOURCE_DETERMINISTIC,
                CONFIDENCE_HIGH,
            )
        for message in _detect_education_conflict(text):
            _append_unique(warnings, message)

    qualifications = _qualification_lines(sections.get("required", []))
    if qualifications:
        _mark(
            provenance,
            confidence_map,
            "required_qualifications",
            SOURCE_DETERMINISTIC,
            CONFIDENCE_HIGH,
        )

    responsibilities = [
        item
        for item in requirements.responsibilities
        if not _METADATA_LINE_RE.match(item)
    ][:MAX_RESPONSIBILITIES]
    if responsibilities:
        _mark(
            provenance,
            confidence_map,
            "responsibilities",
            SOURCE_DETERMINISTIC,
            CONFIDENCE_HIGH,
        )

    employment_text = f"{title}\n{text}"
    employment_type, employment_warnings = _detect_employment_type(employment_text)
    if employment_type is not None:
        _mark(
            provenance,
            confidence_map,
            "employment_type",
            SOURCE_DETERMINISTIC,
            CONFIDENCE_HIGH,
        )
    for message in employment_warnings:
        _append_unique(warnings, message)

    work_mode, work_mode_conflicts = _detect_work_mode(f"{location or ''}\n{employment_text}")
    if work_mode is not None:
        _mark(
            provenance,
            confidence_map,
            "work_mode",
            SOURCE_DETERMINISTIC,
            CONFIDENCE_HIGH,
        )
    for message in work_mode_conflicts:
        _append_unique(contradictions, message)

    work_location = (location or "").strip()[:200] or _detect_location(text)
    if work_location:
        _mark(
            provenance,
            confidence_map,
            "work_location",
            SOURCE_JOB_RECORD if location and location.strip() else SOURCE_DETERMINISTIC,
            CONFIDENCE_HIGH if location and location.strip() else CONFIDENCE_MEDIUM,
        )

    salary, salary_warnings = _detect_salary(text)
    if salary:
        _mark(
            provenance,
            confidence_map,
            "salary_range",
            SOURCE_DETERMINISTIC,
            CONFIDENCE_MEDIUM,
        )
    for message in salary_warnings:
        _append_unique(warnings, message)

    experience_level, level_confidence = _detect_level(title)
    if experience_level is ExperienceLevel.UNKNOWN:
        experience_level, level_confidence = _detect_level(text, contextual=True)
    if experience_level is not ExperienceLevel.UNKNOWN:
        _mark(
            provenance,
            confidence_map,
            "experience_level",
            SOURCE_DETERMINISTIC,
            level_confidence,
        )

    certifications = _detect_certifications(description or "", text)
    if certifications:
        _mark(
            provenance,
            confidence_map,
            "required_certifications",
            SOURCE_DETERMINISTIC,
            CONFIDENCE_HIGH
            if _CERT_HEADING_RE.search(description or "")
            else CONFIDENCE_MEDIUM,
        )

    application_url, is_listing = _detect_application_url(description, job_url)
    if application_url:
        _mark(
            provenance,
            confidence_map,
            "application_url",
            SOURCE_DETERMINISTIC if not is_listing else SOURCE_JOB_RECORD,
            CONFIDENCE_MEDIUM if not is_listing else CONFIDENCE_HIGH,
        )
        _mark(
            provenance,
            confidence_map,
            "application_url_is_listing",
            provenance["application_url"],
            confidence_map["application_url"],
        )
    else:
        _append_unique(
            warnings,
            "The posting has no application URL; the employer's own form must be "
            "supplied before an application can be prepared.",
        )

    for message in _detect_required_preferred_overlap(sections):
        _append_unique(contradictions, message)
    for message in _detect_entry_level_conflict(title, experience_years):
        _append_unique(contradictions, message)

    fields = JobIntelligenceFields(
        job_title=title,
        company=((company or "").strip()[:200] or None),
        description=raw_description,
        required_skills=list(requirements.required_skills)[:MAX_SKILLS],
        preferred_skills=list(requirements.preferred_skills)[:MAX_SKILLS],
        experience_years=experience_years,
        experience_years_max=experience_years_max,
        education_level=requirements.education_level,
        education_field=requirements.education_field,
        education_requirement=_education_requirement(
            requirements.education_level, requirements.education_field
        ),
        required_qualifications=qualifications,
        responsibilities=responsibilities,
        employment_type=employment_type,
        work_location=work_location or None,
        work_mode=work_mode,
        salary_range=salary,
        experience_level=experience_level,
        required_certifications=certifications,
        application_url=application_url,
        application_url_is_listing=is_listing,
    )

    confidence = requirements.confidence
    if not raw_description:
        confidence = CONFIDENCE_LOW

    return JobUnderstanding(
        fields=fields,
        provenance=provenance,
        field_confidence=confidence_map,
        confidence=confidence,
        contradictions=contradictions,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# LLM claim verification and merge
# ---------------------------------------------------------------------------
_WORD_RE = re.compile(r"[a-z0-9+#]+")


def _words(text: str) -> list[str]:
    return _WORD_RE.findall(text.casefold())


def _is_supported_by_text(text: str, value: str) -> bool:
    """True when a model claim is grounded in the posting text.

    The check is deliberately conservative: the value must appear as a phrase, or
    at least 60% of its comparison tokens must occur in the posting. A claim that
    fails is dropped instead of being stored as a requirement.
    """
    value_words = _words(value)
    if not value_words:
        return False
    text_words = _words(text)
    if not text_words:
        return False
    value_phrase = " ".join(value_words)
    if value_phrase in " ".join(text_words):
        return True
    words = set(text_words)
    tokens = [word for word in value_words if len(word) >= 2 or word.isdigit()]
    if not tokens:
        return False
    hits = sum(1 for token in tokens if token in words)
    return hits / len(tokens) >= 0.6


def _years_supported(text: str, years: float) -> bool:
    if years <= 0:
        return False
    number = int(years)
    return bool(
        re.search(
            rf"(?<!\d){number}\s*\+?\s*(?:years?|yrs?)\b",
            text,
            re.IGNORECASE,
        )
    )


def _work_mode_supported(text: str, mode: WorkMode) -> bool:
    for candidate, pattern in _WORK_MODE_PATTERNS:
        if candidate is mode and _matches_unnegated(text, pattern):
            return True
    return False


def _employment_supported(text: str, employment_type: EmploymentType) -> bool:
    for candidate, pattern in _EMPLOYMENT_PATTERNS:
        if candidate is employment_type and _matches_unnegated(text, pattern):
            return True
    return False


def _level_supported(text: str, level: ExperienceLevel) -> bool:
    pattern = _LEVEL_KEYWORDS.get(level)
    return bool(pattern and _matches_unnegated(text, pattern))


def _merge_list(
    *,
    understanding: JobUnderstanding,
    name: str,
    values: list[str],
    limit: int,
    supported: Callable[[str], bool],
) -> None:
    """Fill an empty list field with only the model items the posting supports."""
    if getattr(understanding.fields, name):
        return
    kept: list[str] = []
    for value in values:
        cleaned = _clean_item(value)
        if not cleaned:
            continue
        if supported(cleaned):
            kept.append(cleaned)
        else:
            understanding.dropped_llm_claims.append(f"{name}: {cleaned}")
    if not kept:
        return
    setattr(understanding.fields, name, _dedupe(kept, limit=limit))
    _mark(
        understanding.provenance,
        understanding.field_confidence,
        name,
        SOURCE_LLM,
        CONFIDENCE_MEDIUM,
    )
    understanding.supplemented_fields.append(name)


def merge_llm_understanding(
    base: JobUnderstanding,
    llm: JobDescriptionAnalysisOutput,
    *,
    description: str,
) -> JobUnderstanding:
    """Merge validated model output into deterministic understanding.

    Rules:

    * deterministic values are never overwritten;
    * the model may only fill fields the posting's rules left empty;
    * every model value is verified against the posting before it is kept;
    * disagreements on stated facts are reported, not silently resolved.
    """
    text = normalized_description(description)
    fields = base.fields.model_copy(deep=True)
    understanding = JobUnderstanding(
        fields=fields,
        provenance=dict(base.provenance),
        field_confidence=dict(base.field_confidence),
        confidence=base.confidence,
        contradictions=list(base.contradictions),
        warnings=list(base.warnings),
        dropped_llm_claims=list(base.dropped_llm_claims),
        supplemented_fields=list(base.supplemented_fields),
    )

    if not fields.company and llm.company and _is_supported_by_text(text, llm.company):
        fields.company = _clean_item(llm.company, limit=200)
        _mark(
            understanding.provenance,
            understanding.field_confidence,
            "company",
            SOURCE_LLM,
            CONFIDENCE_MEDIUM,
        )
        understanding.supplemented_fields.append("company")

    if (
        not fields.work_location
        and llm.location
        and _is_supported_by_text(text, llm.location)
    ):
        fields.work_location = _clean_item(llm.location, limit=200)
        _mark(
            understanding.provenance,
            understanding.field_confidence,
            "work_location",
            SOURCE_LLM,
            CONFIDENCE_MEDIUM,
        )
        understanding.supplemented_fields.append("work_location")

    if llm.work_mode is not None:
        llm_mode = WorkMode(llm.work_mode.value)
        if fields.work_mode is None:
            if _work_mode_supported(text, llm_mode):
                fields.work_mode = llm_mode
                _mark(
                    understanding.provenance,
                    understanding.field_confidence,
                    "work_mode",
                    SOURCE_LLM,
                    CONFIDENCE_MEDIUM,
                )
                understanding.supplemented_fields.append("work_mode")
        elif fields.work_mode is not llm_mode and _work_mode_supported(text, llm_mode):
            _append_unique(
                understanding.contradictions,
                "The model classified the role as "
                f"{llm_mode.value} while the posting's explicit wording indicates "
                f"{fields.work_mode.value}; the explicit wording is kept.",
            )

    if llm.employment_type is not None:
        llm_employment = EmploymentType(llm.employment_type.value)
        if fields.employment_type is None:
            if _employment_supported(text, llm_employment):
                fields.employment_type = llm_employment
                _mark(
                    understanding.provenance,
                    understanding.field_confidence,
                    "employment_type",
                    SOURCE_LLM,
                    CONFIDENCE_MEDIUM,
                )
                understanding.supplemented_fields.append("employment_type")
        elif (
            fields.employment_type is not llm_employment
            and _employment_supported(text, llm_employment)
        ):
            _append_unique(
                understanding.contradictions,
                "The model read the employment type as "
                f"{llm_employment.value.replace('_', ' ')} while the posting's "
                f"explicit wording indicates "
                f"{fields.employment_type.value.replace('_', ' ')}; the explicit "
                "wording is kept.",
            )

    llm_level = ExperienceLevel(llm.seniority.value)
    if llm_level is not ExperienceLevel.UNKNOWN:
        if fields.experience_level is ExperienceLevel.UNKNOWN:
            if _level_supported(text, llm_level):
                fields.experience_level = llm_level
                _mark(
                    understanding.provenance,
                    understanding.field_confidence,
                    "experience_level",
                    SOURCE_LLM,
                    CONFIDENCE_MEDIUM,
                )
                understanding.supplemented_fields.append("experience_level")
        elif (
            fields.experience_level is not llm_level
            and _level_supported(text, llm_level)
        ):
            _append_unique(
                understanding.contradictions,
                "The model classified the seniority as "
                f"{llm_level.value} while the posting's explicit wording indicates "
                f"{fields.experience_level.value}; the explicit wording is kept.",
            )

    if llm.min_experience_years is not None and _years_supported(
        text, float(llm.min_experience_years)
    ):
        if fields.experience_years is None:
            fields.experience_years = float(llm.min_experience_years)
            _mark(
                understanding.provenance,
                understanding.field_confidence,
                "experience_years",
                SOURCE_LLM,
                CONFIDENCE_MEDIUM,
            )
            understanding.supplemented_fields.append("experience_years")
        elif abs(fields.experience_years - llm.min_experience_years) >= 1:
            _append_unique(
                understanding.contradictions,
                "The model read the minimum experience as "
                f"{llm.min_experience_years:g} years while the posting's explicit "
                f"wording indicates {fields.experience_years:g}; the explicit "
                "wording is kept.",
            )

    if (
        llm.max_experience_years is not None
        and fields.experience_years_max is None
        and _years_supported(text, float(llm.max_experience_years))
    ):
        fields.experience_years_max = float(llm.max_experience_years)
        _mark(
            understanding.provenance,
            understanding.field_confidence,
            "experience_years_max",
            SOURCE_LLM,
            CONFIDENCE_MEDIUM,
        )
        understanding.supplemented_fields.append("experience_years_max")

    if (
        not fields.salary_range
        and llm.salary_range_text
        and _is_supported_by_text(text, llm.salary_range_text)
    ):
        fields.salary_range = _clean_item(llm.salary_range_text, limit=MAX_SALARY_CHARS)
        _mark(
            understanding.provenance,
            understanding.field_confidence,
            "salary_range",
            SOURCE_LLM,
            CONFIDENCE_MEDIUM,
        )
        understanding.supplemented_fields.append("salary_range")

    _merge_list(
        understanding=understanding,
        name="required_skills",
        values=llm.required_skills,
        limit=MAX_SKILLS,
        supported=lambda value: _is_supported_by_text(text, value),
    )
    _merge_list(
        understanding=understanding,
        name="preferred_skills",
        values=llm.preferred_skills,
        limit=MAX_SKILLS,
        supported=lambda value: _is_supported_by_text(text, value),
    )
    _merge_list(
        understanding=understanding,
        name="responsibilities",
        values=llm.responsibilities,
        limit=MAX_RESPONSIBILITIES,
        supported=lambda value: _is_supported_by_text(text, value),
    )
    _merge_list(
        understanding=understanding,
        name="required_qualifications",
        values=llm.qualifications,
        limit=MAX_QUALIFICATIONS,
        supported=lambda value: (
            _is_supported_by_text(text, value) and not _is_skill_only_line(value)
        ),
    )
    _merge_list(
        understanding=understanding,
        name="required_certifications",
        values=llm.required_certifications,
        limit=MAX_CERTIFICATIONS,
        supported=lambda value: _is_supported_by_text(text, value),
    )

    required_keys = {value.casefold() for value in fields.required_skills}
    filtered_preferred = [
        value
        for value in fields.preferred_skills
        if value.casefold() not in required_keys
    ]
    if len(filtered_preferred) != len(fields.preferred_skills):
        fields.preferred_skills = filtered_preferred
        _append_unique(
            understanding.warnings,
            "A skill was listed as both required and preferred; it is treated as "
            "required only.",
        )

    return understanding


__all__ = [
    "EXTRACTOR_VERSION",
    "FIELD_NAMES",
    "JobUnderstanding",
    "extract_job_understanding",
    "merge_llm_understanding",
]
