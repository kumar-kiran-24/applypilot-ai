"""Explainable, deterministic resume-to-job compatibility scoring.

The engine is pure: it takes extracted job requirements and resume profiles and
returns scores with the evidence behind them. It never calls an LLM, never
invents a skill, and never treats a score as a hiring probability.

Scoring model
-------------
Five dimensions are scored 0-100 and combined with fixed weights:

===========  ======  =====================================================
Dimension    Weight  Evidence
===========  ======  =====================================================
Skills       0.40    Required-skill coverage (plus preferred coverage)
Experience   0.25    Years written in the posting vs dated resume roles
Title        0.15    Shared role language between posting and resume
Projects     0.10    Project technologies overlapping the posting's skills
Education    0.10    Degree level stated in the posting
===========  ======  =====================================================

A dimension the posting gives no information for is *not evaluated*: its weight
is redistributed proportionally instead of awarding a free score. Each result
carries the dimensions that were used, so the overall number is reproducible.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.services.job_requirements import JobRequirements
from app.services.resume_parser import TECH_SKILLS

DIMENSION_WEIGHTS: dict[str, float] = {
    "skills": 0.40,
    "experience": 0.25,
    "title": 0.15,
    "projects": 0.10,
    "education": 0.10,
}

DIMENSION_LABELS: dict[str, str] = {
    "skills": "Skills",
    "experience": "Experience",
    "title": "Title relevance",
    "projects": "Projects",
    "education": "Education",
}

SCORE_DISCLAIMER = (
    "Scores measure overlap between the parsed resume and the information "
    "available in the posting. They are not hiring probabilities."
)

MAX_LISTED_SKILLS = 8
MAX_STRENGTHS = 6
MAX_SUGGESTIONS = 8

_SENIORITY_AND_FILLER = {
    "a",
    "an",
    "and",
    "the",
    "of",
    "for",
    "with",
    "in",
    "at",
    "to",
    "on",
    "or",
    "remote",
    "hybrid",
    "onsite",
    "senior",
    "junior",
    "lead",
    "staff",
    "principal",
    "mid",
    "level",
    "entry",
    "sr",
    "jr",
    "i",
    "ii",
    "iii",
}

# Different spellings of the same role requirement are treated as equal.
_TITLE_SYNONYMS = {
    "developer": "engineer",
    "programmer": "engineer",
    "sde": "engineer",
    "swe": "engineer",
    "software": "software",
    "back": "backend",
    "backend": "backend",
    "front": "frontend",
    "frontend": "frontend",
    "fullstack": "fullstack",
    "full": "fullstack",
    "stack": "fullstack",
    "ml": "machinelearning",
    "ai": "artificialintelligence",
    "qa": "qualityassurance",
    "devops": "devops",
    "sre": "reliability",
}

# Normalised aliases accepted in addition to the shared technology vocabulary.
_SKILL_ALIASES: dict[str, str] = {
    "reactjs": "react",
    "vuejs": "vue",
    "node": "nodejs",
    "postgres": "postgresql",
    "k8s": "kubernetes",
    "golang": "go",
    "ml": "machinelearning",
    "dl": "deeplearning",
    "ts": "typescript",
    "js": "javascript",
    "csharp": "c#",
    "amazonwebservices": "aws",
    "googlecloudplatform": "googlecloud",
    "restful": "rest",
    "restapi": "rest",
    "microservice": "microservices",
    "tailwind": "tailwindcss",
    "powerbi": "powerbi",
    "githubaction": "githubactions",
    "scikitlearn": "scikitlearn",
    "springframework": "spring",
}

_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
_DATE_RE = re.compile(
    r"(?:(?P<month>jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+)?"
    r"(?P<year>(?:19|20)\d{2})",
    re.IGNORECASE,
)
_PRESENT_RE = re.compile(r"\b(?:present|current|now|today)\b", re.IGNORECASE)

_EDUCATION_RANK = {
    "associate": 2,
    "diploma": 2,
    "bachelor": 3,
    "master": 4,
    "doctorate": 5,
}

_DEGREE_PATTERNS: tuple[tuple[int, re.Pattern[str]], ...] = (
    (5, re.compile(r"\b(?:ph\.?\s?d|doctorate|doctoral)\b", re.IGNORECASE)),
    (4, re.compile(r"\b(?:master(?:'|\u2019)?s?|mba|m\.?\s?tech)\b", re.IGNORECASE)),
    (
        3,
        re.compile(
            r"\b(?:bachelor(?:'|\u2019)?s?|undergraduate|b\.?\s?tech|b\.?\s?e\.?)\b",
            re.IGNORECASE,
        ),
    ),
    (2, re.compile(r"\b(?:associate(?:'|\u2019)?s?|diploma)\b", re.IGNORECASE)),
)


def _skill_key(value: str) -> str:
    """Normalise a skill to a comparison key (``Node.js`` -> ``nodejs``)."""
    key = re.sub(r"[\s._\-/]+", "", (value or "").strip().casefold())
    return _SKILL_ALIASES.get(key, key)


_CANONICAL_SKILLS: dict[str, str] = {_skill_key(skill): skill for skill in TECH_SKILLS}


def normalize_skill_key(value: str) -> str:
    """Public skill comparator so services build profiles with the same rules."""
    return _skill_key(value)


def education_level_rank(text: str | None) -> int:
    """Return the highest degree rank mentioned in resume text (0 if none)."""
    for rank, pattern in _DEGREE_PATTERNS:
        if pattern.search(text or ""):
            return rank
    return 0


def _title_tokens(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    raw = re.findall(r"[a-z0-9+#.]+", value.casefold().replace("-", " ").replace("/", " "))
    tokens: list[str] = []
    for token in raw:
        token = token.strip(".")
        if len(token) < 2 or token in _SENIORITY_AND_FILLER:
            continue
        token = _TITLE_SYNONYMS.get(token, token)
        if token not in tokens:
            tokens.append(token)
    return tuple(tokens)


# ---------------------------------------------------------------------------
# Pure input models
# ---------------------------------------------------------------------------
@dataclass(slots=True, frozen=True)
class ExperienceEntry:
    """One parsed work-experience entry."""

    title: str | None
    company: str | None
    description: str | None
    technologies: tuple[str, ...]
    start_date: str | None
    end_date: str | None
    is_current: bool
    skill_keys: frozenset[str]

    @property
    def text(self) -> str:
        parts = [self.title, self.company, self.description, " ".join(self.technologies)]
        return " ".join(part for part in parts if part).casefold()


@dataclass(slots=True, frozen=True)
class EducationEntry:
    """One parsed education entry with a comparable degree rank."""

    degree: str | None
    field_of_study: str | None
    institution: str | None
    details: str | None
    level: int

    @property
    def text(self) -> str:
        parts = [self.degree, self.field_of_study, self.institution, self.details]
        return " ".join(part for part in parts if part).casefold()


@dataclass(slots=True, frozen=True)
class ProjectEntry:
    """One parsed project with the technologies it evidences."""

    name: str
    description: str | None
    technologies: tuple[str, ...]
    skill_keys: frozenset[str]

    @property
    def text(self) -> str:
        parts = [self.name, self.description, " ".join(self.technologies)]
        return " ".join(part for part in parts if part).casefold()


@dataclass(slots=True, frozen=True)
class ResumeProfile:
    """Normalised resume evidence used for scoring."""

    resume_id: str
    filename: str
    skills: tuple[str, ...]
    skill_keys: frozenset[str]
    summary: str | None
    titles: tuple[str, ...]
    experience: tuple[ExperienceEntry, ...]
    education: tuple[EducationEntry, ...]
    projects: tuple[ProjectEntry, ...]
    certifications: tuple[str, ...]


@dataclass(slots=True, frozen=True)
class MatchResult:
    """One resume's explainable score against one posting."""

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
    matched_mentioned_skills: tuple[str, ...]
    missing_required_skills: tuple[str, ...]
    missing_preferred_skills: tuple[str, ...]
    strengths: tuple[str, ...]
    suggestions: tuple[str, ...]
    explanation: str
    confidence: str
    details: dict[str, Any]


