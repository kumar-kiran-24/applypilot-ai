"""Phase 2F.2.3 evidence-based resume-to-job compatibility scoring.

This module extends the deterministic Phase 2C engine instead of replacing it.
It keeps the established five-dimension model and the 40/25/15/10/10 weights,
but scores from *persisted intelligence* when it is current:

* job side - :class:`JobIntelligencePayload` (skills, years, education,
  certifications, responsibilities);
* resume side - :class:`ResumeIntelligencePayload` (skill evidence with a
  source and confidence, dated roles, projects, education, certifications).

When intelligence is missing, stale or failed the same rules run on the stored
parsed resume data and rule-based posting requirements, and the result says so
via ``processing_state`` and ``details["inputs"]``. Nothing is invented, no LLM
is called, no resume text is re-read, and every scored fact carries evidence.

Scoring semantics
-----------------
=================================  ======  ==============================
Dimension                          Weight  Evidence
=================================  ======  ==============================
Required skills (preferred bonus)  0.40    Resume skill evidence
Experience                         0.25    Dated roles + role relevance
Title alignment                    0.15    Role language, then projects
Project relevance                  0.10    Project technologies
Education                          0.10    Degree level and field
=================================  ======  ==============================

A dimension the posting gives no information for is not evaluated; its weight
is redistributed proportionally, exactly as before. Preferred skills continue to
add their established 15% within the skills dimension. Certifications never
change a category score: they are reported as supporting evidence and as a gap
when the posting names one the resume does not show. This keeps the documented
weights intact and avoids double-counting a skill in two categories: a skill is
counted once inside the skills dimension (best evidence wins), and the project
dimension counts project evidence only.

The overall number measures overlap between the resume and the posting. It is
not a hiring probability.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date
from typing import Any

from app.schemas.job_intelligence import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    JobIntelligenceFields,
    JobIntelligencePayload,
)
from app.schemas.resume_intelligence import (
    METHOD_LABELLED_SKILL_GROUP,
    METHOD_SKILLS_SECTION,
    ResumeIntelligenceFields,
    ResumeIntelligencePayload,
    ResumeSkillFact,
)
from app.services.job_requirements import JobRequirements
from app.services.matching_engine import (
    DIMENSION_WEIGHTS,
    ResumeProfile,
    education_level_rank,
    estimate_experience_months,
    normalize_skill_key,
    skill_display_name,
    title_tokens,
)
from app.services.resume_parser import detect_known_skills

#: Bumped whenever the scoring rules change. Part of the match cache key, so a
#: new engine invalidates every stored match without touching the schema.
MATCHING_ALGORITHM_VERSION = "2f.2.3.1"

STATUS_CURRENT = "current"
STATUS_STALE = "stale"
STATUS_MISSING = "missing"
STATUS_FAILED = "failed"

STATE_READY = "ready"
STATE_PARTIAL = "partial"
STATE_INSUFFICIENT = "insufficient_evidence"
STATE_RESUME_FAILED = "resume_extraction_failed"
STATE_INCOMPLETE_JOB = "job_description_incomplete"

PROCESSING_STATES = frozenset(
    {
        STATE_READY,
        STATE_PARTIAL,
        STATE_INSUFFICIENT,
        STATE_RESUME_FAILED,
        STATE_INCOMPLETE_JOB,
    }
)

EVIDENCE_REQUIRED_SKILL = "required_skill"
EVIDENCE_PREFERRED_SKILL = "preferred_skill"
EVIDENCE_EXPERIENCE = "experience"
EVIDENCE_TITLE = "title"
EVIDENCE_PROJECT = "project"
EVIDENCE_EDUCATION = "education"
EVIDENCE_CERTIFICATION = "certification"

#: Safety bounds so one evaluation cannot grow without limit.
MAX_EVIDENCE = 40
MAX_MISSING_EVIDENCE = 12
MAX_GAPS = 8
MAX_STRENGTHS = 6
MAX_SUGGESTIONS = 8

#: Additional aliases on top of the shared vocabulary. Every entry maps one
#: normalized key to the same technology's normalized key; unrelated
#: technologies are never equated.
_EXTRA_ALIASES: dict[str, str] = {
    "js": "javascript",
    "ts": "typescript",
    "ml": "machinelearning",
    "dl": "deeplearning",
    "gcp": "googlecloud",
    "golang": "go",
    "k8s": "kubernetes",
    "postgres": "postgresql",
    "postgre": "postgresql",
    "csharp": "c#",
    "springboot": "spring",
    "vuejs": "vue",
    "angularjs": "angular",
    "mssql": "sqlserver",
    "sklearn": "scikitlearn",
    "restapi": "rest",
    "restfulapi": "rest",
    "restfulapis": "rest",
    "amazonwebservices": "aws",
    "googlecloudplatform": "googlecloud",
}

_EDUCATION_RANK = {
    "associate": 2,
    "diploma": 2,
    "bachelor": 3,
    "master": 4,
    "doctorate": 5,
}

_INTERNSHIP_RE = re.compile(r"\b(?:intern(?:ship)?|trainee|placement|apprentice)\b", re.IGNORECASE)


def canonical_skill_key(value: str) -> str:
    """Normalize one skill to its comparison key, including Phase 2F aliases."""
    key = normalize_skill_key(value)
    return _EXTRA_ALIASES.get(key, key)


def _display_name(key: str, fallback: str | None = None) -> str:
    name = skill_display_name(key)
    return name if name != key else (fallback or key)


def _skill_pairs(skills: tuple[str, ...] | list[str]) -> list[tuple[str, str]]:
    """Return ``(key, display)`` pairs, de-duplicated and order-preserving."""
    seen: set[str] = set()
    pairs: list[tuple[str, str]] = []
    for skill in skills:
        key = canonical_skill_key(skill)
        if not key or key in seen:
            continue
        seen.add(key)
        pairs.append((key, _display_name(key, skill)))
    return pairs


def _text_keys(*texts: str | None) -> frozenset[str]:
    joined = "\n".join(text for text in texts if text)
    return frozenset(canonical_skill_key(name) for name in detect_known_skills(joined))


# ---------------------------------------------------------------------------
# Inputs and result models
# ---------------------------------------------------------------------------
@dataclass(slots=True, frozen=True)
class MatchEvidence:
    """One explainable reason behind a scored fact."""

    category: str
    description: str
    resume_reference: str | None
    confidence: str
    match_reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "description": self.description,
            "resume_reference": self.resume_reference,
            "confidence": self.confidence,
            "match_reason": self.match_reason,
        }


@dataclass(slots=True, frozen=True)
class _SkillPresent:
    """Resume evidence for one skill key (best evidence wins)."""

    key: str
    display: str
    reference: str
    confidence: str
    credit: float


@dataclass(slots=True, frozen=True)
class _Role:
    title: str | None
    company: str | None
    start_date: str | None
    end_date: str | None
    is_current: bool
    is_internship: bool
    skill_keys: frozenset[str]
    reference: str
    months: int | None


@dataclass(slots=True, frozen=True)
class _Project:
    name: str
    skill_keys: frozenset[str]
    reference: str


@dataclass(slots=True, frozen=True)
class AdvancedMatchResult:
    """One resume's evidence-based compatibility evaluation against one job."""

    resume_id: str
    resume_filename: str
    overall_score: float
    skill_score: float | None
    experience_score: float | None
    education_score: float | None
    title_score: float | None
    project_score: float | None
    matched_required_skills: tuple[str, ...]
    matched_preferred_skills: tuple[str, ...]
    missing_required_skills: tuple[str, ...]
    missing_preferred_skills: tuple[str, ...]
    strengths: tuple[str, ...]
    suggestions: tuple[str, ...]
    explanation: str
    confidence: str
    details: dict[str, Any]
    evidence: tuple[MatchEvidence, ...] = ()
    missing_evidence: tuple[str, ...] = ()
    gaps: tuple[str, ...] = ()
    processing_state: str = STATE_READY
    algorithm_version: str = MATCHING_ALGORITHM_VERSION


