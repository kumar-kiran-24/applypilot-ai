"""Phase 2C Stage C: the persistent missing-information queue."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from app.models.application import AnswerSource, ApplicationStatus, QuestionStatus
from app.models.application_answer import ApplicationAnswer, VerificationStatus
from app.models.missing_information import MissingInformationQueue
from app.models.user import User
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
QUEUE = "/api/v1/missing-information"
PROFILE = "/api/v1/profile"
APPLICATIONS = "/api/v1/applications"


async def _prepare_application(
    client: AsyncClient,
    session_factory: SessionFactory,
    *,
    email: str = "queue@example.com",
    google_sub: str = "google-sub-queue",
) -> tuple[User, dict[str, Any]]:
    user = await create_user(session_factory, email=email, google_sub=google_sub)
    job = await create_job(session_factory, user)
    await create_resume(session_factory, user)
    await prepare_profile(client, user)
    response = await client.post(
        f"{APPLICATIONS}/analyze",
        json={"job_id": str(job.id)},
        headers=bearer(user),
    )
    assert response.status_code == 200, response.text
    return user, response.json()


def _keys(items: list[dict[str, Any]]) -> set[str]:
    return {item["question_key"] for item in items}


def _item(items: list[dict[str, Any]], key: str) -> dict[str, Any]:
    return next(item for item in items if item["question_key"] == key)


async def test_queue_detects_missing_required_and_excludes_optional(
    client: AsyncClient,
    session_factory: SessionFactory,
    monkeypatch: Any,
) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "APPLICATION_AUTOMATION_ENABLED", False)
    user, preview = await _prepare_application(client, session_factory)
    headers = bearer(user)

    response = await client.get(f"{QUEUE}/{preview['application_id']}", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()

    required_total = preview["progress"]["required_total"]
    assert body["required_total"] == required_total
    assert body["required_remaining"] >= 1
    assert body["queue_status"] == "pending"
    assert body["ready_to_complete"] is False

    required = _keys(body["required_questions"])
    # Sensitive profile data is prefilled but must be confirmed; the legal
    # declaration is always a fresh per-application confirmation.
    assert "authorization.status" in required
    assert "authorization.sponsorship" in required
    assert "declarations.accuracy" in required
    assert _item(body["required_questions"], "declarations.accuracy")["status"] == (
        "legal_confirmation"
    )
    assert (
        _item(body["required_questions"], "authorization.status")["status"]
        == "sensitive_confirmation"
    )
    assert (
        _item(body["required_questions"], "authorization.sponsorship")["status"]
        == "sensitive_confirmation"
    )

    # Optional unanswered questions are listed but never block the application.
    optional = _keys(body["optional_questions"])
    assert "company.cover_letter" in optional
    assert "contact.linkedin" in optional
    assert all(item["blocks_application"] is False for item in body["optional_questions"])
    assert body["optional_unanswered"] == len(body["optional_questions"])

    listed = await client.get(QUEUE, headers=headers)
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 1
    assert listed.json()["applications"][0]["application_id"] == preview["application_id"]


async def test_confirm_sensitive_and_legal_answers_reaches_ready_state(
    client: AsyncClient,
    session_factory: SessionFactory,
    monkeypatch: Any,
) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "APPLICATION_AUTOMATION_ENABLED", False)
    user, preview = await _prepare_application(
        client, session_factory, email="ready@example.com", google_sub="google-sub-ready"
    )
    url = f"{QUEUE}/{preview['application_id']}"
    headers = bearer(user)

    for key in ("authorization.status", "authorization.sponsorship"):
        confirmed = await client.post(
            f"{url}/questions/{key}/confirm",
            json={"save_for_reuse": True},
            headers=headers,
        )
        assert confirmed.status_code == 200, confirmed.text
        assert key not in _keys(confirmed.json()["required_questions"])

    # A legal declaration is never suggested or confirmed without a value.
    legal = await client.post(
        f"{url}/questions/declarations.accuracy/confirm",
        json={},
        headers=headers,
    )
    assert legal.status_code == 400, legal.text
    assert legal.json()["code"] == "legal_confirmation_required"

    accepted = await client.post(
        f"{url}/questions/declarations.accuracy/answer",
        json={"answer": "Yes", "save_for_reuse": True},
        headers=headers,
    )
    assert accepted.status_code == 200, accepted.text
    final = accepted.json()
    assert final["required_remaining"] == 0
    assert final["ready_to_complete"] is True
    assert final["queue_status"] == "ready_to_complete"
    assert final["progress_percent"] == 100
    assert final["application_status"] == ApplicationStatus.READY_FOR_REVIEW.value
    # Optional questions still remain, but they do not block completion.
    assert final["optional_unanswered"] > 0

    # Sensitive and legal answers are confirmed for this application only.
    answers = (await client.get(f"{PROFILE}/answers", headers=headers)).json()
    keys = {item["question_key"] for item in answers}
    assert "authorization.status" not in keys
    assert "authorization.sponsorship" not in keys
    assert "declarations.accuracy" not in keys


async def test_answer_submission_and_reusable_approval(
    client: AsyncClient,
    session_factory: SessionFactory,
    monkeypatch: Any,
) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "APPLICATION_AUTOMATION_ENABLED", False)
    user, preview = await _prepare_application(
        client, session_factory, email="reuse@example.com", google_sub="google-sub-reuse"
    )
    url = f"{QUEUE}/{preview['application_id']}"
    headers = bearer(user)

    approved = await client.post(
        f"{url}/questions/company.cover_letter/answer",
        json={"answer": "I build reliable APIs.", "save_for_reuse": True},
        headers=headers,
    )
    assert approved.status_code == 200, approved.text
    preview_after = (
        await client.get(f"{APPLICATIONS}/{preview['application_id']}", headers=headers)
    ).json()
    cover = question_by_key(preview_after, "company.cover_letter")
    assert cover["answer"] == "I build reliable APIs."
    assert cover["status"] == "confirmed"

    keys = {
        item["question_key"]
        for item in (await client.get(f"{PROFILE}/answers", headers=headers)).json()
    }
    assert "company.cover_letter" in keys

    # An answer confirmed without approval is not added to the reusable bank.
    not_reused = await client.post(
        f"{url}/questions/contact.linkedin/answer",
        json={"answer": "https://linkedin.com/in/ada", "save_for_reuse": False},
        headers=headers,
    )
    assert not_reused.status_code == 200, not_reused.text
    keys = {
        item["question_key"]
        for item in (await client.get(f"{PROFILE}/answers", headers=headers)).json()
    }
    assert "contact.linkedin" not in keys

    # Invalid answers are rejected by the shared validation.
    invalid = await client.post(
        f"{url}/questions/contact.email/answer",
        json={"answer": "not-an-email"},
        headers=headers,
    )
    assert invalid.status_code == 400
    assert invalid.json()["code"] == "invalid_answer"

    # A document answer must be selected in the workspace, never typed here.
    file_answer = await client.post(
        f"{url}/questions/documents.resume/answer",
        json={"answer": "fake.pdf"},
        headers=headers,
    )
    assert file_answer.status_code == 400
    assert file_answer.json()["code"] == "file_answer_not_supported"


async def test_stale_reusable_answer_is_surfaced_but_optional_does_not_block(
    client: AsyncClient,
    session_factory: SessionFactory,
    monkeypatch: Any,
) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "APPLICATION_AUTOMATION_ENABLED", False)
    user = await create_user(
        session_factory, email="stale-queue@example.com", google_sub="google-sub-stale-q"
    )
    job = await create_job(session_factory, user)
    await create_resume(session_factory, user)
    await prepare_profile(client, user)

    old = datetime.now(timezone.utc) - timedelta(days=900)
    async with session_factory() as session:
        candidate = await _candidate_for(session, user)
        session.add(
            ApplicationAnswer(
                candidate_id=candidate.id,
                question_key="company.cover_letter",
                question_text="Cover letter or additional information",
                answer="An old cover letter.",
                verification_status=VerificationStatus.VERIFIED,
                verified_at=old,
                last_confirmed_at=old,
                confidence=1.0,
                answer_source=AnswerSource.CONFIRMED_REUSABLE_ANSWER,
            )
        )
        await session.commit()

    preview = (
        await client.post(
            f"{APPLICATIONS}/analyze",
            json={"job_id": str(job.id)},
            headers=bearer(user),
        )
    ).json()

    async with session_factory() as session:
        from app.models.application import ApplicationQuestion

        question = await session.scalar(
            select(ApplicationQuestion).where(
                ApplicationQuestion.application_id == UUID(preview["application_id"]),
                ApplicationQuestion.question_key == "company.cover_letter",
            )
        )
        assert question is not None
        question.last_confirmed_at = old
        await session.commit()

    response = await client.get(f"{QUEUE}/{preview['application_id']}", headers=bearer(user))
    assert response.status_code == 200, response.text
    body = response.json()
    assert "company.cover_letter" in _keys(body["stale_answers"])
    stale = _item(body["stale_answers"], "company.cover_letter")
    assert stale["status"] == "stale"
    assert stale["suggested_answer"] == "An old cover letter."
    assert body["stale_count"] >= 1
    # The stale question is optional, so it is not a required blocker.
    assert stale["blocks_application"] is False


async def test_conflicting_reusable_answer_can_be_resolved(
    client: AsyncClient,
    session_factory: SessionFactory,
    monkeypatch: Any,
) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "APPLICATION_AUTOMATION_ENABLED", False)
    user, preview = await _prepare_application(
        client, session_factory, email="conflict@example.com", google_sub="google-sub-conflict"
    )
    headers = bearer(user)

    stored = await client.put(
        f"{PROFILE}/answers/contact.phone",
        json={
            "question_text": "Phone number",
            "answer": "+1 555 0199",
            "verification_status": "verified",
        },
        headers=headers,
    )
    assert stored.status_code == 200, stored.text

    url = f"{QUEUE}/{preview['application_id']}"
    body = (await client.get(url, headers=headers)).json()
    assert body["conflict_count"] >= 1
    conflict = next(item for item in body["conflicts"] if item["question_key"] == "contact.phone")
    assert conflict["stored_answer"] == "+1 555 0199"
    assert conflict["conflicting_source"] == "profile"
    # The required question is already answered for this application, so the
    # conflict is surfaced but does not block it.
    assert conflict["blocks_application"] is False

    resolved = await client.post(
        f"{url}/conflicts/contact.phone/resolve",
        json={"resolution": "use_profile"},
        headers=headers,
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["conflicts"] == []


async def test_dismiss_optional_question_persists_and_is_reversible(
    client: AsyncClient,
    session_factory: SessionFactory,
    monkeypatch: Any,
) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "APPLICATION_AUTOMATION_ENABLED", False)
    user, preview = await _prepare_application(
        client, session_factory, email="dismiss@example.com", google_sub="google-sub-dismiss"
    )
    url = f"{QUEUE}/{preview['application_id']}"
    headers = bearer(user)

    dismissed = await client.post(f"{url}/questions/company.cover_letter/dismiss", headers=headers)
    assert dismissed.status_code == 200, dismissed.text
    assert "company.cover_letter" in _keys(dismissed.json()["dismissed_questions"])
    assert "company.cover_letter" not in _keys(dismissed.json()["optional_questions"])

    # Refreshing and reloading keep the dismissal: queue state is persisted.
    refreshed = await client.post(f"{url}/refresh", headers=headers)
    assert refreshed.status_code == 200, refreshed.text
    assert "company.cover_letter" in _keys(refreshed.json()["dismissed_questions"])
    reloaded = (await client.get(url, headers=headers)).json()
    assert "company.cover_letter" in _keys(reloaded["dismissed_questions"])

    required = await client.post(f"{url}/questions/declarations.accuracy/dismiss", headers=headers)
    assert required.status_code == 400
    assert required.json()["code"] == "required_question_not_dismissible"

    # Answering the dismissed question clears the dismissal.
    answered = await client.post(
        f"{url}/questions/company.cover_letter/answer",
        json={"answer": "A fresh cover letter."},
        headers=headers,
    )
    assert answered.status_code == 200, answered.text
    assert "company.cover_letter" not in _keys(answered.json()["dismissed_questions"])


async def test_queue_is_user_scoped(
    client: AsyncClient,
    session_factory: SessionFactory,
    monkeypatch: Any,
) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "APPLICATION_AUTOMATION_ENABLED", False)
    _owner, preview = await _prepare_application(
        client, session_factory, email="owner-queue@example.com", google_sub="google-owner-q"
    )
    other = await create_user(
        session_factory, email="other-queue@example.com", google_sub="google-other-q"
    )
    url = f"{QUEUE}/{preview['application_id']}"
    other_headers = bearer(other)

    assert (await client.get(url, headers=other_headers)).status_code == 404
    assert (await client.post(f"{url}/refresh", headers=other_headers)).status_code == 404
    assert (
        await client.post(
            f"{url}/questions/company.cover_letter/answer",
            json={"answer": "Stolen answer"},
            headers=other_headers,
        )
    ).status_code == 404
    assert (
        await client.post(f"{url}/questions/company.cover_letter/dismiss", headers=other_headers)
    ).status_code == 404
    assert (
        await client.post(
            f"{url}/conflicts/contact.phone/resolve",
            json={"resolution": "keep_stored"},
            headers=other_headers,
        )
    ).status_code == 404
    # The other user's own queue is empty.
    listed = await client.get(QUEUE, headers=other_headers)
    assert listed.status_code == 200
    assert listed.json()["total"] == 0

    assert (await client.get(url)).status_code == 401


async def test_state_transition_back_to_awaiting_information_and_recovery(
    client: AsyncClient,
    session_factory: SessionFactory,
    monkeypatch: Any,
) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "APPLICATION_AUTOMATION_ENABLED", False)
    user, preview = await _prepare_application(
        client, session_factory, email="transition@example.com", google_sub="google-sub-trans"
    )
    url = f"{QUEUE}/{preview['application_id']}"
    headers = bearer(user)
    application_id = preview["application_id"]

    for key in ("authorization.status", "authorization.sponsorship"):
        confirmed = await client.post(f"{url}/questions/{key}/confirm", json={}, headers=headers)
        assert confirmed.status_code == 200, confirmed.text
    ready = await client.post(
        f"{url}/questions/declarations.accuracy/answer",
        json={"answer": "Yes"},
        headers=headers,
    )
    assert ready.status_code == 200, ready.text
    assert ready.json()["application_status"] == "ready_for_review"

    # Remove a required answer directly, as if the employer's form changed.
    async with session_factory() as session:
        from app.models.application import ApplicationQuestion

        question = await session.scalar(
            select(ApplicationQuestion).where(
                ApplicationQuestion.application_id == UUID(application_id),
                ApplicationQuestion.question_key == "authorization.status",
            )
        )
        assert question is not None
        question.answer = None
        question.status = QuestionStatus.MISSING
        await session.commit()

    back = await client.get(url, headers=headers)
    assert back.status_code == 200, back.text
    assert back.json()["application_status"] == "awaiting_user_information"
    assert back.json()["required_remaining"] >= 1
    assert back.json()["queue_status"] == "pending"

    async with session_factory() as session:
        row = await session.scalar(
            select(MissingInformationQueue).where(
                MissingInformationQueue.application_id == UUID(application_id)
            )
        )
        assert row is not None
        assert row.status.value == "pending"
        assert row.required_remaining >= 1


async def _candidate_for(session: AsyncSession, user: User):
    from app.models.candidate import Candidate

    candidate = await session.scalar(select(Candidate).where(Candidate.user_id == user.id))
    assert candidate is not None
    return candidate


async def test_queue_requires_authentication(client: AsyncClient) -> None:
    assert (await client.get(QUEUE)).status_code == 401
