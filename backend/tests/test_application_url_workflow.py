"""Employer application URL workflow tests.

The discovered job URL is a listing; the employer's application form is a
different URL supplied by the user. These tests cover accepted URLs, rejected
job-board links, invalid/unsafe URLs, preserved listing links and ownership.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from app.core.config import settings
from app.core.security import create_access_token
from app.models.job import Job
from app.models.user import User
from app.services.application_platforms import (
    ApplicationUrlError,
    is_job_board_url,
    normalize_application_url,
    suggest_employer_application_url,
)
from app.services.application_url_security import UnsafeNavigationError
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

SessionFactory = async_sessionmaker[AsyncSession]

APPLICATIONS = "/api/v1/applications"
GLASSDOOR = "https://www.glassdoor.com/job-listing/backend-JV_1.htm"
EMPLOYER_FORM = "https://boards.greenhouse.io/acme/jobs/123"


@pytest.fixture(autouse=True)
def automation_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "APPLICATION_AUTOMATION_ENABLED", False)


@pytest.fixture(autouse=True)
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_validate(
        url: str, *, resolver: Any = None
    ) -> tuple[str, tuple[str, ...]]:
        del resolver
        return normalize_application_url(url), ("93.184.216.34",)

    monkeypatch.setattr(
        "app.services.application_service.validate_application_destination",
        fake_validate,
    )


async def create_user(
    session_factory: SessionFactory,
    *,
    email: str = "ada@example.com",
    google_sub: str = "google-sub-url",
) -> User:
    async with session_factory() as session:
        user = User(
            google_sub=google_sub,
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


async def create_job(
    session_factory: SessionFactory,
    user: User,
    *,
    description: str = "Requirements:\n- Python and FastAPI\n",
) -> Job:
    async with session_factory() as session:
        job = Job(
            user_id=user.id,
            dedupe_key=f"glassdoor-id:{uuid.uuid4().int}",
            source="glassdoor",
            job_title="Senior Backend Engineer",
            company="Acme",
            location="Remote",
            job_url=GLASSDOOR,
            job_description=description,
            email_subject="Jobs for you",
            message_id="msg-url-1",
            received_at=datetime(2026, 11, 2, tzinfo=timezone.utc),
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job


async def prepare_application(
    client: AsyncClient, session_factory: SessionFactory, description: str | None = None
) -> tuple[User, Job, dict[str, Any]]:
    user = await create_user(session_factory)
    job = await create_job(
        session_factory,
        user,
        description=description or "Requirements:\n- Python and FastAPI\n",
    )
    response = await client.post(
        f"{APPLICATIONS}/analyze",
        json={"job_id": str(job.id)},
        headers=bearer(user),
    )
    assert response.status_code == 200, response.text
    return user, job, response.json()


async def test_employer_url_update_preserves_listing_and_careers(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user, _, preview = await prepare_application(client, session_factory)
    application_id = preview["application_id"]

    # The discovered listing is reported as a listing, not a form.
    assert preview["application_url"] == GLASSDOOR
    assert preview["job_listing_url"] == GLASSDOOR
    assert preview["application_url_is_job_board"] is True

    response = await client.post(
        f"{APPLICATIONS}/{application_id}/inspect",
        json={
            "application_url": EMPLOYER_FORM,
            "employer_careers_url": "https://acme.example/careers",
        },
        headers=bearer(user),
    )
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["application_url"] == EMPLOYER_FORM
    assert updated["job_listing_url"] == GLASSDOOR
    assert updated["employer_careers_url"] == "https://acme.example/careers"
    assert updated["application_url_is_job_board"] is False
    assert updated["platform"] == "greenhouse"

    # The fetched application reflects the update as well.
    fetched = await client.get(
        f"{APPLICATIONS}/{application_id}", headers=bearer(user)
    )
    assert fetched.status_code == 200
    assert fetched.json()["application_url"] == EMPLOYER_FORM
    assert fetched.json()["job_listing_url"] == GLASSDOOR


async def test_job_board_application_url_is_rejected_with_specific_error(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user, _, preview = await prepare_application(client, session_factory)
    application_id = preview["application_id"]

    response = await client.post(
        f"{APPLICATIONS}/{application_id}/inspect",
        json={"application_url": "https://www.indeed.com/viewjob?jk=abc123"},
        headers=bearer(user),
    )
    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "job_board_url_not_supported"
    assert "job board" in body["detail"].lower()
    assert "employer" in body["detail"].lower()

    fetched = await client.get(
        f"{APPLICATIONS}/{application_id}", headers=bearer(user)
    )
    assert fetched.json()["application_url"] == GLASSDOOR


async def test_job_board_careers_url_is_rejected(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user, _, preview = await prepare_application(client, session_factory)
    response = await client.post(
        f"{APPLICATIONS}/{preview['application_id']}/inspect",
        json={
            "application_url": EMPLOYER_FORM,
            "employer_careers_url": "https://www.linkedin.com/company/acme",
        },
        headers=bearer(user),
    )
    assert response.status_code == 400
    assert response.json()["code"] == "job_board_url_not_supported"
    assert "careers url" in response.json()["detail"].lower()


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("not-a-url", "invalid_application_url"),
        ("javascript:alert(1)", "invalid_application_url"),
        ("ftp://example.com/apply", "invalid_application_url"),
        ("http://localhost/apply", "unsafe_application_url"),
        ("http://127.0.0.1/apply", "unsafe_application_url"),
        ("http://10.0.0.5/apply", "unsafe_application_url"),
        ("https://user:secret@example.com/apply", "invalid_application_url"),
        ("http://example.com:8080/apply", "unsafe_application_url"),
    ],
)
async def test_invalid_or_unsafe_urls_are_rejected_without_changing_the_application(
    client: AsyncClient,
    session_factory: SessionFactory,
    url: str,
    code: str,
) -> None:
    user, _, preview = await prepare_application(client, session_factory)
    application_id = preview["application_id"]

    response = await client.post(
        f"{APPLICATIONS}/{application_id}/inspect",
        json={"application_url": url},
        headers=bearer(user),
    )
    assert response.status_code == 400, response.text
    assert response.json()["code"] == code
    assert response.json()["detail"]

    fetched = await client.get(
        f"{APPLICATIONS}/{application_id}", headers=bearer(user)
    )
    assert fetched.json()["application_url"] == GLASSDOOR


async def test_host_resolving_to_private_address_is_rejected(
    client: AsyncClient,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, _, preview = await prepare_application(client, session_factory)

    async def private_validate(
        url: str, *, resolver: Any = None
    ) -> tuple[str, tuple[str, ...]]:
        del resolver, url
        raise UnsafeNavigationError(
            "The application host resolved to a private or internal address.",
            code="unsafe_application_host",
        )

    monkeypatch.setattr(
        "app.services.application_service.validate_application_destination",
        private_validate,
    )

    response = await client.post(
        f"{APPLICATIONS}/{preview['application_id']}/inspect",
        json={"application_url": "https://internal.acme.example/apply"},
        headers=bearer(user),
    )
    assert response.status_code == 400
    assert response.json()["code"] == "unsafe_application_host"
    assert "private" in response.json()["detail"].lower()


async def test_another_user_cannot_update_the_application_url(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    _, _, preview = await prepare_application(client, session_factory)
    intruder = await create_user(
        session_factory, email="intruder@example.com", google_sub="google-intruder"
    )

    response = await client.post(
        f"{APPLICATIONS}/{preview['application_id']}/inspect",
        json={"application_url": EMPLOYER_FORM},
        headers=bearer(intruder),
    )
    assert response.status_code == 404


async def test_url_update_requires_authentication(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    _, _, preview = await prepare_application(client, session_factory)
    response = await client.post(
        f"{APPLICATIONS}/{preview['application_id']}/inspect",
        json={"application_url": EMPLOYER_FORM},
    )
    assert response.status_code == 401


async def test_listing_application_reports_the_easy_apply_redirect_flow(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    """The listing is preserved and the form destination starts unvalidated."""
    _, _, preview = await prepare_application(client, session_factory)
    assert preview["application_url"] == GLASSDOOR
    assert preview["job_listing_url"] == GLASSDOOR
    assert preview["redirect_flow"] is True
    assert preview["redirect_status"] == "not_started"
    assert preview["destination_validated"] is False
    assert preview["application_destination_url"] is None
    assert any("Easy Apply" in note for note in preview["notes"])


async def test_suggestion_from_trusted_listing_text_requires_explicit_update(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    description = (
        "Requirements:\n- Python\n\n"
        "Apply on the company site: https://acme.example/careers/apply?job=42\n"
        "More roles: https://www.linkedin.com/company/acme/jobs\n"
    )
    _, _, preview = await prepare_application(
        client, session_factory, description=description
    )
    # The suggestion is exposed but is not stored as the application URL.
    assert (
        preview["suggested_application_url"]
        == "https://acme.example/careers/apply?job=42"
    )
    assert preview["application_url"] == GLASSDOOR

    assert is_job_board_url(GLASSDOOR) is True
    assert is_job_board_url(EMPLOYER_FORM) is False
    assert suggest_employer_application_url(
        "See https://127.0.0.1/apply and https://www.glassdoor.com/job-listing/x"
    ) is None

    user = await create_user(
        session_factory, email="second@example.com", google_sub="google-second"
    )
    # Another user cannot even see the application to use its suggestion.
    response = await client.post(
        f"{APPLICATIONS}/{preview['application_id']}/inspect",
        json={"application_url": preview["suggested_application_url"]},
        headers=bearer(user),
    )
    assert response.status_code == 404


def test_suggestion_ignores_unsafe_and_asset_urls() -> None:
    assert (
        suggest_employer_application_url(
            "Logo https://cdn.example.com/logo.png Apply https://acme.example/jobs/9/apply"
        )
        == "https://acme.example/jobs/9/apply"
    )
    with pytest.raises(ApplicationUrlError):
        normalize_application_url("http://[::1]/apply")