# ---------------------------------------------------------------------------
# Requirement merging (deterministic rules first, intelligence fills gaps)
# ---------------------------------------------------------------------------
def merge_requirements(
    requirements: JobRequirements, fields: JobIntelligenceFields | None
) -> JobRequirements:
    """Fill requirement gaps from job intelligence without overriding rules.

    Phase 2F.1 extraction is evaluated against the posting text and only records
    what the posting states, so it can safely supply values the lightweight
    matching rules left empty. It must never replace a deterministic reading.
    """
    if fields is None:
        return requirements
    updates: dict[str, Any] = {}
    if not requirements.required_skills and fields.required_skills:
        updates["required_skills"] = tuple(fields.required_skills)
    if not requirements.preferred_skills and fields.preferred_skills:
        updates["preferred_skills"] = tuple(fields.preferred_skills)
    if requirements.experience_years is None and fields.experience_years is not None:
        updates["experience_years"] = fields.experience_years
    if requirements.education_level is None and fields.education_level:
        updates["education_level"] = fields.education_level
    if requirements.education_field is None and fields.education_field:
        updates["education_field"] = fields.education_field
    return replace(requirements, **updates) if updates else requirements


# ---------------------------------------------------------------------------
# Resume evidence
# ---------------------------------------------------------------------------
def _skill_evidence(
    resume: ResumeProfile,
    fields: ResumeIntelligenceFields | None,
) -> dict[str, _SkillPresent]:
    """Map every evidenced skill key to its strongest resume reference."""
    present: dict[str, _SkillPresent] = {}

    def add(
        skill_key: str, display: str, reference: str, confidence: str, credit: float
    ) -> None:
        if not skill_key:
            return
        current = present.get(skill_key)
        if current is None or credit > current.credit:
            present[skill_key] = _SkillPresent(
                key=skill_key,
                display=display,
                reference=reference,
                confidence=confidence,
                credit=credit,
            )

    if fields is not None:
        for fact in fields.skills:
            add(
                canonical_skill_key(fact.name),
                fact.name,
                _skill_reference(fact),
                fact.confidence if fact.confidence else CONFIDENCE_MEDIUM,
                _skill_credit(fact),
            )
        for index, role in enumerate(fields.experience):
            label = role.role or role.company or f"role {index + 1}"
            reference = f"experience: {label}"
            for technology in role.technologies:
                add(
                    canonical_skill_key(technology),
                    technology,
                    reference,
                    CONFIDENCE_MEDIUM,
                    0.8,
                )
            for key in _text_keys(*role.responsibilities):
                add(key, _display_name(key), reference, CONFIDENCE_MEDIUM, 0.7)
        for project in fields.projects:
            reference = f"project: {project.title}"
            for technology in (*project.technologies, *project.demonstrated_skills):
                add(
                    canonical_skill_key(technology),
                    technology,
                    reference,
                    CONFIDENCE_MEDIUM,
                    0.75,
                )
        return present

    # No resume intelligence: the stored, explicitly parsed resume data.
    for skill in resume.skills:
        key = canonical_skill_key(skill)
        add(key, _display_name(key, skill), "skills", CONFIDENCE_HIGH, 1.0)
    for entry in resume.experience:
        label = entry.title or entry.company or "role"
        for key in entry.skill_keys:
            add(key, _display_name(key), f"experience: {label}", CONFIDENCE_MEDIUM, 0.8)
    for project in resume.projects:
        for key in project.skill_keys:
            add(key, _display_name(key), f"project: {project.name}", CONFIDENCE_MEDIUM, 0.75)
    return present


