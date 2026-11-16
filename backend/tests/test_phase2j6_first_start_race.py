"""Phase 2J.6: PostgreSQL evidence for the first-start race.

``SELECT ... FOR UPDATE`` cannot lock a row that does not exist, so the very
first execution row for an application is created by a read-then-insert. This
module proves, against a real PostgreSQL instance, that:

* two simultaneous first starts create exactly one execution;
* one request wins and the other receives a structured 409 (never a 500);
* no field is typed twice;
* a failed browser start leaves no execution behind and a retry succeeds;
* different applications and different users proceed independently.

The default suite runs on aiosqlite, where a transaction-scoped advisory lock
does not exist and writers are serialised by the database itself. SQLite
therefore cannot validate the PostgreSQL locking path, so these tests are
opt-in and refuse any database whose name does not contain ``test``:

    APPLYPILOT_POSTGRES_TEST_URL="postgresql+asyncpg://user:pw@host:port/applypilot_test" \
        python -m pytest tests/test_phase2j6_first_start_race.py -q

The tables are dropped and recreated in that isolated database. No browser is
launched: the Phase 2J.3 fake driver records which fields were interacted with.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any

import pytest
import pytest_asyncio
from app.core.config import settings
from app.db.base import Base
from app.models.application_field_execution import ApplicationFieldExecution
from app.services.application_field_fill_service import ApplicationFieldFillService
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tests.test_applications import bearer, create_job
from tests.test_phase2e_field_fill import prepare_application, start_session
from tests.test_phase2j3_unified_fields import (
    BROWSER,
    GREENHOUSE_URL,
    browser_runtime,  # noqa: F401 - pytest fixture
    fill_url,
    prepare_plan,
)

POSTGRES_URL = os.environ.get("APPLYPILOT_POSTGRES_TEST_URL", "")

if not POSTGRES_URL:
    pytest.skip(
        "Set APPLYPILOT_POSTGRES_TEST_URL to an isolated PostgreSQL test database.",
        allow_module_level=True,
    )

if "test" not in (make_url(POSTGRES_URL).database or "").lower():
    pytest.skip(
        "Refusing to run: the database name must contain 'test'.",
        allow_module_level=True,
    )

APPLICATIONS = "/api/v1/applications"
SessionFactory = async_sessionmaker[AsyncSession]


@pytest_asyncio.fixture
async def engine() -> Any:
    """A real PostgreSQL engine with a clean schema in the dedicated test DB."""
    test_engine: AsyncEngine = create_async_engine(POSTGRES_URL)
    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield test_engine
    finally:
        async with test_engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await test_engine.dispose()


async def _execution_rows(
    session_factory: SessionFactory, application_id: str | None = None
) -> list[ApplicationFieldExecution]:
    async with session_factory() as session:
        statement = select(ApplicationFieldExecution)
        if application_id is not None:
            statement = statement.where(
                ApplicationFieldExecution.application_id
                == uuid.UUID(str(application_id))
            )
        return list((await session.scalars(statement)).all())


async def _execution_count(session_factory: SessionFactory) -> int:
    async with session_factory() as session:
        return int(
            await session.scalar(
                select(func.count()).select_from(ApplicationFieldExecution)
            )
            or 0
        )


async def _second_application(
    client: AsyncClient, session_factory: SessionFactory, user: Any
) -> str:
    job = await create_job(session_factory, user, job_url=GREENHOUSE_URL)
    response = await client.post(
        f"{APPLICATIONS}/analyze",
        json={"job_id": str(job.id)},
        headers=bearer(user),
    )
    assert response.status_code == 200, response.text
    return response.json()["application_id"]


async def _plan_for(
    client: AsyncClient, user: Any, application_id: str
) -> dict[str, Any]:
    headers = bearer(user)
    started = await start_session(client, user, application_id)
    assert started.status_code == 200, started.text
    analyzed = await client.post(
        f"{BROWSER}/by-application/{application_id}/form-analysis", headers=headers
    )
    assert analyzed.status_code == 200, analyzed.text
    planned = await client.get(fill_url(application_id, "-plan"), headers=headers)
    assert planned.status_code == 200, planned.text
    return planned.json()


def _filled_field_ids(handle: Any) -> list[str]:
    return [action.field_id for action in handle.actions]


# ---------------------------------------------------------------------------
# 1-4. Two simultaneous first starts: one row, one 409, no duplicate writes
# ---------------------------------------------------------------------------
async def test_two_simultaneous_first_starts_create_one_execution(
    client: AsyncClient,
    session_factory: SessionFactory,
    browser_runtime: Any,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, application_id, plan, handle = await prepare_plan(
        client,
        session_factory,
        browser_runtime,
        email="pg2j6-race@example.com",
        google_sub="google-sub-pg2j6-race",
    )
    assert await _execution_count(session_factory) == 0, "the race needs a cold start"

    headers = bearer(user)
    payload = {"approval_fingerprint": plan["approval_fingerprint"]}

    # Hold both requests at the point where they would read-then-insert, so the
    # race is genuine rather than dependent on event-loop ordering.
    arrived = {"count": 0}
    both_ready = asyncio.Event()
    original = ApplicationFieldFillService._acquire_first_start_lock

    async def gated(self: Any, target: uuid.UUID) -> None:
        arrived["count"] += 1
        if arrived["count"] >= 2:
            both_ready.set()
        await asyncio.wait_for(both_ready.wait(), timeout=10)
        await original(self, target)

    monkeypatch.setattr(
        ApplicationFieldFillService, "_acquire_first_start_lock", gated
    )

    first, second = await asyncio.gather(
        client.post(fill_url(application_id), json=payload, headers=headers),
        client.post(fill_url(application_id), json=payload, headers=headers),
    )
    assert arrived["count"] == 2

    statuses = sorted((first.status_code, second.status_code))
    assert statuses == [200, 409], (first.text, second.text)

    winner = first if first.status_code == 200 else second
    loser = second if first.status_code == 200 else first
    assert loser.json()["code"] in {
        "field_fill_in_progress",
        "field_fill_already_complete",
    }
    assert loser.json()["detail"]

    rows = await _execution_rows(session_factory, application_id)
    assert len(rows) == 1
    assert rows[0].run_number == 1
    assert str(rows[0].id) == winner.json()["execution_id"]
    assert await _execution_count(session_factory) == 1

    # Exactly one run typed into the page, and every field at most once.
    filled = _filled_field_ids(handle)
    assert filled
    assert len(filled) == len(set(filled))


async def test_a_start_after_the_race_replays_or_conflicts_without_a_second_row(
    client: AsyncClient,
    session_factory: SessionFactory,
    browser_runtime: Any,  # noqa: F811
) -> None:
    user, application_id, plan, handle = await prepare_plan(
        client,
        session_factory,
        browser_runtime,
        email="pg2j6-repeat@example.com",
        google_sub="google-sub-pg2j6-repeat",
    )
    headers = bearer(user)
    payload = {"approval_fingerprint": plan["approval_fingerprint"]}

    started = await client.post(
        fill_url(application_id), json=payload, headers=headers
    )
    assert started.status_code == 200, started.text
    completed = len(handle.actions)

    repeated = await client.post(
        fill_url(application_id), json=payload, headers=headers
    )
    # A finished run either replays its stored outcome (idempotent) or reports a
    # structured conflict; it never starts a second run.
    assert repeated.status_code in {200, 409}, repeated.text
    if repeated.status_code == 200:
        assert repeated.json()["execution_id"] == started.json()["execution_id"]
    else:
        assert repeated.json()["code"] in {
            "field_fill_in_progress",
            "field_fill_already_complete",
        }
    assert len(handle.actions) == completed, "the repeated start typed into the page"

    rows = await _execution_rows(session_factory, application_id)
    assert len(rows) == 1
    assert rows[0].run_number == 1


# ---------------------------------------------------------------------------
# 5. Browser startup failure, then a successful retry
# ---------------------------------------------------------------------------
async def test_a_failed_browser_start_leaves_no_execution_and_the_retry_succeeds(
    client: AsyncClient,
    session_factory: SessionFactory,
    browser_runtime: Any,  # noqa: F811
) -> None:
    user, preview, _resume = await prepare_application(
        client,
        session_factory,
        email="pg2j6-browser@example.com",
        google_sub="google-sub-pg2j6-browser",
    )
    application_id = preview["application_id"]
    headers = bearer(user)

    browser_runtime.driver.fail = True
    failed = await start_session(client, user, application_id)
    assert failed.status_code == 503, failed.text
    assert failed.json()["code"] == "browser_automation_unavailable"
    assert await _execution_count(session_factory) == 0

    # The failed attempt released the per-application lock; a retry can succeed.
    browser_runtime.driver.fail = False
    retried = await start_session(client, user, application_id)
    assert retried.status_code == 200, retried.text

    analyzed = await client.post(
        f"{BROWSER}/by-application/{application_id}/form-analysis", headers=headers
    )
    assert analyzed.status_code == 200, analyzed.text
    planned = await client.get(fill_url(application_id, "-plan"), headers=headers)
    assert planned.status_code == 200, planned.text

    filled = await client.post(
        fill_url(application_id),
        json={"approval_fingerprint": planned.json()["approval_fingerprint"]},
        headers=headers,
    )
    assert filled.status_code == 200, filled.text
    assert filled.json()["succeeded_count"] >= 1

    rows = await _execution_rows(session_factory, application_id)
    assert len(rows) == 1
    assert rows[0].run_number == 1
    # The form in the fake page carries legal/CAPTCHA fields, so the honest stop
    # is a human-input pause rather than a completed run; either way it is one row.
    assert rows[0].status.value in {"ready_for_final_review", "needs_user_input"}
    assert rows[0].status.value != "running"


# ---------------------------------------------------------------------------
# 6. Different applications of one user proceed independently
# ---------------------------------------------------------------------------
async def test_two_applications_of_one_user_start_independently(
    client: AsyncClient,
    session_factory: SessionFactory,
    browser_runtime: Any,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "APPLICATION_BROWSER_MAX_ACTIVE_PER_USER", 2)
    user, first_application, first_plan, first_handle = await prepare_plan(
        client,
        session_factory,
        browser_runtime,
        email="pg2j6-two-apps@example.com",
        google_sub="google-sub-pg2j6-two-apps",
    )
    second_application = await _second_application(client, session_factory, user)
    second_plan = await _plan_for(client, user, second_application)

    headers = bearer(user)
    first, second = await asyncio.gather(
        client.post(
            fill_url(first_application),
            json={"approval_fingerprint": first_plan["approval_fingerprint"]},
            headers=headers,
        ),
        client.post(
            fill_url(second_application),
            json={"approval_fingerprint": second_plan["approval_fingerprint"]},
            headers=headers,
        ),
    )
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["execution_id"] != second.json()["execution_id"]

    second_handle = browser_runtime.driver.handles[-1]
    assert second_handle is not first_handle
    assert _filled_field_ids(first_handle)
    assert _filled_field_ids(second_handle)

    assert len(await _execution_rows(session_factory, first_application)) == 1
    assert len(await _execution_rows(session_factory, second_application)) == 1
    assert await _execution_count(session_factory) == 2


# ---------------------------------------------------------------------------
# 7. Different users keep ownership isolation
# ---------------------------------------------------------------------------
async def test_two_users_start_independently_and_ownership_is_enforced(
    client: AsyncClient,
    session_factory: SessionFactory,
    browser_runtime: Any,  # noqa: F811
) -> None:
    owner, owner_application, owner_plan, _owner_handle = await prepare_plan(
        client,
        session_factory,
        browser_runtime,
        email="pg2j6-owner@example.com",
        google_sub="google-sub-pg2j6-owner",
    )
    other, other_application, other_plan, _other_handle = await prepare_plan(
        client,
        session_factory,
        browser_runtime,
        email="pg2j6-other@example.com",
        google_sub="google-sub-pg2j6-other",
    )

    owner_headers = bearer(owner)
    other_headers = bearer(other)
    owner_fill, other_fill = await asyncio.gather(
        client.post(
            fill_url(owner_application),
            json={"approval_fingerprint": owner_plan["approval_fingerprint"]},
            headers=owner_headers,
        ),
        client.post(
            fill_url(other_application),
            json={"approval_fingerprint": other_plan["approval_fingerprint"]},
            headers=other_headers,
        ),
    )
    assert owner_fill.status_code == 200, owner_fill.text
    assert other_fill.status_code == 200, other_fill.text
    assert owner_fill.json()["execution_id"] != other_fill.json()["execution_id"]

    # A foreign owner cannot start (or reach) the other's execution.
    blocked = await client.post(
        fill_url(owner_application),
        json={"approval_fingerprint": owner_plan["approval_fingerprint"]},
        headers=other_headers,
    )
    assert blocked.status_code == 404
    assert blocked.json()["code"] == "application_not_found"

    assert len(await _execution_rows(session_factory, owner_application)) == 1
    assert len(await _execution_rows(session_factory, other_application)) == 1
    assert await _execution_count(session_factory) == 2
