"""Phase 2J.5: PostgreSQL row-lock and concurrency evidence.

The rest of the suite runs on aiosqlite, where ``SELECT ... FOR UPDATE`` is a
no-op. That cannot establish that two fill requests for the same application are
serialised in production, so these tests require a real, isolated PostgreSQL
database:

    APPLYPILOT_POSTGRES_TEST_URL="postgresql+asyncpg://user:pw@host:port/applypilot_test" \
        python -m pytest tests/test_phase2j5_postgres_concurrency.py -q

They refuse to run against a database whose name does not contain ``test`` so a
production URL cannot be used by accident. The tables are dropped and recreated
in that database.

No browser is launched: the fake driver from the Phase 2J.3 suite is used, so
these tests measure database locking and ownership, not Chromium.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from typing import Any

import pytest
import pytest_asyncio
from app.core.config import settings
from app.db.base import Base
from app.models.application_field_execution import ApplicationFieldExecution
from app.models.browser_session import BrowserSession, BrowserSessionStatus
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tests.test_applications import bearer, create_job
from tests.test_phase2e_field_fill import start_session
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
    session_factory: SessionFactory, application_id: str
) -> list[ApplicationFieldExecution]:
    async with session_factory() as session:
        return list(
            (
                await session.scalars(
                    select(ApplicationFieldExecution).where(
                        ApplicationFieldExecution.application_id
                        == uuid.UUID(str(application_id))
                    )
                )
            ).all()
        )


async def _second_application(
    client: AsyncClient, session_factory: SessionFactory, user: Any
) -> str:
    """One more application owned by the same user."""
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
# Two concurrent fills for the same application
# ---------------------------------------------------------------------------
async def test_two_concurrent_fills_execute_each_field_once(
    client: AsyncClient,
    session_factory: SessionFactory,
    browser_runtime: Any,  # noqa: F811
) -> None:
    user, application_id, plan, handle = await prepare_plan(
        client,
        session_factory,
        browser_runtime,
        email="pg-concurrent@example.com",
        google_sub="google-sub-pg-concurrent",
    )
    headers = bearer(user)
    payload = {"approval_fingerprint": plan["approval_fingerprint"]}

    responses = await asyncio.gather(
        client.post(fill_url(application_id), json=payload, headers=headers),
        client.post(fill_url(application_id), json=payload, headers=headers),
    )

    statuses = sorted(response.status_code for response in responses)
    assert 200 in statuses
    # The loser either saw the live run (409) or the finished, idempotent run.
    assert all(code in {200, 409} for code in statuses), statuses

    finished = [r.json() for r in responses if r.status_code == 200]
    assert len({body["execution_id"] for body in finished}) == 1

    rows = await _execution_rows(session_factory, application_id)
    assert len(rows) == 1

    # Every field was typed at most once, however the requests interleaved.
    filled = _filled_field_ids(handle)
    assert filled
    assert len(filled) == len(set(filled))


# ---------------------------------------------------------------------------
# Two concurrent resumes of the same paused run
# ---------------------------------------------------------------------------
async def test_two_concurrent_resumes_are_serialised_by_the_row_lock(
    client: AsyncClient,
    session_factory: SessionFactory,
    browser_runtime: Any,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only one of two simultaneous resumes may type into the page.

    The gate makes both requests reach the row read together, so this test
    really depends on ``SELECT ... FOR UPDATE``: without it both would resume
    the same paused run and type every field twice.
    """
    from app.services.application_field_fill_service import ApplicationFieldFillService

    user, application_id, plan, handle = await prepare_plan(
        client,
        session_factory,
        browser_runtime,
        email="pg-resume@example.com",
        google_sub="google-sub-pg-resume",
    )
    headers = bearer(user)
    payload = {"approval_fingerprint": plan["approval_fingerprint"]}

    # A paused run with no actions yet.
    state = {"busy": True}
    handle.user_input_active = lambda seconds=2.0: state["busy"]
    paused = await client.post(fill_url(application_id), json=payload, headers=headers)
    assert paused.status_code == 200, paused.text
    assert paused.json()["status"] == "needs_user_input"
    assert handle.actions == []
    state["busy"] = False

    arrived = {"count": 0}
    both_ready = asyncio.Event()
    original = ApplicationFieldFillService._latest_analysis

    async def gated(self: Any, *args: Any, **kwargs: Any) -> Any:
        arrived["count"] += 1
        if arrived["count"] >= 2:
            both_ready.set()
        await asyncio.wait_for(both_ready.wait(), timeout=10)
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(ApplicationFieldFillService, "_latest_analysis", gated)

    first, second = await asyncio.gather(
        client.post(fill_url(application_id, "/resume"), json=payload, headers=headers),
        client.post(fill_url(application_id, "/resume"), json=payload, headers=headers),
    )
    assert arrived["count"] == 2

    statuses = sorted((first.status_code, second.status_code))
    assert statuses == [200, 409], (first.text, second.text)
    refused = first if first.status_code == 409 else second
    assert refused.json()["code"] == "field_fill_not_paused"

    # Each field was typed exactly once by the single winning resume.
    filled = _filled_field_ids(handle)
    assert filled
    assert len(filled) == len(set(filled))
    assert len(await _execution_rows(session_factory, application_id)) == 1