def _skill_reference(fact: ResumeSkillFact) -> str:
    if fact.method in {METHOD_SKILLS_SECTION, METHOD_LABELLED_SKILL_GROUP}:
        return "skills section"
    return "resume text"


def _skill_credit(fact: ResumeSkillFact) -> float:
    if not fact.explicit:
        return 0.7
    return 1.0 if fact.confidence == CONFIDENCE_HIGH else 0.9


def _roles(
    resume: ResumeProfile, fields: ResumeIntelligenceFields | None
) -> tuple[tuple[_Role, ...], int, bool]:
    """Return ``(roles, dated_months, has_dated_roles)`` without inventing dates."""
    if fields is not None:
        roles: list[_Role] = []
        for index, fact in enumerate(fields.experience):
            label = fact.role or fact.company or f"role {index + 1}"
            keys = set(_text_keys(*fact.responsibilities))
            for technology in fact.technologies:
                keys.add(canonical_skill_key(technology))
            is_internship = bool(
                _INTERNSHIP_RE.search(f"{fact.role or ''} {fact.company or ''}")
            )
            roles.append(
                _Role(
                    title=fact.role,
                    company=fact.company,
                    start_date=fact.start_date,
                    end_date=fact.end_date,
                    is_current=fact.is_current,
                    is_internship=is_internship,
                    skill_keys=frozenset(keys),
                    reference=f"experience: {label}",
                    months=fact.duration_months,
                )
            )
        months = fields.total_experience_months or 0
        return tuple(roles), months, fields.has_dated_experience

    roles = []
    for entry in resume.experience:
        label = entry.title or entry.company or "role"
        roles.append(
            _Role(
                title=entry.title,
                company=entry.company,
                start_date=entry.start_date,
                end_date=entry.end_date,
                is_current=entry.is_current,
                is_internship=bool(
                    _INTERNSHIP_RE.search(f"{entry.title or ''} {entry.company or ''}")
                ),
                skill_keys=entry.skill_keys,
                reference=f"experience: {label}",
                months=None,
            )
        )
    # The caller recomputes months with its evaluation date.
    return tuple(roles), 0, False


def _projects(
    resume: ResumeProfile, fields: ResumeIntelligenceFields | None
) -> tuple[_Project, ...]:
    if fields is not None:
        projects: list[_Project] = []
        for fact in fields.projects:
            keys = {canonical_skill_key(technology) for technology in fact.technologies}
            keys.update(canonical_skill_key(skill) for skill in fact.demonstrated_skills)
            keys.update(_text_keys(fact.description, fact.contribution))
            projects.append(
                _Project(
                    name=fact.title,
                    skill_keys=frozenset(keys),
                    reference=f"project: {fact.title}",
                )
            )
        return tuple(projects)
    return tuple(
        _Project(
            name=project.name,
            skill_keys=project.skill_keys,
            reference=f"project: {project.name}",
        )
        for project in resume.projects
    )


def _education_evidence(
    resume: ResumeProfile, fields: ResumeIntelligenceFields | None
) -> tuple[int, str | None, str | None, tuple[str, ...]]:
    """Return ``(best_rank, label, reference, text_tokens_source)``."""
    if fields is not None and fields.education:
        best_rank = 0
        best_label: str | None = None
        best_reference: str | None = None
        texts: list[str] = []
        for index, fact in enumerate(fields.education):
            text = " ".join(
                part for part in (fact.degree, fact.specialization) if part
            )
            texts.append(text)
            rank = education_level_rank(text)
            if rank > best_rank:
                best_rank = rank
                best_label = fact.degree or fact.specialization or "degree"
                best_reference = (
                    f"education: {fact.institution or fact.degree or f'entry {index + 1}'}"
                )
        return best_rank, best_label, best_reference, tuple(texts)

    if not resume.education:
        return 0, None, None, ()
    entry = max(resume.education, key=lambda item: (item.level, item.degree or ""))
    label = entry.degree or entry.field_of_study or "degree"
    return (
        entry.level,
        label,
        f"education: {entry.institution or label}",
        tuple(item.text for item in resume.education),
    )


def _certification_keys(values: list[str] | tuple[str, ...]) -> dict[str, str]:
    keys: dict[str, str] = {}
    for value in values:
        normalized = re.sub(r"[^a-z0-9 ]", " ", (value or "").casefold())
        normalized = " ".join(normalized.split())
        if normalized:
            keys.setdefault(normalized, value)
    return keys


def _match_certifications(
    required: tuple[str, ...], resume_certifications: tuple[str, ...]
) -> list[tuple[str, str]]:
    """Return ``(posting_certification, resume_certification)`` pairs."""
    resume_keys = _certification_keys(resume_certifications)
    matched: list[tuple[str, str]] = []
    for posting_name in required:
        wanted = " ".join(
            re.sub(r"[^a-z0-9 ]", " ", posting_name.casefold()).split()
        )
        if not wanted:
            continue
        for key, original in resume_keys.items():
            if wanted in key or key in wanted:
                matched.append((posting_name, original))
                break
            wanted_tokens = set(wanted.split())
            key_tokens = set(key.split())
            if len(wanted_tokens & key_tokens) >= max(2, len(wanted_tokens)):
                matched.append((posting_name, original))
                break
    return matched


