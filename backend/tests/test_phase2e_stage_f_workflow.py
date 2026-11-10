"""Stage F workflow snapshot, submission confirmation and readiness tests.

These are database/API tests with no browser. They prove derivation, ownership,
explicit-confirmation rules, evidence sanitisation and production probes. They
are **not** evidence of live browser behaviour; that lives in the gated real
Chromium test.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from app.core.config import settings
from app.core.security import create_access_token
from app.models.application import Application, ApplicationStatus
from app.models.browser_session import (
    BrowserSession,
    BrowserSessionState,
    BrowserSessionStatus,
)
from app.models.job import Job
from app.models.user import User
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

SessionFactory = async_sessionmaker[AsyncSession]

APPLICATIONS = "/api/v1/applications"


@pytest.fixture(autouse=True)
def automation_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "APPLICATION_AUTOMATION_ENABLED", False)
    monkeypatch.setattr(settings, "APPLICATION_BROWSER_STREAMING_ENABLED", True)


async def create_user(
    session_factory: SessionFactory, *, email: str = "ada@example.com"
) -> User:
    async with session_factory() as session:
        user = User(
            google_sub=f"google-{uuid.uuid4().hex}",
            email=email,
            email_verified=True,
            full_name="Ada Lovelace",
            is_active=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


def bearer(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


async def create_job(session_factory: SessionFactory, user: User) -> Job:
    async with session_factory() as session:
        job = Job(
            user_id=user.id,
            dedupe_key=f"stage-f:{uuid.uuid4().int}",
            source="glassdoor",
            job_title="Senior Backend Engineer",
            company="Acme",
            location="Remote",
            job_url="https://boards.greenhouse.io/acme/jobs/1",
            job_description="Python and FastAPI.",
            email_subject="Jobs for you",
            message_id="msg-stage-f",
            received_at=datetime(2026, 11, 2, tzinfo=timezone.utc),
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job


async def create_application(
    session_factory: SessionFactory,
    user: User,
    job: Job,
    *,
    status: ApplicationStatus = ApplicationStatus.READY_FOR_REVIEW,
    automation_details: dict[str, Any] | None = None,
) -> Application:
    async with session_factory() as session:
        application = Application(
            id=uuid.uuid4(),
            user_id=user.id,
            job_id=job.id,
            platform="greenhouse",
            platform_name="Greenhouse",
            application_url="https://boards.greenhouse.io/acme/jobs/1",
            status=status,
            notes=[],
            missing_fields=[],
            automation_mode="manual",
            inspection_mode="declared",
            automation_details=automation_details or {},
        )
        session.add(application)
        await session.commit()
        await session.refresh(application)
        return application


async def test_workflow_snapshot_derives_needs_approval_and_next_actions(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory)
    job = await create_job(session_factory, user)
    application = await create_application(session_factory, user, job)

    response = await client.get(
        f"{APPLICATIONS}/{application.id}/workflow", headers=bearer(user)
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["workflow_stage"] == "needs_approval"
    assert body["status"] == "ready_for_review"
    # A completed questionnaire is not yet the interactive final review.
    assert body["submission_phase"] is None
    assert body["browser"]["live"] is False
    assert body["interventions"]["blocking_count"] == 0
    assert any(action["action"] == "approve" for action in body["next_actions"])
    assert body["submission"]["confirmed_by_user"] is False
    assert "never clicks Submit" in body["final_submission_note"]


async def test_workflow_reports_expired_browser_and_recovery(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory)
    job = await create_job(session_factory, user)
    application = await create_application(session_factory, user, job)
    async with session_factory() as session:
        session.add(
            BrowserSession(
                id=uuid.uuid4(),
                user_id=user.id,
                application_id=application.id,
                state=BrowserSessionState.PAUSED,
                browser_status=BrowserSessionStatus.EXPIRED,
                allowed_origins=["https://boards.greenhouse.io"],
                checkpoint={},
            )
        )
        await session.commit()

    body = (
        await client.get(
            f"{APPLICATIONS}/{application.id}/workflow", headers=bearer(user)
        )
    ).json()
    assert body["workflow_stage"] == "session_expired"
    assert body["browser"]["expired"] is True
    assert body["recovery"]


async def test_workflow_is_scoped_to_the_owner(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    owner = await create_user(session_factory, email="owner@example.com")
    other = await create_user(session_factory, email="other@example.com")
    job = await create_job(session_factory, owner)
    application = await create_application(session_factory, owner, job)

    response = await client.get(
        f"{APPLICATIONS}/{application.id}/workflow", headers=bearer(other)
    )
    assert response.status_code == 404


async def test_confirmation_requires_an_explicit_user_flag(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory)
    job = await create_job(session_factory, user)
    application = await create_application(
        session_factory, user, job, status=ApplicationStatus.AWAITING_APPROVAL
    )

    response = await client.post(
        f"{APPLICATIONS}/{application.id}/mark-submitted",
        json={"reference": "AP-123"},
        headers=bearer(user),
    )
    assert response.status_code == 409
    assert response.json()["code"] == "submission_not_confirmed"


async def test_confirmation_records_evidence_and_sanitises_url(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory)
    job = await create_job(session_factory, user)
    application = await create_application(
        session_factory, user, job, status=ApplicationStatus.AWAITING_APPROVAL
    )

    response = await client.post(
        f"{APPLICATIONS}/{application.id}/mark-submitted",
        json={
            "confirmed": True,
            "reference": "GH-7788",
            "confirmation_url": (
                "https://boards.greenhouse.io/acme/confirmation?token=secret#done"
            ),
            "confirmation_message": "Thank you for applying.",
        },
        headers=bearer(user),
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "submitted"
    assert response.json()["submission_reference"] == "GH-7788"

    async with session_factory() as session:
        row = await session.get(Application, application.id)
        assert row is not None
        evidence = row.automation_details["submission_confirmation"]
        assert evidence["confirmed_by_user"] is True
        assert evidence["confirmation_url"] == (
            "https://boards.greenhouse.io/acme/confirmation"
        )
        assert "token" not in evidence["confirmation_url"]
        assert row.submitted_at is not None

    workflow = (
        await client.get(
            f"{APPLICATIONS}/{application.id}/workflow", headers=bearer(user)
        )
    ).json()
    assert workflow["workflow_stage"] == "submitted"
    assert workflow["submission_phase"] == "submission_confirmed"
    assert workflow["submission"]["employer_confirmation_detected"] is True


async def test_user_only_confirmation_is_distinguished_from_employer_evidence(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory)
    job = await create_job(session_factory, user)
    application = await create_application(
        session_factory, user, job, status=ApplicationStatus.NEEDS_USER_ACTION
    )

    response = await client.post(
        f"{APPLICATIONS}/{application.id}/mark-submitted",
        json={"confirmed": True},
        headers=bearer(user),
    )
    assert response.status_code == 200, response.text
    workflow = (
        await client.get(
            f"{APPLICATIONS}/{application.id}/workflow", headers=bearer(user)
        )
    ).json()
    assert workflow["submission_phase"] == "user_confirmed_submitted"
    assert workflow["submission"]["employer_confirmation_detected"] is False


async def test_a_submitted_application_cannot_be_confirmed_twice(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory)
    job = await create_job(session_factory, user)
    application = await create_application(
        session_factory, user, job, status=ApplicationStatus.SUBMITTED
    )
    response = await client.post(
        f"{APPLICATIONS}/{application.id}/mark-submitted",
        json={"confirmed": True},
        headers=bearer(user),
    )
    assert response.status_code == 409


def test_state_machine_allows_user_confirmation_without_automated_submission() -> None:
    from app.services.application_state import can_transition

    assert can_transition(
        ApplicationStatus.READY_FOR_REVIEW, ApplicationStatus.SUBMITTED
    )
    assert can_transition(
        ApplicationStatus.AWAITING_APPROVAL, ApplicationStatus.SUBMITTED
    )
    assert not can_transition(
        ApplicationStatus.DRAFT, ApplicationStatus.SUBMITTED
    )
    assert not can_transition(
        ApplicationStatus.CANCELLED, ApplicationStatus.SUBMITTED
    )


async def test_readiness_and_liveness_probes(client: AsyncClient) -> None:
    health = await client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.json()["status"] == "healthy"

    ready = await client.get("/api/v1/ready")
    assert ready.status_code == 200, ready.text
    body = ready.json()
    assert body["status"] == "ready"
    assert body["single_process"] is True
    checks = {item["name"]: item["ok"] for item in body["checks"]}
    assert checks["database"] is True
    # Browser streaming is reported honestly, not forced to true.
    assert body["browser_streaming_available"] is False


async def test_request_correlation_id_is_propagated(client: AsyncClient) -> None:
    accepted = await client.get(
        "/api/v1/health", headers={"X-Request-ID": "stage-f-request-1"}
    )
    assert accepted.headers["X-Request-ID"] == "stage-f-request-1"

    generated = await client.get("/api/v1/health", headers={"X-Request-ID": "bad"})
    assert generated.headers["X-Request-ID"] != "bad"
    assert len(generated.headers["X-Request-ID"]) == 32
