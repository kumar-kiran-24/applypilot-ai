"""Regression tests for the runtime failures found on 2026-11-13.

* Windows event loop: Uvicorn 0.38 selects a selector loop whenever it runs with
  ``--reload``/``--workers``, and a selector loop cannot create the subprocess
  Playwright uses to launch Chromium.
* Launch cleanup: a Chromium that started but whose context failed must not be
  left running, and the failure must name the actual error.
* Migration state: the Alembic history must have exactly one head and that head
  must be the revision that owns ``job_states``.

No real browser is started here; session, destination and security behaviour is
covered by the existing Phase 2E/2H suites.
"""

from __future__ import annotations

import asyncio
import inspect
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import sqlalchemy as sa
from app.core.config import settings
from app.core.event_loop import (
    WINDOWS_LOOP_FACTORY_PATH,
    configure_event_loop_policy,
    loop_supports_subprocess,
    windows_proactor_loop_factory,
)
from app.integrations.browser.persistent import PlaywrightPersistentBrowserDriver
from app.integrations.browser.session import BrowserUnavailableError
from app.models.job_state import JobState

BACKEND_ROOT = Path(__file__).resolve().parents[1]
JOB_STATES_REVISION = "a7c4e18f5b93"
PREVIOUS_REVISION = "c3f9a72d1e05"
EMPLOYER_URL = "https://boards.greenhouse.io/acme/jobs/123"


# ---------------------------------------------------------------------------
# Event loop selection
# ---------------------------------------------------------------------------
def test_windows_factory_returns_a_proactor_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    loop = windows_proactor_loop_factory()
    try:
        assert isinstance(loop, asyncio.ProactorEventLoop)
    finally:
        loop.close()


def test_non_windows_factory_keeps_the_selector_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    loop = windows_proactor_loop_factory()
    try:
        assert isinstance(loop, asyncio.SelectorEventLoop)
    finally:
        loop.close()


def test_selector_loop_is_rejected_only_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    loop = asyncio.SelectorEventLoop()
    try:
        monkeypatch.setattr(sys, "platform", "win32")
        assert loop_supports_subprocess(loop) is False
        monkeypatch.setattr(sys, "platform", "linux")
        # On Linux the selector loop is the normal loop and does support
        # subprocesses, so this must not become a false refusal.
        assert loop_supports_subprocess(loop) is True
    finally:
        loop.close()


@pytest.mark.skipif(sys.platform != "win32", reason="requested event loop is Windows-only")
def test_uvicorn_reload_configuration_uses_the_proactor_factory() -> None:
    """The old failure mode: --reload used to force SelectorEventLoop."""
    import uvicorn

    config = uvicorn.Config(app="app.main:app", loop=WINDOWS_LOOP_FACTORY_PATH, reload=True)
    assert config.use_subprocess is True
    factory = config.get_loop_factory()
    assert factory is not None
    loop = factory()
    try:
        # A Proactor loop is what lets Playwright create its subprocess.
        assert isinstance(loop, asyncio.ProactorEventLoop)
    finally:
        loop.close()


def test_run_server_passes_the_loop_factory_before_starting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.run_server as run_server

    captured: dict[str, Any] = {}
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(run_server, "configure_event_loop_policy", lambda: True)
    monkeypatch.setitem(
        sys.modules,
        "uvicorn",
        SimpleNamespace(run=lambda *args, **kwargs: captured.update(kwargs)),
    )

    assert run_server.main(["--port", "8123", "--reload"]) == 0
    assert captured["loop"] == WINDOWS_LOOP_FACTORY_PATH
    assert captured["port"] == 8123
    assert captured["reload"] is True


def test_policy_configuration_is_a_noop_off_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    assert configure_event_loop_policy() is False


@pytest.mark.skipif(sys.platform != "win32", reason="Proactor policy is Windows-only")
def test_policy_configuration_installs_the_proactor_policy() -> None:
    assert configure_event_loop_policy() is True
    assert isinstance(asyncio.get_event_loop_policy(), asyncio.WindowsProactorEventLoopPolicy)


# ---------------------------------------------------------------------------
# Playwright launch failure handling
# ---------------------------------------------------------------------------
class _FakeBrowser:
    def __init__(self, *, fail_context: bool) -> None:
        self.fail_context = fail_context
        self.closed = False

    async def new_context(self, **_kwargs: Any) -> Any:
        if self.fail_context:
            raise RuntimeError("context creation failed")
        return SimpleNamespace(  # pragma: no cover - not reached in these tests
            new_page=self.new_page, route=self.route
        )

    async def new_page(self) -> Any:  # pragma: no cover - defensive
        raise AssertionError("no page should be created")

    async def route(self, *_args: Any) -> None:  # pragma: no cover - defensive
        return None

    async def close(self) -> None:
        self.closed = True


