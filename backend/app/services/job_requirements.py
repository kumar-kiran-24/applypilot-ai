"""Deterministic processing of a discovered job posting.

Glassdoor alert emails carry only a short block of text, and many postings have
no description at all. This module extracts what is literally present and never
invents a requirement: skills are only those found in the text, experience is
only a number actually written next to "years", and education is only a level
explicitly named.

Everything here is rule-based, so the result for the same posting text is
identical on every run and no personal data leaves the backend.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.services.resume_parser import detect_known_skills

MAX_DESCRIPTION_CHARS = 8000
MAX_RESPONSIBILITIES = 12
MAX_SKILLS = 40

#: Bumped when the extraction rules change so cached match details can be
#: identified as stale by comparing the stored job fingerprint.
REQUIREMENTS_VERSION = "1"

_REQUIRED_HEADING_RE = re.compile(
    r"^(?:requirements?|required(?:\s+skills)?|must[\s-]haves?|qualifications?|"
    r"minimum\s+qualifications?|what\s+you(?:'|\u2019)?ll\s+need|what\s+we(?:'|\u2019)?re\s+looking\s+for|"
    r"who\s+you\s+are|skills?\s+(?:and|&)\s+experience)\b",
    re.IGNORECASE,
)
_PREFERRED_HEADING_RE = re.compile(
    r"^(?:preferred(?:\s+(?:skills|qualifications))?|nice[\s-]to[\s-]haves?|"
    r"bonus(?:\s+points)?|desired|good\s+to\s+have|additional\s+(?:skills|qualifications)|"
    r"plus(?:es)?)\b",
    re.IGNORECASE,
)
_RESPONSIBILITY_HEADING_RE = re.compile(
    r"^(?:responsibilities|what\s+you(?:'|\u2019)?ll\s+do|what\s+you\s+will\s+do|the\s+role|"
    r"about\s+the\s+role|key\s+duties|duties|day[\s-]to[\s-]day)\b",
    re.IGNORECASE,
)
_OTHER_HEADING_RE = re.compile(
    r"^(?:benefits|perks|about\s+(?:us|the\s+company)|compensation|salary|"
    r"equal\s+opportunity|how\s+to\s+apply|why\s+(?:join|work))\b",
    re.IGNORECASE,
)
_BULLET_RE = re.compile(
    r"^[\s\-\u2013\u2014\u2022*\u00b7\u25aa\u25e6\u25cf\d.)]+"
)

_YEARS_RANGE_RE = re.compile(
    r"(?P<low>\d{1,2})\s*(?:\+|plus)?\s*(?:-|\u2013|\u2014|to)\s*(?P<high>\d{1,2})\s*"
    r"(?:years?|yrs?)\b",
    re.IGNORECASE,
)
_YEARS_SINGLE_RE = re.compile(
    r"(?P<low>\d{1,2})\s*(?:\+|plus)?\s*(?:years?|yrs?)\b",
    re.IGNORECASE,
)
_EXPERIENCE_CONTEXT_RE = re.compile(r"\bexperience\b|\bbackground\b|\bworking\b", re.IGNORECASE)
_YEAR_NUMBER_RE = re.compile(r"\b(?:19|20)\d{2}\b")

_EDUCATION_LEVELS: tuple[tuple[int, str, re.Pattern[str]], ...] = (
    (5, "doctorate", re.compile(r"\b(?:ph\.?\s?d|doctorate|doctoral)\b", re.IGNORECASE)),
    (
        4,
        "master",
        re.compile(
            r"\b(?:master(?:'|\u2019)?s?|mba|m\.?\s?s\.?|m\.?\s?tech)\b",
            re.IGNORECASE,
        ),
    ),
    (
        3,
        "bachelor",
        re.compile(
            r"\b(?:bachelor(?:'|\u2019)?s?|undergraduate|b\.?\s?s\.?|b\.?\s?tech|b\.?\s?e\.?)\b",
            re.IGNORECASE,
        ),
    ),
    (2, "associate", re.compile(r"\b(?:associate(?:'|\u2019)?s?|diploma)\b", re.IGNORECASE)),
)
_DEGREE_CONTEXT_RE = re.compile(
    r"\b(?:degree|bachelor(?:'|\u2019)?s?|master(?:'|\u2019)?s?|ph\.?\s?d|doctorate|diploma|"
    r"qualification|education)\b",
    re.IGNORECASE,
)
_FIELD_RE = re.compile(
    r"\b(?:degree|bachelor(?:'|\u2019)?s?|master(?:'|\u2019)?s?|ph\.?\s?d|doctorate|diploma)\b"
    r"[^.\n]{0,40}?\bin\s+(?P<field>[A-Za-z][A-Za-z &/,\-]{2,60})",
    re.IGNORECASE,
)
_FIELD_STOPWORDS = {
    "a",
    "an",
    "the",
    "related",
    "relevant",
    "equivalent",
    "or",
    "and",
    "with",
    "from",
    "any",
}


@dataclass(slots=True, frozen=True)
class JobRequirements:
    """Normalised, evidence-only view of a job posting."""

    job_title: str
    company: str | None
    location: str | None
    description: str | None
    required_skills: tuple[str, ...]
    preferred_skills: tuple[str, ...]
    mentioned_skills: tuple[str, ...]
    experience_years: float | None
    education_level: str | None
    education_field: str | None
    responsibilities: tuple[str, ...]
    has_description: bool
    skills_source: str
    confidence: str

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe representation (used in cached match details)."""
        return {
            "job_title": self.job_title,
            "company": self.company,
            "location": self.location,
            "required_skills": list(self.required_skills),
            "preferred_skills": list(self.preferred_skills),
            "mentioned_skills": list(self.mentioned_skills),
            "experience_years": self.experience_years,
            "education_level": self.education_level,
            "education_field": self.education_field,
            "responsibilities": list(self.responsibilities),
            "has_description": self.has_description,
            "skills_source": self.skills_source,
            "confidence": self.confidence,
        }


