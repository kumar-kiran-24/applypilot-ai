"""Phase 2F.2.1 tests: deterministic resume intelligence extraction.

The fixtures are synthetic. No real candidate data is used and no PDF is read:
the extractor is exercised against resume text and stored parsed data exactly
as the service will supply them.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from app.schemas.resume_intelligence import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    METHOD_KNOWN_SKILL_VOCABULARY,
    SOURCE_DETERMINISTIC,
    SOURCE_NOT_STATED,
    SOURCE_RESUME_TEXT,
    ResumeIntelligencePayload,
    SkillCategory,
)
from app.services.matching_engine import normalize_skill_key
from app.services.resume_intelligence_extraction import (
    EXTRACTOR_VERSION,
    extract_resume_intelligence,
)

AS_OF = date(2026, 11, 1)

ALLOWED_SOURCES = {SOURCE_RESUME_TEXT, SOURCE_DETERMINISTIC, SOURCE_NOT_STATED}
ALLOWED_CONFIDENCE = {CONFIDENCE_HIGH, CONFIDENCE_MEDIUM, CONFIDENCE_LOW}

RESUME_TEXT = """Jane Doe
jane@example.com | +1 555 0100

Professional Summary
Backend engineer with six years building APIs in Python.

Technical Skills
Languages: Python, TypeScript, JavaScript, JS
Databases: PostgreSQL, MongoDB
Cloud: AWS, Azure
Frameworks: FastAPI, React, Node
Tools: Docker, Kubernetes, Node.js
AI/ML: PyTorch
Soft Skills: Communication, Leadership

Work Experience
Senior Backend Engineer | Acme Corp | Berlin
Jan 2020 - Present
Designed a payments service handling 2M requests daily.
Mentored four engineers and reviewed pull requests.

Software Engineer | Beta Ltd | Remote
2017 - 2019
Built REST APIs with Django and PostgreSQL.

Projects
ApplyPilot: Job application agent
Python service using FastAPI and PostgreSQL that reduced screening time by 40%.
Contributed the resume matching engine.

Education
University of Berlin
MSc in Computer Science
Graduated with honors, GPA 3.9
2013 - 2015

Certifications
AWS Certified Solutions Architect - Amazon Web Services, 2022
Certified Kubernetes Administrator
Advanced Data Science - Random Text
"""

PARSED_ONLY: dict[str, Any] = {
    "professional_summary": "Data engineer focused on batch pipelines.",
    "experience": [
        {
            "title": "Data Engineer",
            "company": "Acme",
            "start_date": "Jan 2019",
            "end_date": "Dec 2021",
            "is_current": False,
            "description": "Built ETL pipelines in Python and Spark.",
        }
    ],
    "projects": [
        {
            "name": "Lakehouse",
            "description": "Spark pipeline for event data.",
            "technologies": ["Spark"],
            "url": None,
        }
    ],
    "education": [
        {
            "institution": "State University",
            "degree": "BSc",
            "field_of_study": "Computer Science",
            "start_date": "2015",
            "end_date": "2018",
            "details": "Dean's list",
        }
    ],
    "certifications": ["AWS Certified Developer - Amazon Web Services, 2021"],
}


def analyse(**overrides: Any):
    options: dict[str, Any] = {"text": RESUME_TEXT, "as_of": AS_OF}
    options.update(overrides)
    return extract_resume_intelligence(**options)


def normalised_lines(text: str) -> set[str]:
    return {
        re.sub(r"\s+", " ", line).strip()
        for line in text.splitlines()
        if line.strip()
    }


def all_facts(result: Any) -> list[Any]:
    fields = result.fields
    return [
        *fields.skills,
        *fields.experience,
        *fields.projects,
        *fields.education,
        *fields.certifications,
    ]


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------
def test_skills_are_extracted_with_categories() -> None:
    fields = analyse().fields
    by_name = {skill.name: skill for skill in fields.skills}

    assert by_name["Python"].category is SkillCategory.LANGUAGE
    assert by_name["FastAPI"].category is SkillCategory.FRAMEWORK
    assert by_name["PostgreSQL"].category is SkillCategory.DATABASE
    assert by_name["AWS"].category is SkillCategory.CLOUD
    assert by_name["Docker"].category is SkillCategory.TOOL
    assert by_name["PyTorch"].category is SkillCategory.AI_ML
    assert by_name["Communication"].category is SkillCategory.SOFT_SKILL
    assert by_name["Leadership"].category is SkillCategory.SOFT_SKILL

    # A recognisable label makes the category explicit rather than inferred.
    assert by_name["PostgreSQL"].category_inferred is False
    assert by_name["PostgreSQL"].explicit is True
    # Technologies only mentioned in prose are evidence, not declarations.
    assert by_name["Django"].explicit is False
    assert by_name["Django"].category_inferred is True
    assert by_name["Django"].method == METHOD_KNOWN_SKILL_VOCABULARY


def test_skills_were_found_in_every_expected_category() -> None:
    fields = analyse().fields
    categories = {skill.category for skill in fields.skills}

    assert {
        SkillCategory.LANGUAGE,
        SkillCategory.FRAMEWORK,
        SkillCategory.DATABASE,
        SkillCategory.CLOUD,
        SkillCategory.TOOL,
        SkillCategory.AI_ML,
        SkillCategory.SOFT_SKILL,
    } <= categories


def test_duplicate_and_alias_skills_collapse_to_one_fact() -> None:
    fields = analyse().fields
    names = [skill.name for skill in fields.skills]
    keys = [normalize_skill_key(name) for name in names]

    # JS -> JavaScript, Node/Node.js -> Node.js, Python listed once.
    assert len(keys) == len(set(keys))
    assert names.count("JavaScript") == 1
    assert names.count("Node.js") == 1
    assert names.count("Python") == 1
    assert "JS" not in names
    assert "Node" not in names


def test_soft_skills_are_only_recorded_when_explicitly_listed() -> None:
    text = """Jane Doe