# ---------------------------------------------------------------------------
# Lock acquisition and release
# ---------------------------------------------------------------------------
async def test_row_lock_blocks_a_second_writer_until_it_is_released(
    client: AsyncClient,
    session_factory: SessionFactory,
    browser_runtime: Any,  # noqa: F811
) -> None:
    user, application_id, plan, _handle = await prepare_plan(
        client,
        session_factory,
        browser_runtime,
        email="pg-lock@example.com",
        google_sub="google-sub-pg-lock",
    )
    await client.post(
        fill_url(application_id),
        json={"approval_fingerprint": plan["approval_fingerprint"]},
        headers=bearer(user),
    )
    rows = await _execution_rows(session_factory, application_id)
    assert len(rows) == 1
    execution_id = rows[0].id

    async def probe() -> tuple[float, bool]:
        async with session_factory() as session:
            began = time.monotonic()
            locked = await session.scalar(
                select(ApplicationFieldExecution)
                .where(ApplicationFieldExecution.id == execution_id)
                .with_for_update()
            )
            waited = time.monotonic() - began
            await session.rollback()
            return waited, locked is not None

    async with session_factory() as holder:
        await holder.scalar(
            select(ApplicationFieldExecution)
            .where(ApplicationFieldExecution.id == execution_id)
            .with_for_update()
        )
        task = asyncio.create_task(probe())
        # The second writer must not get the lock while it is held.
        await asyncio.sleep(0.4)
        assert not task.done(), "the row lock did not block a second writer"
        await holder.commit()

    waited, found = await asyncio.wait_for(task, timeout=5)
    assert found is True
    assert waited >= 0.2, f"lock was not actually held (waited {waited:.3f}s)"

    # Released: a fresh writer acquires it immediately.
    began = time.monotonic()
    async with session_factory() as session:
        await session.scalar(
            select(ApplicationFieldExecution)
            .where(ApplicationFieldExecution.id == execution_id)
            .with_for_update()
        )
        await session.rollback()
    assert time.monotonic() - began < 0.2