def _normalize_text(value: str | None) -> str:
    if not value:
        return ""
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    lines = [" ".join(line.split()) for line in text.split("\n")]
    return "\n".join(line for line in lines if line).strip()[:MAX_DESCRIPTION_CHARS]


def _heading_content(line: str, pattern: re.Pattern[str]) -> str | None:
    """Return inline content for a heading line, or ``""`` for a bare heading.

    ``None`` means the line is not a heading. Short label-style lines and lines
    ending in a colon are treated as headings so that "Requirements:" starts a
    section without swallowing the rest of the description.
    """
    stripped = line.strip()
    match = pattern.match(stripped)
    if not match:
        return None
    remainder = stripped[match.end() :].strip()
    if remainder.startswith((":", "\u2013", "-", "\u2014")):
        return remainder[1:].strip()
    # "Must have 5 years of experience" is a prefix heading with inline
    # content; "Requirements and qualifications" simply starts the section.
    return remainder


def _section_lines(text: str) -> tuple[list[str], dict[str, list[str]]]:
    """Split description lines into header text and classified sections."""
    header: list[str] = []
    sections: dict[str, list[str]] = {
        "required": [],
        "preferred": [],
        "responsibilities": [],
    }
    current: str | None = None

    for line in text.split("\n"):
        for name, pattern in (
            ("required", _REQUIRED_HEADING_RE),
            ("preferred", _PREFERRED_HEADING_RE),
            ("responsibilities", _RESPONSIBILITY_HEADING_RE),
            ("other", _OTHER_HEADING_RE),
        ):
            content = _heading_content(line, pattern)
            if content is None:
                continue
            if name == "other":
                current = "other"
            else:
                current = name
                if content:
                    sections[name].append(content)
            break
        else:
            if current == "other":
                continue
            if current is None:
                header.append(line)
            else:
                sections[current].append(line)
    return header, sections


def _skills(text: str) -> list[str]:
    return sorted(detect_known_skills(text), key=str.casefold)[:MAX_SKILLS]


