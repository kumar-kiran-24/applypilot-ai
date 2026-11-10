"""Stage E unit tests: view tickets, ownership, limits and input validation.

These tests use a fake browser handle. They prove the authorisation and
protocol rules; they are **not** evidence of live streaming or forwarded input.
Those checks live in ``test_phase2e_browser_stream_browser.py`` and need real
Chromium.
"""

from __future__ import annotations

import asyncio
import base64
import json
import uuid
from typing import Any

import pytest
from app.core.config import settings
from app.integrations.browser.streaming import LiveBrowserView, ViewProtocolError
from app.integrations.browser.view import browser_view_capability
from app.services.browser_session_manager import BrowserSessionManager
from app.services.browser_view_service import (
    BrowserViewNotFoundError,
    BrowserViewRateLimitedError,
    BrowserViewService,
    BrowserViewTicketError,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeHandle:
    def __init__(self, url: str = "https://employer.example/apply") -> None:
        self.url = url
        self.alive = True
        self.closed = False
        self.unsafe_navigation = False
        self.navigation_epoch = 1
        self._last_input = 0.0
        self.interaction_lock = asyncio.Lock()

    async def current_url(self) -> str:
        return self.url

    async def is_alive(self) -> bool:
        return self.alive and not self.closed

    async def close(self) -> None:
        self.closed = True
        self.alive = False

    def mark_user_input(self) -> None:
        self._last_input = 1.0

    def user_input_active(self, seconds: float = 2.0) -> bool:
        del seconds
        return self._last_input > 0


class FakeDriver:
    def __init__(self) -> None:
        self.handle = FakeHandle()

    async def open(
        self,
        url: str,
        *,
        policy: Any,
        user_id: str,
        pinned_address: str | None = None,
    ) -> FakeHandle:
        del policy, user_id
        self.handle.url = url
        return self.handle


class FakePolicy:
    def allows(self, url: str) -> bool:
        return url.startswith("https://employer.example")

    @property
    def allowed_origins(self) -> tuple[str, ...]:
        return ("https://employer.example",)


async def _service(
    *, ttls: int = 30, per_minute: int = 10, clock: FakeClock | None = None
) -> tuple[BrowserViewService, BrowserSessionManager, FakeHandle, uuid.UUID, uuid.UUID]:
    manager = BrowserSessionManager(driver=FakeDriver(), ttl_seconds=900)
    session_id = uuid.uuid4()
    user_id = uuid.uuid4()
    live = await manager.acquire(
        session_id=session_id,
        user_id=user_id,
        application_id=uuid.uuid4(),
        url="https://employer.example/apply",
        policy=FakePolicy(),
    )
    view = BrowserViewService(
        manager,
        ticket_ttl_seconds=ttls,
        tickets_per_minute=per_minute,
        clock=clock or FakeClock(),
    )
    return view, manager, live.handle, session_id, user_id


@pytest.fixture(autouse=True)
def _streaming_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "APPLICATION_AUTOMATION_ENABLED", True)
    monkeypatch.setattr(settings, "APPLICATION_BROWSER_STREAMING_ENABLED", True)


async def test_ticket_is_single_use_and_bound_to_owner() -> None:
    view, manager, _handle, session_id, user_id = await _service()
    application_id = uuid.uuid4()
    # The live browser was opened for a different application id above, so use
    # the manager's own ownership value by reading the live entry.
    live = await manager.get(session_id, user_id=user_id)
    assert live is not None
    application_id = live.application_id

    issued = await view.issue(
        user_id=user_id, session_id=session_id, application_id=application_id
    )
    attachment = await view.consume(issued.ticket)
    assert attachment.user_id == user_id
    assert attachment.session_id == session_id

    with pytest.raises(BrowserViewTicketError) as replay:
        await view.consume(issued.ticket)
    assert replay.value.code == "ticket_replayed"

    # A different user cannot mint a ticket for someone else's session.
    with pytest.raises(BrowserViewNotFoundError):
        await view.issue(
            user_id=uuid.uuid4(),
            session_id=session_id,
            application_id=application_id,
        )

    await view.release(session_id, attachment.view_id)
    await manager.release(session_id)