# ---------------------------------------------------------------------------
# Evidence helpers
# ---------------------------------------------------------------------------
def _canonical_skill_name(key: str) -> str:
    return _CANONICAL_SKILLS.get(key, key)


def _skill_display_order(skills: list[str]) -> list[str]:
    return sorted(skills, key=str.casefold)


def _date_month(value: str | None, *, end_of_year: bool = False) -> int | None:
    """Return an absolute month index for a parsed resume date, or ``None``."""
    if not value:
        return None
    match = _DATE_RE.search(value)
    if not match:
        return None
    year = int(match.group("year"))
    raw_month = match.group("month")
    month = _MONTHS.get(raw_month.casefold()[:3]) if raw_month else None
    if month is None:
        month = 12 if end_of_year else 1
    return year * 12 + month


def estimate_experience_months(
    experience: tuple[ExperienceEntry, ...], *, as_of: date
) -> tuple[int, bool]:
    """Return ``(months, has_dated_roles)`` from role date ranges.

    The total is a sum of dated roles; overlapping roles are not merged. Roles
    without a usable start date contribute nothing, so the result is never an
    invented estimate.
    """
    as_of_month = as_of.year * 12 + as_of.month
    total = 0
    dated = False
    for entry in experience:
        start = _date_month(entry.start_date)
        if start is None:
            continue
        if entry.is_current or _PRESENT_RE.search(entry.end_date or ""):
            end = as_of_month
        else:
            end = _date_month(entry.end_date, end_of_year=True)
        if end is None:
            continue
        if end < start:
            continue
        total += min(end - start, 480)
        dated = True
    return min(total, 720), dated


