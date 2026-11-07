"""Phase 2B: candidate profile, dynamic questionnaire and reusable answers."""

from __future__ import annotations

from typing import Any

from app.models.application import QuestionStatus, QuestionType
from app.models.candidate import Candidate
from app.models.resume import Resume
from app.models.user import User
from app.services.application_questionnaire import build_questionnaire
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.test_applications import (
    bearer,
    create_job,
    create_resume,
    create_user,
    prepare_profile,
    question_by_key,
)

SessionFactory = async_sessionmaker[AsyncSession]

PROFILE = "/api/v1/profile"
APPLICATIONS = "/api/v1/applications"


def flatten(preview: dict[str, Any]) -> list[dict[str, Any]]:
    return [question for section in preview["sections"] for question in section["questions"]]


# ---------------------------------------------------------------------------
# Profile and completeness
# ---------------------------------------------------------------------------
async def test_profile_completeness_tracks_missing_and_added_fields(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory, email="profile@example.com")
    headers = bearer(user)

    initial = await client.get(f"{PROFILE}/completeness", headers=headers)
    assert initial.status_code == 200, initial.text
    body = initial.json()
    assert body["percent"] < 100
    assert "contact.phone" in body["missing_required"]
    assert "skills" in body["missing_required"]
    assert all({"field", "label", "section", "required"} <= set(item) for item in body["fields"])

    updated = await client.put(
        PROFILE,
        json={
            "phone": "+1 555 0100",
            "phone_country_code": "+1",
            "address_line1": "1 Analytical Engine Way",
            "city": "Austin",
            "region": "TX",
            "postal_code": "78701",
            "country": "United States",
            "work_authorization": "US citizen",
            "requires_sponsorship": False,
            "skills": ["Python", "fastapi", "Python"],
            "notice_period_days": 30,
            "available_from": "2026-12-01",
        },
        headers=headers,
    )
    assert updated.status_code == 200, updated.text
    # Skills are de-duplicated case-insensitively and keep the first spelling.
    assert updated.json()["skills"] == ["Python", "fastapi"]

    for path, payload in (
        (
            f"{PROFILE}/education",
            {
                "institution": "University of London",
                "degree": "BSc Computer Science",
                "start_date": "2018-09-01",
                "end_date": "2021-06-30",
            },
        ),
        (
            f"{PROFILE}/experience",
            {
                "company": "Acme",
                "designation": "Backend Engineering Intern",
                "employment_type": "internship",
                "is_current": True,
            },
        ),
    ):
        response = await client.post(path, json=payload, headers=headers)
        assert response.status_code == 201, response.text

    preferences = await client.put(
        f"{PROFILE}/preferences",
        json={
            "preferred_work_modes": ["remote", "hybrid"],
            "relocation_preference": "negotiable",
            "expected_salary": "120000",
            "currency": "usd",
        },
        headers=headers,
    )
    assert preferences.status_code == 200, preferences.text
    assert preferences.json()["currency"] == "USD"

    final = (await client.get(f"{PROFILE}/completeness", headers=headers)).json()
    assert final["percent"] > body["percent"]
    assert "contact.phone" not in final["missing_required"]
    assert "skills" not in final["missing_required"]
    assert "authorization.sponsorship" not in final["missing_required"]
    assert final["missing_required"] == []