Professional Summary
Known for leadership and clear communication across teams.

Work Experience
Software Engineer | Acme
2020 - 2022
Built internal tools.
"""

    fields = analyse(text=text).fields

    assert not [skill for skill in fields.skills if skill.category is SkillCategory.SOFT_SKILL]


def test_skills_section_is_not_treated_as_a_skill() -> None:
    names = {skill.name.casefold() for skill in analyse().fields.skills}

    assert "technical skills" not in names
    assert "languages" not in names
    assert "soft skills" not in names


# ---------------------------------------------------------------------------
# Experience
# ---------------------------------------------------------------------------
def test_experience_entries_capture_role_dates_and_evidence() -> None:
    fields = analyse().fields
    current, previous = fields.experience

    assert current.role == "Senior Backend Engineer"
    assert current.company == "Acme Corp"
    assert current.location == "Berlin"
    assert current.is_current is True
    assert current.start_date == "Jan 2020"
    assert current.duration_months is not None and current.duration_months > 0

    assert current.achievements == [
        "Designed a payments service handling 2M requests daily."
    ]
    assert current.responsibilities == [
        "Mentored four engineers and reviewed pull requests."
    ]
    assert current.source == SOURCE_RESUME_TEXT
    assert "Senior Backend Engineer" in (current.evidence or "")

    assert previous.company == "Beta Ltd"
    assert previous.is_current is False
    assert {"Django", "PostgreSQL"} <= set(previous.technologies)


def test_total_experience_is_the_sum_of_dated_roles() -> None:
    fields = analyse().fields

    assert fields.has_dated_experience is True
    assert fields.total_experience_months is not None
    assert fields.total_experience_months == sum(
        entry.duration_months for entry in fields.experience
    )


def test_total_experience_is_none_without_usable_dates() -> None:
    result = extract_resume_intelligence(
        text=None,
        parsed_data={
            "experience": [
                {"title": "Engineer", "company": "Acme", "description": "Built things."}
            ]
        },
        as_of=AS_OF,
    )

    assert result.fields.has_dated_experience is False
    assert result.fields.total_experience_months is None
    assert result.fields.experience[0].duration_months is None


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------
def test_projects_capture_contribution_technologies_and_outcomes() -> None:
    fields = analyse().fields
    project = fields.projects[0]

    assert project.title == "ApplyPilot"
    assert {"Python", "FastAPI", "PostgreSQL"} <= set(project.technologies)
    assert {"Python", "FastAPI", "PostgreSQL"} <= set(project.demonstrated_skills)
    assert project.contribution == "Contributed the resume matching engine."
    assert len(project.measurable_outcomes) == 1
    assert "40%" in project.measurable_outcomes[0]
    assert project.source == SOURCE_RESUME_TEXT
    assert project.evidence == "ApplyPilot: Job application agent"


def test_project_without_numbers_reports_no_measurable_outcome() -> None:
    text = """Jane Doe

