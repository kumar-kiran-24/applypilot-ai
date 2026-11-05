"""Tests for deterministic job requirement extraction and match scoring."""

from __future__ import annotations

from datetime import date

from app.services.job_requirements import build_job_requirements
from app.services.matching_engine import (
    EducationEntry,
    ExperienceEntry,
    ProjectEntry,
    ResumeProfile,
    estimate_experience_months,
    match_resume,
    normalize_skill_key,
    rank_results,
)

AS_OF = date(2026, 11, 4)

BACKEND_JD = """Requirements:
- 5+ years of experience building backend services
- Python, FastAPI, PostgreSQL and Docker
- Bachelor's degree in Computer Science
Preferred:
- Kubernetes
- AWS
Responsibilities:
- Build and operate APIs
- Review code
"""


def make_requirements(**overrides: object):
    values: dict[str, object] = {
        "job_title": "Senior Backend Engineer",
        "company": "Acme",
        "location": "Remote",
        "description": BACKEND_JD,
    }
    values.update(overrides)
    return build_job_requirements(**values)  # type: ignore[arg-type]


def experience(
    title: str,
    start: str | None,
    end: str | None,
    *,
    description: str = "",
    current: bool = False,
    skills: tuple[str, ...] = (),
) -> ExperienceEntry:
    return ExperienceEntry(
        title=title,
        company="Acme",
        description=description,
        technologies=skills,
        start_date=start,
        end_date=end,
        is_current=current,
        skill_keys=frozenset(normalize_skill_key(skill) for skill in skills),
    )


def profile(
    filename: str,
    skills: tuple[str, ...],
    *,
    experience_entries: tuple[ExperienceEntry, ...] = (),
    education_entries: tuple[EducationEntry, ...] = (),
    projects: tuple[ProjectEntry, ...] = (),
    summary: str | None = None,
    resume_id: str = "resume-1",
) -> ResumeProfile:
    return ResumeProfile(
        resume_id=resume_id,
        filename=filename,
        skills=skills,
        skill_keys=frozenset(normalize_skill_key(skill) for skill in skills),
        summary=summary,
        titles=tuple(
            entry.title for entry in experience_entries if entry.title
        ),
        experience=experience_entries,
        education=education_entries,
        projects=projects,
        certifications=(),
    )


def strong_profile() -> ResumeProfile:
    return profile(
        "strong.pdf",
        (
            "Python",
            "FastAPI",
            "PostgreSQL",
            "Docker",
            "Kubernetes",
            "AWS",
            "React",
        ),
        experience_entries=(
            experience(
                "Senior Backend Engineer",
                "Jan 2020",
                None,
                current=True,
                description="Built Python FastAPI services on PostgreSQL and Docker.",
                skills=("Python", "FastAPI", "PostgreSQL", "Docker"),
            ),
        ),
        education_entries=(
            EducationEntry(
                degree="Bachelor of Science",
                field_of_study="Computer Science",
                institution="State University",
                details=None,
                level=3,
            ),
        ),
        projects=(
            ProjectEntry(
                name="Payments API",
                description="FastAPI service with PostgreSQL",
                technologies=("FastAPI", "PostgreSQL", "Docker"),
                skill_keys=frozenset(
                    normalize_skill_key(skill)
                    for skill in ("FastAPI", "PostgreSQL", "Docker")
                ),
            ),
        ),
        summary="Backend engineer with Python and FastAPI experience.",
        resume_id="strong-id",
    )


# ---------------------------------------------------------------------------
# Requirement extraction
# ---------------------------------------------------------------------------
def test_extracts_sections_without_inventing_requirements() -> None:
    requirements = make_requirements()

    assert requirements.required_skills == (
        "Docker",
        "FastAPI",
        "PostgreSQL",
        "Python",
    )
    assert requirements.preferred_skills == ("AWS", "Kubernetes")
    assert requirements.experience_years == 5
    assert requirements.education_level == "bachelor"
    assert requirements.education_field == "Computer Science"
    assert requirements.has_description is True
    assert requirements.skills_source == "explicit_sections"
    assert requirements.confidence == "high"
    assert requirements.responsibilities == ("Build and operate APIs", "Review code")


def test_missing_description_yields_no_invented_requirements() -> None:
    requirements = make_requirements(job_title="React Developer", description=None)

    assert requirements.has_description is False
    assert requirements.required_skills == ()
    assert requirements.preferred_skills == ()
    assert requirements.experience_years is None
    assert requirements.education_level is None
    assert requirements.mentioned_skills == ("React",)
    assert requirements.confidence == "low"


def test_prose_only_description_keeps_skills_but_not_requirements() -> None:
    requirements = make_requirements(
        job_title="Data Engineer",
        description=(
            "Join a small data team. You will work with Spark, Airflow and Python "
            "to build reliable pipelines for our analytics platform every day."
        ),
    )

    assert requirements.required_skills == ()
    assert requirements.preferred_skills == ()
    assert set(requirements.mentioned_skills) == {"Airflow", "Python", "Spark"}
    assert requirements.skills_source == "description_mentions"
    assert requirements.confidence == "medium"
    assert requirements.experience_years is None
    assert requirements.education_level is None


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
def test_strong_resume_scores_high_with_explained_dimensions() -> None:
    requirements = make_requirements()
    result = match_resume(requirements, strong_profile(), as_of=AS_OF)

    assert result.overall_score >= 90
    assert result.skill_score == 100.0
    assert result.experience_score == 100.0
    assert result.education_score == 100.0
    assert result.title_score == 100.0
    assert result.missing_required_skills == ()
    assert result.confidence == "high"
    assert "education" in result.details["evaluated_dimensions"]
    assert result.details["estimated_experience_years"] >= 5


