"""Phase 2C Stage A/B: answer matching, provenance, freshness and conflicts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from app.models.application import (
    AnswerSource,
    InspectionMode,
    QuestionStatus,
    QuestionType,
)
from app.models.application_answer import ApplicationAnswer, VerificationStatus
from app.models.candidate import Candidate
from app.models.education import Education
from app.models.preference import JobPreference, RelocationPreference
from app.models.resume import Resume
from app.models.user import User
from app.services.application_adapters import (
    SECTION_CONTACT,
    ApplicationInspection,
    FormFieldSpec,
)
from app.services.application_answer_matching import (
    ApplicationAnswerMatcher,
    QuestionDescriptor,
)
from app.services.application_platforms import ApplicationPlatform
from app.services.application_questionnaire import build_questionnaire
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.test_applications import bearer, create_user

SessionFactory = async_sessionmaker[AsyncSession]
PROFILE = "/api/v1/profile"


def _candidate(**overrides: Any) -> Candidate:
    values: dict[str, Any] = {
        "full_name": "Ada Lovelace",
        "email": "ada@example.com",
        "phone": "5550100",
        "phone_country_code": "+1",
        "work_authorization": "US citizen",
        "requires_sponsorship": False,
        "skills": ["Python", "FastAPI"],
    }
    values.update(overrides)
    candidate = Candidate(**values)
    candidate.education = [
        Education(institution="University of London", degree="BSc Computer Science")
    ]
    candidate.preference = JobPreference(
        relocation_preference=RelocationPreference.YES,
        preferred_work_modes=["remote"],
        expected_salary=Decimal("120000"),
        currency="USD",
    )
    return candidate


def _user() -> User:
    return User(
        google_sub="phase2c",
        email="ada@example.com",
        full_name="Ada Lovelace",
        email_verified=True,
        is_active=True,
    )


def _stored_answer(
    key: str,
    text: str,
    value: str,
    *,
    verified: bool = True,
    confirmed_at: datetime | None = None,
    is_legal: bool = False,
    is_sensitive: bool = False,
    freshness_days: int | None = None,
) -> ApplicationAnswer:
    return ApplicationAnswer(
        question_key=key,
        question_text=text,
        answer=value,
        verification_status=(
            VerificationStatus.VERIFIED if verified else VerificationStatus.UNKNOWN
        ),
        verified_at=confirmed_at if verified else None,
        last_confirmed_at=confirmed_at if verified else None,
        is_legal=is_legal,
        is_sensitive=is_sensitive,
        freshness_days=freshness_days,
        confidence=1.0 if verified else 0.5,
        answer_source=(
            AnswerSource.CONFIRMED_REUSABLE_ANSWER
            if verified
            else AnswerSource.USER_ENTERED
        ),
    )


# ---------------------------------------------------------------------------
# Normalisation and semantics
# ---------------------------------------------------------------------------
def test_normalisation_and_phone_question_synonyms() -> None:
    matcher = ApplicationAnswerMatcher()
    assert matcher.normalize_question("  What is your  Mobile-Number? ") == "mobile number"
    assert matcher.normalize_question("Contact Number") == "contact number"

    candidate = _candidate()
    for label in ("Mobile number", "Phone number", "Contact number", "Telephone"):
        matched = matcher.match_candidate_profile(label, candidate)
        assert matched is not None, label
        assert matched.value == "+1 5550100"
        assert matched.source is AnswerSource.VERIFIED_PROFILE
        assert matched.status is QuestionStatus.CONFIRMED
        assert matched.requires_confirmation is False
        assert matched.source_record_id == "candidate.phone"


def test_education_and_relocation_semantics() -> None:
    matcher = ApplicationAnswerMatcher()
    candidate = _candidate()

    for label in ("Highest qualification", "Education level", "Degree"):
        matched = matcher.match_candidate_profile(label, candidate)
        assert matched is not None, label
        assert matched.value == "BSc Computer Science"
        assert matched.source is AnswerSource.VERIFIED_PROFILE

    relocation = matcher.match_candidate_profile(
        "Are you willing to relocate?", candidate
    )
    assert relocation is not None
    assert relocation.value == "Yes"
    # Relocation is sensitive: it is prefilled but never pre-confirmed.
    assert relocation.is_sensitive is True
    assert relocation.requires_confirmation is True
    assert relocation.status is QuestionStatus.SUGGESTED


def test_superficially_similar_questions_are_not_matched() -> None:
    matcher = ApplicationAnswerMatcher()
    candidate = _candidate()
    candidate.experiences = []

    for label in (
        "What is your desired company?",
        "Do you offer relocation assistance?",
        "Are you unwilling to relocate?",
        "What is your phone type?",
        "What is your previous employer?",
    ):
        assert matcher.identify_field_category(label) is None, label
        assert matcher.match_candidate_profile(label, candidate) is None, label


# ---------------------------------------------------------------------------
# Provenance, priority and missing fields
# ---------------------------------------------------------------------------
def test_provenance_priority_and_ai_drafts_are_never_verified() -> None:
    matcher = ApplicationAnswerMatcher()
    candidate = _candidate(phone=None)
    resume = Resume(
        original_filename="ada.pdf",
        skills=["COBOL"],
        parsed_data={
            "email": "resume@example.com",
            "phone": "+1 999 0000",
            "professional_summary": "Backend engineer.",
        },
    )
    questions = [
        QuestionDescriptor(
            question_key="contact.email",
            question_text="Email address",
            field_type=QuestionType.EMAIL,
            required=True,
        ),
        QuestionDescriptor(
            question_key="contact.phone",
            question_text="Phone number",
            field_type=QuestionType.PHONE,
            required=True,
        ),
        QuestionDescriptor(
            question_key="declarations.accuracy",
            question_text="I certify this application is accurate.",
            field_type=QuestionType.DECLARATION,
            required=True,
            is_legal=True,
        ),
        QuestionDescriptor(
            question_key="custom.motivation",
            question_text="Why do you want this role?",
            field_type=QuestionType.TEXTAREA,
        ),
    ]

    answer_map = matcher.build_application_answer_map(
        questions,
        candidate=candidate,
        resume=resume,
        ai_suggestions={"custom.motivation": "A draft that needs review."},
    )

    email = answer_map.get("contact.email")
    assert email is not None
    assert email.value == "ada@example.com"
    assert email.source is AnswerSource.VERIFIED_PROFILE

    phone = answer_map.get("contact.phone")
    assert phone is not None
    assert phone.value == "+1 999 0000"
    assert phone.source is AnswerSource.RESUME_SUGGESTION
    assert phone.status is QuestionStatus.SUGGESTED
    assert phone.requires_confirmation is True
    assert phone.confidence <= 0.75

    legal = answer_map.get("declarations.accuracy")
    assert legal is not None
    assert legal.value is None
    assert legal.requires_confirmation is True
    assert legal.is_legal is True
    assert legal.status is not QuestionStatus.CONFIRMED

    draft = answer_map.get("custom.motivation")
    assert draft is not None
    assert draft.source is AnswerSource.AI_DRAFT
    assert draft.status is QuestionStatus.SUGGESTED
    assert draft.requires_confirmation is True


def test_required_fields_are_missing_while_optional_fields_may_stay_empty() -> None:
    matcher = ApplicationAnswerMatcher()
    questions = [
        QuestionDescriptor(
            question_key="authorization.status",
            question_text="Work authorization",
            required=True,
        ),
        QuestionDescriptor(
            question_key="custom.portfolio_note",
            question_text="Anything else we should know?",
            required=False,
        ),
    ]
    answer_map = matcher.build_application_answer_map(questions, candidate=None)
    missing = {item.question_key for item in answer_map.missing_required}
    assert "authorization.status" in missing
    assert "custom.portfolio_note" not in missing
    assert "custom.portfolio_note" in answer_map.optional_unanswered


# ---------------------------------------------------------------------------
# Reusable answers, freshness and conflicts
# ---------------------------------------------------------------------------
def test_only_confirmed_non_sensitive_reusable_answers_are_used() -> None:
    matcher = ApplicationAnswerMatcher()
    now = datetime.now(timezone.utc)
    verified = _stored_answer(
        "company.cover_letter",
        "Cover letter or additional information",
        "I build reliable APIs.",
        confirmed_at=now,
    )
    unverified = _stored_answer(
        "company.why_us",
        "Why do you want to work here?",
        "Unconfirmed draft",
        verified=False,
    )
    legal = _stored_answer(
        "declarations.accuracy",
        "I certify this application is accurate.",
        "Yes",
        confirmed_at=now,
        is_legal=True,
    )
    forbidden = _stored_answer(
        "account.password",
        "Account password",
        "secret-value",
        confirmed_at=now,
    )

    matched = matcher.match_reusable_answers(
        "Cover letter or additional information",
        [verified, unverified, legal, forbidden],
    )
    assert matched is not None
    assert matched.value == "I build reliable APIs."
    assert matched.source is AnswerSource.CONFIRMED_REUSABLE_ANSWER
    assert matched.status is QuestionStatus.CONFIRMED

    assert matcher.match_reusable_answers("Why do you want to work here?", [unverified]) is None
    assert (
        matcher.match_reusable_answers(
            "I certify this application is accurate.", [legal]
        )
        is None
    )
    assert matcher.match_reusable_answers("Account password", [forbidden]) is None


def test_sensitive_reusable_answer_requires_fresh_confirmation() -> None:
    matcher = ApplicationAnswerMatcher()
    now = datetime.now(timezone.utc)
    salary = _stored_answer(
        "compensation.expected_salary",
        "Expected salary",
        "120000 USD",
        confirmed_at=now,
        is_sensitive=True,
    )
    matched = matcher.match_reusable_answers("Expected salary", [salary])
    assert matched is not None
    assert matched.value == "120000 USD"
    assert matched.is_sensitive is True
    assert matched.requires_confirmation is True
    assert matched.status is QuestionStatus.SUGGESTED


def test_answer_freshness_and_override() -> None:
    matcher = ApplicationAnswerMatcher()
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=400)

    stale = _stored_answer(
        "contact.phone", "Phone number", "+1 5550100", confirmed_at=old
    )
    assessment = matcher.validate_answer_freshness(stale, now=now)
    assert assessment.fresh is False
    assert assessment.needs_reconfirmation is True
    assert assessment.age_days is not None and assessment.age_days >= 400

    overridden = _stored_answer(
        "contact.phone",
        "Phone number",
        "+1 5550100",
        confirmed_at=old,
        freshness_days=1000,
    )
    assert matcher.validate_answer_freshness(overridden, now=now).fresh is True

    never_confirmed = _stored_answer(
        "contact.email", "Email address", "ada@example.com", confirmed_at=None
    )
    unconfirmed_assessment = matcher.validate_answer_freshness(never_confirmed, now=now)
    assert unconfirmed_assessment.fresh is False
    assert unconfirmed_assessment.age_days is None

    stale_list = matcher.detect_stale_answers([stale, overridden], now=now)
    assert [item.question_key for item in stale_list] == ["contact.phone"]


def test_conflicts_detect_profile_drift_and_cross_answer_disagreement() -> None:
    matcher = ApplicationAnswerMatcher()
    now = datetime.now(timezone.utc)
    candidate = _candidate()  # profile phone is "+1 5550100"

    same_phone = _stored_answer(
        "contact.phone", "Phone number", "+1-555-0100", confirmed_at=now
    )
    assert matcher.detect_answer_conflicts(candidate, [same_phone]) == ()

    drifted = _stored_answer(
        "contact.phone", "Phone number", "+1 555 0199", confirmed_at=now
    )
    profile_conflicts = matcher.detect_answer_conflicts(candidate, [drifted])
    assert len(profile_conflicts) == 1
    assert profile_conflicts[0].conflicting_source == "profile"
    assert profile_conflicts[0].conflicting_answer == "+1 5550100"

    alternate = _stored_answer(
        "contact.phone_alt", "Mobile number", "+1 555 0188", confirmed_at=now
    )
    cross_conflicts = matcher.detect_answer_conflicts(candidate, [same_phone, alternate])
    assert any(
        item.conflicting_source == "contact.phone_alt" for item in cross_conflicts
    )


# ---------------------------------------------------------------------------
# Questionnaire integration for inspected forms
# ---------------------------------------------------------------------------
def test_inspected_custom_question_uses_semantic_profile_match() -> None:
    inspection = ApplicationInspection(
        platform=ApplicationPlatform.GREENHOUSE,
        mode=InspectionMode.DECLARED,
        fields=(
            FormFieldSpec(
                question_key="custom.whatsapp_number",
                question_text="Mobile number",
                field_type=QuestionType.PHONE,
                section=SECTION_CONTACT,
                required=True,
            ),
            FormFieldSpec(
                question_key="custom.highest_qualification",
                question_text="Highest qualification",
                field_type=QuestionType.TEXT,
                section="education",
            ),
            FormFieldSpec(
                question_key="custom.dream_company",
                question_text="What is your desired company?",
                field_type=QuestionType.TEXT,
                section="company",
            ),
        ),
        requires_account=None,
        account_reason=None,
        notes=(),
        form_found=False,
    )

    questions = build_questionnaire(
        candidate=_candidate(),
        user=_user(),
        resume=None,
        inspection=inspection,
    )
    by_key = {question.question_key: question for question in questions}
    assert by_key["custom.whatsapp_number"].answer == "+1 5550100"
    assert by_key["custom.whatsapp_number"].answer_source is AnswerSource.PROFILE
    assert by_key["custom.highest_qualification"].answer == "BSc Computer Science"
    # A superficially similar but different question is never answered.
    assert by_key["custom.dream_company"].answer is None


# ---------------------------------------------------------------------------
# Review and resolution API
# ---------------------------------------------------------------------------
async def test_answer_bank_review_is_user_scoped_and_reports_conflicts(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    owner = await create_user(session_factory, email="owner-2c@example.com")
    other = await create_user(
        session_factory,
        email="other-2c@example.com",
        google_sub="google-sub-other-2c",
    )

    stored = await client.put(
        f"{PROFILE}/answers/contact.phone",
        json={
            "question_text": "Phone number",
            "answer": "+1 555 0111",
            "verification_status": "verified",
            "is_sensitive": False,
        },
        headers=bearer(owner),
    )
    assert stored.status_code == 200, stored.text
    body = stored.json()
    assert body["answer_source"] == "previously_approved"
    assert body["confidence"] == 1.0
    assert body["last_confirmed_at"] is not None
    assert body["verified_at"] is not None

    empty_review = await client.get(f"{PROFILE}/answers/review", headers=bearer(other))
    assert empty_review.status_code == 200, empty_review.text
    assert empty_review.json()["total"] == 0

    # Changing the verified profile creates a conflict with the stored answer.
    updated = await client.put(
        PROFILE,
        json={"phone": "+1 555 0100"},
        headers=bearer(owner),
    )
    assert updated.status_code == 200, updated.text

    review = await client.get(f"{PROFILE}/answers/review", headers=bearer(owner))
    assert review.status_code == 200, review.text
    payload = review.json()
    assert payload["total"] == 1
    assert payload["verified"] == 1
    assert any(item["question_key"] == "contact.phone" for item in payload["conflicts"])
    conflict = next(
        item for item in payload["conflicts"] if item["question_key"] == "contact.phone"
    )
    assert conflict["conflicting_source"] == "profile"
    assert conflict["stored_answer"] == "+1 555 0111"
    assert "use_profile" in conflict["resolution_options"]

    resolved = await client.post(
        f"{PROFILE}/answers/contact.phone/resolve",
        json={"resolution": "use_profile"},
        headers=bearer(owner),
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["answer"] == "+1 555 0100"
    assert resolved.json()["verification_status"] == "verified"

    after = (await client.get(f"{PROFILE}/answers/review", headers=bearer(owner))).json()
    assert after["conflicts"] == []


async def test_stale_answers_can_be_reconfirmed_and_edited(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    owner = await create_user(session_factory, email="stale-2c@example.com")
    headers = bearer(owner)
    created = await client.put(
        f"{PROFILE}/answers/contact.email",
        json={
            "question_text": "Email address",
            "answer": "old@example.com",
            "verification_status": "verified",
        },
        headers=headers,
    )
    assert created.status_code == 200, created.text

    async with session_factory() as session:
        row = await session.scalar(
            select(ApplicationAnswer).where(
                ApplicationAnswer.question_key == "contact.email"
            )
        )
        assert row is not None
        row.last_confirmed_at = datetime.now(timezone.utc) - timedelta(days=900)
        await session.commit()

    review = (await client.get(f"{PROFILE}/answers/review", headers=headers)).json()
    assert [item["question_key"] for item in review["stale"]] == ["contact.email"]

    corrected = await client.post(
        f"{PROFILE}/answers/contact.email/resolve",
        json={"resolution": "edited", "answer": "new@example.com"},
        headers=headers,
    )
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["answer"] == "new@example.com"
    refreshed = (await client.get(f"{PROFILE}/answers/review", headers=headers)).json()
    assert refreshed["stale"] == []

    missing_value = await client.post(
        f"{PROFILE}/answers/contact.email/resolve",
        json={"resolution": "edited"},
        headers=headers,
    )
    assert missing_value.status_code == 422

    still_stored = await client.post(
        f"{PROFILE}/answers/contact.email/resolve",
        json={"resolution": "keep_stored"},
        headers=headers,
    )
    assert still_stored.status_code == 200
    assert still_stored.json()["answer"] == "new@example.com"


async def test_review_and_resolution_require_authentication(client: AsyncClient) -> None:
    assert (await client.get(f"{PROFILE}/answers/review")).status_code == 401
    response = await client.post(
        f"{PROFILE}/answers/contact.phone/resolve",
        json={"resolution": "keep_stored"},
    )
    assert response.status_code == 401