def _detect_experience_years(text: str) -> float | None:
    """Return the minimum number of years literally requested, if any."""
    candidates: list[float] = []
    for line in text.split("\n"):
        if _YEAR_NUMBER_RE.search(line) and not re.search(r"years?|yrs?", line, re.IGNORECASE):
            continue
        range_match = _YEARS_RANGE_RE.search(line)
        if range_match:
            low = float(range_match.group("low"))
            high = float(range_match.group("high"))
            if high < low:
                low, high = high, low
            if 0 < low <= 40:
                candidates.append(low)
                continue
        # A bare "3+ years" line still refers to experience in a job posting.
        if (
            not _EXPERIENCE_CONTEXT_RE.search(line)
            and not _YEARS_RANGE_RE.search(line)
            and not re.match(r"^[\s\-\u2013\u2014\u2022*\u00b7\u25aa\u25e6\u25cf]*\d", line)
        ):
            continue
        for match in _YEARS_SINGLE_RE.finditer(line):
            value = float(match.group("low"))
            if 0 < value <= 40:
                candidates.append(value)
                break
    if not candidates:
        return None
    return min(candidates)


def _detect_education(text: str) -> tuple[str | None, str | None]:
    """Return ``(level, field)`` for the education requirement named in text."""
    for _rank, level, pattern in _EDUCATION_LEVELS:
        match = pattern.search(text)
        if not match:
            continue
        field_match = _FIELD_RE.search(text)
        field_value: str | None = None
        if field_match:
            candidate = field_match.group("field").strip(" .,;:-")
            words = [word for word in candidate.split() if word.casefold() not in _FIELD_STOPWORDS]
            if words:
                field_value = " ".join(words[:6])
        return level, field_value
    return None, None


def _responsibilities(lines: list[str]) -> tuple[str, ...]:
    cleaned: list[str] = []
    for line in lines:
        value = _BULLET_RE.sub("", line).strip()
        if not value or len(value) < 3 or len(value) > 300:
            continue
        if value.casefold() not in {entry.casefold() for entry in cleaned}:
            cleaned.append(value)
        if len(cleaned) >= MAX_RESPONSIBILITIES:
            break
    return tuple(cleaned)


def build_job_requirements(
    *,
    job_title: str,
    company: str | None,
    location: str | None,
    description: str | None,
) -> JobRequirements:
    """Normalise a discovered posting and extract only evidence-based fields."""
    title = " ".join((job_title or "").split())
    text = _normalize_text(description)
    has_description = len(text) >= 20

    _header, sections = _section_lines(text) if text else ([], {})
    required_text = "\n".join(sections.get("required", []))
    preferred_text = "\n".join(sections.get("preferred", []))

    required_skills = _skills(required_text)
    preferred_skills = _skills(preferred_text)
    mentioned_skills = _skills(f"{title}\n{text}")

    # A skill cannot be optional and mandatory at the same time.
    required_keys = {skill.casefold() for skill in required_skills}
    preferred_skills = [
        skill for skill in preferred_skills if skill.casefold() not in required_keys
    ]

    if required_skills or preferred_skills:
        skills_source = "explicit_sections"
    elif mentioned_skills:
        skills_source = "description_mentions"
    else:
        skills_source = "none"

    # Requirements only count when the posting states them. The whole text is
    # used as a fallback because alert emails often lose their headings.
    years_text = required_text or text
    experience_years = _detect_experience_years(years_text) if text else None
    education_level, education_field = (
        _detect_education(required_text or text) if text else (None, None)
    )

    if has_description and (required_skills or preferred_skills):
        confidence = "high"
    elif has_description and mentioned_skills:
        confidence = "medium"
    else:
        confidence = "low"

    return JobRequirements(
        job_title=title,
        company=company,
        location=location,
        description=text or None,
        required_skills=tuple(required_skills),
        preferred_skills=tuple(preferred_skills),
        mentioned_skills=tuple(mentioned_skills),
        experience_years=experience_years,
        education_level=education_level,
        education_field=education_field,
        responsibilities=_responsibilities(sections.get("responsibilities", [])),
        has_description=has_description,
        skills_source=skills_source,
        confidence=confidence,
    )


__all__ = [
    "MAX_DESCRIPTION_CHARS",
    "REQUIREMENTS_VERSION",
    "JobRequirements",
    "build_job_requirements",
]