def test_missing_skills_are_reported_without_claiming_possession() -> None:
    requirements = make_requirements()
    weak = profile(
        "weak.pdf",
        ("Python",),
        experience_entries=(
            experience("Software Engineer", "Jan 2025", None, current=True),
        ),
    )
    result = match_resume(requirements, weak, as_of=AS_OF)

    assert result.missing_required_skills == ("Docker", "FastAPI", "PostgreSQL")
    assert "Docker" not in result.matched_required_skills
    assert any("does not mention" in item for item in result.suggestions)
    assert all("Docker" not in item for item in result.strengths)
    assert result.overall_score < 60


def test_preferred_skills_add_a_small_bonus_only() -> None:
    requirements = make_requirements()
    base = profile(
        "base.pdf",
        ("Python", "FastAPI", "PostgreSQL", "Docker"),
        experience_entries=(experience("Backend Engineer", "Jan 2020", None, current=True),),
    )
    with_preferred = profile(
        "preferred.pdf",
        ("Python", "FastAPI", "PostgreSQL", "Docker", "AWS", "Kubernetes"),
        experience_entries=(experience("Backend Engineer", "Jan 2020", None, current=True),),
    )

    base_result = match_resume(requirements, base, as_of=AS_OF)
    preferred_result = match_resume(requirements, with_preferred, as_of=AS_OF)

    assert base_result.skill_score == 85.0
    assert preferred_result.skill_score == 100.0
    assert preferred_result.overall_score > base_result.overall_score


def test_education_dimension_is_skipped_when_posting_does_not_ask() -> None:
    requirements = make_requirements(
        description="Requirements:\n- Python and FastAPI\n- 3+ years of experience\n"
    )
    result = match_resume(requirements, strong_profile(), as_of=AS_OF)

    assert result.education_score is None
    assert "education" not in result.details["evaluated_dimensions"]
    assert set(result.details["evaluated_dimensions"]) == {
        "experience",
        "projects",
        "skills",
        "title",
    }


def test_scores_stay_within_bounds_for_an_empty_resume() -> None:
    requirements = make_requirements()
    empty = profile("empty.pdf", ())
    result = match_resume(requirements, empty, as_of=AS_OF)

    for value in (
        result.overall_score,
        result.skill_score,
        result.experience_score,
        result.education_score,
        result.title_score,
        result.project_score,
    ):
        if value is not None:
            assert 0.0 <= value <= 100.0
    assert result.overall_score == 0.0
    assert result.confidence == "low"
    assert any(
        "No structured skills" in item for item in result.suggestions
    )


def test_experience_uses_dated_roles() -> None:
    requirements = make_requirements(
        description="Requirements:\n- 5+ years of backend experience\n- Python\n"
    )
    junior = profile(
        "junior.pdf",
        ("Python",),
        experience_entries=(experience("Backend Engineer", "Jan 2024", None, current=True),),
    )
    senior = profile(
        "senior.pdf",
        ("Python",),
        experience_entries=(experience("Backend Engineer", "Jan 2018", None, current=True),),
    )

    junior_result = match_resume(requirements, junior, as_of=AS_OF)
    senior_result = match_resume(requirements, senior, as_of=AS_OF)

    assert junior_result.experience_score < senior_result.experience_score
    assert senior_result.experience_score == 100.0


def test_estimate_experience_months_is_zero_without_dates() -> None:
    months, dated = estimate_experience_months(
        (experience("Engineer", None, None),), as_of=AS_OF
    )

    assert months == 0
    assert dated is False


def test_identical_inputs_produce_identical_results() -> None:
    requirements = make_requirements()
    resume = strong_profile()

    first = match_resume(requirements, resume, as_of=AS_OF)
    second = match_resume(requirements, resume, as_of=AS_OF)

    assert first == second


def test_ranking_orders_by_score_then_deterministic_tiebreak() -> None:
    requirements = make_requirements()
    strong = match_resume(requirements, strong_profile(), as_of=AS_OF)
    weak = match_resume(
        requirements,
        profile("weak.pdf", ("Python",), resume_id="weak-id"),
        as_of=AS_OF,
    )
    tied_a = match_resume(
        requirements,
        profile("a.pdf", ("Python",), resume_id="tie-b"),
        as_of=AS_OF,
    )
    tied_b = match_resume(
        requirements,
        profile("b.pdf", ("Python",), resume_id="tie-a"),
        as_of=AS_OF,
    )

    ordered = rank_results([weak, tied_b, strong, tied_a])

    assert ordered[0].resume_id == "strong-id"
    # Equal scores fall back to filename, then resume id.
    assert [item.resume_id for item in ordered[1:]] == ["tie-b", "tie-a", "weak-id"]
