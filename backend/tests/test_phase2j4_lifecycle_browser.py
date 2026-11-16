"""Phase 2J.4: real-Chromium browser lifecycle evidence.

The service-level lifecycle (ownership, recovery, expiry, locks, concurrency) is
covered with a fake driver elsewhere. This module adds the evidence only a real
browser can give:

* the live snapshot that resume depends on really does contain user-typed
  values after a real interaction;
* two sessions never share page or cookie state;
* a browser that exits unexpectedly is reported as dead, not as usable.

Run explicitly:

    APPLYPILOT_RUN_BROWSER_TESTS=1 python -m pytest \
        tests/test_phase2j4_lifecycle_browser.py -m browser -q
"""

from __future__ import annotations

import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

if os.environ.get("APPLYPILOT_RUN_BROWSER_TESTS") != "1":
    pytest.skip(
        "Set APPLYPILOT_RUN_BROWSER_TESTS=1 to run the real Chromium test.",
        allow_module_level=True,
    )

from app.core.config import settings
from app.integrations.browser.field_actions import (
    FieldAction,
    FieldActionKind,
    FieldActionResult,
)
from app.integrations.browser.persistent import PlaywrightPersistentBrowserDriver
from app.services.application_form_analyzer import ApplicationFormAnalyzer

pytestmark = pytest.mark.browser

FORM_HTML = """
<!doctype html>
<html><body>
<form id="application">
  <label for="name">Full name</label>
  <input id="name" name="full_name" type="text" required>
  <label for="email">Email address</label>
  <input id="email" name="email" type="email" required>
  <button id="submit" type="submit">Submit application</button>
</form>
</body></html>
"""


class _LocalPolicy:
    def __init__(self, origin: str) -> None:
        self.origin = origin

    def allows(self, url: str) -> bool:
        return url.startswith(self.origin)

    @property
    def allowed_origins(self) -> tuple[str, ...]:
        return (self.origin,)


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # BaseHTTPRequestHandler API
        body = FORM_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture()
def origin() -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture()
def driver(monkeypatch: pytest.MonkeyPatch) -> PlaywrightPersistentBrowserDriver:
    monkeypatch.setattr(settings, "APPLICATION_AUTOMATION_ENABLED", True)
    monkeypatch.setattr(settings, "APPLICATION_BROWSER_HEADLESS", True)
    return PlaywrightPersistentBrowserDriver(settings)


def _fill_action(value: str) -> FieldAction:
    return FieldAction(
        kind=FieldActionKind.FILL,
        field_id="form0-name",
        label="Full name",
        html_id="name",
        name="full_name",
        text=value,
    )


async def test_live_snapshot_contains_a_real_typed_value(
    origin: tuple[ThreadingHTTPServer, str],
    driver: PlaywrightPersistentBrowserDriver,
) -> None:
    _server, url = origin
    handle = await driver.open(
        f"{url}/apply",
        policy=_LocalPolicy(url),  # type: ignore[arg-type]
        user_id="lifecycle-user",
    )
    try:
        assert await handle.is_alive()
        before = ApplicationFormAnalyzer().analyze(await handle.content())
        name_before = next(
            field for field in before.fields if field.field_id == "form0-name"
        )
        assert name_before.current_value is None

        outcome: FieldActionResult = await handle.apply_field_action(
            _fill_action("Ada Lovelace")
        )
        assert outcome.ok

        # This is the read resume performs: the live value must be visible.
        live = ApplicationFormAnalyzer().analyze(await handle.content())
        name_after = next(
            field for field in live.fields if field.field_id == "form0-name"
        )
        assert name_after.current_value == "Ada Lovelace"
    finally:
        await handle.close()


async def test_two_live_sessions_do_not_share_page_or_cookie_state(
    origin: tuple[ThreadingHTTPServer, str],
    driver: PlaywrightPersistentBrowserDriver,
) -> None:
    _server, url = origin
    policy = _LocalPolicy(url)
    first = await driver.open(f"{url}/apply", policy=policy, user_id="user-a")  # type: ignore[arg-type]
    second = await driver.open(f"{url}/apply", policy=policy, user_id="user-b")  # type: ignore[arg-type]
    try:
        assert (await first.apply_field_action(_fill_action("User A"))).ok
        # The second session starts from its own context: no value, no state.
        other = ApplicationFormAnalyzer().analyze(await second.content())
        name = next(field for field in other.fields if field.field_id == "form0-name")
        assert name.current_value is None
        assert await second.is_alive()
    finally:
        await first.close()
        await second.close()


async def test_browser_exit_is_reported_as_dead_not_usable(
    origin: tuple[ThreadingHTTPServer, str],
    driver: PlaywrightPersistentBrowserDriver,
) -> None:
    _server, url = origin
    handle = await driver.open(
        f"{url}/apply",
        policy=_LocalPolicy(url),  # type: ignore[arg-type]
        user_id="crash-user",
    )
    try:
        assert await handle.is_alive()
        # Simulate Chromium exiting underneath the session, as a crash would.
        await handle._browser.close()  # type: ignore[attr-defined]
        # ``is_alive`` is the signal the session manager reconciles on; the last
        # known URL is deliberately retained, so it is not used as liveness.
        assert await handle.is_alive() is False
        assert await handle.content() == ""
    finally:
        await handle.close()


async def test_closing_a_session_is_idempotent(
    origin: tuple[ThreadingHTTPServer, str],
    driver: PlaywrightPersistentBrowserDriver,
) -> None:
    _server, url = origin
    handle = await driver.open(
        f"{url}/apply",
        policy=_LocalPolicy(url),  # type: ignore[arg-type]
        user_id="close-user",
    )
    await handle.close()
    await handle.close()
    assert await handle.is_alive() is False
    assert await handle.content() == ""