def _years_from_months(months: int) -> float:
    return round(months / 12, 1)


def _job_skill_keys(requirements: JobRequirements) -> frozenset[str]:
    return frozenset(
        _skill_key(skill)
        for skill in (*requirements.required_skills, *requirements.preferred_skills)
    )


def _all_job_skill_keys(requirements: JobRequirements) -> frozenset[str]:
    return frozenset(_skill_key(skill) for skill in requirements.mentioned_skills)


# ---------------------------------------------------------------------------
# Dimension scoring
# ---------------------------------------------------------------------------
def _score_skills(
    requirements: JobRequirements, resume: ResumeProfile
) -> tuple[float | None, list[str], list[str], list[str]]:
    """Return ``(score_fraction, matched_required, matched_preferred, matched_mentioned)``."""
    matched = resume.skill_keys
    required_matches = [
        skill for skill in requirements.required_skills if _skill_key(skill) in matched
    ]
    preferred_matches = [
        skill for skill in requirements.preferred_skills if _skill_key(skill) in matched
    ]
    mentioned_matches = [
        skill for skill in requirements.mentioned_skills if _skill_key(skill) in matched
    ]

    if requirements.required_skills:
        required_coverage = len(required_matches) / len(requirements.required_skills)
        if requirements.preferred_skills:
            preferred_coverage = len(preferred_matches) / len(requirements.preferred_skills)
            value = 0.85 * required_coverage + 0.15 * preferred_coverage
        else:
            value = required_coverage
    elif requirements.preferred_skills:
        # Only preferences were stated; full coverage is the ceiling.
        value = 0.9 * (len(preferred_matches) / len(requirements.preferred_skills))
    elif requirements.mentioned_skills:
        # Technologies named in prose are evidence, not an explicit requirement,
        # so the dimension is capped below a stated-requirements match.
        value = 0.9 * (len(mentioned_matches) / len(requirements.mentioned_skills))
    else:
        return None, [], [], []

    return (
        min(1.0, max(0.0, value)),
        required_matches,
        preferred_matches,
        mentioned_matches,
    )


def _experience_relevance(
    requirements: JobRequirements, resume: ResumeProfile
) -> float:
    """Share of roles whose text mentions a posting skill or title token."""
    if not resume.experience:
        return 0.0
    skill_keys = _all_job_skill_keys(requirements)
    job_title_tokens = set(_title_tokens(requirements.job_title))
    relevant = 0
    for entry in resume.experience:
        title_tokens = set(_title_tokens(entry.title))
        if (entry.skill_keys & skill_keys) or (title_tokens & job_title_tokens):
            relevant += 1
    return relevant / len(resume.experience)


def _score_experience(
    requirements: JobRequirements,
    resume: ResumeProfile,
    *,
    as_of: date,
) -> tuple[float, int, bool]:
    """Return ``(score_fraction, dated_months, has_dated_roles)``."""
    months, has_dated = estimate_experience_months(resume.experience, as_of=as_of)
    required_years = requirements.experience_years

    if required_years is None:
        if not resume.experience:
            return 0.0, months, has_dated
        relevance = _experience_relevance(requirements, resume)
        return min(1.0, 0.35 + 0.65 * relevance), months, has_dated

    if not resume.experience:
        return 0.0, months, has_dated
    if not has_dated:
        # The resume shows roles but no usable dates: partial credit, never a
        # claimed year count.
        return 0.4, months, has_dated
    years = months / 12
    return min(1.0, years / required_years), months, has_dated


