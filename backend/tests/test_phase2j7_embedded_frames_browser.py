"""Phase 2J.7: real-Chromium evidence for embedded third-party frames.

Live validation against Greenhouse and Lever showed the navigation guard treating
a CAPTCHA widget's **subframe** navigation as an escape attempt. That marked the
session unsafe and released the browser, so the next form analysis failed with
``browser_session_expired`` on a perfectly normal employment form.

These tests pin the corrected boundary in a real browser:

* an embedded cross-origin frame (the CAPTCHA/consent-widget shape) loads and the
  session stays healthy;
* a top-level navigation to the same unapproved origin is still refused and still
  marks the session unsafe, so the SSRF/navigation boundary is unchanged.

Run explicitly:

    APPLYPILOT_RUN_BROWSER_TESTS=1 python -m pytest \
        tests/test_phase2j7_embedded_frames_browser.py -m browser -q
"""

from __future__ import annotations

import asyncio
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from playwright.async_api import Error as PlaywrightError

if os.environ.get("APPLYPILOT_RUN_BROWSER_TESTS") != "1":
    pytest.skip(
        "Set APPLYPILOT_RUN_BROWSER_TESTS=1 to run the real Chromium test.",
        allow_module_level=True,
    )

from app.core.config import settings
from app.integrations.browser.persistent import PlaywrightPersistentBrowserDriver

pytestmark = pytest.mark.browser

WIDGET_HTML = """
<!doctype html><html><body>
<h1>Third-party widget</h1>
<p id="widget">challenge-widget-loaded</p>
</body></html>
"""

FORM_HTML = """
<!doctype html><html><body>
<form id="application">
  <label for="name">Full name</label>
  <input id="name" name="full_name" type="text" required>
</form>
<iframe id="widget" src="{widget_url}" width="300" height="80"></iframe>
</body></html>
"""


class _LocalPolicy:
    """Allow exactly one loopback origin, like the real session policy."""

    redirect_flow = False

    def __init__(self, origin: str) -> None:
        self.origin = origin

    def allows(self, url: str) -> bool:
        return url.startswith(self.origin)

    @property
    def allowed_origins(self) -> tuple[str, ...]:
        return (self.origin,)


class _Handler(BaseHTTPRequestHandler):
    form_template = ""
    widget_url = ""

    def do_GET(self) -> None:  # BaseHTTPRequestHandler API
        if self.path.startswith("/widget"):
            body = WIDGET_HTML.encode("utf-8")
        else:
            body = self.form_template.format(widget_url=self.widget_url).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def _serve() -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


@pytest.fixture()
def origins() -> tuple[str, str]:
    """Two loopback origins: the employer page and the embedded widget."""
    widget_server, widget_origin = _serve()
    _Handler.widget_url = f"{widget_origin}/widget"
    _Handler.form_template = FORM_HTML
    form_server, form_origin = _serve()
    try:
        yield form_origin, widget_origin
    finally:
        for server in (form_server, widget_server):
            server.shutdown()
            server.server_close()


@pytest.fixture()
def driver(monkeypatch: pytest.MonkeyPatch) -> PlaywrightPersistentBrowserDriver:
    monkeypatch.setattr(settings, "APPLICATION_AUTOMATION_ENABLED", True)
    monkeypatch.setattr(settings, "APPLICATION_BROWSER_HEADLESS", True)
    return PlaywrightPersistentBrowserDriver(settings)


async def test_an_embedded_cross_origin_frame_keeps_the_session_healthy(
    origins: tuple[str, str],
    driver: PlaywrightPersistentBrowserDriver,
) -> None:
    form_origin, widget_origin = origins
    handle = await driver.open(
        f"{form_origin}/apply",
        policy=_LocalPolicy(form_origin),  # type: ignore[arg-type]
        user_id="embedded-frame-user",
    )
    try:
        for _ in range(60):
            if len(await handle.content()) and "widget" in await handle.content():
                break
            await asyncio.sleep(0.1)

        # The third-party frame really loaded (a different origin than the page).
        page = handle._page  # type: ignore[attr-defined]
        frames = [frame.url for frame in page.frames]
        assert any(frame.startswith(widget_origin) for frame in frames), frames
        assert widget_origin != form_origin

        # ...and the session is still considered safe and alive.
        assert handle.unsafe_navigation is False
        assert await handle.is_alive() is True
        html = await handle.content()
        assert 'name="full_name"' in html

        # The embedded frame is still not addressable as an approved destination.
        assert _LocalPolicy(form_origin).allows(f"{widget_origin}/widget") is False
    finally:
        await handle.close()


async def test_a_top_level_navigation_to_the_same_origin_is_still_blocked(
    origins: tuple[str, str],
    driver: PlaywrightPersistentBrowserDriver,
) -> None:
    form_origin, widget_origin = origins
    handle = await driver.open(
        f"{form_origin}/apply",
        policy=_LocalPolicy(form_origin),  # type: ignore[arg-type]
        user_id="top-level-escape-user",
    )
    try:
        page = handle._page  # type: ignore[attr-defined]
        with pytest.raises(PlaywrightError):
            await page.goto(f"{widget_origin}/widget", timeout=10_000)
        await asyncio.sleep(0.2)
        assert handle.unsafe_navigation is True
        # The refused destination was never loaded (Chromium shows its own error
        # document instead), so the unapproved origin is never reached.
        assert not page.url.startswith(widget_origin)
        assert await handle.is_alive() is True
    finally:
        await handle.close()
