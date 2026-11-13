"""Phase 2G: controlled ATS form compatibility without a live employer.

These tests use local HTML shaped like common applicant-tracking-system forms.
No browser, network, employer account or submission is involved. They cover
the deterministic analysis and planning path that decides which fields may be
filled, which values are safe, and which questions always stay with the user.
"""

from __future__ import annotations

from app.models.candidate import Candidate
from app.models.education import Education
from app.models.experience import Experience
from app.schemas.application_fill import FillAction, FillFieldStatus
from app.schemas.application_form import FieldCategory
from app.services.application_answer_matching import (
    ApplicationAnswerMatcher,
    QuestionDescriptor,
)
from app.services.application_field_planner import FieldExecutionPlanner
from app.services.application_form_analyzer import ApplicationFormAnalyzer

STEP_ONE = """
<html><body>
<form id="step-one">
  <label for="full">Full name</label>
  <input id="full" name="full_name" type="text" required>

  <label for="email">Email address</label>
  <input id="email" name="email" type="email" required>

  <label for="phone">Mobile number</label>
  <input id="phone" name="phone" type="tel">

  <label for="address1">Address line 1</label>
  <input id="address1" name="address_line1">

  <label for="city">City</label>
  <input id="city" name="city">

  <label for="zip">Postal code</label>
  <input id="zip" name="postal_code">

  <label for="country">Country</label>
  <select id="country" name="country">
    <option value="">Choose...</option>
    <option>United States</option>
    <option>United Kingdom</option>
  </select>

  <label for="preferred">Preferred name</label>
  <input id="preferred" name="preferred_name" type="text" value="Ada">

  <label for="auth">Are you legally authorized to work?</label>
  <select id="auth" name="work_authorization">
    <option value="">Choose...</option>
    <option>Yes</option>
    <option>No</option>
  </select>

  <button type="button">Next</button>
</form>
</body></html>
"""

STEP_TWO = """
<html><body>
<form id="step-two">
  <label for="school">School name</label>
  <input id="school" name="school">

  <label for="degree">Degree</label>
  <input id="degree" name="degree">

  <label for="major">Field of study</label>
  <input id="major" name="field_of_study">

  <label for="grad">Graduation year</label>
  <input id="grad" name="graduation_year">

  <label for="employer">Current employer</label>
  <input id="employer" name="current_employer">

  <label for="title">Job title</label>
  <input id="title" name="job_title">

  <label for="experience">Describe your most relevant work experience</label>
  <textarea id="experience" name="experience_summary"></textarea>

  <label for="why">Why do you want to work here?</label>
  <textarea id="why" name="why_company"></textarea>

  <label for="clearance">Security clearance level</label>
  <input id="clearance" name="clearance" type="text">

  <label for="resume">Upload resume</label>
  <input id="resume" name="resume" type="file">

  <label for="agree">I certify that the information is true and complete</label>
  <input id="agree" name="certify" type="checkbox">
</form>
</body></html>
"""


def candidate() -> Candidate:
    return Candidate(
        full_name="Ada Lovelace",
        email="ada@example.com",
        phone="5550100",
        phone_country_code="+1",
        address_line1="1 Analytical Way",
        city="Austin",
        region="TX",
        postal_code="78701",
        country="United States",
        education=[
            Education(
                institution="University of London",
                degree="BSc",
                specialization="Computer Science",
            )
        ],
        experiences=[
            Experience(
                company="Analytical Engines",
                designation="Senior Engineer",
                is_current=True,
            )
        ],
    )


def analyzed(html: str):
    return ApplicationFormAnalyzer().analyze(html)


def plan(html: str):
    return FieldExecutionPlanner().build(
        list(analyzed(html).fields),
        candidate=candidate(),
        resume=None,
        resume_owned=True,
    )


def by_label(fields, label):
    return next(item for item in fields if item.label == label)


def test_contact_and_address_step_uses_only_verified_profile_values() -> None:
    result = analyzed(STEP_ONE)
    fields = {item.label: item for item in result.fields}

    assert fields["Full name"].classification is FieldCategory.PERSONAL_NAME
    assert fields["Email address"].classification is FieldCategory.EMAIL
    assert fields["Mobile number"].classification is FieldCategory.PHONE
    assert fields["Address line 1"].classification is FieldCategory.ADDRESS
    assert fields["City"].classification is FieldCategory.LOCATION
    assert fields["Postal code"].classification is FieldCategory.ADDRESS
    assert fields["Country"].classification is FieldCategory.ADDRESS
    assert fields["Preferred name"].classification is FieldCategory.PERSONAL_NAME
    assert (
        fields["Are you legally authorized to work?"].classification
        is FieldCategory.WORK_AUTHORIZATION
    )

    planned = {item.label: item for item in plan(STEP_ONE).fields}

    assert planned["Full name"].status is FillFieldStatus.READY
    assert planned["Full name"].proposed_value == "Ada Lovelace"
    assert planned["Email address"].proposed_value == "ada@example.com"
    assert planned["Mobile number"].proposed_value == "+1 5550100"
    assert planned["Address line 1"].proposed_value == "1 Analytical Way"
    assert planned["City"].proposed_value == "Austin"
    assert planned["Postal code"].proposed_value == "78701"
    assert planned["Country"].action is FillAction.SELECT
    assert planned["Country"].proposed_value == "United States"

    # A value that already exists belongs to the user and is never overwritten.
    assert planned["Preferred name"].status is FillFieldStatus.ALREADY_FILLED
    assert planned["Preferred name"].proposed_value is None

    # Work authorization is legally sensitive and always user-controlled.
    authorization = planned["Are you legally authorized to work?"]
    assert authorization.status is FillFieldStatus.NEEDS_HUMAN_INPUT
    assert authorization.proposed_value is None


