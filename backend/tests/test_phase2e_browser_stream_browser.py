"""Stage E real integration test: streaming and input on a real Chromium page.

This starts the actual FastAPI app on a loopback port, attaches a real CDP
screencast to the existing persistent Playwright page, and exchanges JSON over a
real WebSocket. It proves:

* a real JPEG frame arrives from the backend page;
* mouse clicks, text input and wheel scrolling reach that page;
* navigation is reported and invalidates the analysis epoch;
* single-use tickets are rejected on replay;
* a non-owner cannot mint a ticket;
* closing/releasing the browser closes the live view and cleans up resources.

It is skipped unless ``APPLYPILOT_RUN_BROWSER_TESTS=1``. It is not a mock test.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

if os.environ.get("APPLYPILOT_RUN_BROWSER_TESTS") != "1":
    pytest.skip(
        "Set APPLYPILOT_RUN_BROWSER_TESTS=1 to launch the real Chromium streaming test.",
        allow_module_level=True,
    )

import uvicorn
import websockets
from app.core.config import settings
from app.integrations.browser.field_actions import FieldAction, FieldActionKind
from app.integrations.browser.persistent import PlaywrightPersistentBrowserDriver
from app.main import app
from app.services.application_field_fill_service import _coordination_pause
from app.services.browser_session_manager import (
    BrowserSessionManager,
    set_browser_session_manager,
)
from app.services.browser_view_service import (
    BrowserViewNotFoundError,
    BrowserViewService,
    set_browser_view_service,
)
from websockets.exceptions import ConnectionClosed, InvalidStatus

pytestmark = pytest.mark.browser

FORM_HTML = b"""<!doctype html>
<html><body>
<h1>Controlled streaming form</h1>
<button id="press" onclick="document.getElementById('outcome').textContent='clicked'"
  style="margin:20px;width:160px;height:50px">Press me</button>