Projects
Internal Tool: A small utility for the team.
Built the tool with Python.
"""

    project = analyse(text=text).fields.projects[0]

    assert project.measurable_outcomes == []
    assert project.contribution == "Built the tool with Python."


# ---------------------------------------------------------------------------
# Education and certifications
# ---------------------------------------------------------------------------
def test_education_captures_year_and_explicit_achievements() -> None:
    education = analyse().fields.education[0]

    assert education.institution == "University of Berlin"
    assert "MSc" in (education.degree or "")
    assert education.specialization == "Computer Science"
    assert education.graduation_year == 2015
    assert education.achievements == ["Graduated with honors, GPA 3.9"]
    assert education.confidence == CONFIDENCE_HIGH


def test_certifications_capture_issuer_and_date_only_when_recognisable() -> None:
    certifications = analyse().fields.certifications

    first = certifications[0]
    assert first.name == "AWS Certified Solutions Architect"
    assert first.issuing_organization == "Amazon Web Services"
    assert first.issued_on == "2022"

    second = certifications[1]
    assert second.name == "Certified Kubernetes Administrator"
    assert second.issuing_organization is None
    assert second.issued_on is None

    # An unrecognised trailing fragment is kept as part of the name, never
    # promoted to an issuer.
    third = certifications[2]
    assert third.issuing_organization is None
    assert third.name == "Advanced Data Science - Random Text"


# ---------------------------------------------------------------------------
# Provenance and confidence
# ---------------------------------------------------------------------------
def test_every_fact_carries_provenance_and_verbatim_evidence() -> None:
    result = analyse()
    lines = normalised_lines(RESUME_TEXT)

    for fact in all_facts(result):
        assert fact.source in ALLOWED_SOURCES
        assert fact.confidence in ALLOWED_CONFIDENCE
        assert fact.method
        if fact.source == SOURCE_RESUME_TEXT:
            assert fact.evidence, f"{fact!r} has no evidence snippet"
            assert fact.evidence in lines


def test_field_provenance_and_confidence_are_reported() -> None:
    result = analyse()

    assert result.provenance["skills"] == SOURCE_RESUME_TEXT
    assert result.provenance["experience"] == SOURCE_RESUME_TEXT
    assert result.provenance["education"] == SOURCE_RESUME_TEXT
    assert result.provenance["certifications"] == SOURCE_RESUME_TEXT
    assert result.field_confidence["skills"] in ALLOWED_CONFIDENCE
    assert result.confidence == CONFIDENCE_HIGH
    assert result.extractor_version == EXTRACTOR_VERSION


def test_payload_validates_and_round_trips() -> None:
    payload = analyse().to_payload()

    restored = ResumeIntelligencePayload.model_validate(
        payload.model_dump(mode="json")
    )

    assert restored.fields.skills[0].name
    assert restored.provenance["skills"] == SOURCE_RESUME_TEXT


def test_extraction_is_deterministic() -> None:
    first = analyse().to_payload().model_dump(mode="json")
    second = analyse().to_payload().model_dump(mode="json")

    assert first == second


# ---------------------------------------------------------------------------
# Missing, incomplete and unscannable resumes
# ---------------------------------------------------------------------------
def test_resume_without_sections_does_not_invent_facts() -> None:
    result = analyse(text="Jane Doe\njane@example.com\n")

    fields = result.fields
    assert fields.skills == []
    assert fields.experience == []
    assert fields.projects == []
    assert fields.education == []
    assert fields.certifications == []
    assert result.confidence == CONFIDENCE_LOW

    # Absence stays absence: each field is "not stated", never a negative claim.
    assert set(result.provenance.values()) == {SOURCE_NOT_STATED}
    assert all(value == CONFIDENCE_LOW for value in result.field_confidence.values())
    assert any("Nothing was inferred" in warning for warning in result.warnings)


def test_empty_text_produces_an_empty_but_valid_analysis() -> None:
    result = analyse(text="", parsed_data=None)

    assert result.text_available is False
    assert result.fields.skills == []
    assert result.fields.total_experience_months is None
    assert result.provenance["skills"] == SOURCE_NOT_STATED
    assert any(
        "original resume text was not available" in warning
        for warning in result.warnings
    )


def test_analysis_without_source_text_uses_parsed_data() -> None:
    result = extract_resume_intelligence(
        text=None,
        parsed_data=PARSED_ONLY,
        stored_skills=["Python", "Spark"],
        as_of=AS_OF,
    )

    assert {skill.name for skill in result.fields.skills} == {"Python", "Spark"}
    assert result.fields.experience[0].company == "Acme"
    assert result.fields.projects[0].title == "Lakehouse"
    assert result.fields.education[0].graduation_year == 2018
    assert result.fields.certifications[0].issuing_organization == "Amazon Web Services"

    for fact in all_facts(result):
        assert fact.evidence is None
        assert fact.source == SOURCE_DETERMINISTIC
    assert result.provenance["skills"] == SOURCE_DETERMINISTIC
    assert any(
        "original resume text was not available" in warning
        for warning in result.warnings
    )


def test_unreadable_parsed_data_is_ignored_without_raising() -> None:
    result = analyse(text=None, parsed_data={"experience": "not-a-list"})

    assert result.fields.skills == []
    assert result.fields.experience == []
    assert result.fields.projects == []
    assert result.fields.education == []
    assert result.confidence == CONFIDENCE_LOW
    assert any(
        "could not be read and was ignored" in warning for warning in result.warnings
    )


def test_no_warning_claims_the_candidate_lacks_a_skill() -> None:
    result = analyse()

    for warning in result.warnings:
        assert "lacks" not in warning.casefold()
        assert "missing" not in warning.casefold()


def test_summary_is_preserved_verbatim() -> None:
    fields = analyse().fields

    assert fields.summary == "Backend engineer with six years building APIs in Python."


def test_max_items_are_bounded() -> None:
    text = "Jane Doe\n\nTechnical Skills\n" + ", ".join(
        f"Tool{i}" for i in range(200)
    )

    fields = analyse(text=text).fields

    assert len(fields.skills) <= 80