def _score_education(
    requirements: JobRequirements, resume: ResumeProfile
) -> float | None:
    if requirements.education_level is None:
        return None
    required_rank = _EDUCATION_RANK.get(requirements.education_level, 0)
    if required_rank == 0:
        return None
    best_rank = max((entry.level for entry in resume.education), default=0)
    if best_rank == 0:
        value = 0.0
    elif best_rank >= required_rank:
        value = 1.0
    else:
        value = max(0.25, 0.75 * (best_rank / required_rank))

    if requirements.education_field:
        field_tokens = {
            token
            for token in _title_tokens(requirements.education_field)
            if token not in {"computer", "science", "engineering"}
        }
        if field_tokens and any(
            field_tokens & set(_title_tokens(entry.text)) for entry in resume.education
        ):
            value = min(1.0, value + 0.1)
    return value


def _score_title(
    requirements: JobRequirements, resume: ResumeProfile
) -> tuple[float | None, tuple[str, ...]]:
    job_tokens = set(_title_tokens(requirements.job_title))
    if not job_tokens:
        return None, ()
    resume_tokens: set[str] = set()
    for title in resume.titles:
        resume_tokens.update(_title_tokens(title))
    if resume.summary:
        resume_tokens.update(_title_tokens(resume.summary[:400]))
    matched = tuple(sorted(job_tokens & resume_tokens))
    return len(matched) / len(job_tokens), matched


def _score_projects(
    requirements: JobRequirements, resume: ResumeProfile
) -> float | None:
    job_keys = _job_skill_keys(requirements) or _all_job_skill_keys(requirements)
    if not job_keys:
        return None
    if not resume.projects:
        return 0.0
    best_overlap = 0
    for project in resume.projects:
        best_overlap = max(best_overlap, len(project.skill_keys & job_keys))
    return min(1.0, best_overlap / len(job_keys))


def _best_project(
    requirements: JobRequirements, resume: ResumeProfile
) -> tuple[ProjectEntry, list[str]] | None:
    job_keys = _job_skill_keys(requirements) or _all_job_skill_keys(requirements)
    if not job_keys or not resume.projects:
        return None
    scored: list[tuple[int, ProjectEntry]] = []
    for project in resume.projects:
        overlap = project.skill_keys & job_keys
        if overlap:
            scored.append((len(overlap), project))
    if not scored:
        return None
    scored.sort(key=lambda item: (-item[0], item[1].name.casefold()))
    _count, project = scored[0]
    matched = _skill_display_order(
        [_canonical_skill_name(key) for key in project.skill_keys & job_keys]
    )[:4]
    return project, matched


def _best_education(resume: ResumeProfile) -> EducationEntry | None:
    if not resume.education:
        return None
    return max(resume.education, key=lambda entry: (entry.level, entry.degree or ""))


def _format_skill_list(skills: list[str], limit: int = MAX_LISTED_SKILLS) -> str:
    shown = skills[:limit]
    text = ", ".join(shown)
    if len(skills) > limit:
        text += f" and {len(skills) - limit} more"
    return text