def test_education_and_work_history_use_profile_without_guessing() -> None:
    result = analyzed(STEP_TWO)
    fields = {item.label: item for item in result.fields}

    assert fields["School name"].classification is FieldCategory.COLLEGE
    assert fields["Degree"].classification is FieldCategory.DEGREE
    assert fields["Graduation year"].classification is FieldCategory.GRADUATION_YEAR
    assert (
        fields["Current employer"].classification
        is FieldCategory.EMPLOYMENT_HISTORY
    )
    assert fields["Job title"].classification is FieldCategory.EMPLOYMENT_HISTORY
    assert fields["Why do you want to work here?"].classification is (
        FieldCategory.UNKNOWN
    )
    assert fields["Security clearance level"].classification is FieldCategory.UNKNOWN
    assert fields["Upload resume"].classification is FieldCategory.RESUME_UPLOAD
    assert (
        fields["I certify that the information is true and complete"].classification
        is FieldCategory.LEGAL_DECLARATION
    )

    planned = {item.label: item for item in plan(STEP_TWO).fields}

    assert planned["School name"].proposed_value == "University of London"
    assert planned["Degree"].proposed_value == "BSc"
    assert planned["Field of study"].proposed_value == "Computer Science"
    assert planned["Current employer"].proposed_value == "Analytical Engines"
    assert planned["Job title"].proposed_value == "Senior Engineer"
    assert planned["Describe your most relevant work experience"].status is (
        FillFieldStatus.READY
    )
    assert "Analytical Engines" in (
        planned["Describe your most relevant work experience"].proposed_value or ""
    )

    # No source exists for graduation year, so nothing is invented.
    assert planned["Graduation year"].status is FillFieldStatus.SKIPPED
    assert planned["Graduation year"].proposed_value is None

    # Custom, unknown and legal questions are queued for the user.
    for label in (
        "Why do you want to work here?",
        "Security clearance level",
        "I certify that the information is true and complete",
    ):
        assert planned[label].status is FillFieldStatus.NEEDS_HUMAN_INPUT
        assert planned[label].proposed_value is None

    upload = planned["Upload resume"]
    assert upload.status is FillFieldStatus.NEEDS_HUMAN_INPUT
    assert upload.action is FillAction.UPLOAD
    assert upload.proposed_value is None


def test_name_labels_never_resolve_to_the_applicant_name() -> None:
    """Regression: a generic "name" must not win over institution/employer.

    These labels previously resolved to ``personal.full_name`` because the
    one-word phrase "name" tied with the specific category and won on
    dictionary order. Filling an employer or school with the applicant's name
    is a data-integrity defect, so the specific labels are asserted here.
    """

    matcher = ApplicationAnswerMatcher()
    profile = candidate()
    cases = {
        "School name": ("education.institution", "University of London"),
        "University name": ("education.institution", "University of London"),
        "College name": ("education.institution", "University of London"),
        "Name of school": ("education.institution", "University of London"),
        "Company name": ("experience.current_company", "Analytical Engines"),
        "Employer name": ("experience.current_company", "Analytical Engines"),
        "Name of employer": ("experience.current_company", "Analytical Engines"),
        "Job title": ("experience.current_title", "Senior Engineer"),
    }

    for label, (category, value) in cases.items():
        descriptor = QuestionDescriptor(question_text=label)
        assert matcher.identify_field_category(descriptor) == category
        matched = matcher.match_candidate_profile(descriptor, profile)
        assert matched is not None
        assert matched.value == value
        assert matched.value != "Ada Lovelace"

    # Plain personal-name labels still use the applicant name.
    assert matcher.identify_field_category(
        QuestionDescriptor(question_text="Full name")
    ) == "personal.full_name"


STEP_TWO_WITH_USER_VALUE = STEP_TWO.replace(
    '<input id="school" name="school">',
    '<input id="school" name="school" value="User Entered University">',
)


def test_two_step_form_is_independent_and_reanalysis_keeps_user_values() -> None:
    first = analyzed(STEP_ONE)
    second = analyzed(STEP_TWO)

    first_labels = {item.label for item in first.fields}
    second_labels = {item.label for item in second.fields}
    assert "Preferred name" in first_labels
    assert "Preferred name" not in second_labels
    assert "School name" in second_labels
    assert "School name" not in first_labels
    assert not {item.field_id for item in first.fields} & {
        item.field_id for item in second.fields
    }

    # Re-reading a step after the user typed into a field reports it as already
    # filled instead of replacing the user's value with a profile value.
    replanned = {item.label: item for item in plan(STEP_TWO_WITH_USER_VALUE).fields}
    school = replanned["School name"]
    assert school.status is FillFieldStatus.ALREADY_FILLED
    assert school.proposed_value is None
    assert replanned["Degree"].status is FillFieldStatus.READY