def _install_fake_playwright(
    monkeypatch: pytest.MonkeyPatch,
    *,
    browser: _FakeBrowser | None = None,
    launch_error: Exception | None = None,
) -> SimpleNamespace:
    """Replace ``playwright.async_api.async_playwright`` with an in-process fake."""
    import playwright.async_api as async_api

    state = SimpleNamespace(stopped=False, launch_kwargs=None)

    async def stop() -> None:
        state.stopped = True

    async def launch(**kwargs: Any) -> _FakeBrowser:
        state.launch_kwargs = kwargs
        if launch_error is not None:
            raise launch_error
        assert browser is not None
        return browser

    async def start() -> Any:
        return SimpleNamespace(chromium=SimpleNamespace(launch=launch), stop=stop)

    monkeypatch.setattr(async_api, "async_playwright", lambda: SimpleNamespace(start=start))
    return state


async def test_partial_launch_closes_browser_and_stops_playwright(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A context failure after launch must not leave Chromium running."""
    monkeypatch.setattr(settings, "APPLICATION_AUTOMATION_ENABLED", True)
    browser = _FakeBrowser(fail_context=True)
    state = _install_fake_playwright(monkeypatch, browser=browser)
    driver = PlaywrightPersistentBrowserDriver(settings)

    with pytest.raises(BrowserUnavailableError):
        await driver.open(EMPLOYER_URL, policy=object(), user_id="user-1")

    assert browser.closed is True
    assert state.stopped is True


async def test_launch_failure_stops_playwright(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "APPLICATION_AUTOMATION_ENABLED", True)
    state = _install_fake_playwright(
        monkeypatch, launch_error=RuntimeError("chromium executable missing")
    )
    driver = PlaywrightPersistentBrowserDriver(settings)

    with pytest.raises(BrowserUnavailableError) as error:
        await driver.open(EMPLOYER_URL, policy=object(), user_id="user-1")

    assert state.stopped is True
    assert isinstance(error.value.__cause__, RuntimeError)


@pytest.mark.skipif(sys.platform != "win32", reason="selector-loop refusal is Windows-only")
def test_windows_selector_loop_fails_with_actionable_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 503 must tell the operator how to start the backend correctly."""
    monkeypatch.setattr(settings, "APPLICATION_AUTOMATION_ENABLED", True)
    driver = PlaywrightPersistentBrowserDriver(settings)
    loop = asyncio.SelectorEventLoop()
    try:
        with pytest.raises(BrowserUnavailableError) as error:
            loop.run_until_complete(driver.open(EMPLOYER_URL, policy=object(), user_id="user-1"))
    finally:
        loop.close()

    assert "scripts.run_server" in str(error.value)


# ---------------------------------------------------------------------------
# Migration state and the job_states table
# ---------------------------------------------------------------------------
def test_alembic_history_has_one_head_that_owns_job_states() -> None:
    from alembic.config import Config as AlembicConfig
    from alembic.script import ScriptDirectory

    config = AlembicConfig(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    script = ScriptDirectory.from_config(config)

    assert script.get_heads() == [JOB_STATES_REVISION]
    revision = script.get_revision(JOB_STATES_REVISION)
    assert revision is not None
    assert revision.down_revision == PREVIOUS_REVISION
    # The head must be the migration that creates the table the API queries.
    assert "job_states" in inspect.getsource(revision.module.upgrade)


async def test_job_states_table_matches_the_model(engine: Any) -> None:
    async with engine.connect() as connection:
        tables = await connection.run_sync(lambda sync: sa.inspect(sync).get_table_names())
    assert "job_states" in tables

    table = JobState.__table__
    assert {column.name for column in table.columns} == {
        "id",
        "user_id",
        "job_id",
        "is_saved",
        "is_dismissed",
        "saved_at",
        "dismissed_at",
        "created_at",
        "updated_at",
    }
    unique_names = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }
    assert "uq_job_states_user_id_job_id" in unique_names
    index_names = {index.name for index in table.indexes}
    assert {
        "ix_job_states_user_id",
        "ix_job_states_job_id",
        "ix_job_states_user_id_is_saved",
    } <= index_names