# ---------------------------------------------------------------------------
# Dimension scoring
# ---------------------------------------------------------------------------
def _coverage(
    pairs: list[tuple[str, str]], present: dict[str, _SkillPresent]
) -> tuple[float | None, list[tuple[str, str, _SkillPresent]]]:
    if not pairs:
        return None, []
    credit = 0.0
    matched: list[tuple[str, str, _SkillPresent]] = []
    for key, display in pairs:
        evidence = present.get(key)
        if evidence is None:
            continue
        credit += min(1.0, max(0.0, evidence.credit))
        matched.append((key, display, evidence))
    return min(1.0, credit / len(pairs)), matched


def _score_skills(
    requirements: JobRequirements, present: dict[str, _SkillPresent]
) -> tuple[
    float | None,
    list[tuple[str, str, _SkillPresent]],
    list[tuple[str, str, _SkillPresent]],
    list[tuple[str, str, _SkillPresent]],
    float | None,
]:
    required_pairs = _skill_pairs(list(requirements.required_skills))
    preferred_pairs = _skill_pairs(list(requirements.preferred_skills))
    mentioned_pairs = _skill_pairs(list(requirements.mentioned_skills))

    required_credit, required_matches = _coverage(required_pairs, present)
    preferred_credit, preferred_matches = _coverage(preferred_pairs, present)
    mentioned_credit, mentioned_matches = _coverage(mentioned_pairs, present)

    if required_credit is not None:
        if preferred_credit is not None:
            value = 0.85 * required_credit + 0.15 * preferred_credit
        else:
            value = required_credit
    elif preferred_credit is not None:
        value = 0.9 * preferred_credit
    elif mentioned_credit is not None:
        value = 0.9 * mentioned_credit
    else:
        return None, [], [], [], None
    return (
        min(1.0, max(0.0, value)),
        required_matches,
        preferred_matches,
        mentioned_matches,
        required_credit,
    )


def _role_relevance(role: _Role, job_keys: set[str], job_tokens: set[str]) -> bool:
    if role.skill_keys & job_keys:
        return True
    return bool(set(title_tokens(role.title)) & job_tokens)


def _score_experience(
    *,
    required_years: float | None,
    months: int,
    has_dated: bool,
    roles: tuple[_Role, ...],
    relevant: int,
) -> float:
    if not roles:
        return 0.0
    relevance = relevant / len(roles)
    if required_years is None:
        return min(1.0, 0.35 + 0.65 * relevance)
    if not has_dated:
        # Roles exist but carry no usable dates: partial credit, never a claim.
        return 0.4
    duration = min(1.0, (months / 12) / required_years)
    return min(1.0, 0.85 * duration + 0.15 * relevance)


def _score_title(
    requirements: JobRequirements,
    roles: tuple[_Role, ...],
    projects: tuple[_Project, ...],
    summary: str | None,
) -> tuple[float | None, tuple[str, ...]]:
    job_tokens = set(title_tokens(requirements.job_title))
    if not job_tokens:
        return None, ()
    role_tokens: set[str] = set()
    for role in roles:
        role_tokens.update(title_tokens(role.title))
    # A professional summary is candidate-stated role evidence. The previous
    # engine counted it, so it keeps full credit to preserve score semantics.
    role_tokens.update(title_tokens(summary))
    project_tokens: set[str] = set()
    for project in projects:
        project_tokens.update(title_tokens(project.name))
    matched: list[str] = []
    credit = 0.0
    for token in sorted(job_tokens):
        if token in role_tokens:
            credit += 1.0
            matched.append(token)
        elif token in project_tokens:
            # A project name is weaker role evidence than a designation.
            credit += 0.5
            matched.append(token)
    return min(1.0, credit / len(job_tokens)), tuple(matched)


def _score_projects(
    requirements: JobRequirements, projects: tuple[_Project, ...]
) -> tuple[float | None, _Project | None, list[str]]:
    pairs = _skill_pairs(
        list(
            (*requirements.required_skills, *requirements.preferred_skills)
            or requirements.mentioned_skills
        )
    )
    if not pairs:
        return None, None, []
    if not projects:
        return 0.0, None, []
    job_keys = {key for key, _display in pairs}
    best: tuple[int, _Project, list[str]] | None = None
    for project in projects:
        overlap = project.skill_keys & job_keys
        if not overlap:
            continue
        names = sorted(
            {_display_name(key) for key in overlap}, key=str.casefold
        )
        if best is None or len(overlap) > best[0]:
            best = (len(overlap), project, names)
    if best is None:
        return 0.0, None, []
    return min(1.0, best[0] / len(job_keys)), best[1], best[2][:4]


def _score_education(
    *,
    required_level: str | None,
    required_field: str | None,
    rank: int,
    text_sources: tuple[str, ...],
) -> float | None:
    required_rank = _EDUCATION_RANK.get((required_level or "").casefold(), 0)
    if required_rank == 0:
        return None
    if rank == 0:
        value = 0.0
    elif rank >= required_rank:
        value = 1.0
    else:
        value = max(0.25, 0.75 * (rank / required_rank))
    if required_field:
        field_tokens = {
            token
            for token in title_tokens(required_field)
            if token not in {"computer", "science", "engineering"}
        }
        if field_tokens and any(
            field_tokens & set(title_tokens(text)) for text in text_sources
        ):
            value = min(1.0, value + 0.1)
    return value


