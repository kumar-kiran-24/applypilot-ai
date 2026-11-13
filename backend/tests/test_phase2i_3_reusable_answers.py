"""Phase 2I.3 tests: reusable-answer library, trust, reuse and consistency.

Everything runs against the real service stack and the in-memory database. No
LLM, browser or network call is involved. The tests assert the guarantees the
phase promises: ownership, honest trust levels, provenance that survives edits
and reuse, conservative matching, conflict handling and no automatic writes.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from app.models.application import AnswerSource
from app.models.application_answer import (
    AnswerType,
    ApplicationAnswer,
    VerificationStatus,
)
from app.models.user import User
from app.services.answer_library import AnswerLibrary
from httpx import AsyncClient
from sqlalchemy import select
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
ANSWERS = f"{PROFILE}/answers"


async def prepare_application(
    client: AsyncClient, user: User, job: Any
) -> dict[str, Any]:
    response = await client.post(
        f"{APPLICATIONS}/analyze",
        json={"job_id": str(job.id)},
        headers=bearer(user),
    )
    assert response.status_code == 200, response.text
    return response.json()


async def create_answer(
    client: AsyncClient,
    user: User,
    key: str,
    *,
    question_text: str,
    answer: str | None,
    verification: str = "unknown",
    source: str | None = None,
    is_sensitive: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "question_text": question_text,
        "answer": answer,
        "verification_status": verification,
        "is_sensitive": is_sensitive,
    }
    if source:
        payload["answer_source"] = source
    response = await client.put(
        f"{ANSWERS}/{key}", json=payload, headers=bearer(user)
    )
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Categories and trust (deterministic, no database)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("question_key", "question_text", "expected"),
    [
        (
            "custom.intro",
            "Tell us about yourself and your background.",
            "personal_introduction",
        ),
        (
            "custom.experience",
            "Describe your work experience with backend systems.",
            "work_experience",
        ),
        (
            "custom.project",
            "Tell us about a project you are proud of.",
            "projects",
        ),
        (
            "custom.skills",
            "What technical skills would you bring to this role?",
            "technical_skills",
        ),
        (
            "custom.achievements",
            "Describe an achievement you are proud of.",
            "achievements",
        ),
        ("custom.strengths", "What is your greatest strength?", "strengths"),
        (
            "custom.goals",
            "What are your career goals for the next five years?",
            "career_goals",
        ),
        (
            "custom.education",
            "Summarise your education and degree.",
            "education",
        ),
        ("custom.unrelated", "Why do you want this job?", "career_goals"),
        ("custom.other", "Describe a time you resolved a conflict.", "other"),
        ("experience.summary", "Anything", "work_experience"),
        ("contact.phone", "Anything", "other"),
    ],
)
def test_categories_are_derived_from_existing_questions(
    question_key: str, question_text: str, expected: str
) -> None:
    library = AnswerLibrary()

    assert library.category_for(question_key, question_text) == expected


def _answer(
    *,
    value: str | None,
    source: AnswerSource,
    verified: bool = False,
    key: str = "custom.answer",
    text: str = "A question",
) -> ApplicationAnswer:
    return ApplicationAnswer(
        question_key=key,
        question_text=text,
        answer=value,
        answer_type=AnswerType.TEXT,
        verification_status=(
            VerificationStatus.VERIFIED if verified else VerificationStatus.UNKNOWN
        ),
        answer_source=source,
    )


def test_trust_levels_never_promote_ai_drafts_to_facts() -> None:
    library = AnswerLibrary()

    draft = library.trust_for(
        _answer(value="I built things.", source=AnswerSource.AI_DRAFT)
    )
    assert draft.level == "ai_draft"
    assert draft.verified is False
    assert draft.needs_review is True

    resume = library.trust_for(
        _answer(value="Python", source=AnswerSource.RESUME_SUGGESTION)
    )
    assert resume.level == "resume_suggestion"
    assert resume.verified is False

    written = library.trust_for(
        _answer(value="Answer", source=AnswerSource.USER_ENTERED)
    )
    assert written.level == "user_written"
    assert written.needs_review is True

    reviewed = library.trust_for(
        _answer(value="Answer", source=AnswerSource.USER_ENTERED, verified=True)
    )
    assert reviewed.level == "user_reviewed"
    assert reviewed.verified is True

    fact = library.trust_for(
        _answer(value="Yes", source=AnswerSource.VERIFIED_PROFILE, verified=True)
    )
    assert fact.level == "verified_fact"

    empty = library.trust_for(
        _answer(value=None, source=AnswerSource.AI_DRAFT)
    )
    assert empty.level == "needs_information"
    assert empty.needs_review is True


# ---------------------------------------------------------------------------
# Listing, search and filters
# ---------------------------------------------------------------------------
async def test_answers_can_be_searched_and_filtered(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory)
    await create_answer(
        client,
        user,
        "custom.intro",
        question_text="Tell us about yourself.",
        answer="I am a backend engineer.",
        verification="verified",
    )
    await create_answer(
        client,
        user,
        "custom.skills",
        question_text="What technical skills do you have?",
        answer="Python, FastAPI and PostgreSQL.",
        source="ai_suggestion",
    )
    await create_answer(
        client,
        user,
        "contact.phone",
        question_text="Phone number",
        answer="+1 555 0100",
        verification="verified",
    )

    everything = await client.get(ANSWERS, headers=bearer(user))
    assert everything.status_code == 200, everything.text
    assert len(everything.json()) == 3
    trust_levels = {item["question_key"]: item["trust_level"] for item in everything.json()}
    assert trust_levels["custom.intro"] == "user_reviewed"
    assert trust_levels["custom.skills"] == "ai_draft"
    assert trust_levels["contact.phone"] == "user_reviewed"
    assert all("category_label" in item for item in everything.json())

    search = await client.get(f"{ANSWERS}?q=fastapi", headers=bearer(user))
    assert [item["question_key"] for item in search.json()] == ["custom.skills"]

    category = await client.get(
        f"{ANSWERS}?category=technical_skills", headers=bearer(user)
    )
    assert [item["question_key"] for item in category.json()] == ["custom.skills"]

    trust = await client.get(f"{ANSWERS}?trust=verified_fact", headers=bearer(user))
    assert trust.json() == []

    combined = await client.get(
        f"{ANSWERS}?q=engineer&category=personal_introduction",
        headers=bearer(user),
    )
    assert [item["question_key"] for item in combined.json()] == ["custom.intro"]

    invalid = await client.get(f"{ANSWERS}?category=made_up", headers=bearer(user))
    assert invalid.status_code == 422


async def test_answers_are_isolated_per_user(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    owner = await create_user(session_factory, email="owner@example.com")
    other = await create_user(
        session_factory,
        email="other@example.com",
        google_sub="google-sub-other-answers",
    )
    await create_answer(
        client,
        owner,
        "custom.private",
        question_text="Tell us about yourself.",
        answer="Only the owner sees this.",
        verification="verified",
    )

    listed = await client.get(ANSWERS, headers=bearer(other))
    assert listed.status_code == 200
    assert listed.json() == []

    detail = await client.get(f"{ANSWERS}/custom.private", headers=bearer(other))
    assert detail.status_code == 404

    categories = await client.get(f"{ANSWERS}/categories", headers=bearer(other))
    assert categories.status_code == 200
    assert categories.json()["total"] == 0
    assert categories.json()["categories"] == []


async def test_categories_report_counts_and_trust(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory)
    await create_answer(
        client,
        user,
        "custom.intro",
        question_text="Tell us about yourself.",
        answer="Backend engineer.",
        verification="verified",
    )
    await create_answer(
        client,
        user,
        "custom.skills",
        question_text="What technical skills do you have?",
        answer="Python.",
        source="ai_suggestion",
    )

    response = await client.get(f"{ANSWERS}/categories", headers=bearer(user))
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 2
    by_category = {item["category"]: item["count"] for item in payload["categories"]}
    assert by_category == {"personal_introduction": 1, "technical_skills": 1}
    assert payload["trust_levels"] == {"user_reviewed": 1, "ai_draft": 1}


# ---------------------------------------------------------------------------
# Detail, evidence and edit provenance
# ---------------------------------------------------------------------------
async def test_answer_detail_shows_evidence_and_conflicts(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory)
    await prepare_profile(client, user)
    await create_answer(
        client,
        user,
        "contact.phone",
        question_text="Phone number",
        answer="+1 555 0111",
        verification="verified",
    )

    response = await client.get(f"{ANSWERS}/contact.phone", headers=bearer(user))
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["answer"]["trust_level"] == "user_reviewed"
    assert payload["answer"]["trust_label"] == "User-reviewed answer"
    assert payload["conflicts"]
    conflict = payload["conflicts"][0]
    assert conflict["stored_answer"] == "+1 555 0111"
    assert conflict["conflicting_answer"] == "+1 555 0100"
    assert conflict["conflicting_source"] == "profile"
    kinds = {item["kind"] for item in payload["evidence"]}
    assert "answer" in kinds
    assert "profile" in kinds
    assert any(
        item["verified"] and item["kind"] == "profile"
        for item in payload["evidence"]
    )


async def test_editing_a_verified_answer_requires_reconfirmation(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory)
    created = await create_answer(
        client,
        user,
        "custom.intro",
        question_text="Tell us about yourself.",
        answer="I am a backend engineer.",
        verification="verified",
    )
    assert created["verified_at"] is not None
    assert created["last_confirmed_at"] is not None

    edited = await client.put(
        f"{ANSWERS}/custom.intro",
        json={"answer": "I am a platform engineer."},
        headers=bearer(user),
    )
    assert edited.status_code == 200, edited.text
    payload = edited.json()
    assert payload["answer"] == "I am a platform engineer."
    assert payload["verification_status"] == "unknown"
    assert payload["verified_at"] is None
    assert payload["last_confirmed_at"] is None
    assert payload["answer_source"] == "user_input"
    assert payload["trust_level"] == "user_written"
    assert payload["needs_review"] is True

    confirmed = await client.put(
        f"{ANSWERS}/custom.intro",
        json={"verification_status": "verified"},
        headers=bearer(user),
    )
    assert confirmed.status_code == 200, confirmed.text
    reviewed = confirmed.json()
    assert reviewed["verification_status"] == "verified"
    assert reviewed["last_confirmed_at"] is not None
    assert reviewed["answer_source"] == "previously_approved"
    assert reviewed["trust_level"] == "user_reviewed"


async def test_ai_draft_stays_unverified_when_saved(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory)
    draft = await create_answer(
        client,
        user,
        "custom.strengths",
        question_text="What is your greatest strength?",
        answer="I keep systems reliable under load.",
        source="ai_suggestion",
    )
    assert draft["verification_status"] == "unknown"
    assert draft["verified_at"] is None
    assert draft["trust_level"] == "ai_draft"
    assert draft["trust_label"] == "AI-generated draft"
    assert draft["needs_review"] is True


# ---------------------------------------------------------------------------
# Candidate knowledge consistency
# ---------------------------------------------------------------------------
async def test_knowledge_report_lists_gaps_without_changing_facts(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory)
    await prepare_profile(client, user)
    await create_answer(
        client,
        user,
        "contact.phone",
        question_text="Phone number",
        answer="+1 555 0111",
        verification="verified",
    )
    await create_answer(
        client,
        user,
        "custom.skills",
        question_text="What technical skills do you have?",
        answer="Python.",
        source="ai_suggestion",
    )

    async with session_factory() as session:
        row = await session.scalar(
            select(ApplicationAnswer).where(
                ApplicationAnswer.question_key == "contact.phone"
            )
        )
        assert row is not None
        row.last_confirmed_at = datetime.now(timezone.utc) - timedelta(days=900)
        await session.commit()

    response = await client.get(f"{ANSWERS}/knowledge", headers=bearer(user))
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["total_answers"] == 2
    assert payload["verified_answers"] == 1
    assert payload["needs_review"] >= 1
    assert payload["summary"]
    assert "never changes verified information" in payload["disclaimer"]

    missing_kinds = {item["kind"] for item in payload["missing_information"]}
    assert "profile_field" in missing_kinds
    assert any(
        item["field"] == "skills" and item["required"]
        for item in payload["missing_information"]
    )
    assert any(
        item["question_key"] == "contact.phone"
        for item in payload["conflicts"]
    )
    assert any(
        item["question_key"] == "contact.phone" for item in payload["outdated"]
    )
    assert [item["question_key"] for item in payload["unverified"]] == [
        "custom.skills"
    ]

    # The report is read-only: the stored answer and profile are untouched.
    detail = await client.get(f"{ANSWERS}/contact.phone", headers=bearer(user))
    assert detail.json()["answer"]["answer"] == "+1 555 0111"


# ---------------------------------------------------------------------------
# Reuse across applications
# ---------------------------------------------------------------------------
async def test_reuse_suggestions_match_the_question_and_flag_conflicts(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory)
    job = await create_job(session_factory, user)
    await create_resume(session_factory, user)
    await prepare_profile(client, user)
    preview = await prepare_application(client, user, job)
    phone_question = question_by_key(preview, "contact.phone")

    await create_answer(
        client,
        user,
        "contact.phone",
        question_text="Phone number",
        answer="+1 555 0111",
        verification="verified",
    )
    await create_answer(
        client,
        user,
        "company.cover_letter",
        question_text="Cover letter",
        answer="I build reliable backend services.",
        verification="verified",
    )

    response = await client.get(
        f"{APPLICATIONS}/{preview['application_id']}/questions/{phone_question['id']}/reusable-answers",
        headers=bearer(user),
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["question_key"] == "contact.phone"
    assert payload["job_title"] == "Senior Backend Engineer"
    assert "Nothing is inserted" in payload["message"]

    keys = [item["question_key"] for item in payload["suggestions"]]
    assert keys == ["contact.phone"]
    suggestion = payload["suggestions"][0]
    assert suggestion["relevance"] == 0.9
    assert "same field" in suggestion["reason"]
    assert suggestion["category"] == "other"
    assert suggestion["requires_review"] is False
    # The profile still says +1 555 0100, so this saved value conflicts.
    assert suggestion["safe_to_insert"] is False
    assert suggestion["conflicts"][0]["conflicting_source"] == "profile"

    # No write happened: the application question is exactly as it was.
    after = await client.get(
        f"{APPLICATIONS}/{preview['application_id']}", headers=bearer(user)
    )
    question = question_by_key(after.json(), "contact.phone")
    assert question["answer"] == phone_question["answer"]
    assert question["status"] == phone_question["status"]
    assert question["answer_source"] == phone_question["answer_source"]


async def test_reuse_suggestions_exclude_unrelated_and_legal_answers(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory)
    job = await create_job(session_factory, user)
    await prepare_profile(client, user)
    preview = await prepare_application(client, user, job)
    cover_question = question_by_key(preview, "company.cover_letter")
    legal_question = question_by_key(preview, "declarations.accuracy")

    await create_answer(
        client,
        user,
        "custom.skills",
        question_text="What technical skills do you have?",
        answer="Python, FastAPI and PostgreSQL.",
        verification="verified",
    )
    profile = await client.get(PROFILE, headers=bearer(user))
    candidate_id = profile.json()["id"]
    async with session_factory() as session:
        session.add(
            ApplicationAnswer(
                candidate_id=uuid.UUID(candidate_id),
                question_key="declarations.accuracy",
                question_text="I certify the information is accurate.",
                answer="Yes",
                answer_type=AnswerType.BOOLEAN,
                verification_status=VerificationStatus.UNKNOWN,
                is_legal=True,
                answer_source=AnswerSource.USER_ENTERED,
                confidence=0.5,
            )
        )
        await session.commit()

    unrelated = await client.get(
        f"{APPLICATIONS}/{preview['application_id']}/questions/{cover_question['id']}/reusable-answers",
        headers=bearer(user),
    )
    assert unrelated.status_code == 200, unrelated.text
    assert unrelated.json()["suggestions"] == []

    legal = await client.get(
        f"{APPLICATIONS}/{preview['application_id']}/questions/{legal_question['id']}/reusable-answers",
        headers=bearer(user),
    )
    assert legal.status_code == 200, legal.text
    assert legal.json()["suggestions"] == []


async def test_reuse_suggestions_are_user_scoped(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    owner = await create_user(session_factory, email="suggest-owner@example.com")
    other = await create_user(
        session_factory,
        email="suggest-other@example.com",
        google_sub="google-sub-suggest-other",
    )
    job = await create_job(session_factory, owner)
    await prepare_profile(client, owner)
    preview = await prepare_application(client, owner, job)
    question = question_by_key(preview, "contact.phone")
    await create_answer(
        client,
        other,
        "contact.phone",
        question_text="Phone number",
        answer="+1 555 0999",
        verification="verified",
    )

    response = await client.get(
        f"{APPLICATIONS}/{preview['application_id']}/questions/{question['id']}/reusable-answers",
        headers=bearer(other),
    )
    assert response.status_code == 404


async def test_reusing_an_answer_preserves_provenance(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory)
    job = await create_job(session_factory, user)
    await prepare_profile(client, user)
    preview = await prepare_application(client, user, job)
    question = question_by_key(preview, "contact.phone")
    await create_answer(
        client,
        user,
        "contact.phone",
        question_text="Phone number",
        answer="+1 555 0100",
        verification="verified",
    )

    inserted = await client.patch(
        f"{APPLICATIONS}/{preview['application_id']}/questions/{question['id']}",
        json={
            "answer": "+1 555 0100",
            "confirmed": False,
            "reused_answer_key": "contact.phone",
        },
        headers=bearer(user),
    )
    assert inserted.status_code == 200, inserted.text
    saved_question = question_by_key(inserted.json(), "contact.phone")
    assert saved_question["answer"] == "+1 555 0100"
    assert saved_question["answer_source"] == "previously_approved"
    assert saved_question["status"] == "suggested"
    assert saved_question["requires_confirmation"] is True
    assert saved_question["source_record_id"] == "application_answer:contact.phone"

    confirmed = await client.patch(
        f"{APPLICATIONS}/{preview['application_id']}/questions/{question['id']}",
        json={
            "answer": "+1 555 0100",
            "confirmed": True,
            "reused_answer_key": "contact.phone",
        },
        headers=bearer(user),
    )
    assert confirmed.status_code == 200, confirmed.text
    confirmed_question = question_by_key(confirmed.json(), "contact.phone")
    assert confirmed_question["status"] == "confirmed"
    assert confirmed_question["answer_source"] == "previously_approved"
    assert (
        confirmed_question["source_record_id"]
        == "application_answer:contact.phone"
    )


async def test_reuse_rejects_an_answer_the_user_does_not_own(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    owner = await create_user(session_factory, email="reuse-owner@example.com")
    other = await create_user(
        session_factory,
        email="reuse-other@example.com",
        google_sub="google-sub-reuse-other",
    )
    job = await create_job(session_factory, owner)
    await prepare_profile(client, owner)
    preview = await prepare_application(client, owner, job)
    question = question_by_key(preview, "contact.phone")
    await create_answer(
        client,
        other,
        "custom.other_only",
        question_text="A question only the other user answered.",
        answer="Secret value",
        verification="verified",
    )

    response = await client.patch(
        f"{APPLICATIONS}/{preview['application_id']}/questions/{question['id']}",
        json={
            "answer": "Secret value",
            "confirmed": False,
            "reused_answer_key": "custom.other_only",
        },
        headers=bearer(owner),
    )
    assert response.status_code == 404
    assert "No reusable answer is stored" in response.json()["detail"]


async def test_reuse_suggestions_do_not_match_on_a_shared_word(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    """A suggestion must come from real wording overlap, not a shared word."""
    user = await create_user(session_factory)
    job = await create_job(session_factory, user)
    await prepare_profile(client, user)
    preview = await prepare_application(client, user, job)
    question = question_by_key(preview, "company.cover_letter")

    await create_answer(
        client,
        user,
        "custom.weak_match",
        question_text="What is your current company?",
        answer="Acme",
        verification="verified",
    )

    response = await client.get(
        f"{APPLICATIONS}/{preview['application_id']}/questions/{question['id']}/reusable-answers",
        headers=bearer(user),
    )
    assert response.status_code == 200, response.text
    assert response.json()["suggestions"] == []