# ---------------------------------------------------------------------------
# Narrative generation (facts only)
# ---------------------------------------------------------------------------
def _build_strengths(
    requirements: JobRequirements,
    resume: ResumeProfile,
    *,
    matched_required: list[str],
    matched_preferred: list[str],
    matched_mentioned: list[str],
    title_matches: tuple[str, ...],
    months: int,
    has_dated: bool,
    education_score: float | None,
    project_evidence: tuple[ProjectEntry, list[str]] | None,
) -> list[str]:
    strengths: list[str] = []
    if matched_required:
        strengths.append(
            f"Matches {len(matched_required)} of {len(requirements.required_skills)} "
            f"required skills: {_format_skill_list(matched_required)}."
        )
    elif requirements.mentioned_skills and matched_mentioned:
        strengths.append(
            f"Mentions {len(matched_mentioned)} of the "
            f"{len(requirements.mentioned_skills)} technologies named in the posting: "
            f"{_format_skill_list(matched_mentioned)}."
        )
    if matched_preferred:
        strengths.append(
            f"Also covers preferred skills: {_format_skill_list(matched_preferred)}."
        )
    if requirements.experience_years is not None and has_dated:
        years = _years_from_months(months)
        if years >= requirements.experience_years:
            strengths.append(
                f"About {years:g} years of dated experience meets the "
                f"{requirements.experience_years:g}-year requirement."
            )
    elif requirements.experience_years is None and resume.experience:
        count = len(resume.experience)
        strengths.append(
            f"Includes {count} work experience {'entry' if count == 1 else 'entries'}."
        )
    if education_score is not None and education_score >= 0.99:
        entry = _best_education(resume)
        if entry is not None:
            label = entry.degree or entry.field_of_study or "degree"
            strengths.append(
                f"Education includes {label}, meeting the "
                f"{requirements.education_level} requirement."
            )
    if title_matches:
        strengths.append(f"Title language overlaps on {', '.join(title_matches[:4])}.")
    if project_evidence is not None:
        project, matched = project_evidence
        strengths.append(f"Project '{project.name}' uses {', '.join(matched)}.")
    return strengths[:MAX_STRENGTHS]


def _build_suggestions(
    requirements: JobRequirements,
    resume: ResumeProfile,
    *,
    missing_required: list[str],
    missing_preferred: list[str],
    months: int,
    has_dated: bool,
    education_score: float | None,
) -> list[str]:
    suggestions: list[str] = []
    for skill in missing_required[:5]:
        suggestions.append(
            f"The posting lists {skill} as a requirement; this resume does not mention it."
        )
    if len(missing_required) > 5:
        suggestions.append(
            f"{len(missing_required) - 5} more required skills are not mentioned on this resume."
        )
    for skill in missing_preferred[:3]:
        suggestions.append(
            f"{skill} is listed as preferred and is not mentioned on this resume."
        )
    if not resume.skills and not resume.experience and not resume.education:
        suggestions.append(
            "No structured skills, experience or education were extracted from this resume."
        )
    if requirements.experience_years is not None:
        if not resume.experience:
            suggestions.append(
                f"The posting asks for {requirements.experience_years:g} years of experience, "
                "and no work experience was extracted from this resume."
            )
        elif not has_dated:
            suggestions.append(
                "The posting asks for "
                f"{requirements.experience_years:g} years of experience; the roles on this "
                "resume have no usable dates, so the total could not be verified."
            )
        elif months / 12 < requirements.experience_years:
            suggestions.append(
                f"The posting asks for {requirements.experience_years:g} years; this resume "
                f"shows about {_years_from_months(months):g} years of dated experience."
            )
    if (
        requirements.education_level is not None
        and education_score is not None
        and education_score == 0
    ):
        suggestions.append(
            f"The posting mentions a {requirements.education_level} qualification, and no "
            "education was extracted from this resume."
        )
    if (
        (requirements.required_skills or requirements.mentioned_skills)
        and not resume.projects
    ):
        suggestions.append(
            "No projects were extracted from this resume; a relevant project can show "
            "applied skills."
        )
    if not requirements.has_description:
        suggestions.append(
            "The posting has no job description, so only the title and the available "
            "information were evaluated."
        )
    if not suggestions:
        suggestions.append(
            "No specific gaps were identified in the information available for this posting."
        )
    return suggestions[:MAX_SUGGESTIONS]