# ---------------------------------------------------------------------------
# Narrative and evidence assembly
# ---------------------------------------------------------------------------
def _percent(value: float | None) -> float | None:
    return None if value is None else round(min(1.0, max(0.0, value)) * 100, 1)


def _format_list(values: list[str], limit: int = 8) -> str:
    shown = values[:limit]
    text = ", ".join(shown)
    if len(values) > limit:
        text += f" and {len(values) - limit} more"
    return text


def _skill_evidence_entries(
    matched: list[tuple[str, str, _SkillPresent]], *, required: bool
) -> list[MatchEvidence]:
    evidence: list[MatchEvidence] = []
    for _key, display, present in matched:
        same_spelling = present.display.casefold().strip() == display.casefold().strip()
        evidence.append(
            MatchEvidence(
                category=EVIDENCE_REQUIRED_SKILL if required else EVIDENCE_PREFERRED_SKILL,
                description=(
                    f"{display} is evidenced by the resume "
                    f"({present.reference})."
                ),
                resume_reference=present.reference,
                confidence=present.confidence,
                match_reason=(
                    "Exact skill match."
                    if same_spelling
                    else f"Equivalent skill match: the resume states '{present.display}'."
                ),
            )
        )
    return evidence


def _processing_state(
    *,
    job_fields: JobIntelligenceFields | None,
    resume_fields: ResumeIntelligenceFields | None,
    job_intelligence_state: str,
    resume_intelligence_state: str,
    has_resume_evidence: bool,
    requirements: JobRequirements,
) -> str:
    if resume_intelligence_state == STATUS_FAILED and not has_resume_evidence:
        return STATE_RESUME_FAILED
    if not has_resume_evidence:
        return STATE_INSUFFICIENT
    if job_fields is None and not requirements.has_description:
        return STATE_INCOMPLETE_JOB
    if job_intelligence_state != STATUS_CURRENT or resume_intelligence_state != STATUS_CURRENT:
        return STATE_PARTIAL
    return STATE_READY


def _result_confidence(
    *,
    state: str,
    has_description: bool,
    job_confidence: str,
    resume_confidence: str,
    required_coverage: float | None,
) -> str:
    if state in {STATE_INSUFFICIENT, STATE_RESUME_FAILED}:
        return CONFIDENCE_LOW
    if not has_description or job_confidence == CONFIDENCE_LOW:
        return CONFIDENCE_LOW
    coverage = required_coverage if required_coverage is not None else 0.0
    if job_confidence == CONFIDENCE_HIGH and resume_confidence == CONFIDENCE_HIGH:
        return CONFIDENCE_HIGH if coverage >= 0.6 else CONFIDENCE_MEDIUM
    return CONFIDENCE_MEDIUM


def _build_strengths(
    *,
    requirements: JobRequirements,
    matched_required: list[str],
    matched_preferred: list[str],
    months: int,
    has_dated: bool,
    education_score: float | None,
    education_label: str | None,
    title_matches: tuple[str, ...],
    project_evidence: tuple[_Project, list[str]] | None,
    matched_certifications: list[tuple[str, str]],
    roles: tuple[_Role, ...],
) -> list[str]:
    strengths: list[str] = []
    if matched_required:
        strengths.append(
            f"Matches {len(matched_required)} of "
            f"{len(requirements.required_skills)} required skills: "
            f"{_format_list(matched_required)}."
        )
    if matched_preferred:
        strengths.append(
            f"Also covers preferred skills: {_format_list(matched_preferred)}."
        )
    if requirements.experience_years is not None and has_dated:
        years = round(months / 12, 1)
        if years >= requirements.experience_years:
            strengths.append(
                f"About {years:g} years of dated experience meets the "
                f"{requirements.experience_years:g}-year requirement."
            )
    elif requirements.experience_years is None and roles:
        strengths.append(
            f"Includes {len(roles)} work experience "
            f"{'entry' if len(roles) == 1 else 'entries'}."
        )
    if education_score is not None and education_score >= 0.99:
        strengths.append(
            f"Education includes {education_label or 'a degree'}, meeting the "
            f"{requirements.education_level} requirement."
        )
    if title_matches:
        strengths.append(
            f"Role language overlaps on {', '.join(title_matches[:4])}."
        )
    if project_evidence is not None:
        project, skills = project_evidence
        strengths.append(f"Project '{project.name}' shows {', '.join(skills)}.")
    if matched_certifications:
        strengths.append(
            "Certifications match the posting: "
            f"{_format_list([posting for posting, _resume in matched_certifications])}."
        )
    return strengths[:MAX_STRENGTHS]