<label for="name">Full name</label>
<input id="name" style="margin:20px;width:320px;height:38px">
<div id="outcome" style="margin:20px">idle</div>
<a id="next" href="/second" style="margin:20px">Go to second page</a>
<div style="height:3000px"></div>
</body></html>
"""

SECOND_HTML = b"""<!doctype html>
<html><body><h1>Second employer page</h1>
<label for="city">City</label><input id="city"></body></html>
"""


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = SECOND_HTML if self.path == "/second" else FORM_HTML
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


class _Policy:
    def __init__(self, origin: str) -> None:
        self.origin = origin

    def allows(self, url: str) -> bool:
        return url.startswith(self.origin)

    @property
    def allowed_origins(self) -> tuple[str, ...]:
        return (self.origin,)


def _start_form_server() -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


async def _start_api(port_holder: list[int]) -> tuple[uvicorn.Server, asyncio.Task[None]]:
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=0,
        log_level="warning",
        lifespan="off",
    )
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
    task = asyncio.create_task(server.serve())
    for _ in range(200):
        if server.started and server.servers:
            break
        await asyncio.sleep(0.025)
    assert server.started and server.servers
    port_holder.append(server.servers[0].sockets[0].getsockname()[1])
    return server, task


async def _recv_until(
    socket: Any, kind: str, *, timeout: float = 15.0
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    seen: list[dict[str, Any]] = []
    while loop.time() < deadline:
        raw = await asyncio.wait_for(socket.recv(), timeout=deadline - loop.time())
        message = json.loads(raw)
        seen.append(message)
        if message.get("type") == kind:
            return message, seen
    raise AssertionError(f"No {kind!r} message arrived within {timeout}s")


def _ws_url(port: int, ticket: str) -> str:
    return f"ws://127.0.0.1:{port}/api/v1/browser-sessions/view/ws?ticket={ticket}"


async def test_real_chromium_stream_frames_input_navigation_and_ticket_rules() -> None:
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(settings, "APPLICATION_AUTOMATION_ENABLED", True)
    monkeypatch.setattr(settings, "APPLICATION_BROWSER_HEADLESS", True)
    monkeypatch.setattr(settings, "APPLICATION_BROWSER_STREAMING_ENABLED", True)

    form_server, origin = _start_form_server()
    manager = BrowserSessionManager(
        driver=PlaywrightPersistentBrowserDriver(settings), ttl_seconds=900
    )
    set_browser_session_manager(manager)
    view_service = BrowserViewService(manager, ticket_ttl_seconds=45, tickets_per_minute=30)
    set_browser_view_service(view_service)
    ports: list[int] = []
    api_server: uvicorn.Server | None = None
    api_task: asyncio.Task[None] | None = None
    handle = None
    try:
        application_id = uuid.uuid4()
        session_id = uuid.uuid4()
        user_id = uuid.uuid4()
        live = await manager.acquire(
            session_id=session_id,
            user_id=user_id,
            application_id=application_id,
            url=f"{origin}/",
            policy=_Policy(origin),  # type: ignore[arg-type]
        )
        handle = live.handle
        assert await handle.is_alive()

        # Another user cannot mint a view for this browser session.
        with pytest.raises(BrowserViewNotFoundError):
            await view_service.issue(
                user_id=uuid.uuid4(),
                session_id=session_id,
                application_id=application_id,
            )

        issued = await view_service.issue(
            user_id=user_id,
            session_id=session_id,
            application_id=application_id,
        )
        assert issued.ticket
        assert "9222" not in issued.ticket

        api_server, api_task = await _start_api(ports)
        port = ports[0]

        # A foreign web origin is refused at the handshake, before the ticket is
        # consumed, so the same ticket remains usable for the owning user.
        with pytest.raises(InvalidStatus) as blocked:
            async with websockets.connect(
                _ws_url(port, issued.ticket), origin="http://evil.example"
            ):
                pass  # pragma: no cover - the handshake cannot succeed
        assert blocked.value.response.status_code == 403

        async with websockets.connect(
            _ws_url(port, issued.ticket),
            origin="http://127.0.0.1:3000",
            max_size=4 * 1024 * 1024,
        ) as socket:
            ready, seen = await _recv_until(socket, "ready")
            assert ready["width"] == 1280 and ready["height"] == 900
            assert ready["can_interact"] is True
            assert ready["mode"] == "cdp_relay"

            frame, frame_messages = await _recv_until(socket, "frame")
            raw = base64.b64decode(frame["data"])
            assert raw[:2] == b"\xff\xd8"  # real JPEG produced by Chromium
            assert len(raw) > 1000
            assert frame["width"] == 1280

            # Mouse click reaches the same page.
            box = await handle._page.locator("#press").bounding_box()  # type: ignore[attr-defined]
            assert box is not None
            x = box["x"] + box["width"] / 2
            y = box["y"] + box["height"] / 2
            await socket.send(json.dumps({"type": "mouse", "event": "down", "x": x, "y": y}))
            await socket.send(json.dumps({"type": "mouse", "event": "up", "x": x, "y": y}))
            await asyncio.sleep(0.25)
            assert (
                await handle._page.locator("#outcome").inner_text()  # type: ignore[attr-defined]
                == "clicked"
            )

            # Keyboard/text input reaches the focused page field.
            await handle._page.locator("#name").click()  # type: ignore[attr-defined]
            await socket.send(json.dumps({"type": "text", "text": "Ada Lovelace"}))
            await asyncio.sleep(0.2)
            assert (
                await handle._page.locator("#name").input_value()  # type: ignore[attr-defined]
                == "Ada Lovelace"
            )
            # A forwarded editing key reaches the same focused field.
            await socket.send(json.dumps({"type": "key", "event": "press", "key": "Backspace"}))
            await asyncio.sleep(0.2)
            assert (
                await handle._page.locator("#name").input_value()  # type: ignore[attr-defined]
                == "Ada Lovelac"
            )
            # Forwarded input marks the page as user-active for automation.
            assert handle.user_input_active()

            # Stage C must not overwrite a value the user just typed.
            refused = await handle.apply_field_action(
                FieldAction(
                    kind=FieldActionKind.FILL,
                    field_id="name",
                    label="Full name",
                    html_id="name",
                    name="name",
                    text="Someone Else",
                )
            )
            assert refused.ok is False
            assert (
                await handle._page.locator("#name").input_value()  # type: ignore[attr-defined]
                == "Ada Lovelac"
            )

            # Wheel scrolling reaches the page.
            await socket.send(
                json.dumps({"type": "mouse", "event": "wheel", "x": 100, "y": 100, "deltaY": 600})
            )
            await asyncio.sleep(0.25)
            assert (
                await handle._page.evaluate("window.scrollY")  # type: ignore[attr-defined]
            ) > 0

            # Navigation is detected, reported and invalidates the old analysis.
            before_epoch = handle.navigation_epoch
            await handle._page.evaluate("window.scrollTo(0, 0)")  # type: ignore[attr-defined]
            link = await handle._page.locator("#next").bounding_box()  # type: ignore[attr-defined]
            assert link is not None
            await socket.send(
                json.dumps(
                    {
                        "type": "mouse",
                        "event": "down",
                        "x": link["x"] + 5,
                        "y": link["y"] + link["height"] / 2,
                    }
                )
            )
            await socket.send(
                json.dumps(
                    {
                        "type": "mouse",
                        "event": "up",
                        "x": link["x"] + 5,
                        "y": link["y"] + link["height"] / 2,
                    }
                )
            )
            navigated, _ = await _recv_until(socket, "navigated", timeout=10)
            assert navigated["analysis_stale"] is True
            assert navigated["url"].endswith("/second")
            assert handle.navigation_epoch > before_epoch
            assert _coordination_pause(handle, before_epoch) is not None
            assert await handle.current_url() == f"{origin}/second"

            # No browser secret is ever put on the wire.
            wire = json.dumps(seen + frame_messages).lower()
            for forbidden in ("set-cookie", "cookie:", "storage_state", "authorization"):
                assert forbidden not in wire

        # An expired ticket is refused even while the browser is still live.
        short_lived = BrowserViewService(manager, ticket_ttl_seconds=1)
        expired_ticket = await short_lived.issue(
            user_id=user_id,
            session_id=session_id,
            application_id=application_id,
        )
        await asyncio.sleep(1.1)
        set_browser_view_service(short_lived)
        try:
            async with websockets.connect(
                _ws_url(port, expired_ticket.ticket),
                origin="http://127.0.0.1:3000",
            ) as expired_socket:
                expired_error, _ = await _recv_until(expired_socket, "error")
                assert expired_error["code"] == "ticket_expired"
        finally:
            set_browser_view_service(view_service)

        # Let the transport deliver the close and release the active view.
        for _ in range(100):
            if view_service.active_views() == 0:
                break
            await asyncio.sleep(0.05)
        assert view_service.active_views() == 0

        # The single-use ticket cannot be replayed.
        async with websockets.connect(
            _ws_url(port, issued.ticket),
            origin="http://127.0.0.1:3000",
        ) as replay:
            error, _ = await _recv_until(replay, "error")
            assert error["code"] == "ticket_replayed"

        for _ in range(100):
            if view_service.active_views() == 0:
                break
            await asyncio.sleep(0.05)

        # Releasing the browser (what cancel does) closes the view and page.
        issued_two = await view_service.issue(
            user_id=user_id,
            session_id=session_id,
            application_id=application_id,
        )
        async with websockets.connect(
            _ws_url(port, issued_two.ticket),
            origin="http://127.0.0.1:3000",
        ) as expiring:
            await _recv_until(expiring, "ready")
            await manager.release(session_id)
            closed_message, _ = await _recv_until(expiring, "session_closed", timeout=10)
            assert closed_message["code"] == "browser_session_closed"
            with pytest.raises(ConnectionClosed):
                await asyncio.wait_for(expiring.recv(), timeout=5)

        assert await handle.is_alive() is False
        assert await manager.get(session_id, user_id=user_id) is None
        assert view_service.active_views() == 0
    finally:
        if api_server is not None:
            api_server.should_exit = True
        if api_task is not None:
            try:
                await asyncio.wait_for(api_task, timeout=10)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                api_task.cancel()
        await manager.close_all()
        set_browser_view_service(None)
        set_browser_session_manager(None)
        form_server.shutdown()
        form_server.server_close()
        monkeypatch.undo()


async def test_real_chromium_crash_closes_the_view_and_cleans_up() -> None:
    """A crashed browser is detected, reported and never left registered."""
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(settings, "APPLICATION_AUTOMATION_ENABLED", True)
    monkeypatch.setattr(settings, "APPLICATION_BROWSER_HEADLESS", True)
    monkeypatch.setattr(settings, "APPLICATION_BROWSER_STREAMING_ENABLED", True)

    form_server, origin = _start_form_server()
    manager = BrowserSessionManager(
        driver=PlaywrightPersistentBrowserDriver(settings), ttl_seconds=900
    )
    set_browser_session_manager(manager)
    view_service = BrowserViewService(manager, ticket_ttl_seconds=45, tickets_per_minute=30)
    set_browser_view_service(view_service)
    ports: list[int] = []
    api_server: uvicorn.Server | None = None
    api_task: asyncio.Task[None] | None = None
    try:
        application_id = uuid.uuid4()
        session_id = uuid.uuid4()
        user_id = uuid.uuid4()
        live = await manager.acquire(
            session_id=session_id,
            user_id=user_id,
            application_id=application_id,
            url=f"{origin}/",
            policy=_Policy(origin),  # type: ignore[arg-type]
        )
        handle = live.handle
        assert await handle.is_alive()

        issued = await view_service.issue(
            user_id=user_id,
            session_id=session_id,
            application_id=application_id,
        )
        api_server, api_task = await _start_api(ports)

        async with websockets.connect(
            _ws_url(ports[0], issued.ticket),
            origin="http://127.0.0.1:3000",
        ) as socket:
            await _recv_until(socket, "ready")
            await _recv_until(socket, "frame")

            # Kill the browser process underneath the session. This is not a
            # graceful close: the page/context and CDP session disappear.
            await handle._browser.close()  # type: ignore[attr-defined]

            closed_message, _ = await _recv_until(socket, "session_closed", timeout=20)
            assert closed_message["code"] == "browser_session_closed"
            with pytest.raises(ConnectionClosed):
                await asyncio.wait_for(socket.recv(), timeout=5)

        # The dead browser is never handed to a later request, and it is not
        # left in the registry or counted as live.
        assert await manager.get(session_id, user_id=user_id) is None
        assert manager.live_count() == 0
        assert await manager.sweep_expired() == 0

        for _ in range(100):
            if view_service.active_views() == 0:
                break
            await asyncio.sleep(0.05)
        assert view_service.active_views() == 0
    finally:
        if api_server is not None:
            api_server.should_exit = True
        if api_task is not None:
            try:
                await asyncio.wait_for(api_task, timeout=10)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                api_task.cancel()
        await manager.close_all()
        set_browser_view_service(None)
        set_browser_session_manager(None)
        form_server.shutdown()
        form_server.server_close()
        monkeypatch.undo()


__all__: list[Any] = []
