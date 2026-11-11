"""Deterministic resume parsing.

PDF text is extracted with ``pypdf``; the text is then parsed with explicit
rules (never a generative model), so a resume can only ever yield information
that is literally present in the document. Scanned PDFs raise
``ResumeOcrRequiredError`` instead of silently producing an empty profile.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any

from pypdf import PdfReader

# Lower bound on extracted characters before a PDF is considered scanned.
MIN_TEXT_CHARACTERS = 20
MAX_SKILLS = 80
MAX_ENTRIES = 20
MAX_LINES_PER_ENTRY = 40


class ResumeExtractionError(Exception):
    """PDF text could not be extracted; ``code`` is safe to expose."""

    code = "pdf_unreadable"
    message = "The PDF could not be read. Upload a valid, unencrypted PDF."


class ResumeOcrRequiredError(ResumeExtractionError):
    """The PDF contains no selectable text, so OCR is required."""

    code = "ocr_required"
    message = (
        "This PDF appears to be scanned and contains no selectable text. "
        "OCR is required before its content can be parsed."
    )


@dataclass(slots=True)
class ParsedResume:
    """Structured result of a resume parse."""

    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    professional_summary: str | None = None
    skills: list[str] = field(default_factory=list)
    education: list[dict[str, Any]] = field(default_factory=list)
    experience: list[dict[str, Any]] = field(default_factory=list)
    projects: list[dict[str, Any]] = field(default_factory=list)
    certifications: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "full_name": self.full_name,
            "email": self.email,
            "phone": self.phone,
            "professional_summary": self.professional_summary,
            "education": self.education,
            "experience": self.experience,
            "projects": self.projects,
            "certifications": self.certifications,
            "links": self.links,
        }


# ---------------------------------------------------------------------------
# PDF text extraction
# ---------------------------------------------------------------------------
def extract_text_from_pdf(data: bytes) -> str:
    """Return the selectable text of a PDF or raise ``ResumeExtractionError``."""
    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted:
            # Empty-password decryption covers PDFs that are "protected" but
            # still openable; anything else needs the owner's password.
            try:
                unlocked = reader.decrypt("")
            except Exception:
                unlocked = 0
            if not unlocked:
                raise ResumeExtractionError
        pages = [page.extract_text() or "" for page in reader.pages]
    except ResumeExtractionError:
        raise
    except Exception as exc:  # malformed / truncated / unsupported PDF
        raise ResumeExtractionError from exc

    text = "\n".join(pages).strip()
    if len(re.sub(r"\s+", "", text)) < MIN_TEXT_CHARACTERS:
        raise ResumeOcrRequiredError
    return text


# ---------------------------------------------------------------------------
# Heuristic text parsing
# ---------------------------------------------------------------------------
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(
    r"(?<![\w.])(?:\+\d{1,3}[\s.\-]?)?"
    r"(?:\(\d{1,4}\)[\s.\-]?|\d{2,4}[\s.\-]){1,4}\d{2,4}(?![\w])"
)
URL_RE = re.compile(r"https?://[^\s<>\"')]+")
YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
MONTH_RE = (
    r"(?:(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+)?"
)
DATE_RANGE_RE = re.compile(
    r"(?P<start>" + MONTH_RE + r"(?:19|20)\d{2})"
    r"\s*(?:-|\u2013|\u2014|to|until)\s*"
    r"(?P<end>" + MONTH_RE + r"(?:19|20)\d{2}|present|current|now)",
    re.IGNORECASE,
)
BULLET_RE = re.compile(r"^[\s\-\u2013\u2014•*·▪◦●]+")
NUMBERED_RE = re.compile(r"^\s*\d{1,2}[.)]\s+")

SECTION_HEADINGS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "summary",
        re.compile(
            r"^(professional\s+summary|career\s+summary|executive\s+summary|summary|"
            r"profile|professional\s+profile|objective|career\s+objective|about\s+me)"
            r"\s*:?\s*$",
            re.IGNORECASE,
        ),
    ),
    (
        "skills",
        re.compile(
            r"^(technical\s+skills|core\s+competencies|core\s+skills|key\s+skills|"
            r"skills\s*(?:&|and)\s*interests|skills|technologies|tech\s+stack)"
            r"\s*:?\s*$",
            re.IGNORECASE,
        ),
    ),
    (
        "experience",
        re.compile(
            r"^(work\s+experience|professional\s+experience|employment\s+history|"
            r"employment|experience|work\s+history|career\s+history)"
            r"\s*:?\s*$",
            re.IGNORECASE,
        ),
    ),
    (
        "education",
        re.compile(
            r"^(education|academic\s+background|academic\s+qualifications|"
            r"educational\s+qualifications|qualifications)"
            r"\s*:?\s*$",
            re.IGNORECASE,
        ),
    ),
    (
        "projects",
        re.compile(
            r"^(personal\s+projects|academic\s+projects|selected\s+projects|"
            r"side\s+projects|projects)"
            r"\s*:?\s*$",
            re.IGNORECASE,
        ),
    ),
    (
        "certifications",
        re.compile(
            r"^(certifications|certificates|licenses\s*(?:&|and)\s*certifications|"
            r"licenses|courses\s*(?:&|and)\s*certifications)"
            r"\s*:?\s*$",
            re.IGNORECASE,
        ),
    ),
)

INLINE_HEADING_RE = re.compile(
    r"^(?P<heading>[A-Za-z][A-Za-z &/]{1,40}):\s*(?P<content>\S.*)$"
)

NAME_BLACKLIST = {
    "resume",
    "curriculum vitae",
    "cv",
    "profile",
    "contact",
    "personal details",
    "personal information",
}

DEGREE_RE = re.compile(
    r"\b(bachelor|master|doctorate|ph\.?\s?d|mba|b\.?\s?tech|m\.?\s?tech|"
    r"b\.?\s?sc|m\.?\s?sc|b\.?\s?e|m\.?\s?e|b\.?\s?a|m\.?\s?a|b\.?\s?com|"
    r"m\.?\s?com|bachelor of|master of|associate|diploma|postgraduate|undergraduate)\b",
    re.IGNORECASE,
)
INSTITUTION_RE = re.compile(
    r"\b(university|college|institute|school|academy|polytechnic|iit|nit)\b",
    re.IGNORECASE,
)

# Canonical technologies used to detect skills that are present in the document
# but not listed under a "Skills" heading. Matching is whole-token only.
TECH_SKILLS: tuple[str, ...] = (
    "Python", "Java", "JavaScript", "TypeScript", "C", "C++", "C#", "Go", "Rust",
    "Ruby", "PHP", "Scala", "Kotlin", "Swift", "R", "MATLAB", "Perl", "Bash",
    "SQL", "PostgreSQL", "MySQL", "SQLite", "MongoDB", "Redis", "Cassandra",
    "Oracle", "DynamoDB", "Elasticsearch", "Snowflake", "BigQuery", "Redshift",
    "FastAPI", "Django", "Flask", "Spring", "Spring Boot", "Express", "NestJS",
    "Node.js", "React", "Next.js", "Vue", "Angular", "Svelte", "Tailwind CSS",
    "HTML", "CSS", "Sass", "Redux", "GraphQL", "REST", "gRPC", "WebSockets",
    "Docker", "Kubernetes", "Terraform", "Ansible", "Helm", "Jenkins",
    "GitHub Actions", "GitLab CI", "CircleCI", "AWS", "Azure", "GCP",
    "Google Cloud", "Lambda", "EC2", "S3", "Kafka", "RabbitMQ", "Celery",
    "Airflow", "Spark", "Hadoop", "dbt", "Pandas", "NumPy", "SciPy",
    "scikit-learn", "TensorFlow", "PyTorch", "Keras", "OpenAI", "LangChain",
    "Hugging Face", "Computer Vision", "NLP", "Machine Learning", "Deep Learning",
    "Data Analysis", "Data Visualization", "Tableau", "Power BI", "Looker",
    "Excel", "Git", "Linux", "Agile", "Scrum", "Jira", "Figma", "Playwright",
    "Selenium", "Pytest", "Jest", "Cypress", "Vitest", "Microservices",
    "CI/CD", "DevOps", "ETL", "Data Modeling", "Unit Testing",
)
_TECH_SKILL_MAP: dict[str, str] = {skill.lower(): skill for skill in TECH_SKILLS}
_TECH_SKILL_RE = re.compile(
    r"(?<![\w.+#])(?:"
    + "|".join(re.escape(skill) for skill in sorted(TECH_SKILLS, key=len, reverse=True))
    + r")(?![\w+#])",
    re.IGNORECASE,
)

COMMON_SKILL_STOPWORDS = {
    "and",
    "with",
    "using",
    "the",
    "of",
    "in",
    "other",
    "etc",
    "others",
}


def _clean_line(line: str) -> str:
    cleaned = NUMBERED_RE.sub("", line)
    cleaned = BULLET_RE.sub("", cleaned)
    return re.sub(r"[ \t]+", " ", cleaned).strip()


def _is_heading(line: str) -> tuple[str, str] | None:
    """Return ``(section, inline_content)`` when ``line`` is a heading."""
    cleaned = _clean_line(line).strip().rstrip(":").strip()
    if not cleaned:
        return None
    for section, pattern in SECTION_HEADINGS:
        if pattern.match(cleaned):
            return section, ""

    match = INLINE_HEADING_RE.match(_clean_line(line))
    if match:
        heading = match.group("heading").strip()
        for section, pattern in SECTION_HEADINGS:
            if pattern.match(heading):
                return section, match.group("content").strip()
    return None


def _split_sections(lines: list[str]) -> tuple[list[str], dict[str, list[str]]]:
    """Split cleaned lines into a header block and per-section content."""
    header: list[str] = []
    sections: dict[str, list[str]] = {}
    current: str | None = None

    for line in lines:
        heading = _is_heading(line)
        if heading is not None:
            current, inline = heading
            sections.setdefault(current, [])
            if inline:
                sections[current].append(inline)
            continue
        if current is None:
            header.append(line)
        else:
            sections[current].append(line)
    return header, sections


def _blocks(lines: list[str]) -> list[list[str]]:
    """Group non-empty lines into blocks separated by blank lines."""
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.strip():
            current.append(line)
            if len(current) >= MAX_LINES_PER_ENTRY:
                blocks.append(current)
                current = []
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    return blocks


def _find_name(header_lines: list[str]) -> str | None:
    for line in header_lines[:8]:
        candidate = _clean_line(line)
        if not candidate or len(candidate) > 70:
            continue
        lowered = candidate.lower()
        if (
            lowered in NAME_BLACKLIST
            or "@" in candidate
            or URL_RE.search(candidate)
            or PHONE_RE.search(candidate)
            or re.search(r"\d", candidate)
        ):
            continue
        words = [word for word in candidate.split() if word]
        if not 2 <= len(words) <= 5:
            continue
        if not all(re.fullmatch(r"[A-Za-z][A-Za-z.'\-]*", word) for word in words):
            continue
        return candidate
    return None


def _find_phone(text: str) -> str | None:
    for match in PHONE_RE.finditer(text):
        digits = re.sub(r"\D", "", match.group(0))
        if 7 <= len(digits) <= 15:
            return match.group(0).strip()
    return None


def _split_skill_tokens(lines: list[str]) -> list[str]:
    tokens: list[str] = []
    for line in lines:
        for chunk in re.split(r"[,;|/•·]| {2,}", line):
            token = _clean_line(chunk).strip(" .:-")
            if not token or len(token) > 45:
                continue
            if token.lower() in COMMON_SKILL_STOPWORDS:
                continue
            tokens.append(token)
    return tokens


def _detect_known_skills(text: str) -> list[str]:
    found: dict[str, str] = {}
    for match in _TECH_SKILL_RE.finditer(text):
        canonical = _TECH_SKILL_MAP.get(match.group(0).lower())
        if canonical:
            found.setdefault(canonical.lower(), canonical)
    return list(found.values())


def detect_known_skills(text: str) -> list[str]:
    """Return canonical technology names mentioned in ``text``.

    Shared with job-description processing so job requirements and resume
    skills are detected from exactly the same vocabulary.
    """
    return _detect_known_skills(text)


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        key = item.casefold()
        if key and key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _is_date_line(line: str) -> bool:
    """True when a line is nothing but a date range (and separators)."""
    stripped = DATE_RANGE_RE.sub("", line).strip(" .,|·-\u2013\u2014")
    return bool(DATE_RANGE_RE.search(line)) and not stripped


def _split_before_dates(lines: list[str]) -> list[list[str]]:
    """Split a run of role lines into entries; each date follows its header."""
    date_indices = [index for index, line in enumerate(lines) if DATE_RANGE_RE.search(line)]
    if len(date_indices) <= 1:
        return [lines]
    entries: list[list[str]] = []
    start = 0
    for index in date_indices[1:]:
        header_index = index - 1
        if header_index > start:
            entries.append(lines[start:header_index])
            start = header_index
    entries.append(lines[start:])
    return [entry for entry in entries if entry]


def _split_after_dates(lines: list[str]) -> list[list[str]]:
    """Split education-style entries, where the dates close each entry."""
    date_indices = [index for index, line in enumerate(lines) if DATE_RANGE_RE.search(line)]
    if len(date_indices) <= 1:
        return [lines]
    entries: list[list[str]] = []
    start = 0
    for index in date_indices[:-1]:
        entries.append(lines[start : index + 1])
        start = index + 1
    entries.append(lines[start:])
    return [entry for entry in entries if entry]


def _date_range(text: str) -> tuple[str | None, str | None, bool]:
    match = DATE_RANGE_RE.search(text)
    if not match:
        return None, None, False
    end = match.group("end").strip()
    is_current = end.lower() in {"present", "current", "now"}
    return match.group("start").strip(), None if is_current else end, is_current


def _parse_education(lines: list[str]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for block in _blocks(lines):
        for entry_lines in _split_after_dates(block):
            institution: str | None = None
            degree: str | None = None
            field_of_study: str | None = None
            details: list[str] = []

            for raw in entry_lines:
                line = _clean_line(raw)
                if not line:
                    continue
                if institution is None and INSTITUTION_RE.search(line):
                    institution = line
                    continue
                if degree is None and DEGREE_RE.search(line):
                    degree = line
                    field_match = re.search(
                        r"\bin\s+([A-Za-z][A-Za-z &,\-]{2,60})", line, re.IGNORECASE
                    )
                    if field_match:
                        field_of_study = field_match.group(1).strip(" ,.-")
                    continue
                if _is_date_line(line):
                    continue
                details.append(line)

            if institution is None and degree is None:
                if not details:
                    continue
                institution = details.pop(0)
            start, end, _ = _date_range(" ".join(entry_lines))
            entries.append(
                {
                    "institution": institution,
                    "degree": degree,
                    "field_of_study": field_of_study,
                    "start_date": start,
                    "end_date": end,
                    "details": " ".join(details) or None,
                }
            )
            if len(entries) >= MAX_ENTRIES:
                break
    return entries


def _split_header(line: str) -> list[str]:
    parts = re.split(r"\s*(?:\||\u2014|\u2013| - | at | @ )\s*", line, flags=re.IGNORECASE)
    return [part.strip(" ,") for part in parts if part.strip(" ,")]


def _parse_experience(lines: list[str]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for block in _blocks(lines):
        for entry_lines in _split_before_dates(block):
            cleaned = [_clean_line(raw) for raw in entry_lines]
            cleaned = [line for line in cleaned if line]
            if not cleaned:
                continue

            header = cleaned[0]
            start, end, is_current = _date_range(" ".join(cleaned[:2]))
            header_without_dates = DATE_RANGE_RE.sub("", header).strip(" ,|-\u2013\u2014")
            parts = _split_header(header_without_dates)

            title = parts[0] if parts else None
            company = parts[1] if len(parts) > 1 else None
            location = parts[2] if len(parts) > 2 else None
            description = [
                line
                for line in cleaned[1:]
                if not _is_date_line(line) and not DATE_RANGE_RE.fullmatch(line.strip())
            ]

            entries.append(
                {
                    "title": title,
                    "company": company,
                    "location": location,
                    "start_date": start,
                    "end_date": end,
                    "is_current": is_current,
                    "description": "\n".join(description) or None,
                }
            )
            if len(entries) >= MAX_ENTRIES:
                break
    return entries


def _parse_projects(lines: list[str]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for block in _blocks(lines):
        cleaned = [_clean_line(raw) for raw in block]
        cleaned = [line for line in cleaned if line]
        if not cleaned:
            continue

        header = cleaned[0]
        name = re.split(r"\s*[:|\u2014\u2013]\s*|\s+-\s+", header, maxsplit=1)[0].strip()
        body = " ".join(cleaned)
        url_match = URL_RE.search(body)
        entries.append(
            {
                "name": name or header,
                "description": "\n".join(cleaned[1:]) or None,
                "technologies": _detect_known_skills(body),
                "url": url_match.group(0).rstrip(".,;") if url_match else None,
            }
        )
        if len(entries) >= MAX_ENTRIES:
            break
    return entries


def resume_sections(text: str) -> tuple[list[str], dict[str, list[str]]]:
    """Return ``(header_lines, sections)`` using the parser's heading rules.

    Shared with resume intelligence so section detection and parsing can never
    disagree about where a section starts.
    """
    raw_lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return _split_sections([_clean_line(line) for line in raw_lines])


def split_skill_tokens(lines: list[str]) -> list[str]:
    """Split skill-section lines into individual tokens (same rules as parsing)."""
    return _split_skill_tokens(lines)


def parse_resume_text(text: str) -> ParsedResume:
    """Extract structured information from resume text using explicit rules."""
    header, sections = resume_sections(text)

    email_match = EMAIL_RE.search(text)
    links = _dedupe(
        [url.rstrip(".,;)") for url in URL_RE.findall(text) if not url.startswith("mailto:")]
    )

    summary_lines = [line for line in sections.get("summary", []) if line]
    professional_summary = " ".join(summary_lines).strip()[:4000] or None

    declared_skills = _split_skill_tokens(sections.get("skills", []))
    skills = _dedupe(declared_skills + _detect_known_skills(text))[:MAX_SKILLS]

    certifications = _dedupe(
        [
            _clean_line(line).strip(" .")
            for line in sections.get("certifications", [])
            if _clean_line(line) and not _is_heading(_clean_line(line))
        ]
    )[:MAX_ENTRIES]

    return ParsedResume(
        full_name=_find_name(header),
        email=email_match.group(0) if email_match else None,
        phone=_find_phone(text),
        professional_summary=professional_summary,
        skills=skills,
        education=_parse_education(sections.get("education", [])),
        experience=_parse_experience(sections.get("experience", [])),
        projects=_parse_projects(sections.get("projects", [])),
        certifications=certifications,
        links=links[:10],
    )


def parse_resume(data: bytes) -> ParsedResume:
    """Extract text from ``data`` and return its structured representation."""
    return parse_resume_text(extract_text_from_pdf(data))


__all__ = [
    "MIN_TEXT_CHARACTERS",
    "ParsedResume",
    "ResumeExtractionError",
    "ResumeOcrRequiredError",
    "detect_known_skills",
    "extract_text_from_pdf",
    "parse_resume",
    "parse_resume_text",
    "resume_sections",
    "split_skill_tokens",
]