def _build_suggestions(
    *,
    requirements: JobRequirements,
    missing_required: list[str],
    missing_preferred: list[str],
    months: int,
    has_dated: bool,
    roles: tuple[_Role, ...],
    education_score: float | None,
    projects: tuple[_Project, ...],
    missing_certifications: list[str],
) -> list[str]:
    suggestions: list[str] = []
    for skill in missing_required[:5]:
        suggestions.append(
            f"The posting lists {skill} as a requirement; this resume does not "
            "mention it."
        )
    if len(missing_required) > 5:
        suggestions.append(
            f"{len(missing_required) - 5} more required skills are not mentioned "
            "on this resume."
        )
    for skill in missing_preferred[:3]:
        suggestions.append(
            f"{skill} is listed as preferred and is not mentioned on this resume."
        )
    for certification in missing_certifications[:2]:
        suggestions.append(
            f"The posting names {certification} as a certification; it was not "
            "found on this resume."
        )
    if not roles:
        suggestions.append(
            "No work experience was extracted from this resume, so experience "
            "could not be verified."
        )
    if requirements.experience_years is not None and roles:
        if not has_dated:
            suggestions.append(
                f"The posting asks for {requirements.experience_years:g} years of "
                "experience; the roles on this resume have no usable dates, so "
                "the total could not be verified."
            )
        elif months / 12 < requirements.experience_years:
            suggestions.append(
                f"The posting asks for {requirements.experience_years:g} years; "
                f"this resume shows about {months / 12:g} years of dated experience."
            )
    if (
        requirements.education_level is not None
        and education_score is not None
        and education_score == 0
    ):
        suggestions.append(
            f"The posting mentions a {requirements.education_level} qualification, "
            "and no education was extracted from this resume."
        )
    if (requirements.required_skills or requirements.mentioned_skills) and not projects:
        suggestions.append(
            "No projects were extracted from this resume; a relevant project can "
            "show applied skills."
        )
    if not requirements.has_description:
        suggestions.append(
            "The posting has no job description, so only the title and the "
            "available information were evaluated."
        )
    if not suggestions:
        suggestions.append(
            "No specific gaps were identified in the information available for "
            "this posting."
        )
    return suggestions[:MAX_SUGGESTIONS]