async def test_profile_subresources_are_scoped_to_the_owner(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    owner = await create_user(session_factory, email="owner@example.com")
    other = await create_user(
        session_factory,
        email="intruder@example.com",
        google_sub="google-sub-intruder",
    )
    owner_headers = bearer(owner)
    other_headers = bearer(other)

    education = (
        await client.post(
            f"{PROFILE}/education",
            json={"institution": "MIT", "degree": "MSc"},
            headers=owner_headers,
        )
    ).json()
    experience = (
        await client.post(
            f"{PROFILE}/experience",
            json={"company": "Acme", "designation": "Engineer"},
            headers=owner_headers,
        )
    ).json()
    project = (
        await client.post(
            f"{PROFILE}/projects",
            json={"name": "ApplyPilot", "technologies": ["Python"]},
            headers=owner_headers,
        )
    ).json()

    for method, path, payload in (
        ("PATCH", f"{PROFILE}/education/{education['id']}", {"degree": "PhD"}),
        ("DELETE", f"{PROFILE}/education/{education['id']}", None),
        ("PATCH", f"{PROFILE}/experience/{experience['id']}", {"designation": "CTO"}),
        ("DELETE", f"{PROFILE}/experience/{experience['id']}", None),
        ("PATCH", f"{PROFILE}/projects/{project['id']}", {"name": "Stolen"}),
        ("DELETE", f"{PROFILE}/projects/{project['id']}", None),
    ):
        response = await client.request(
            method, path, json=payload, headers=other_headers
        )
        assert response.status_code == 404, (method, path, response.text)

    # The owner can still update entries.
    renamed = await client.patch(
        f"{PROFILE}/projects/{project['id']}",
        json={"name": "ApplyPilot AI"},
        headers=owner_headers,
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "ApplyPilot AI"

    profile = (await client.get(PROFILE, headers=owner_headers)).json()
    assert profile["education"][0]["institution"] == "MIT"
    assert profile["experiences"][0]["company"] == "Acme"


# ---------------------------------------------------------------------------
# Reusable answers
# ---------------------------------------------------------------------------
async def test_reusable_answers_are_user_scoped_and_legal_answers_are_refused(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    owner = await create_user(session_factory, email="answers@example.com")
    other = await create_user(
        session_factory, email="other-answers@example.com", google_sub="google-sub-2"
    )

    stored = await client.put(
        f"{PROFILE}/answers/work_authorization",
        json={
            "question_text": "Are you authorized to work here?",
            "answer": "Yes, US citizen",
            "answer_type": "text",
            "verification_status": "verified",
            "is_sensitive": True,
        },
        headers=bearer(owner),
    )
    assert stored.status_code == 200, stored.text
    assert stored.json()["verified_at"] is not None
    assert stored.json()["is_sensitive"] is True

    mine = await client.get(f"{PROFILE}/answers", headers=bearer(owner))
    assert mine.status_code == 200
    assert [item["question_key"] for item in mine.json()] == ["work_authorization"]

    theirs = await client.get(f"{PROFILE}/answers", headers=bearer(other))
    assert theirs.status_code == 200
    assert theirs.json() == []

    legal = await client.put(
        f"{PROFILE}/answers/declarations.accuracy",
        json={
            "question_text": "I confirm this application is accurate.",
            "answer": "Yes",
            "is_legal": True,
        },
        headers=bearer(owner),
    )
    assert legal.status_code == 400
    assert legal.json()["code"] == "legal_answer_not_reusable"

    # Credentials, one-time codes and payment details are never stored.
    for key, text in (
        ("account.password", "Account password"),
        ("account.otp", "One-time verification code"),
        ("payment.card_number", "Credit card number"),
    ):
        forbidden = await client.put(
            f"{PROFILE}/answers/{key}",
            json={
                "question_text": text,
                "answer": "secret-value",
                "verification_status": "verified",
            },
            headers=bearer(owner),
        )
        assert forbidden.status_code == 400, (key, forbidden.text)
        assert forbidden.json()["code"] == "forbidden_answer"

    removed = await client.delete(
        f"{PROFILE}/answers/work_authorization", headers=bearer(owner)
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()["question_key"] == "work_authorization"
    assert (await client.get(f"{PROFILE}/answers", headers=bearer(owner))).json() == []


# ---------------------------------------------------------------------------
# Smart prefill and question types
# ---------------------------------------------------------------------------
def _candidate() -> Candidate:
    return Candidate(
        full_name="Ada Lovelace",
        email="ada@example.com",
        phone="+1 555 0100",
        phone_country_code="+1",
        work_authorization="US citizen",
        requires_sponsorship=False,
        skills=["Python", "FastAPI"],
    )


def _resume() -> Resume:
    return Resume(
        original_filename="ada.pdf",
        skills=["COBOL"],
        parsed_data={
            "full_name": "A. Lovelace",
            "email": "resume@example.com",
            "professional_summary": "Backend engineer.",
        },
    )


def test_prefill_priority_and_ai_drafts_are_never_verified() -> None:
    user = User(
        google_sub="sub",
        email="ada@example.com",
        full_name="Ada Lovelace",
        email_verified=True,
        is_active=True,
    )
    questions = build_questionnaire(
        candidate=_candidate(),
        user=user,
        resume=_resume(),
        approved_answers={"compensation.expected_salary": "120000 USD"},
        ai_suggestions={
            "company.cover_letter": "Draft text that must be reviewed.",
            "contact.email": "ai-must-not-override-profile@example.com",
        },
    )
    by_key = {question.question_key: question for question in questions}

    # 1. Verified profile wins over resume and AI.
    email = by_key["contact.email"]
    assert email.answer == "ada@example.com"
    assert email.answer_source.value == "profile"
    assert email.status is QuestionStatus.CONFIRMED

    # The profile's curated skills win over resume skills.
    assert by_key["skills.primary"].answer_source.value == "profile"
    assert by_key["skills.primary"].answer == "Python, FastAPI"

    # 3. A previously approved answer is reused and remains confirmed.
    salary = by_key["compensation.expected_salary"]
    assert salary.answer_source.value == "previously_approved"
    assert salary.status is QuestionStatus.CONFIRMED

    # 4. AI text is a suggestion only: never verified, never profile-sourced.
    cover = by_key["company.cover_letter"]
    assert cover.answer == "Draft text that must be reviewed."
    assert cover.answer_source.value == "ai_suggestion"
    assert cover.status is QuestionStatus.SUGGESTED
    assert cover.requires_confirmation is True

    # 5. Missing facts stay missing instead of being invented.
    assert by_key["address.city"].answer is None
    assert by_key["address.city"].status is QuestionStatus.MISSING

    # Legal declarations are never prefilled or pre-confirmed.
    declaration = by_key["declarations.accuracy"]
    assert declaration.answer is None
    assert declaration.is_legal is True
    assert declaration.requires_confirmation is True


def test_question_types_cover_select_multi_radio_checkbox_file_and_declaration() -> None:
    user = User(
        google_sub="sub",
        email="ada@example.com",
        full_name="Ada Lovelace",
        email_verified=True,
        is_active=True,
    )
    questions = build_questionnaire(candidate=_candidate(), user=user, resume=None)
    by_key = {question.question_key: question for question in questions}

    assert by_key["availability.relocation"].field_type is QuestionType.RADIO
    assert by_key["availability.relocation"].options == ("Yes", "No", "Negotiable")
    assert by_key["availability.work_mode"].field_type is QuestionType.RADIO
    assert by_key["documents.resume"].field_type is QuestionType.FILE
    assert by_key["declarations.accuracy"].field_type is QuestionType.DECLARATION
    assert by_key["company.cover_letter"].field_type is QuestionType.TEXTAREA
    assert by_key["contact.email"].field_type is QuestionType.EMAIL
    assert by_key["contact.phone"].field_type is QuestionType.PHONE
    assert by_key["availability.notice_period"].field_type is QuestionType.NUMBER
    # Every question carries its provenance metadata.
    for question in questions:
        assert question.question_key
        assert 0 <= question.confidence <= 1
        assert isinstance(question.requires_confirmation, bool)
        assert isinstance(question.is_sensitive, bool)
        assert isinstance(question.is_legal, bool)


# ---------------------------------------------------------------------------
# End-to-end questionnaire
# ---------------------------------------------------------------------------
async def test_questionnaire_validation_save_resume_and_legal_confirmation(
    client: AsyncClient, session_factory: SessionFactory, monkeypatch: Any
) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "APPLICATION_AUTOMATION_ENABLED", False)
    user = await create_user(session_factory, email="flow@example.com")
    job = await create_job(session_factory, user)
    await create_resume(session_factory, user)
    await prepare_profile(client, user)
    headers = bearer(user)

    preview = (
        await client.post(
            f"{APPLICATIONS}/analyze", json={"job_id": str(job.id)}, headers=headers
        )
    ).json()
    application_id = preview["application_id"]

    relocation = question_by_key(preview, "availability.relocation")
    invalid = await client.patch(
        f"{APPLICATIONS}/{application_id}/questions/{relocation['id']}",
        json={"answer": "Maybe", "confirmed": True},
        headers=headers,
    )
    assert invalid.status_code == 400
    assert invalid.json()["code"] == "invalid_answer"

    declaration = question_by_key(preview, "declarations.accuracy")
    rejected = await client.patch(
        f"{APPLICATIONS}/{application_id}/questions/{declaration['id']}",
        json={"answer": "No", "confirmed": True},
        headers=headers,
    )
    assert rejected.status_code == 400
    assert rejected.json()["code"] == "invalid_answer"

    accepted = await client.patch(
        f"{APPLICATIONS}/{application_id}/questions/{declaration['id']}",
        json={"answer": "Yes", "confirmed": True},
        headers=headers,
    )
    assert accepted.status_code == 200, accepted.text
    assert question_by_key(accepted.json(), "declarations.accuracy")["status"] == "confirmed"

    # The legal declaration is not stored in the reusable answer bank.
    answers = (await client.get(f"{PROFILE}/answers", headers=headers)).json()
    assert all(item["question_key"] != "declarations.accuracy" for item in answers)

    # Save a draft answer, then reload: progress and provenance survive.
    cover = question_by_key(preview, "company.cover_letter")
    saved = await client.patch(
        f"{APPLICATIONS}/{application_id}/questions/{cover['id']}",
        json={"answer": "I build reliable APIs.", "confirmed": False},
        headers=headers,
    )
    assert saved.status_code == 200, saved.text
    cover_saved = question_by_key(saved.json(), "company.cover_letter")
    assert cover_saved["answer"] == "I build reliable APIs."
    assert cover_saved["status"] == "suggested"

    reloaded = (await client.get(f"{APPLICATIONS}/{application_id}", headers=headers)).json()
    cover_reloaded = question_by_key(reloaded, "company.cover_letter")
    assert cover_reloaded["answer"] == "I build reliable APIs."
    assert cover_reloaded["status"] == "suggested"
    assert cover_reloaded["answer_source"] == "user_input"

    confirmed = await client.patch(
        f"{APPLICATIONS}/{application_id}/questions/{cover['id']}",
        json={"answer": "I build reliable APIs.", "confirmed": True},
        headers=headers,
    )
    assert confirmed.status_code == 200
    confirmed_cover = question_by_key(confirmed.json(), "company.cover_letter")
    assert confirmed_cover["status"] == "confirmed"
    assert confirmed_cover["answer_source"] == "user_input"
    assert confirmed_cover["confidence"] == 1.0

    # Confirming a sensitive answer writes the unambiguous value to the profile
    # and also stores it for reuse.
    authorization = question_by_key(confirmed.json(), "authorization.status")
    if authorization["status"] != "confirmed":
        stored = await client.patch(
            f"{APPLICATIONS}/{application_id}/questions/{authorization['id']}",
            json={"answer": "US citizen", "confirmed": True},
            headers=headers,
        )
        assert stored.status_code == 200, stored.text
        final = stored.json()
    else:
        final = confirmed.json()
    assert final["progress"]["percent"] > 0

    answers = (await client.get(f"{PROFILE}/answers", headers=headers)).json()
    keys = {item["question_key"] for item in answers}
    assert "company.cover_letter" in keys
    assert "authorization.status" in keys
    authorization_answer = next(
        item for item in answers if item["question_key"] == "authorization.status"
    )
    assert authorization_answer["is_sensitive"] is True
    assert authorization_answer["verification_status"] == "verified"

    # Re-analysis keeps the confirmed answers and their provenance.
    again = await client.post(
        f"{APPLICATIONS}/analyze", json={"job_id": str(job.id)}, headers=headers
    )
    assert again.status_code == 200, again.text
    assert question_by_key(again.json(), "company.cover_letter")["answer"] == (
        "I build reliable APIs."
    )
    assert question_by_key(again.json(), "declarations.accuracy")["status"] == "confirmed"


async def test_profile_questionnaire_requires_authentication(client: AsyncClient) -> None:
    assert (await client.get(f"{PROFILE}/completeness")).status_code == 401
    assert (await client.get(f"{PROFILE}/answers")).status_code == 401
    assert (await client.post(f"{PROFILE}/education", json={})).status_code == 401