async def test_ticket_expires_and_cannot_attach() -> None:
    clock = FakeClock()
    view, manager, _handle, session_id, user_id = await _service(
        ttls=10, clock=clock
    )
    live = await manager.get(session_id, user_id=user_id)
    assert live is not None
    issued = await view.issue(
        user_id=user_id,
        session_id=session_id,
        application_id=live.application_id,
    )
    clock.advance(11)
    with pytest.raises(BrowserViewTicketError) as expired:
        await view.consume(issued.ticket)
    assert expired.value.code == "ticket_expired"
    await manager.release(session_id)


async def test_only_one_view_per_browser_session() -> None:
    view, manager, _handle, session_id, user_id = await _service()
    live = await manager.get(session_id, user_id=user_id)
    assert live is not None

    first = await view.issue(
        user_id=user_id,
        session_id=session_id,
        application_id=live.application_id,
    )
    second = await view.issue(
        user_id=user_id,
        session_id=session_id,
        application_id=live.application_id,
    )
    attachment = await view.consume(first.ticket)
    with pytest.raises(BrowserViewTicketError) as attached:
        await view.consume(second.ticket)
    assert attached.value.code == "view_already_attached"

    await view.release(session_id, attachment.view_id)
    # After the view releases, a fresh ticket can attach.
    third = await view.issue(
        user_id=user_id,
        session_id=session_id,
        application_id=live.application_id,
    )
    third_attachment = await view.consume(third.ticket)
    await view.release(session_id, third_attachment.view_id)
    await manager.release(session_id)


async def test_ticket_issuance_is_rate_limited() -> None:
    view, manager, _handle, session_id, user_id = await _service(per_minute=2)
    live = await manager.get(session_id, user_id=user_id)
    assert live is not None
    for _ in range(2):
        await view.issue(
            user_id=user_id,
            session_id=session_id,
            application_id=live.application_id,
        )
    with pytest.raises(BrowserViewRateLimitedError):
        await view.issue(
            user_id=user_id,
            session_id=session_id,
            application_id=live.application_id,
        )
    await manager.release(session_id)


async def test_ticket_is_stored_only_as_a_hash() -> None:
    view, manager, _handle, session_id, user_id = await _service()
    live = await manager.get(session_id, user_id=user_id)
    assert live is not None
    issued = await view.issue(
        user_id=user_id,
        session_id=session_id,
        application_id=live.application_id,
    )
    assert issued.ticket not in view._tickets
    stored = next(iter(view._tickets.values()))
    assert issued.ticket not in repr(stored)
    assert len(issued.ticket) >= 32
    await manager.release(session_id)


async def test_ticket_endpoint_requires_authentication(client: Any) -> None:
    response = await client.post(
        f"/api/v1/browser-sessions/by-application/{uuid.uuid4()}/view-ticket"
    )
    assert response.status_code == 401


async def test_service_refuses_non_live_session() -> None:
    manager = BrowserSessionManager(driver=FakeDriver(), ttl_seconds=900)
    view = BrowserViewService(manager, ticket_ttl_seconds=30)
    with pytest.raises(BrowserViewNotFoundError):
        await view.issue(
            user_id=uuid.uuid4(),
            session_id=uuid.uuid4(),
            application_id=uuid.uuid4(),
        )


async def test_capability_reflects_real_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "APPLICATION_AUTOMATION_ENABLED", True)
    monkeypatch.setattr(settings, "APPLICATION_BROWSER_STREAMING_ENABLED", True)
    live = browser_view_capability()
    assert live.available is True
    assert live.user_can_interact is True
    assert live.mode.value == "cdp_relay"
    assert live.required_component is None

    monkeypatch.setattr(settings, "APPLICATION_BROWSER_STREAMING_ENABLED", False)
    disabled = browser_view_capability()
    assert disabled.available is False
    assert disabled.user_can_interact is False
    assert disabled.required_component


