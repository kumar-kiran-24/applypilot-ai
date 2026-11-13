"""Phase 2I.1 API tests: job listing search/filter/sort, saved/dismissed state
and the dashboard application pipeline.

Every assertion goes through the authenticated API, so ownership scoping is
exercised the same way the browser exercises it.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.security import create_access_token
from app.models.application import Application, ApplicationStage, ApplicationStatus
from app.models.job import Job
from app.models.job_state import JobState
from app.models.user import User
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

SessionFactory = async_sessionmaker[AsyncSession]

JOBS = "/api/v1/jobs"
APPLICATIONS = "/api/v1/applications"


async def create_user(
    session_factory: SessionFactory,
    *,
    email: str,
    google_sub: str,
    full_name: str = "Ada Lovelace",
) -> User:
    async with session_factory() as session:
        user = User(
            google_sub=google_sub,
            email=email,
            email_verified=True,
            full_name=full_name,
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
    title: str = "Senior Backend Engineer",
    company: str | None = "Acme",
    location: str | None = "Remote",
    description: str | None = "Python, FastAPI and PostgreSQL.",
    received_days_ago: int = 0,
) -> Job:
    async with session_factory() as session:
        job = Job(
            user_id=user.id,
            dedupe_key=f"glassdoor-id:{uuid.uuid4().int}",
            source="glassdoor",
            job_title=title,
            company=company,
            location=location,
            job_url=f"https://www.glassdoor.com/job-listing/{uuid.uuid4()}.htm",
            job_description=description,
            email_subject="5 new jobs for you",
            message_id=f"msg-{uuid.uuid4()}",
            received_at=datetime.now(timezone.utc) - timedelta(days=received_days_ago),
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job


async def count_state_rows(
    session_factory: SessionFactory, user: User, job: Job
) -> int:
    async with session_factory() as session:
        return int(
            await session.scalar(
                select(func.count(JobState.id)).where(
                    JobState.user_id == user.id, JobState.job_id == job.id
                )
            )
            or 0
        )


# ---------------------------------------------------------------------------
# Search, filters and sorting
# ---------------------------------------------------------------------------
async def test_search_matches_title_company_and_description(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")
    backend_role = await create_job(
        session_factory, user, title="Backend Engineer", company="Acme"
    )
    await create_job(
        session_factory,
        user,
        title="Account Executive",
        company="Globex",
        description="Quota-carrying sales role.",
    )

    by_title = await client.get(f"{JOBS}?q=backend", headers=bearer(user))
    by_company = await client.get(f"{JOBS}?q=globex", headers=bearer(user))
    by_description = await client.get(f"{JOBS}?q=postgresql", headers=bearer(user))

    assert by_title.status_code == 200
    assert [job["id"] for job in by_title.json()["jobs"]] == [str(backend_role.id)]
    assert [job["company"] for job in by_company.json()["jobs"]] == ["Globex"]
    assert [job["id"] for job in by_description.json()["jobs"]] == [str(backend_role.id)]

    missing = await client.get(f"{JOBS}?q=nonexistent-term", headers=bearer(user))
    assert missing.json() == {"total": 0, "jobs": []}


async def test_search_treats_wildcards_literally(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")
    await create_job(session_factory, user, title="100% Remote Engineer")
    await create_job(session_factory, user, title="Staff Engineer")

    percent = await client.get(f"{JOBS}?q=100%25", headers=bearer(user))
    underscore = await client.get(f"{JOBS}?q=_", headers=bearer(user))

    assert percent.json()["total"] == 1
    # `%` matched the literal percent sign in one title, not both postings.
    assert percent.json()["jobs"][0]["job_title"] == "100% Remote Engineer"
    # `_` is a literal underscore here, and no stored field contains one.
    assert underscore.json()["total"] == 0


async def test_company_and_location_filters(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")
    await create_job(session_factory, user, company="Acme", location="Austin, TX")
    await create_job(session_factory, user, company="Globex", location="Berlin, DE")

    response = await client.get(
        f"{JOBS}?company=acme&location=austin", headers=bearer(user)
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["jobs"][0]["company"] == "Acme"


async def test_sorting_and_pagination(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")
    for title, days in (("Zeta Engineer", 2), ("Alpha Engineer", 1), ("Mid Engineer", 0)):
        await create_job(session_factory, user, title=title, received_days_ago=days)

    by_title = await client.get(f"{JOBS}?sort=title_asc", headers=bearer(user))
    by_received = await client.get(f"{JOBS}?sort=received_desc", headers=bearer(user))
    first_page = await client.get(f"{JOBS}?sort=title_asc&limit=2", headers=bearer(user))
    second_page = await client.get(
        f"{JOBS}?sort=title_asc&limit=2&offset=2", headers=bearer(user)
    )

    assert [job["job_title"] for job in by_title.json()["jobs"]] == [
        "Alpha Engineer",
        "Mid Engineer",
        "Zeta Engineer",
    ]
    assert [job["job_title"] for job in by_received.json()["jobs"]] == [
        "Mid Engineer",
        "Alpha Engineer",
        "Zeta Engineer",
    ]
    assert [job["job_title"] for job in first_page.json()["jobs"]] == [
        "Alpha Engineer",
        "Mid Engineer",
    ]
    # total describes the filtered set, not the page.
    assert first_page.json()["total"] == 3
    assert [job["job_title"] for job in second_page.json()["jobs"]] == ["Zeta Engineer"]


async def test_invalid_sort_and_state_are_rejected(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")

    bad_sort = await client.get(f"{JOBS}?sort=drop_table", headers=bearer(user))
    bad_state = await client.get(f"{JOBS}?state=everything", headers=bearer(user))
    too_long = await client.get(f"{JOBS}?q={'x' * 201}", headers=bearer(user))

    assert bad_sort.status_code == 422
    assert bad_state.status_code == 422
    assert too_long.status_code == 422


async def test_listing_is_scoped_to_the_authenticated_user(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    owner = await create_user(session_factory, email="owner@example.com", google_sub="g-o")
    other = await create_user(session_factory, email="other@example.com", google_sub="g-x")
    await create_job(session_factory, owner, title="Owner Role")

    response = await client.get(f"{JOBS}?q=owner", headers=bearer(other))

    assert response.status_code == 200
    assert response.json() == {"total": 0, "jobs": []}


async def test_saved_ordering_puts_most_recent_save_first(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")
    first = await create_job(session_factory, user, title="First Saved")
    second = await create_job(session_factory, user, title="Second Saved")

    assert (
        await client.post(f"{JOBS}/{first.id}/save", headers=bearer(user))
    ).status_code == 200
    assert (
        await client.post(f"{JOBS}/{second.id}/save", headers=bearer(user))
    ).status_code == 200

    response = await client.get(f"{JOBS}/saved", headers=bearer(user))

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert payload["jobs"][0]["id"] == str(second.id)
    assert all(job["user_state"]["saved"] for job in payload["jobs"])


# ---------------------------------------------------------------------------
# Saved / dismissed recommendation state
# ---------------------------------------------------------------------------
async def test_save_dismiss_restore_lifecycle(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")
    job = await create_job(session_factory, user)

    saved = await client.post(f"{JOBS}/{job.id}/save", headers=bearer(user))
    assert saved.status_code == 200
    assert saved.json()["user_state"]["saved"] is True
    assert saved.json()["user_state"]["dismissed"] is False
    assert saved.json()["user_state"]["saved_at"] is not None

    in_active = await client.get(JOBS, headers=bearer(user))
    in_saved = await client.get(f"{JOBS}?state=saved", headers=bearer(user))
    assert in_active.json()["total"] == 1
    assert in_saved.json()["total"] == 1

    unsaved = await client.delete(f"{JOBS}/{job.id}/save", headers=bearer(user))
    assert unsaved.status_code == 200
    assert unsaved.json()["user_state"]["saved"] is False
    assert unsaved.json()["user_state"]["saved_at"] is None
    assert (await client.get(f"{JOBS}/saved", headers=bearer(user))).json()["total"] == 0

    dismissed = await client.post(f"{JOBS}/{job.id}/dismiss", headers=bearer(user))
    assert dismissed.status_code == 200
    assert dismissed.json()["user_state"]["dismissed"] is True

    # A dismissed posting leaves the default list but is still retrievable.
    assert (await client.get(JOBS, headers=bearer(user))).json()["total"] == 0
    dismissed_only = await client.get(f"{JOBS}?state=dismissed", headers=bearer(user))
    everything = await client.get(f"{JOBS}?state=all", headers=bearer(user))
    assert dismissed_only.json()["total"] == 1
    assert everything.json()["total"] == 1
    detail = await client.get(f"{JOBS}/{job.id}", headers=bearer(user))
    assert detail.status_code == 200
    assert detail.json()["user_state"]["dismissed"] is True

    restored = await client.post(f"{JOBS}/{job.id}/restore", headers=bearer(user))
    assert restored.status_code == 200
    assert restored.json()["user_state"]["dismissed"] is False
    assert (await client.get(JOBS, headers=bearer(user))).json()["total"] == 1


async def test_repeated_state_calls_never_duplicate_rows(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")
    job = await create_job(session_factory, user)

    for _ in range(3):
        assert (
            await client.post(f"{JOBS}/{job.id}/save", headers=bearer(user))
        ).status_code == 200
    for _ in range(2):
        assert (
            await client.post(f"{JOBS}/{job.id}/dismiss", headers=bearer(user))
        ).status_code == 200
    assert (
        await client.post(f"{JOBS}/{job.id}/restore", headers=bearer(user))
    ).status_code == 200

    assert await count_state_rows(session_factory, user, job) == 1


async def test_saved_and_dismissed_are_mutually_exclusive(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")
    job = await create_job(session_factory, user)

    await client.post(f"{JOBS}/{job.id}/save", headers=bearer(user))
    dismissed = await client.post(f"{JOBS}/{job.id}/dismiss", headers=bearer(user))
    assert dismissed.json()["user_state"] == {
        "saved": False,
        "dismissed": True,
        "saved_at": None,
        "dismissed_at": dismissed.json()["user_state"]["dismissed_at"],
    }

    saved_again = await client.post(f"{JOBS}/{job.id}/save", headers=bearer(user))
    assert saved_again.json()["user_state"]["saved"] is True
    assert saved_again.json()["user_state"]["dismissed"] is False
    # Saving a dismissed posting restores it to the default list.
    assert (await client.get(JOBS, headers=bearer(user))).json()["total"] == 1


async def test_state_changes_require_ownership(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    owner = await create_user(session_factory, email="owner@example.com", google_sub="g-o")
    other = await create_user(session_factory, email="other@example.com", google_sub="g-x")
    job = await create_job(session_factory, owner)

    save = await client.post(f"{JOBS}/{job.id}/save", headers=bearer(other))
    unsave = await client.delete(f"{JOBS}/{job.id}/save", headers=bearer(other))
    dismiss = await client.post(f"{JOBS}/{job.id}/dismiss", headers=bearer(other))
    restore = await client.post(f"{JOBS}/{job.id}/restore", headers=bearer(other))

    assert {save.status_code, unsave.status_code, dismiss.status_code, restore.status_code} == {404}
    assert await count_state_rows(session_factory, owner, job) == 0


async def test_state_endpoints_require_authentication(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")
    job = await create_job(session_factory, user)

    assert (await client.post(f"{JOBS}/{job.id}/save")).status_code == 401
    assert (await client.delete(f"{JOBS}/{job.id}/save")).status_code == 401
    assert (await client.post(f"{JOBS}/{job.id}/dismiss")).status_code == 401
    assert (await client.post(f"{JOBS}/{job.id}/restore")).status_code == 401
    assert (await client.get(f"{JOBS}/saved")).status_code == 401


# ---------------------------------------------------------------------------
# Dashboard pipeline aggregation
# ---------------------------------------------------------------------------
async def create_application(
    session_factory: SessionFactory,
    user: User,
    *,
    status: ApplicationStatus,
    stage: ApplicationStage | None = None,
    title: str = "Backend Engineer",
) -> Application:
    job = await create_job(session_factory, user, title=title)
    async with session_factory() as session:
        application = Application(
            user_id=user.id,
            job_id=job.id,
            platform="greenhouse",
            platform_name="Greenhouse",
            application_url="https://boards.greenhouse.io/acme/jobs/1",
            status=status,
            stage=stage,
        )
        session.add(application)
        await session.commit()
        await session.refresh(application)
        return application


async def test_pipeline_counts_use_stored_status_and_stage(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory, email="a@example.com", google_sub="g-a")
    await create_application(session_factory, user, status=ApplicationStatus.DRAFT)
    await create_application(
        session_factory, user, status=ApplicationStatus.AWAITING_APPROVAL
    )
    await create_application(
        session_factory,
        user,
        status=ApplicationStatus.SUBMITTED,
        stage=ApplicationStage.APPLIED,
    )
    await create_application(
        session_factory,
        user,
        status=ApplicationStatus.SUBMITTED,
        stage=ApplicationStage.INTERVIEW,
    )
    await create_application(
        session_factory,
        user,
        status=ApplicationStatus.SUBMITTED,
        stage=ApplicationStage.REJECTED,
    )
    await create_application(session_factory, user, status=ApplicationStatus.FAILED)
    await create_application(session_factory, user, status=ApplicationStatus.CANCELLED)

    response = await client.get(f"{APPLICATIONS}/pipeline", headers=bearer(user))

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 7
    assert payload["in_progress"] == 2
    assert payload["submitted"] == 3
    assert payload["failed"] == 1
    assert payload["cancelled"] == 1
    assert payload["by_stage"] == {
        "applied": 1,
        "screening": 0,
        "interview": 1,
        "offer": 0,
        "rejected": 1,
        "withdrawn": 0,
    }
    assert len(payload["recent"]) == 5
    assert {entry["stage"] for entry in payload["recent"]} <= {None, "applied", "interview", "rejected"}


async def test_pipeline_is_empty_and_scoped_per_user(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    owner = await create_user(session_factory, email="owner@example.com", google_sub="g-o")
    other = await create_user(session_factory, email="other@example.com", google_sub="g-x")
    await create_application(
        session_factory,
        owner,
        status=ApplicationStatus.SUBMITTED,
        stage=ApplicationStage.OFFER,
    )

    empty = await client.get(f"{APPLICATIONS}/pipeline", headers=bearer(other))
    owned = await client.get(f"{APPLICATIONS}/pipeline", headers=bearer(owner))
    anonymous = await client.get(f"{APPLICATIONS}/pipeline")

    assert empty.json()["total"] == 0
    assert empty.json()["recent"] == []
    assert all(count == 0 for count in empty.json()["by_stage"].values())
    assert owned.json()["by_stage"]["offer"] == 1
    assert anonymous.status_code == 401