# ---------------------------------------------------------------------------
# Duplicate execution prevention
# ---------------------------------------------------------------------------
async def test_a_second_execution_row_for_the_same_application_is_rejected(
    client: AsyncClient,
    session_factory: SessionFactory,
    browser_runtime: Any,  # noqa: F811
) -> None:
    user, application_id, plan, _handle = await prepare_plan(
        client,
        session_factory,
        browser_runtime,
        email="pg-duplicate@example.com",
        google_sub="google-sub-pg-duplicate",
    )
    await client.post(
        fill_url(application_id),
        json={"approval_fingerprint": plan["approval_fingerprint"]},
        headers=bearer(user),
    )
    rows = await _execution_rows(session_factory, application_id)
    assert len(rows) == 1

    first = rows[0]
    async with session_factory() as session:
        session.add(
            ApplicationFieldExecution(
                id=uuid.uuid4(),
                user_id=first.user_id,
                application_id=first.application_id,
                browser_session_id=first.browser_session_id,
                analysis_id=first.analysis_id,
                analysis_version=first.analysis_version,
                run_number=2,
                plan=[],
                results=[],
                summary={},
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

    rows = await _execution_rows(session_factory, application_id)
    assert len(rows) == 1
    assert rows[0].run_number == 1


# ---------------------------------------------------------------------------
# Failure, rollback and lock release
# ---------------------------------------------------------------------------
async def test_a_failed_browser_open_leaves_no_execution_and_frees_the_session(
    client: AsyncClient,
    session_factory: SessionFactory,
    browser_runtime: Any,  # noqa: F811
) -> None:
    user, application_id, _plan, _handle = await prepare_plan(
        client,
        session_factory,
        browser_runtime,
        email="pg-failure@example.com",
        google_sub="google-sub-pg-failure",
    )
    headers = bearer(user)
    runtime = browser_runtime
    # Drop the live handle so the next start really has to launch a browser.
    existing = await client.get(
        f"{BROWSER}/by-application/{application_id}", headers=headers
    )
    assert existing.status_code == 200, existing.text
    await runtime.manager.release(uuid.UUID(existing.json()["session_id"]))
    runtime.driver.fail = True

    failed = await client.post(
        BROWSER, json={"application_id": application_id}, headers=headers
    )
    assert failed.status_code == 503, failed.text
    assert failed.json()["code"] == "browser_automation_unavailable"

    async with session_factory() as session:
        row = await session.scalar(
            select(BrowserSession).where(
                BrowserSession.application_id == uuid.UUID(application_id)
            )
        )
        assert row is not None
        assert row.browser_status is not BrowserSessionStatus.OPEN
        assert row.failure_code == "browser_automation_unavailable"
        executions = await session.scalar(
            select(func.count()).select_from(ApplicationFieldExecution)
        )
        assert executions == 0

    # The failed attempt released the per-application lock: recovery works.
    runtime.driver.fail = False
    recovered = await client.post(
        BROWSER, json={"application_id": application_id}, headers=headers
    )
    assert recovered.status_code == 200, recovered.text
    analyzed = await client.post(
        f"{BROWSER}/by-application/{application_id}/form-analysis", headers=headers
    )
    assert analyzed.status_code == 200, analyzed.text
    refreshed = await client.get(fill_url(application_id, "-plan"), headers=headers)
    assert refreshed.status_code == 200, refreshed.text
    filled = await client.post(
        fill_url(application_id),
        json={"approval_fingerprint": refreshed.json()["approval_fingerprint"]},
        headers=headers,
    )
    assert filled.status_code == 200, filled.text
    assert filled.json()["succeeded_count"] >= 1


# ---------------------------------------------------------------------------
# Two applications owned by the same user
# ---------------------------------------------------------------------------
async def test_two_applications_of_one_user_fill_independently(
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
        email="pg-two-apps@example.com",
        google_sub="google-sub-pg-two-apps",
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

    first_ids = set(_filled_field_ids(first_handle))
    second_handle = browser_runtime.driver.handles[-1]
    second_ids = set(_filled_field_ids(second_handle))
    assert first_ids
    assert second_ids
    assert first_ids != second_ids or first_handle is not second_handle
    assert len(await _execution_rows(session_factory, first_application)) == 1
    assert len(await _execution_rows(session_factory, second_application)) == 1


# ---------------------------------------------------------------------------
# Different users
# ---------------------------------------------------------------------------
async def test_another_user_cannot_reach_a_foreign_execution(
    client: AsyncClient,
    session_factory: SessionFactory,
    browser_runtime: Any,  # noqa: F811
) -> None:
    owner, owner_application, owner_plan, owner_handle = await prepare_plan(
        client,
        session_factory,
        browser_runtime,
        email="pg-owner@example.com",
        google_sub="google-sub-pg-owner",
    )
    intruder, intruder_application, intruder_plan, _intruder_handle = await prepare_plan(
        client,
        session_factory,
        browser_runtime,
        email="pg-intruder@example.com",
        google_sub="google-sub-pg-intruder",
    )

    owner_headers = bearer(owner)
    intruder_headers = bearer(intruder)
    # Concurrent runs for two different users must not interfere.
    owner_fill, intruder_fill = await asyncio.gather(
        client.post(
            fill_url(owner_application),
            json={"approval_fingerprint": owner_plan["approval_fingerprint"]},
            headers=owner_headers,
        ),
        client.post(
            fill_url(intruder_application),
            json={"approval_fingerprint": intruder_plan["approval_fingerprint"]},
            headers=intruder_headers,
        ),
    )
    assert owner_fill.status_code == 200, owner_fill.text
    assert intruder_fill.status_code == 200, intruder_fill.text
    assert {body["execution_id"] for body in (owner_fill.json(), intruder_fill.json())}
    assert owner_fill.json()["execution_id"] != intruder_fill.json()["execution_id"]

    # Ownership is still checked on the foreign application.
    blocked = await client.post(
        fill_url(owner_application, "/resume"),
        json={"approval_fingerprint": owner_plan["approval_fingerprint"]},
        headers=intruder_headers,
    )
    assert blocked.status_code == 404
    assert blocked.json()["code"] == "application_not_found"
    assert len(await _execution_rows(session_factory, owner_application)) == 1
    assert len(await _execution_rows(session_factory, intruder_application)) == 1
    assert owner_handle is not None