def _explanation(
    *,
    filename: str,
    matched_required: list[str],
    matched_mentioned: list[str],
    months: int,
    has_dated: bool,
    title_matches: tuple[str, ...],
    requirements: JobRequirements,
) -> str:
    parts: list[str] = []
    if matched_required:
        parts.append(
            f"covers {len(matched_required)} of "
            f"{len(requirements.required_skills)} required skills"
        )
    elif requirements.mentioned_skills and matched_mentioned:
        parts.append(
            f"mentions {len(matched_mentioned)} of the "
            f"{len(requirements.mentioned_skills)} technologies named in the posting"
        )
    if requirements.experience_years is not None and has_dated:
        years = round(months / 12, 1)
        if years >= requirements.experience_years:
            parts.append(f"shows about {years:g} years of dated experience")
    if title_matches:
        parts.append(f"title language overlaps on {', '.join(title_matches[:3])}")
    if not parts:
        parts.append(
            "is the closest available match for the limited information in this posting"
        )
    return f"{filename} " + ", ".join(parts) + "."


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def match_resume_with_evidence(
    requirements: JobRequirements,
    resume: ResumeProfile,
    *,
    as_of: date,
    job_intelligence: JobIntelligencePayload | None = None,
    job_intelligence_current: bool = False,
    job_intelligence_state: str = STATUS_MISSING,
    resume_intelligence: ResumeIntelligencePayload | None = None,
    resume_intelligence_current: bool = False,
    resume_intelligence_state: str = STATUS_MISSING,
    resume_error_code: str | None = None,
) -> AdvancedMatchResult:
    """Score one resume against one posting with itemized evidence."""
    job_fields = (
        job_intelligence.fields
        if job_intelligence is not None and job_intelligence_current
        else None
    )
    resume_fields = (
        resume_intelligence.fields
        if resume_intelligence is not None and resume_intelligence_current
        else None
    )

    effective = merge_requirements(requirements, job_fields)
    present = _skill_evidence(resume, resume_fields)
    roles, months, has_dated = _roles(resume, resume_fields)
    if resume_fields is None:
        # The parsed profile path computes months against the evaluation date.
        months, has_dated = estimate_experience_months(resume.experience, as_of=as_of)
    projects = _projects(resume, resume_fields)
    residency_rank, education_label, education_reference, education_texts = (
        _education_evidence(resume, resume_fields)
    )
    required_certifications = tuple(job_fields.required_certifications) if job_fields else ()
    resume_certifications = (
        tuple(fact.name for fact in resume_fields.certifications)
        if resume_fields is not None
        else resume.certifications
    )
    matched_certifications = _match_certifications(
        required_certifications, resume_certifications
    )
    unmatched_certifications = [
        name
        for name in required_certifications
        if name not in {posting for posting, _resume in matched_certifications}
    ]

    (
        skill_fraction,
        required_matches,
        preferred_matches,
        mentioned_matches,
        required_credit,
    ) = _score_skills(effective, present)
    job_keys = {
        key
        for key, _display in _skill_pairs(
            list(
                (*effective.required_skills, *effective.preferred_skills)
                or effective.mentioned_skills
            )
        )
    }
    job_tokens = set(title_tokens(effective.job_title))
    relevant_roles = [
        role for role in roles if _role_relevance(role, job_keys, job_tokens)
    ]
    experience_fraction = _score_experience(
        required_years=effective.experience_years,
        months=months,
        has_dated=has_dated,
        roles=roles,
        relevant=len(relevant_roles),
    )
    title_fraction, title_matches = _score_title(
        effective, roles, projects, resume.summary
    )
    project_fraction, project_evidence, project_skill_names = _score_projects(
        effective, projects
    )
    education_fraction = _score_education(
        required_level=effective.education_level,
        required_field=effective.education_field,
        rank=residency_rank,
        text_sources=education_texts,
    )

    matched_required = [display for _key, display, _present in required_matches]
    matched_preferred = [display for _key, display, _present in preferred_matches]
    matched_mentioned = [display for _key, display, _present in mentioned_matches]

    missing_required = [
        display
        for key, display in _skill_pairs(list(effective.required_skills))
        if key not in present
    ]
    missing_preferred = [
        display
        for key, display in _skill_pairs(list(effective.preferred_skills))
        if key not in present
    ]

    raw_dimensions: dict[str, float | None] = {
        "skills": skill_fraction,
        "experience": experience_fraction,
        "education": education_fraction,
        "title": title_fraction,
        "projects": project_fraction,
    }
    available = {
        key: value for key, value in raw_dimensions.items() if value is not None
    }
    total_weight = sum(DIMENSION_WEIGHTS[key] for key in available)
    overall = (
        sum(DIMENSION_WEIGHTS[key] * value for key, value in available.items())
        / total_weight
        * 100
        if available
        else 0.0
    )
    effective_weights = {
        key: round(DIMENSION_WEIGHTS[key] / total_weight, 4)
        for key in available
    }

    has_resume_evidence = bool(
        resume.skills
        or resume.experience
        or resume.education
        or resume.projects
        or resume.summary
        or (resume_fields is not None and _fields_have_evidence(resume_fields))
    )
    state = _processing_state(
        job_fields=job_fields,
        resume_fields=resume_fields,
        job_intelligence_state=job_intelligence_state,
        resume_intelligence_state=resume_intelligence_state,
        has_resume_evidence=has_resume_evidence,
        requirements=effective,
    )
    resume_confidence = (
        resume_intelligence.confidence
        if resume_intelligence is not None and resume_intelligence_current
        else (CONFIDENCE_MEDIUM if has_resume_evidence else CONFIDENCE_LOW)
    )
    job_confidence = (
        job_intelligence.confidence
        if job_intelligence is not None and job_intelligence_current
        else effective.confidence
    )
    confidence = _result_confidence(
        state=state,
        has_description=effective.has_description,
        job_confidence=job_confidence,
        resume_confidence=resume_confidence,
        required_coverage=required_credit,
    )

    # --- Evidence -------------------------------------------------------
    evidence: list[MatchEvidence] = []
    evidence.extend(_skill_evidence_entries(required_matches, required=True))
    evidence.extend(_skill_evidence_entries(preferred_matches, required=False))
    for role in relevant_roles[:3]:
        shared = sorted(
            {_display_name(key) for key in role.skill_keys & job_keys},
            key=str.casefold,
        )
        reason = (
            f"Role overlaps the posting on {', '.join(shared[:4])}."
            if shared
            else "Role language overlaps the posting title."
        )
        description = f"{role.title or 'Role'}"
        if role.company:
            description += f" at {role.company}"
        if role.is_internship:
            description += " (internship)"
        evidence.append(
            MatchEvidence(
                category=EVIDENCE_EXPERIENCE,
                description=description,
                resume_reference=role.reference,
                confidence=CONFIDENCE_HIGH if shared else CONFIDENCE_MEDIUM,
                match_reason=reason,
            )
        )
    if title_matches:
        evidence.append(
            MatchEvidence(
                category=EVIDENCE_TITLE,
                description=f"Job title '{effective.job_title}'.",
                resume_reference=", ".join(
                    role.reference for role in roles[:2]
                )
                or None,
                confidence=CONFIDENCE_MEDIUM,
                match_reason=(
                    "Shared role language: " + ", ".join(title_matches[:5]) + "."
                ),
            )
        )
    if project_evidence is not None:
        evidence.append(
            MatchEvidence(
                category=EVIDENCE_PROJECT,
                description=f"Project '{project_evidence.name}'.",
                resume_reference=project_evidence.reference,
                confidence=CONFIDENCE_MEDIUM,
                match_reason=(
                    "The project evidences "
                    + ", ".join(project_skill_names)
                    + "."
                ),
            )
        )
    if education_fraction is not None:
        evidence.append(
            MatchEvidence(
                category=EVIDENCE_EDUCATION,
                description=f"Education evidence: {education_label or 'not stated'}.",
                resume_reference=education_reference,
                confidence=(
                    CONFIDENCE_HIGH
                    if education_fraction >= 0.99
                    else CONFIDENCE_MEDIUM
                ),
                match_reason=(
                    f"The posting asks for {effective.education_level}; "
                    + (
                        "the resume's degree level meets it."
                        if education_fraction >= 0.99
                        else "the resume does not clearly meet it."
                    )
                ),
            )
        )
    for posting_name, resume_name in matched_certifications:
        evidence.append(
            MatchEvidence(
                category=EVIDENCE_CERTIFICATION,
                description=f"{posting_name} requested; resume shows {resume_name}.",
                resume_reference="certifications",
                confidence=CONFIDENCE_MEDIUM,
                match_reason="Certification named in the posting is present on the resume.",
            )
        )

    missing_evidence: list[str] = []
    for skill in missing_required:
        missing_evidence.append(
            f"Required skill '{skill}' is not stated on this resume."
        )
    for skill in missing_preferred:
        missing_evidence.append(
            f"Preferred skill '{skill}' is not stated on this resume."
        )
    for certification in unmatched_certifications:
        missing_evidence.append(
            f"Certification '{certification}' is not stated on this resume."
        )
    if (
        effective.experience_years is not None
        and roles
        and not has_dated
    ):
        missing_evidence.append(
            "The roles on this resume have no usable dates, so total experience "
            "is unknown rather than absent."
        )
    has_education_entries = bool(
        resume.education or (resume_fields is not None and resume_fields.education)
    )
    if effective.education_level is not None and not has_education_entries:
        missing_evidence.append(
            "No education is stated on this resume; this is unknown, not a "
            "finding that the candidate is unqualified."
        )
    missing_evidence = missing_evidence[:MAX_MISSING_EVIDENCE]

    gaps: list[str] = []
    if effective.required_skills:
        gaps.append(
            f"Required skills: {len(required_matches)} of "
            f"{len(_skill_pairs(list(effective.required_skills)))} evidenced."
        )
    if preferred_matches or missing_preferred:
        total_preferred = len(_skill_pairs(list(effective.preferred_skills)))
        gaps.append(
            f"Preferred skills: {len(preferred_matches)} of "
            f"{total_preferred} evidenced."
        )
    if roles:
        gaps.append(
            f"Experience: {len(relevant_roles)} of {len(roles)} roles relevant"
            + (
                f", about {months / 12:g} years dated."
                if has_dated
                else ", dates not usable."
            )
        )
    if project_fraction is not None:
        gaps.append(
            f"Projects: {len(project_skill_names)} posting skills evidenced by "
            f"{'a project' if project_evidence is not None else 'no project'}."
        )
    if education_fraction is not None:
        gaps.append(
            "Education: requirement stated and "
            + ("met by the resume." if education_fraction >= 0.99 else "not clearly met.")
        )
    if required_certifications:
        gaps.append(
            f"Certifications: {len(matched_certifications)} of "
            f"{len(required_certifications)} named in the posting evidenced."
        )
    gaps = gaps[:MAX_GAPS]

    strengths = _build_strengths(
        requirements=effective,
        matched_required=matched_required,
        matched_preferred=matched_preferred,
        months=months,
        has_dated=has_dated,
        education_score=education_fraction,
        education_label=education_label,
        title_matches=title_matches,
        project_evidence=(
            (project_evidence, project_skill_names)
            if project_evidence is not None
            else None
        ),
        matched_certifications=matched_certifications,
        roles=roles,
    )
    suggestions = _build_suggestions(
        requirements=effective,
        missing_required=missing_required,
        missing_preferred=missing_preferred,
        months=months,
        has_dated=has_dated,
        roles=roles,
        education_score=education_fraction,
        projects=projects,
        missing_certifications=unmatched_certifications,
    )

    details: dict[str, Any] = {
        "algorithm_version": MATCHING_ALGORITHM_VERSION,
        "processing_state": state,
        "inputs": {
            "job_intelligence": job_intelligence_state,
            "resume_intelligence": resume_intelligence_state,
            "resume_error_code": resume_error_code,
        },
        "evidence": [item.to_dict() for item in evidence[:MAX_EVIDENCE]],
        "missing_evidence": missing_evidence,
        "gaps": gaps,
        "evaluated_dimensions": sorted(available),
        "effective_weights": effective_weights,
        "estimated_experience_years": (
            round(months / 12, 1) if has_dated else None
        ),
        "required_experience_years": effective.experience_years,
        "resume_has_dated_experience": has_dated,
        "skills_source": effective.skills_source,
        "job_has_description": effective.has_description,
        "certifications": {
            "required": list(required_certifications),
            "matched": [
                {"requested": posting, "resume": resume_name}
                for posting, resume_name in matched_certifications
            ],
        },
        "intelligence_confidence": {
            "job": job_confidence,
            "resume": resume_confidence,
        },
    }

    return AdvancedMatchResult(
        resume_id=resume.resume_id,
        resume_filename=resume.filename,
        overall_score=round(min(100.0, max(0.0, overall)), 1),
        skill_score=_percent(skill_fraction),
        experience_score=_percent(experience_fraction),
        education_score=_percent(education_fraction),
        title_score=_percent(title_fraction),
        project_score=_percent(project_fraction),
        matched_required_skills=tuple(sorted(matched_required, key=str.casefold)),
        matched_preferred_skills=tuple(sorted(matched_preferred, key=str.casefold)),
        missing_required_skills=tuple(sorted(missing_required, key=str.casefold)),
        missing_preferred_skills=tuple(sorted(missing_preferred, key=str.casefold)),
        strengths=tuple(strengths),
        suggestions=tuple(suggestions),
        explanation=_explanation(
            filename=resume.filename,
            matched_required=matched_required,
            matched_mentioned=matched_mentioned,
            months=months,
            has_dated=has_dated,
            title_matches=title_matches,
            requirements=effective,
        ),
        confidence=confidence,
        details=details,
        evidence=tuple(evidence[:MAX_EVIDENCE]),
        missing_evidence=tuple(missing_evidence),
        gaps=tuple(gaps),
        processing_state=state,
        algorithm_version=MATCHING_ALGORITHM_VERSION,
    )


def _fields_have_evidence(fields: ResumeIntelligenceFields) -> bool:
    return bool(
        fields.summary
        or fields.skills
        or fields.experience
        or fields.projects
        or fields.education
        or fields.certifications
    )


__all__ = [
    "EVIDENCE_CERTIFICATION",
    "EVIDENCE_EDUCATION",
    "EVIDENCE_EXPERIENCE",
    "EVIDENCE_PREFERRED_SKILL",
    "EVIDENCE_PROJECT",
    "EVIDENCE_REQUIRED_SKILL",
    "EVIDENCE_TITLE",
    "MATCHING_ALGORITHM_VERSION",
    "PROCESSING_STATES",
    "STATE_INCOMPLETE_JOB",
    "STATE_INSUFFICIENT",
    "STATE_PARTIAL",
    "STATE_READY",
    "STATE_RESUME_FAILED",
    "STATUS_CURRENT",
    "STATUS_FAILED",
    "STATUS_MISSING",
    "STATUS_STALE",
    "AdvancedMatchResult",
    "MatchEvidence",
    "canonical_skill_key",
    "match_resume_with_evidence",
    "merge_requirements",
]