def _build_explanation(
    requirements: JobRequirements,
    *,
    matched_required: list[str],
    matched_mentioned: list[str],
    title_matches: tuple[str, ...],
    months: int,
    has_dated: bool,
    resume_filename: str,
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
        years = _years_from_months(months)
        if years >= requirements.experience_years:
            parts.append(f"shows about {years:g} years of dated experience")
    if title_matches:
        parts.append(f"title language overlaps on {', '.join(title_matches[:3])}")
    if not parts:
        parts.append(
            "is the closest available match for the limited information in this posting"
        )
    return f"{resume_filename} " + ", ".join(parts) + "."


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def match_resume(
    requirements: JobRequirements,
    resume: ResumeProfile,
    *,
    as_of: date,
) -> MatchResult:
    """Score one resume against one posting using explainable dimensions."""
    (
        skill_fraction,
        matched_required,
        matched_preferred,
        matched_mentioned,
    ) = _score_skills(requirements, resume)
    experience_fraction, months, has_dated = _score_experience(
        requirements, resume, as_of=as_of
    )
    education_fraction = _score_education(requirements, resume)
    title_fraction, title_matches = _score_title(requirements, resume)
    project_fraction = _score_projects(requirements, resume)

    missing_required = [
        skill
        for skill in requirements.required_skills
        if _skill_key(skill) not in resume.skill_keys
    ]
    missing_preferred = [
        skill
        for skill in requirements.preferred_skills
        if _skill_key(skill) not in resume.skill_keys
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
    if available:
        total_weight = sum(DIMENSION_WEIGHTS[key] for key in available)
        overall = (
            sum(DIMENSION_WEIGHTS[key] * value for key, value in available.items())
            / total_weight
            * 100
        )
    else:  # pragma: no cover - title is available whenever a title exists
        overall = 0.0

    effective_weights = {
        key: round(DIMENSION_WEIGHTS[key] / total_weight, 4) if available else 0.0
        for key in available
    }

    project_evidence = _best_project(requirements, resume)
    strengths = _build_strengths(
        requirements,
        resume,
        matched_required=matched_required,
        matched_preferred=matched_preferred,
        matched_mentioned=matched_mentioned,
        title_matches=title_matches,
        months=months,
        has_dated=has_dated,
        education_score=education_fraction,
        project_evidence=project_evidence,
    )
    suggestions = _build_suggestions(
        requirements,
        resume,
        missing_required=missing_required,
        missing_preferred=missing_preferred,
        months=months,
        has_dated=has_dated,
        education_score=education_fraction,
    )

    has_resume_evidence = bool(
        resume.skills
        or resume.experience
        or resume.education
        or resume.projects
        or resume.summary
    )
    confidence = requirements.confidence if has_resume_evidence else "low"

    details: dict[str, Any] = {
        "evaluated_dimensions": sorted(available),
        "effective_weights": effective_weights,
        "estimated_experience_years": _years_from_months(months) if has_dated else None,
        "required_experience_years": requirements.experience_years,
        "resume_has_dated_experience": has_dated,
        "skills_source": requirements.skills_source,
        "job_has_description": requirements.has_description,
    }

    def percent(value: float | None) -> float | None:
        return None if value is None else round(min(1.0, max(0.0, value)) * 100, 1)

    return MatchResult(
        resume_id=resume.resume_id,
        resume_filename=resume.filename,
        overall_score=round(min(100.0, max(0.0, overall)), 1),
        skill_score=percent(skill_fraction),
        experience_score=percent(experience_fraction),
        education_score=percent(education_fraction),
        title_score=percent(title_fraction),
        project_score=percent(project_fraction),
        matched_required_skills=tuple(_skill_display_order(matched_required)),
        matched_preferred_skills=tuple(_skill_display_order(matched_preferred)),
        matched_mentioned_skills=tuple(_skill_display_order(matched_mentioned)),
        missing_required_skills=tuple(
            _skill_display_order([_canonical_skill_name(_skill_key(s)) for s in missing_required])
        ),
        missing_preferred_skills=tuple(
            _skill_display_order([_canonical_skill_name(_skill_key(s)) for s in missing_preferred])
        ),
        strengths=tuple(strengths),
        suggestions=tuple(suggestions),
        explanation=_build_explanation(
            requirements,
            matched_required=matched_required,
            matched_mentioned=matched_mentioned,
            title_matches=title_matches,
            months=months,
            has_dated=has_dated,
            resume_filename=resume.filename,
        ),
        confidence=confidence,
        details=details,
    )


def rank_results(results: list[MatchResult]) -> list[MatchResult]:
    """Order results deterministically: score, then skills, filename and id."""
    return sorted(
        results,
        key=lambda result: (
            -result.overall_score,
            -(result.skill_score if result.skill_score is not None else -1.0),
            result.resume_filename.casefold(),
            result.resume_id,
        ),
    )


__all__ = [
    "DIMENSION_LABELS",
    "DIMENSION_WEIGHTS",
    "SCORE_DISCLAIMER",
    "EducationEntry",
    "ExperienceEntry",
    "MatchResult",
    "ProjectEntry",
    "ResumeProfile",
    "education_level_rank",
    "estimate_experience_months",
    "match_resume",
    "normalize_skill_key",
    "rank_results",
]
