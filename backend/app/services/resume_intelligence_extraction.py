"""Deterministic resume-intelligence extraction (Phase 2F.2.1).

This module is the non-generative half of resume intelligence. It reads the
resume text already extracted at upload time (and, when text is unavailable, the
stored parsed fields) with explicit rules, and records where each value came
from. It never invents a qualification: a value exists only when the resume
states it.

Design notes
------------
* Absence is not evidence of absence. A missing skill is never inferred to be
  missing from the candidate's ability; the field simply has no evidence.
* Evidence snippets are verbatim lines from the resume, whitespace-normalised
  and length-bounded. When text is unavailable the fact still exists but its
  source is ``deterministic`` and its evidence is ``None``.
* Skills are de-duplicated on ``normalize_skill_key`` (the same comparator the
  matching engine uses), so ``Node.js``/``Node``, ``JS``/``JavaScript`` and
  ``ML``/``Machine Learning`` collapse into one fact.
* Category assignment is flagged as inferred unless the resume grouped the
  skills itself (``Databases: PostgreSQL``), which keeps evidence and
  interpretation visibly separate.
* Extraction is an analysis, not a parse: rebuilding it from the same inputs and
  version yields an identical payload, which is what makes it cacheable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from pydantic import ValidationError

from app.schemas.resume import ParsedResumeData
from app.schemas.resume_intelligence import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    METHOD_CERTIFICATION_ENTRY,
    METHOD_EDUCATION_ENTRY,
    METHOD_EXPERIENCE_ENTRY,
    METHOD_KNOWN_SKILL_VOCABULARY,
    METHOD_LABELLED_SKILL_GROUP,
    METHOD_PROJECT_ENTRY,
    METHOD_SKILLS_SECTION,
    METHOD_STORED_SKILL_LIST,
    SOURCE_DETERMINISTIC,
    SOURCE_NOT_STATED,
    SOURCE_RESUME_TEXT,
    ResumeCertificationFact,
    ResumeEducationFact,
    ResumeExperienceFact,
    ResumeIntelligenceFields,
    ResumeIntelligencePayload,
    ResumeProjectFact,
    ResumeSkillFact,
    SkillCategory,
)
from app.services.matching_engine import (
    ExperienceEntry,
    estimate_experience_months,
    normalize_skill_key,
)
from app.services.resume_parser import (
    detect_known_skills,
    parse_resume_text,
    resume_sections,
    split_skill_tokens,
)

#: Bumped whenever extraction rules change; part of the resume cache key.
EXTRACTOR_VERSION = "2f.2.1"

MAX_SKILLS = 80
MAX_ENTRIES = 20
MAX_BULLETS = 8
MAX_ITEM_CHARS = 300
MAX_EVIDENCE_CHARS = 240
MAX_SUMMARY_CHARS = 4000
MAX_TOTAL_EXPERIENCE_MONTHS = 720

SECTION_ORDER: tuple[str, ...] = (
    "summary",
    "skills",
    "experience",
    "education",
    "projects",
    "certifications",
)

#: Explicit, inferred category for every canonical technology the shared
#: vocabulary knows. Skills outside this map keep ``SkillCategory.OTHER``.
SKILL_CATEGORIES: dict[str, SkillCategory] = {
    # Programming and markup languages
    "Python": SkillCategory.LANGUAGE,
    "Java": SkillCategory.LANGUAGE,
    "JavaScript": SkillCategory.LANGUAGE,
    "TypeScript": SkillCategory.LANGUAGE,
    "C": SkillCategory.LANGUAGE,
    "C++": SkillCategory.LANGUAGE,
    "C#": SkillCategory.LANGUAGE,
    "Go": SkillCategory.LANGUAGE,
    "Rust": SkillCategory.LANGUAGE,
    "Ruby": SkillCategory.LANGUAGE,
    "PHP": SkillCategory.LANGUAGE,
    "Scala": SkillCategory.LANGUAGE,
    "Kotlin": SkillCategory.LANGUAGE,
    "Swift": SkillCategory.LANGUAGE,
    "R": SkillCategory.LANGUAGE,
    "MATLAB": SkillCategory.LANGUAGE,
    "Perl": SkillCategory.LANGUAGE,
    "Bash": SkillCategory.LANGUAGE,
    "SQL": SkillCategory.LANGUAGE,
    "HTML": SkillCategory.LANGUAGE,
    "CSS": SkillCategory.LANGUAGE,
    "Sass": SkillCategory.LANGUAGE,
    # Frameworks and protocols
    "FastAPI": SkillCategory.FRAMEWORK,
    "Django": SkillCategory.FRAMEWORK,
    "Flask": SkillCategory.FRAMEWORK,
    "Spring": SkillCategory.FRAMEWORK,
    "Spring Boot": SkillCategory.FRAMEWORK,
    "Express": SkillCategory.FRAMEWORK,
    "NestJS": SkillCategory.FRAMEWORK,
    "Node.js": SkillCategory.FRAMEWORK,
    "React": SkillCategory.FRAMEWORK,
    "Next.js": SkillCategory.FRAMEWORK,
    "Vue": SkillCategory.FRAMEWORK,
    "Angular": SkillCategory.FRAMEWORK,
    "Svelte": SkillCategory.FRAMEWORK,
    "Tailwind CSS": SkillCategory.FRAMEWORK,
    "GraphQL": SkillCategory.FRAMEWORK,
    "REST": SkillCategory.FRAMEWORK,
    "gRPC": SkillCategory.FRAMEWORK,
    "WebSockets": SkillCategory.FRAMEWORK,
    # Libraries
    "Redux": SkillCategory.LIBRARY,
    "Pandas": SkillCategory.LIBRARY,
    "NumPy": SkillCategory.LIBRARY,
    "SciPy": SkillCategory.LIBRARY,
    # Databases
    "PostgreSQL": SkillCategory.DATABASE,
    "MySQL": SkillCategory.DATABASE,
    "SQLite": SkillCategory.DATABASE,
    "MongoDB": SkillCategory.DATABASE,
    "Redis": SkillCategory.DATABASE,
    "Cassandra": SkillCategory.DATABASE,
    "Oracle": SkillCategory.DATABASE,
    "DynamoDB": SkillCategory.DATABASE,
    "Elasticsearch": SkillCategory.DATABASE,
    "Snowflake": SkillCategory.DATABASE,
    "BigQuery": SkillCategory.DATABASE,
    "Redshift": SkillCategory.DATABASE,
    # Cloud platforms
    "AWS": SkillCategory.CLOUD,
    "Azure": SkillCategory.CLOUD,
    "GCP": SkillCategory.CLOUD,
    "Google Cloud": SkillCategory.CLOUD,
    "Lambda": SkillCategory.CLOUD,
    "EC2": SkillCategory.CLOUD,
    "S3": SkillCategory.CLOUD,
    # Developer tools
    "Docker": SkillCategory.TOOL,
    "Kubernetes": SkillCategory.TOOL,
    "Terraform": SkillCategory.TOOL,
    "Ansible": SkillCategory.TOOL,
    "Helm": SkillCategory.TOOL,
    "Jenkins": SkillCategory.TOOL,
    "GitHub Actions": SkillCategory.TOOL,
    "GitLab CI": SkillCategory.TOOL,
    "CircleCI": SkillCategory.TOOL,
    "Kafka": SkillCategory.TOOL,
    "RabbitMQ": SkillCategory.TOOL,
    "Celery": SkillCategory.TOOL,
    "Airflow": SkillCategory.TOOL,
    "Spark": SkillCategory.TOOL,
    "Hadoop": SkillCategory.TOOL,
    "dbt": SkillCategory.TOOL,
    "Tableau": SkillCategory.TOOL,
    "Power BI": SkillCategory.TOOL,
    "Looker": SkillCategory.TOOL,
    "Excel": SkillCategory.TOOL,
    "Git": SkillCategory.TOOL,
    "Linux": SkillCategory.TOOL,
    "Jira": SkillCategory.TOOL,
    "Figma": SkillCategory.TOOL,
    "Playwright": SkillCategory.TOOL,
    "Selenium": SkillCategory.TOOL,
    "Pytest": SkillCategory.TOOL,
    "Jest": SkillCategory.TOOL,
    "Cypress": SkillCategory.TOOL,
    "Vitest": SkillCategory.TOOL,
    # AI / machine learning
    "scikit-learn": SkillCategory.AI_ML,
    "TensorFlow": SkillCategory.AI_ML,
    "PyTorch": SkillCategory.AI_ML,
    "Keras": SkillCategory.AI_ML,
    "OpenAI": SkillCategory.AI_ML,
    "LangChain": SkillCategory.AI_ML,
    "Hugging Face": SkillCategory.AI_ML,
    "Computer Vision": SkillCategory.AI_ML,
    "NLP": SkillCategory.AI_ML,
    "Machine Learning": SkillCategory.AI_ML,
    "Deep Learning": SkillCategory.AI_ML,
    # Practices and methods
    "Data Analysis": SkillCategory.OTHER,
    "Data Visualization": SkillCategory.OTHER,
    "Agile": SkillCategory.OTHER,
    "Scrum": SkillCategory.OTHER,
    "Microservices": SkillCategory.OTHER,
    "CI/CD": SkillCategory.OTHER,
    "DevOps": SkillCategory.OTHER,
    "ETL": SkillCategory.OTHER,
    "Data Modeling": SkillCategory.OTHER,
    "Unit Testing": SkillCategory.OTHER,
}

#: Soft skills are only recorded when the resume explicitly lists them.
SOFT_SKILLS: tuple[str, ...] = (
    "Adaptability",
    "Analytical Skills",
    "Attention to Detail",
    "Collaboration",
    "Communication",
    "Conflict Resolution",
    "Creativity",
    "Critical Thinking",
    "Decision Making",
    "Interpersonal Skills",
    "Leadership",
    "Mentoring",
    "Negotiation",
    "Ownership",
    "Presentation Skills",
    "Problem Solving",
    "Project Management",
    "Public Speaking",
    "Stakeholder Management",
    "Strategic Planning",
    "Teamwork",
    "Time Management",
    "Written Communication",
)

_CATEGORY_BY_KEY: dict[str, SkillCategory] = {
    normalize_skill_key(name): category for name, category in SKILL_CATEGORIES.items()
}
_SOFT_SKILL_KEYS: frozenset[str] = frozenset(
    normalize_skill_key(name) for name in SOFT_SKILLS
)
_CANONICAL_NAMES: dict[str, str] = {
    normalize_skill_key(name): name for name in (*SKILL_CATEGORIES, *SOFT_SKILLS)
}

_SECTION_LABELS: dict[str, SkillCategory] = {
    "ai": SkillCategory.AI_ML,
    "ai & ml": SkillCategory.AI_ML,
    "ai/ml": SkillCategory.AI_ML,
    "ai and ml": SkillCategory.AI_ML,
    "cloud": SkillCategory.CLOUD,
    "cloud platforms": SkillCategory.CLOUD,
    "cloud services": SkillCategory.CLOUD,
    "data science": SkillCategory.AI_ML,
    "database": SkillCategory.DATABASE,
    "databases": SkillCategory.DATABASE,
    "developer tools": SkillCategory.TOOL,
    "framework": SkillCategory.FRAMEWORK,
    "frameworks": SkillCategory.FRAMEWORK,
    "interpersonal skills": SkillCategory.SOFT_SKILL,
    "language": SkillCategory.LANGUAGE,
    "languages": SkillCategory.LANGUAGE,
    "libraries": SkillCategory.LIBRARY,
    "library": SkillCategory.LIBRARY,
    "machine learning": SkillCategory.AI_ML,
    "ml": SkillCategory.AI_ML,
    "other": SkillCategory.OTHER,
    "others": SkillCategory.OTHER,
    "platforms": SkillCategory.TOOL,
    "programming": SkillCategory.LANGUAGE,
    "programming languages": SkillCategory.LANGUAGE,
    "soft skill": SkillCategory.SOFT_SKILL,
    "soft skills": SkillCategory.SOFT_SKILL,
    "technical skills": SkillCategory.OTHER,
    "technologies": SkillCategory.OTHER,
    "tooling": SkillCategory.TOOL,
    "tools": SkillCategory.TOOL,
}

#: Tokens that are section labels rather than skills.
_SKILL_NOISE: frozenset[str] = frozenset(
    {
        *_SECTION_LABELS,
        "advanced",
        "beginner",
        "expert",
        "familiar",
        "intermediate",
        "proficient",
        "skill",
        "skills",
        "technical",
    }
)

_GROUP_LABEL_RE = re.compile(r"^(?P<label>[A-Za-z][A-Za-z &/+.-]{1,30}):\s*(?P<content>\S.*)$")
_BULLET_RE = re.compile(r"^\s*(?:[-\u2013\u2014\u2022*\u00b7\u25aa\u25e6\u25cf]+|\d{1,2}[.)])\s*")
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_MEASUREMENT_RE = re.compile(
    r"\b\d[\d,.]*\s*(?:%|percent\b|x\b|k\b|m\b|bn\b|million\b|billion\b|hours?\b|"
    r"days?\b|weeks?\b|months?\b|years?\b|users?\b|customers?\b|requests?\b|"
    r"events?\b|records?\b|transactions?\b|teams?\b|engineers?\b|people\b|"
    r"projects?\b|clients?\b)",
    re.IGNORECASE,
)
_IMPROVEMENT_RE = re.compile(
    r"\b(?:reduced|increased|improved|cut|saved|grew|boosted|accelerated|scaled|"
    r"decreased|lowered|raised|doubled|tripled)\b",
    re.IGNORECASE,
)
_CONTRIBUTION_VERB_RE = re.compile(
    r"^(?:built|designed|developed|implemented|created|led|architected|engineered|"
    r"contributed|improved|automated|deployed|migrated|optimized|optimised|"
    r"maintained|shipped|launched|integrated|refactored|mentored|analyzed|"
    r"analysed|researched|tested|delivered|owned)\b",
    re.IGNORECASE,
)
_EDUCATION_ACHIEVEMENT_RE = re.compile(
    r"\b(?:gpa|honou?rs?|cum laude|magna|summa|distinction|dean'?s list|rank|"
    r"scholarship|gold medal|first class|topper)\b",
    re.IGNORECASE,
)
_GRADUATION_LINE_RE = re.compile(r"\bgraduat\w+", re.IGNORECASE)
_CERT_SPLIT_RE = re.compile(
    r"\s+(?:-|\u2013|\u2014|\||\u00b7)\s+|\s+(?:issued\s+by|by|from)\s+",
    re.IGNORECASE,
)
_CERT_DATE_RE = re.compile(
    r"\b(?:(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+)?"
    r"(?:19|20)\d{2}\b",
    re.IGNORECASE,
)
#: A separator part is only treated as an issuer when it names a known body.
_ISSUER_HINT_RE = re.compile(
    r"\b(?:amazon|aws|microsoft|google|oracle|cisco|comptia|pmi|project management "
    r"institute|scrum alliance|linux foundation|coursera|udemy|edx|databricks|"
    r"salesforce|meta|ibm|nvidia|hackerrank|isc2|ec-council|red hat|vmware|"
    r"kubernetes|cncf|autodesk|adobe|sas|cloudera)\b",
    re.IGNORECASE,
)


@dataclass(slots=True)
class ResumeUnderstanding:
    """One extraction result before it is persisted."""

    fields: ResumeIntelligenceFields
    confidence: str
    provenance: dict[str, str] = field(default_factory=dict)
    field_confidence: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    text_available: bool = False
    sections_found: list[str] = field(default_factory=list)
    extractor_version: str = EXTRACTOR_VERSION

    def to_payload(self) -> ResumeIntelligencePayload:
        """Return the validated, JSON-safe payload used by the cache."""
        return ResumeIntelligencePayload(
            fields=self.fields,
            provenance=dict(self.provenance),
            field_confidence=dict(self.field_confidence),
            confidence=self.confidence,
            warnings=list(self.warnings),
        )


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------
def _collapse(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _trim(value: str, limit: int = MAX_ITEM_CHARS) -> str:
    cleaned = _collapse(value)
    return cleaned[:limit]


def _evidence(text: str | None, *needles: str | None) -> str | None:
    """Return the first verbatim line containing one of ``needles``."""
    if not text:
        return None
    lines = [_collapse(line) for line in text.splitlines()]
    lines = [line for line in lines if line]
    for needle in needles:
        target = _collapse(needle or "").casefold()
        if not target:
            continue
        for line in lines:
            if target in line.casefold():
                return line[:MAX_EVIDENCE_CHARS]
    return None


def _bullet_lines(description: str | None) -> list[str]:
    """Split a stored multi-line description into clean bullet strings."""
    lines: list[str] = []
    for raw in (description or "").splitlines():
        cleaned = _trim(_BULLET_RE.sub("", raw))
        if cleaned:
            lines.append(cleaned)
    return lines


def _has_measurable_outcome(line: str) -> bool:
    """True only when the resume states a number with a result unit/verb."""
    if _MEASUREMENT_RE.search(line):
        return True
    return bool(_IMPROVEMENT_RE.search(line) and re.search(r"\d", line))


def _detected_skills(*texts: str | None) -> list[str]:
    """Canonical technologies mentioned in any of ``texts``, de-duplicated."""
    joined = "\n".join(text for text in texts if text)
    found: dict[str, str] = {}
    for name in detect_known_skills(joined):
        found.setdefault(normalize_skill_key(name), name)
    return list(found.values())


def _skill_name(token: str) -> tuple[str, str]:
    """Return ``(display_name, comparison_key)`` for one skill token."""
    cleaned = _collapse(token).strip(" .,:;-")
    key = normalize_skill_key(cleaned)
    return _CANONICAL_NAMES.get(key, cleaned), key


def _is_noise_token(token: str) -> bool:
    cleaned = _collapse(token).strip(" .,:;-")
    if len(cleaned) < 2 or not re.search(r"[A-Za-z]", cleaned):
        return True
    return cleaned.casefold() in _SKILL_NOISE


def _labelled_group(line: str) -> tuple[SkillCategory | None, str] | None:
    """Return ``(category, items)`` when the line is ``Label: items``.

    A label is metadata rather than a skill, so it is never emitted as one. The
    category is ``None`` when the label is not one we recognise; the items are
    still explicit evidence, just without a stated category.
    """
    match = _GROUP_LABEL_RE.match(line.strip())
    if match is None:
        return None
    label = _collapse(match.group("label")).strip()
    content = match.group("content").strip()
    if not content or normalize_skill_key(label) in _CANONICAL_NAMES:
        # ``Python: advanced`` is a proficiency note, not a category group.
        return None
    return _SECTION_LABELS.get(label.casefold()), content


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------
def _skill_fact(
    *,
    name: str,
    key: str,
    category: SkillCategory | None,
    explicit: bool,
    category_inferred: bool,
    method: str,
    text: str | None,
    source: str,
) -> ResumeSkillFact:
    resolved = category or _CATEGORY_BY_KEY.get(key)
    if resolved is None and key in _SOFT_SKILL_KEYS:
        resolved = SkillCategory.SOFT_SKILL
    resolved = resolved or SkillCategory.OTHER
    known_category = resolved is not SkillCategory.OTHER
    confidence = (
        CONFIDENCE_HIGH if explicit and known_category else CONFIDENCE_MEDIUM
    )
    return ResumeSkillFact(
        name=name,
        category=resolved,
        explicit=explicit,
        category_inferred=category_inferred,
        source=source,
        confidence=confidence,
        evidence=_evidence(text, name),
        method=method,
    )


def _skill_facts(
    text: str | None,
    parsed: ParsedResumeData,
    stored_skills: list[str] | None,
) -> list[ResumeSkillFact]:
    """Build one fact per distinct skill, preferring explicitly listed evidence."""
    facts: dict[str, ResumeSkillFact] = {}
    source = SOURCE_RESUME_TEXT if text else SOURCE_DETERMINISTIC

    if text:
        sections = resume_sections(text)[1]
        skill_lines = sections.get("skills", [])
        # Pass 1: explicit category groups (``Databases: PostgreSQL``).
        unlabelled: list[str] = []
        for line in skill_lines:
            group = _labelled_group(line)
            if group is None:
                unlabelled.append(line)
                continue
            category, content = group
            for token in split_skill_tokens([content]):
                if _is_noise_token(token):
                    continue
                name, key = _skill_name(token)
                facts.setdefault(
                    key,
                    _skill_fact(
                        name=name,
                        key=key,
                        category=category,
                        explicit=True,
                        category_inferred=category is None,
                        method=METHOD_LABELLED_SKILL_GROUP,
                        text=text,
                        source=source,
                    ),
                )
        # Pass 2: the remaining skills-section tokens.
        for token in split_skill_tokens(unlabelled):
            if _is_noise_token(token):
                continue
            name, key = _skill_name(token)
            facts.setdefault(
                key,
                _skill_fact(
                    name=name,
                    key=key,
                    category=None,
                    explicit=True,
                    category_inferred=True,
                    method=METHOD_SKILLS_SECTION,
                    text=text,
                    source=source,
                ),
            )
        # Pass 3: technologies named anywhere else in the document are evidence
        # of exposure, not declaration, so they are flagged ``explicit=False``.
        for name in detect_known_skills(text):
            key = normalize_skill_key(name)
            if key in facts:
                continue
            facts[key] = _skill_fact(
                name=name,
                key=key,
                category=None,
                explicit=False,
                category_inferred=True,
                method=METHOD_KNOWN_SKILL_VOCABULARY,
                text=text,
                source=source,
            )
    else:
        # No text: the stored skill list is the only evidence available.
        for token in stored_skills or []:
            if _is_noise_token(token):
                continue
            name, key = _skill_name(token)
            facts.setdefault(
                key,
                _skill_fact(
                    name=name,
                    key=key,
                    category=None,
                    explicit=True,
                    category_inferred=True,
                    method=METHOD_STORED_SKILL_LIST,
                    text=None,
                    source=source,
                ),
            )

    return list(facts.values())[:MAX_SKILLS]


# ---------------------------------------------------------------------------
# Experience
# ---------------------------------------------------------------------------
def _duration_months(entry: Any, as_of: date) -> int | None:
    months, dated = estimate_experience_months(
        (
            ExperienceEntry(
                title=entry.title,
                company=entry.company,
                description=entry.description,
                technologies=(),
                start_date=entry.start_date,
                end_date=entry.end_date,
                is_current=entry.is_current,
                skill_keys=frozenset(),
            ),
        ),
        as_of=as_of,
    )
    return months if dated else None


def _experience_facts(
    text: str | None, parsed: ParsedResumeData, as_of: date
) -> list[ResumeExperienceFact]:
    facts: list[ResumeExperienceFact] = []
    for entry in parsed.experience[:MAX_ENTRIES]:
        bullets = _bullet_lines(entry.description)
        achievements = [line for line in bullets if _has_measurable_outcome(line)]
        responsibilities = [line for line in bullets if not _has_measurable_outcome(line)]
        evidence = _evidence(text, entry.company, entry.title)
        months = _duration_months(entry, as_of)
        has_identity = bool(entry.title or entry.company)
        if has_identity and (months is not None or bullets):
            confidence = CONFIDENCE_HIGH
        elif has_identity:
            confidence = CONFIDENCE_MEDIUM
        else:
            confidence = CONFIDENCE_LOW
        facts.append(
            ResumeExperienceFact(
                company=entry.company,
                role=entry.title,
                location=entry.location,
                start_date=entry.start_date,
                end_date=entry.end_date,
                is_current=entry.is_current,
                duration_months=months,
                responsibilities=responsibilities[:MAX_BULLETS],
                achievements=achievements[:MAX_BULLETS],
                technologies=_detected_skills(
                    entry.title, entry.company, entry.description
                ),
                source=SOURCE_RESUME_TEXT if evidence else SOURCE_DETERMINISTIC,
                confidence=confidence,
                evidence=evidence,
                method=METHOD_EXPERIENCE_ENTRY,
            )
        )
    return facts


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------
def _project_facts(text: str | None, parsed: ParsedResumeData) -> list[ResumeProjectFact]:
    facts: list[ResumeProjectFact] = []
    for project in parsed.projects[:MAX_ENTRIES]:
        bullets = _bullet_lines(project.description)
        contribution = next(
            (line for line in bullets if _CONTRIBUTION_VERB_RE.match(line)),
            None,
        )
        outcomes = [line for line in bullets if _has_measurable_outcome(line)]
        demonstrated = _detected_skills(
            project.name, project.description, " ".join(project.technologies)
        )
        technologies = _detected_skills(" ".join(project.technologies))
        evidence = _evidence(text, project.name)
        has_detail = bool(project.description or project.technologies)
        facts.append(
            ResumeProjectFact(
                title=project.name,
                description=project.description,
                technologies=technologies,
                contribution=contribution,
                demonstrated_skills=demonstrated,
                measurable_outcomes=outcomes[:MAX_BULLETS],
                url=project.url,
                source=SOURCE_RESUME_TEXT if evidence else SOURCE_DETERMINISTIC,
                confidence=CONFIDENCE_HIGH if has_detail else CONFIDENCE_MEDIUM,
                evidence=evidence,
                method=METHOD_PROJECT_ENTRY,
            )
        )
    return facts


# ---------------------------------------------------------------------------
# Education
# ---------------------------------------------------------------------------
def _education_achievements(details: str | None) -> list[str]:
    return [
        line
        for line in _bullet_lines(details)
        if _EDUCATION_ACHIEVEMENT_RE.search(line)
    ][:MAX_BULLETS]


def _graduation_year(entry: Any) -> int | None:
    match = _YEAR_RE.search(entry.end_date or "")
    if match:
        return int(match.group(0))
    for line in _bullet_lines(entry.details):
        if _GRADUATION_LINE_RE.search(line):
            year = _YEAR_RE.search(line)
            if year:
                return int(year.group(0))
    return None


def _education_facts(
    text: str | None, parsed: ParsedResumeData
) -> list[ResumeEducationFact]:
    facts: list[ResumeEducationFact] = []
    for entry in parsed.education[:MAX_ENTRIES]:
        evidence = _evidence(text, entry.institution, entry.degree)
        if entry.institution and entry.degree:
            confidence = CONFIDENCE_HIGH
        elif entry.institution or entry.degree:
            confidence = CONFIDENCE_MEDIUM
        else:
            confidence = CONFIDENCE_LOW
        facts.append(
            ResumeEducationFact(
                institution=entry.institution,
                degree=entry.degree,
                specialization=entry.field_of_study,
                start_date=entry.start_date,
                end_date=entry.end_date,
                graduation_year=_graduation_year(entry),
                achievements=_education_achievements(entry.details),
                source=SOURCE_RESUME_TEXT if evidence else SOURCE_DETERMINISTIC,
                confidence=confidence,
                evidence=evidence,
                method=METHOD_EDUCATION_ENTRY,
            )
        )
    return facts


# ---------------------------------------------------------------------------
# Certifications
# ---------------------------------------------------------------------------
def _certification_fact(text: str | None, raw: str) -> ResumeCertificationFact:
    cleaned = _trim(raw)
    date_match = _CERT_DATE_RE.search(cleaned)
    issued_on = _collapse(date_match.group(0)) if date_match else None
    body = _CERT_DATE_RE.sub("", cleaned) if issued_on else cleaned
    body = _collapse(body).strip(" ,;.-")

    parts = _CERT_SPLIT_RE.split(body, maxsplit=1)
    name = parts[0].strip(" ,;.-")
    issuer = parts[1].strip(" ,;.-") if len(parts) > 1 else None
    if issuer is not None and not _ISSUER_HINT_RE.search(issuer):
        # Do not guess an issuer from an unrecognised fragment.
        name, issuer = body.strip(" ,;.-"), None
    if not name:
        name = cleaned

    evidence = _evidence(text, cleaned, name)
    confidence = (
        CONFIDENCE_HIGH if (issuer or issued_on) else CONFIDENCE_MEDIUM
    )
    return ResumeCertificationFact(
        name=name,
        issuing_organization=issuer,
        issued_on=issued_on,
        source=SOURCE_RESUME_TEXT if evidence else SOURCE_DETERMINISTIC,
        confidence=confidence,
        evidence=evidence,
        method=METHOD_CERTIFICATION_ENTRY,
    )


def _certification_facts(
    text: str | None, parsed: ParsedResumeData
) -> list[ResumeCertificationFact]:
    facts: list[ResumeCertificationFact] = []
    seen: set[str] = set()
    for raw in parsed.certifications[:MAX_ENTRIES]:
        if not isinstance(raw, str) or not raw.strip():
            continue
        key = _collapse(raw).casefold()
        if key in seen:
            continue
        seen.add(key)
        facts.append(_certification_fact(text, raw))
    return facts


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------
def _confidence_rank(value: str) -> int:
    return {CONFIDENCE_HIGH: 3, CONFIDENCE_MEDIUM: 2, CONFIDENCE_LOW: 1}.get(value, 0)


def _field_confidence(facts: list[Any]) -> str:
    if not facts:
        return CONFIDENCE_LOW
    ranks = [_confidence_rank(fact.confidence) for fact in facts]
    if all(rank >= 3 for rank in ranks):
        return CONFIDENCE_HIGH
    return CONFIDENCE_MEDIUM


def _field_source(facts: list[Any]) -> str:
    if not facts:
        return SOURCE_NOT_STATED
    if any(fact.source == SOURCE_RESUME_TEXT for fact in facts):
        return SOURCE_RESUME_TEXT
    return SOURCE_DETERMINISTIC


def _overall_confidence(filled: int, *, text_available: bool) -> str:
    if filled == 0:
        return CONFIDENCE_LOW
    if text_available and filled >= 3:
        return CONFIDENCE_HIGH
    if filled >= 2:
        return CONFIDENCE_MEDIUM
    return CONFIDENCE_MEDIUM if text_available else CONFIDENCE_LOW


def _parse_stored_data(parsed_data: ParsedResumeData | dict[str, Any] | None) -> tuple[
    ParsedResumeData, bool
]:
    """Return ``(data, readable)``; unreadable stored JSON never raises."""
    if isinstance(parsed_data, ParsedResumeData):
        return parsed_data, True
    try:
        return ParsedResumeData.model_validate(parsed_data or {}), True
    except ValidationError:
        return ParsedResumeData(), False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def extract_resume_intelligence(
    *,
    text: str | None,
    parsed_data: ParsedResumeData | dict[str, Any] | None = None,
    stored_skills: list[str] | None = None,
    as_of: date | None = None,
) -> ResumeUnderstanding:
    """Extract structured evidence from resume text and/or stored parsed data.

    ``text`` may be ``None`` when the PDF is scanned or unreadable. In that case
    only the stored parsed fields are used, evidence snippets are omitted and the
    confidence reflects the missing source text. Nothing is invented in either
    mode.
    """
    as_of = as_of or date.today()
    parsed, readable = _parse_stored_data(parsed_data)
    clean_text = (text or "").strip()
    text_available = bool(clean_text)
    warnings: list[str] = []

    if not readable:
        warnings.append(
            "The stored parsed resume data could not be read and was ignored; "
            "nothing was inferred from it."
        )

    if text_available and not _has_any_parsed_content(parsed, stored_skills):
        # A resume stored before parsing existed can still be analysed.
        parsed = ParsedResumeData.model_validate(parse_resume_text(clean_text).to_dict())

    sections = resume_sections(clean_text)[1] if text_available else {}
    sections_found = [name for name in SECTION_ORDER if sections.get(name)]

    skills = _skill_facts(
        clean_text if text_available else None, parsed, stored_skills
    )
    experience = _experience_facts(clean_text if text_available else None, parsed, as_of)
    projects = _project_facts(clean_text if text_available else None, parsed)
    education = _education_facts(clean_text if text_available else None, parsed)
    certifications = _certification_facts(clean_text if text_available else None, parsed)

    months, has_dated = estimate_experience_months(
        tuple(
            ExperienceEntry(
                title=entry.title,
                company=entry.company,
                description=entry.description,
                technologies=(),
                start_date=entry.start_date,
                end_date=entry.end_date,
                is_current=entry.is_current,
                skill_keys=frozenset(),
            )
            for entry in parsed.experience[:MAX_ENTRIES]
        ),
        as_of=as_of,
    )
    total_months = min(months, MAX_TOTAL_EXPERIENCE_MONTHS) if has_dated else None

    summary = _trim(parsed.professional_summary or "", MAX_SUMMARY_CHARS) or None
    fields = ResumeIntelligenceFields(
        summary=summary,
        total_experience_months=total_months,
        has_dated_experience=has_dated,
        skills=skills,
        experience=experience,
        projects=projects,
        education=education,
        certifications=certifications,
        sections_found=sections_found,
    )

    provenance: dict[str, str] = {
        "summary": SOURCE_RESUME_TEXT if summary else SOURCE_NOT_STATED,
        "skills": _field_source(skills),
        "experience": _field_source(experience),
        "projects": _field_source(projects),
        "education": _field_source(education),
        "certifications": _field_source(certifications),
    }
    field_confidence: dict[str, str] = {
        "summary": CONFIDENCE_HIGH if summary else CONFIDENCE_LOW,
        "skills": _field_confidence(skills),
        "experience": _field_confidence(experience),
        "projects": _field_confidence(projects),
        "education": _field_confidence(education),
        "certifications": _field_confidence(certifications),
    }

    if not text_available:
        warnings.append(
            "The original resume text was not available, so evidence snippets "
            "could not be captured; only the stored parsed fields were used."
        )
    if not any(
        (skills, experience, projects, education, certifications, summary)
    ):
        warnings.append(
            "No extractable content was found in this resume. Nothing was "
            "inferred from the missing information."
        )
    if text_available and "skills" not in sections and any(
        not skill.explicit for skill in skills
    ):
        warnings.append(
            "No skills section was found; skills were detected from technologies "
            "mentioned in the resume."
        )
    if text_available and not sections_found:
        warnings.append(
            "No standard resume section headings were detected; extraction used "
            "prose-level signals only."
        )

    filled = sum(
        1
        for group in (skills, experience, projects, education, certifications)
        if group
    )
    return ResumeUnderstanding(
        fields=fields,
        confidence=_overall_confidence(filled, text_available=text_available),
        provenance=provenance,
        field_confidence=field_confidence,
        warnings=warnings,
        text_available=text_available,
        sections_found=sections_found,
    )


def _has_any_parsed_content(
    parsed: ParsedResumeData, stored_skills: list[str] | None
) -> bool:
    return bool(
        parsed.professional_summary
        or stored_skills
        or parsed.experience
        or parsed.projects
        or parsed.education
        or parsed.certifications
    )


__all__ = [
    "EXTRACTOR_VERSION",
    "SECTION_ORDER",
    "SKILL_CATEGORIES",
    "SOFT_SKILLS",
    "ResumeUnderstanding",
    "extract_resume_intelligence",
]
