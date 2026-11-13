"""Phase 2I.2 tests: evidence retrieval, guarded drafting and answer management.

The LLM is faked at the client boundary. Every other step (ownership, profile,
resume, job intelligence, answer bank, conflicts, persistence) is the real
service stack against the in-memory database.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from app.integrations.llm.groq_client import LLMError
from app.models.user import User
from app.services.answer_assistant import ApplicationAnswerAssistant
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.test_applications import (
    APPLICATIONS,
    bearer,
    create_job,
    create_resume,
    create_user,
    flatten_questions,
    prepare_profile,
    question_by_key,
)

SessionFactory = async_sessionmaker[AsyncSession]
ASSISTANT = "/api/v1/assistant/answers/draft"
PROFILE = "/api/v1/profile"


class FakeLLM:
    """Minimal stand-in for ``GroqLLMClient`` with a fixed JSON answer."""

    model = "fake-answer-model"

    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.payload = payload or {}
        self.calls: list[dict[str, str]] = []

    async def complete_json(self, *, system: str, user: str) -> str:
        self.calls.append({"system": system, "user": user})
        return json.dumps(self.payload)


class FailingLLM:
    model = "fake-answer-model"

    async def complete_json(self, *, system: str, user: str) -> str:
        raise LLMError("upstream unavailable")


async def prepare_application(
    client: AsyncClient, session_factory: SessionFactory, user: User, job: Any
) -> dict[str, Any]:
    response = await client.post(
        f"{APPLICATIONS}/analyze",
        json={"job_id": str(job.id)},
        headers=bearer(user),
    )
    assert response.status_code == 200, response.text
    return response.json()


async def create_application_context(
    client: AsyncClient, session_factory: SessionFactory
) -> tuple[User, Any, dict[str, Any]]:
    user = await create_user(session_factory)
    job = await create_job(session_factory, user)
    await create_resume(session_factory, user)
    await prepare_profile(client, user)
    preview = await prepare_application(client, session_factory, user, job)
    return user, job, preview


def use_llm(monkeypatch: pytest.MonkeyPatch, llm: Any) -> None:
    monkeypatch.setattr(ApplicationAnswerAssistant, "_client", lambda self: llm)


# ---------------------------------------------------------------------------
# Evidence retrieval and literal facts
# ---------------------------------------------------------------------------
async def test_literal_profile_question_is_answered_without_the_llm(
    client: AsyncClient, session_factory: SessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    user, job, preview = await create_application_context(client, session_factory)
    email = question_by_key(preview, "contact.email")

    # No LLM is patched: the literal fact must not need one.
    response = await client.post(
        ASSISTANT,
        json={
            "application_id": preview["application_id"],
            "question_id": email["id"],
        },
        headers=bearer(user),
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["draft_answer"] == "ada@example.com"
    assert body["status"] == "drafted"
    assert body["llm_status"] == "disabled"
    assert body["confidence"] == 1.0
    assert body["question_key"] == "contact.email"
    assert any(
        item["id"] == "profile.contact.email" and item["used"]
        for item in body["evidence"]
    )
    assert body["save_reusable_allowed"] is True
    assert body["conflicts"] == []


async def test_sensitive_value_is_returned_but_requires_confirmation(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user, _job, preview = await create_application_context(client, session_factory)
    sponsorship = question_by_key(preview, "authorization.sponsorship")

    response = await client.post(
        ASSISTANT,
        json={"application_id": preview["application_id"], "question_id": sponsorship["id"]},
        headers=bearer(user),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["is_sensitive"] is True
    assert body["status"] == "needs_confirmation"
    assert body["draft_answer"] == "No"
    assert body["llm_status"] == "disabled"


# ---------------------------------------------------------------------------
# Generation, evidence citations and persistence
# ---------------------------------------------------------------------------
async def test_draft_cites_evidence_and_can_be_saved_and_reused(
    client: AsyncClient, session_factory: SessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    user, _job, preview = await create_application_context(client, session_factory)
    headers = bearer(user)
    created = await client.post(
        f"{PROFILE}/experience",
        json={
            "company": "Acme",
            "designation": "Backend Engineer",
            "description": "Built FastAPI services and improved reliability.",
            "technologies": ["Python", "FastAPI"],
            "is_current": True,
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text

    cover_letter = question_by_key(preview, "company.cover_letter")
    llm = FakeLLM(
        {
            "answer": (
                "I build FastAPI services and focus on reliability, which fits "
                "this role's backend work."
            ),
            "evidence_ids": ["profile.experience.0", "job.description"],
            "missing_information": [],
            "inferred_claims": ["Interest in the company's backend work."],
            "confidence": 0.78,
        }
    )
    use_llm(monkeypatch, llm)

    response = await client.post(
        ASSISTANT,
        json={
            "application_id": preview["application_id"],
            "question_id": cover_letter["id"],
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["status"] == "drafted"
    assert body["llm_status"] == "used"
    assert body["llm_model"] == "fake-answer-model"
    assert body["draft_answer"].startswith("I build FastAPI")
    assert 0 < body["confidence"] <= 0.85
    assert body["confidence_label"] in {"high", "medium"}
    assert body["inferred_claims"] == ["Interest in the company's backend work."]
    used = {item["id"] for item in body["evidence"] if item["used"]}
    assert {"profile.experience.0", "job.description"} <= used
    # The prompt contains only the retrieved evidence, never the whole profile.
    assert "ada@example.com" not in llm.calls[0]["user"]
    assert "profile.experience.0" in llm.calls[0]["user"]

    # Save to the existing application question (unconfirmed draft).
    saved = await client.patch(
        f"{APPLICATIONS}/{preview['application_id']}/questions/{cover_letter['id']}",
        json={"answer": body["draft_answer"], "confirmed": False},
        headers=headers,
    )
    assert saved.status_code == 200, saved.text
    refreshed = (
        await client.get(f"{APPLICATIONS}/{preview['application_id']}", headers=headers)
    ).json()
    assert question_by_key(refreshed, "company.cover_letter")["answer"] == body["draft_answer"]

    # Save the same text as an unverified reusable answer.
    reusable = await client.put(
        f"{PROFILE}/answers/{body['question_key']}",
        json={
            "question_text": cover_letter["question_text"],
            "answer": body["draft_answer"],
            "answer_source": "ai_suggestion",
            "confidence": body["confidence"],
        },
        headers=headers,
    )
    assert reusable.status_code == 200, reusable.text
    assert reusable.json()["verification_status"] == "unknown"
    listed = (await client.get(f"{PROFILE}/answers", headers=headers)).json()
    assert any(item["question_key"] == body["question_key"] for item in listed)


async def test_unsupported_numbers_discard_the_draft(
    client: AsyncClient, session_factory: SessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    user, _job, preview = await create_application_context(client, session_factory)
    cover_letter = question_by_key(preview, "company.cover_letter")
    use_llm(
        monkeypatch,
        FakeLLM(
            {
                "answer": "I improved uptime to 99.99% and cut latency by 47%.",
                "evidence_ids": ["profile.experience.0"],
                "confidence": 0.9,
            }
        ),
    )

    response = await client.post(
        ASSISTANT,
        json={"application_id": preview["application_id"], "question_id": cover_letter["id"]},
        headers=bearer(user),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["draft_answer"] is None
    assert body["status"] == "unavailable"
    assert "99.99" in body["warnings"][0] or "99.99" in " ".join(body["warnings"])
    assert body["save_reusable_allowed"] is False


# ---------------------------------------------------------------------------
# Missing, conflicting and prohibited information
# ---------------------------------------------------------------------------
async def test_insufficient_evidence_returns_a_request_for_information(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(
        session_factory, email="empty@example.com", google_sub="google-empty"
    )
    job = await create_job(session_factory, user)
    preview = await prepare_application(client, session_factory, user, job)

    response = await client.post(
        ASSISTANT,
        json={
            "application_id": preview["application_id"],
            "question_text": "Tell me about a project you are proud of.",
            "allow_llm": False,
        },
        headers=bearer(user),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "insufficient_information"
    assert body["draft_answer"] is None
    assert body["missing_information"]
    assert body["missing_information"][0]["field"] == "candidate_evidence"
    assert body["save_reusable_allowed"] is False


async def test_legal_declarations_are_never_drafted(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user, _job, preview = await create_application_context(client, session_factory)
    declaration = question_by_key(preview, "declarations.accuracy")

    response = await client.post(
        ASSISTANT,
        json={"application_id": preview["application_id"], "question_id": declaration["id"]},
        headers=bearer(user),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["is_legal"] is True
    assert body["status"] == "insufficient_information"
    assert body["draft_answer"] is None
    assert body["missing_information"][0]["field"] == "legal_declaration"
    assert body["save_reusable_allowed"] is False


async def test_conflicting_saved_answer_is_reported(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user, _job, preview = await create_application_context(client, session_factory)
    headers = bearer(user)
    stored = await client.put(
        f"{PROFILE}/answers/contact.phone",
        json={
            "question_text": "Phone number",
            "answer": "+1 555 9999",
            "verification_status": "verified",
        },
        headers=headers,
    )
    assert stored.status_code == 200, stored.text

    phone = question_by_key(preview, "contact.phone")
    response = await client.post(
        ASSISTANT,
        json={"application_id": preview["application_id"], "question_id": phone["id"]},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["draft_answer"] == "+1 555 9999"
    assert body["conflicts"]
    conflict = body["conflicts"][0]
    assert conflict["question_key"] == "contact.phone"
    assert conflict["stored_answer"] == "+1 555 9999"
    assert conflict["conflicting_source"] == "profile"


async def test_missing_sensitive_fact_is_not_invented(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user, job, _preview = await create_application_context(client, session_factory)
    # Clear the authorization facts and build an application without them.
    updated = await client.put(PROFILE, json={"work_authorization": None}, headers=bearer(user))
    assert updated.status_code == 200, updated.text
    preview = await prepare_application(client, session_factory, user, job)
    authorization = question_by_key(preview, "authorization.status")

    response = await client.post(
        ASSISTANT,
        json={
            "application_id": preview["application_id"],
            "question_id": authorization["id"],
        },
        headers=bearer(user),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["draft_answer"] is None
    assert body["status"] == "insufficient_information"
    missing = body["missing_information"][0]
    assert missing["field"] == "authorization.status"
    assert missing["sensitive"] is True


# ---------------------------------------------------------------------------
# Provider failure and isolation
# ---------------------------------------------------------------------------
async def test_llm_failure_returns_evidence_instead_of_a_draft(
    client: AsyncClient, session_factory: SessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    user, _job, preview = await create_application_context(client, session_factory)
    cover_letter = question_by_key(preview, "company.cover_letter")
    use_llm(monkeypatch, FailingLLM())

    response = await client.post(
        ASSISTANT,
        json={"application_id": preview["application_id"], "question_id": cover_letter["id"]},
        headers=bearer(user),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "unavailable"
    assert body["draft_answer"] is None
    assert body["llm_status"] == "failed"
    assert body["evidence"]
    assert any("did not return a usable draft" in item for item in body["warnings"])


async def test_applications_and_jobs_are_user_scoped(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    owner, job, preview = await create_application_context(client, session_factory)
    intruder = await create_user(
        session_factory, email="mallory@example.com", google_sub="google-mallory"
    )

    by_application = await client.post(
        ASSISTANT,
        json={"application_id": preview["application_id"], "question_text": "Tell me about yourself."},
        headers=bearer(intruder),
    )
    assert by_application.status_code == 404

    by_job = await client.post(
        ASSISTANT,
        json={"job_id": str(job.id), "question_text": "Tell me about yourself."},
        headers=bearer(intruder),
    )
    assert by_job.status_code == 404

    other = await client.post(
        ASSISTANT,
        json={
            "application_id": preview["application_id"],
            "question_id": "00000000-0000-4000-8000-000000000000",
        },
        headers=bearer(owner),
    )
    assert other.status_code == 400


async def test_custom_question_gets_a_stable_key_without_an_application(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(
        session_factory, email="custom@example.com", google_sub="google-custom"
    )
    job = await create_job(session_factory, user)
    await create_resume(session_factory, user)
    await prepare_profile(client, user)

    payload = {
        "job_id": str(job.id),
        "question_text": "How do you approach code reviews?",
        "allow_llm": False,
    }
    first = await client.post(ASSISTANT, json=payload, headers=bearer(user))
    second = await client.post(ASSISTANT, json=payload, headers=bearer(user))
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["application_id"] is None
    assert body["question_key"].startswith("assistant.")
    assert body["question_key"] == second.json()["question_key"]
    # No LLM was allowed, but deterministic evidence is still returned.
    assert body["status"] == "unavailable"
    assert body["evidence"]