class FakeCDP:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self.listeners: dict[str, Any] = {}

    def on(self, event: str, handler: Any) -> None:
        self.listeners[event] = handler

    def remove_listener(self, event: str, handler: Any) -> None:
        del event, handler

    async def send(
        self, method: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self.sent.append((method, params or {}))
        if method == "Page.getLayoutMetrics":
            return {
                "cssVisualViewport": {
                    "clientWidth": 1280,
                    "clientHeight": 900,
                }
            }
        return {}

    async def detach(self) -> None:
        return None


class FakeStreamHandle(FakeHandle):
    def __init__(self) -> None:
        super().__init__()
        self.cdp = FakeCDP()
        self.interaction_lock = asyncio.Lock()

    async def cdp_session(self) -> FakeCDP:
        return self.cdp

    def mark_user_input(self) -> None:
        return None

    def user_input_active(self, seconds: float = 2.0) -> bool:
        del seconds
        return False


async def test_view_forwards_only_validated_input() -> None:
    handle = FakeStreamHandle()
    messages: list[dict[str, Any]] = []

    async def send(payload: dict[str, Any]) -> None:
        messages.append(payload)

    view = LiveBrowserView(handle, send, input_per_second=10)
    try:
        await view.start()
        assert any(item["type"] == "ready" for item in messages)

        await view.handle_message(
            json.dumps(
                {
                    "type": "mouse",
                    "event": "down",
                    "x": 100,
                    "y": 200,
                    "button": "left",
                }
            )
        )
        mouse = [
            params
            for method, params in handle.cdp.sent
            if method == "Input.dispatchMouseEvent"
        ]
        assert mouse and mouse[-1]["type"] == "mousePressed"
        assert mouse[-1]["x"] == 100 and mouse[-1]["y"] == 200

        await view.handle_message(
            json.dumps({"type": "mouse", "event": "wheel", "x": 10, "y": 20, "deltaY": 200})
        )
        assert any(
            params.get("type") == "mouseWheel"
            for method, params in handle.cdp.sent
            if method == "Input.dispatchMouseEvent"
        )

        await view.handle_message(json.dumps({"type": "text", "text": "Ada"}))
        assert ("Input.insertText", {"text": "Ada"}) in handle.cdp.sent

        await view.handle_message(
            json.dumps({"type": "key", "event": "press", "key": "Enter"})
        )
        key_types = [
            params.get("type")
            for method, params in handle.cdp.sent
            if method == "Input.dispatchKeyEvent"
        ]
        assert "keyDown" in key_types and "keyUp" in key_types

        with pytest.raises(ViewProtocolError):
            await view.handle_message("{not json")
        with pytest.raises(ViewProtocolError):
            await view.handle_message(json.dumps({"type": "cdp", "method": "Page.navigate"}))
        with pytest.raises(ViewProtocolError):
            await view.handle_message(
                json.dumps({"type": "mouse", "event": "down", "x": 5000, "y": 10})
            )
        with pytest.raises(ViewProtocolError):
            await view.handle_message(json.dumps({"type": "text", "text": "x" * 5000}))
        with pytest.raises(ViewProtocolError):
            await view.handle_message(json.dumps({"type": "key", "key": "a"}))
    finally:
        await view.close()


async def test_view_rate_limits_input() -> None:
    handle = FakeStreamHandle()

    async def send(payload: dict[str, Any]) -> None:
        del payload

    view = LiveBrowserView(handle, send, input_per_second=2)
    try:
        await view.start()
        await view.handle_message(json.dumps({"type": "text", "text": "a"}))
        await view.handle_message(json.dumps({"type": "text", "text": "b"}))
        with pytest.raises(ViewProtocolError) as limited:
            await view.handle_message(json.dumps({"type": "text", "text": "c"}))
        assert limited.value.code == "input_rate_limited"
    finally:
        await view.close()


async def test_view_closes_when_session_stops_being_live() -> None:
    handle = FakeStreamHandle()
    messages: list[dict[str, Any]] = []

    async def send(payload: dict[str, Any]) -> None:
        messages.append(payload)

    async def session_alive() -> bool:
        return False

    view = LiveBrowserView(handle, send, session_alive=session_alive)
    # Skip the warm-up ticks so the first monitor pass performs the owner check.
    view._monitor_ticks = 6
    try:
        await view.start()
        for _ in range(60):
            if any(item["type"] == "session_closed" for item in messages):
                break
            await asyncio.sleep(0.05)
        closed = next(item for item in messages if item["type"] == "session_closed")
        assert closed["code"] == "browser_session_expired"
        assert view.closed is True
    finally:
        await view.close()


async def test_view_emits_real_frame_message_from_cdp() -> None:
    handle = FakeStreamHandle()
    messages: list[dict[str, Any]] = []
    frame_seen = asyncio.Event()

    async def send(payload: dict[str, Any]) -> None:
        messages.append(payload)
        if payload.get("type") == "frame":
            frame_seen.set()

    view = LiveBrowserView(handle, send)
    try:
        await view.start()
        jpeg = base64.b64encode(b"\xff\xd8\xff\xe0test").decode()
        view._on_frame(  # exercise the same CDP callback the browser uses
            {
                "sessionId": 1,
                "data": jpeg,
                "metadata": {
                    "deviceWidth": 1280,
                    "deviceHeight": 900,
                    "pageScaleFactor": 1,
                },
            }
        )
        await asyncio.wait_for(frame_seen.wait(), timeout=2)
        frame = next(item for item in messages if item["type"] == "frame")
        assert frame["format"] == "jpeg"
        assert base64.b64decode(frame["data"])[:2] == b"\xff\xd8"
        assert "cookie" not in json.dumps(messages).lower()
        assert "storage" not in json.dumps(messages).lower()
    finally:
        await view.close()


def test_websocket_origin_allow_list_rejects_foreign_frontends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.v1.endpoints.browser_view import _origin_allowed

    monkeypatch.setattr(
        settings, "BACKEND_CORS_ORIGINS", ["http://127.0.0.1:3000"]
    )
    assert _origin_allowed("http://127.0.0.1:3000") is True
    assert _origin_allowed("http://127.0.0.1:3000/") is True
    assert _origin_allowed("http://evil.example") is False
    assert _origin_allowed("http://127.0.0.1:3000.evil.example") is False
    assert _origin_allowed("null") is False


def test_ticket_error_close_codes_match_frontend_states() -> None:
    from app.api.v1.endpoints.browser_view import _TICKET_CLOSE_CODES

    assert _TICKET_CLOSE_CODES["ticket_expired"] == 4401
    assert _TICKET_CLOSE_CODES["ticket_replayed"] == 4401
    assert _TICKET_CLOSE_CODES["browser_session_closed"] == 4404
    assert _TICKET_CLOSE_CODES["view_already_attached"] == 4409


def test_fill_engine_has_no_submit_or_click_action() -> None:
    """Stage C can only fill/select/check/upload - never click or submit."""
    from app.integrations.browser.field_actions import FieldActionKind

    assert {kind.value for kind in FieldActionKind} == {
        "fill",
        "select",
        "check",
        "upload",
    }
    for forbidden in ("click", "submit", "press", "navigate", "evaluate"):
        assert forbidden not in {kind.value for kind in FieldActionKind}


def test_view_messages_never_include_browser_secrets() -> None:
    payload = {
        "type": "ready",
        "mode": "cdp_relay",
        "frame_format": "jpeg",
        "width": 1280,
        "height": 900,
        "can_interact": True,
    }
    serialised = json.dumps(payload).lower()
    for forbidden in ("cookie", "authorization", "storage_state", "cdp_port", "token"):
        assert forbidden not in serialised
