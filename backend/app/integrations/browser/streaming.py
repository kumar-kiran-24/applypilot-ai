"""Live CDP screencast relay for one persistent Playwright page.

The relay attaches a Chrome DevTools Protocol session to the **same** page the
Stage C/D services use. It does not launch a browser, open a second context or
copy storage state. Frames are JPEG images streamed in memory over an
authenticated WebSocket; user input is translated into a narrow allow-list of
CDP ``Input`` commands and serialised with automation through the page's
interaction lock.

Deliberately absent: a public CDP endpoint, arbitrary CDP method forwarding,
cookies, storage state, authentication headers, screenshots on disk, and any
submit/apply command. CAPTCHA, OTP, MFA and legal declarations are performed by
the user on the streamed page.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

from app.core.logging import get_logger
from app.integrations.browser.persistent import PersistentBrowserHandle

logger = get_logger(__name__)

SendJson = Callable[[dict[str, Any]], Awaitable[None]]


class ViewProtocolError(RuntimeError):
    """A forwarded view message was malformed or outside the allow-list."""

    def __init__(self, message: str, code: str = "invalid_view_message") -> None:
        self.code = code
        self.message = message
        super().__init__(message)


#: Special keys only. Printable text goes through ``Input.insertText``.
_SPECIAL_KEYS: dict[str, tuple[str, int]] = {
    "Enter": ("Enter", 13),
    "Tab": ("Tab", 9),
    "Backspace": ("Backspace", 8),
    "Escape": ("Escape", 27),
    "Delete": ("Delete", 46),
    "ArrowUp": ("ArrowUp", 38),
    "ArrowDown": ("ArrowDown", 40),
    "ArrowLeft": ("ArrowLeft", 37),
    "ArrowRight": ("ArrowRight", 39),
    "Home": ("Home", 36),
    "End": ("End", 35),
    "PageUp": ("PageUp", 33),
    "PageDown": ("PageDown", 34),
    " ": ("Space", 32),
}

_MOUSE_EVENTS = {"move", "down", "up", "wheel"}
_BUTTONS = {"left", "right", "middle"}


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ViewProtocolError(f"{name} must be a number.")
    number = float(value)
    if not math.isfinite(number):
        raise ViewProtocolError(f"{name} must be finite.")
    return number


def _safe_display_url(url: str) -> str:
    """Origin + path only; query strings/fragments can carry one-time tokens."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return ""
    return f"{parts.scheme}://{parts.netloc}{parts.path or '/'}"[:500]


class LiveBrowserView:
    """One authenticated view over an existing persistent browser page."""

    def __init__(
        self,
        handle: PersistentBrowserHandle,
        send: SendJson,
        *,
        input_per_second: int = 120,
        max_seconds: int = 3600,
        max_width: int = 1280,
        max_height: int = 900,
        quality: int = 55,
        fps: int = 25,
        session_alive: Callable[[], Awaitable[bool]] | None = None,
    ) -> None:
        self._handle = handle
        self._send = send
        # Optional owner check (session TTL/ownership); a False result closes
        # the view even while the browser process itself is still running.
        self._session_alive = session_alive
        self._monitor_ticks = 0
        self._input_per_second = input_per_second
        self._max_seconds = max_seconds
        self._max_width = max_width
        self._max_height = max_height
        self._quality = quality
        self._min_frame_interval = 1.0 / max(1, fps)
        self._cdp: Any = None
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1)
        self._closed = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._width = max_width
        self._height = max_height
        self._started = time.monotonic()
        self._last_frame_sent = 0.0
        self._input_times: deque[float] = deque()
        self._epoch = 0
        self.frames_sent = 0
        self.close_code = 1000
        self.close_reason = ""

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    async def wait_closed(self) -> None:
        await self._closed.wait()

    async def start(self) -> None:
        """Attach CDP, report the viewport, then start emitting frames."""
        self._loop = asyncio.get_running_loop()
        self._cdp = await self._handle.cdp_session()
        self._cdp.on("Page.screencastFrame", self._on_frame)
        await self._cdp.send("Page.enable")
        metrics = await self._cdp.send("Page.getLayoutMetrics")
        viewport = (
            metrics.get("cssVisualViewport")
            or metrics.get("layoutViewport")
            or {}
        )
        self._width = int(viewport.get("clientWidth") or self._max_width)
        self._height = int(viewport.get("clientHeight") or self._max_height)
        self._epoch = self._handle.navigation_epoch

        await self._send(
            {
                "type": "ready",
                "mode": "cdp_relay",
                "frame_format": "jpeg",
                "width": self._width,
                "height": self._height,
                "can_interact": True,
                "note": (
                    "This is the live employer page in your ApplyPilot browser "
                    "session. Complete CAPTCHA, OTP, MFA and legal declarations "
                    "yourself. ApplyPilot never submits the application."
                ),
            }
        )
        await self._cdp.send(
            "Page.startScreencast",
            {
                "format": "jpeg",
                "quality": self._quality,
                "maxWidth": self._max_width,
                "maxHeight": self._max_height,
                "everyNthFrame": 1,
            },
        )
        self._tasks = [
            asyncio.create_task(self._pump_frames()),
            asyncio.create_task(self._monitor()),
        ]

    # --- Frames --------------------------------------------------------------
    def _on_frame(self, params: dict[str, Any]) -> None:
        session_id = params.get("sessionId")
        if session_id is not None and self._loop is not None:
            self._loop.create_task(self._ack(session_id))
        if self._queue.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                self._queue.get_nowait()
        with contextlib.suppress(asyncio.QueueFull):
            self._queue.put_nowait(params)

    async def _ack(self, session_id: int) -> None:
        if self._cdp is None:
            return
        with contextlib.suppress(Exception):
            await self._cdp.send(
                "Page.screencastFrameAck", {"sessionId": session_id}
            )

    async def _pump_frames(self) -> None:
        while not self._closed.is_set():
            try:
                frame = await asyncio.wait_for(self._queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            now = time.monotonic()
            if now - self._last_frame_sent < self._min_frame_interval:
                continue
            data = frame.get("data")
            if not isinstance(data, str) or not data:
                continue
            metadata = frame.get("metadata") or {}
            payload = {
                "type": "frame",
                "format": "jpeg",
                "data": data,
                "width": int(metadata.get("deviceWidth") or self._width),
                "height": int(metadata.get("deviceHeight") or self._height),
                "scale": float(metadata.get("pageScaleFactor") or 1),
            }
            try:
                await self._send(payload)
            except Exception:
                await self.close()
                return
            self._last_frame_sent = now
            self.frames_sent += 1

    # --- Health, navigation and expiry ---------------------------------------
    async def _monitor(self) -> None:
        while not self._closed.is_set():
            await asyncio.sleep(0.75)
            if self._closed.is_set():
                return
            if time.monotonic() - self._started > self._max_seconds:
                await self._finish(4408, "view_timeout")
                return
            try:
                alive = await self._handle.is_alive()
            except Exception:
                alive = False
            if not alive:
                await self._finish(4404, "browser_session_closed")
                return
            # Re-check ownership/TTL roughly every 5s; the browser process can
            # outlive the persisted session's expiry without any other API call.
            self._monitor_ticks += 1
            if self._session_alive is not None and self._monitor_ticks % 7 == 0:
                try:
                    still_owned = await self._session_alive()
                except Exception:
                    still_owned = False
                if not still_owned:
                    await self._finish(4404, "browser_session_expired")
                    return
            if self._handle.unsafe_navigation:
                await self._finish(4403, "navigation_blocked")
                return
            epoch = self._handle.navigation_epoch
            if epoch != self._epoch:
                self._epoch = epoch
                try:
                    url = _safe_display_url(await self._handle.current_url())
                except Exception:
                    url = ""
                await self._send(
                    {
                        "type": "navigated",
                        "url": url,
                        "analysis_stale": True,
                        "message": (
                            "The employer's page moved to a new URL. The stored "
                            "form analysis is stale; re-analyze the form before "
                            "resuming automation."
                        ),
                    }
                )

    async def _finish(self, code: int, reason: str) -> None:
        if self._closed.is_set():
            return
        self.close_code = code
        self.close_reason = reason
        with contextlib.suppress(Exception):
            await self._send(
                {
                    "type": "session_closed",
                    "code": reason,
                    "message": (
                        "The live browser view closed. The browser session may "
                        "have expired, been cancelled, or reached its view limit."
                    ),
                }
            )
        await self.close()

    # --- Input ---------------------------------------------------------------
    async def handle_message(self, raw: str) -> None:
        if self.closed:
            raise ViewProtocolError("The view is closed.", "view_closed")
        if len(raw) > 8192:
            raise ViewProtocolError("The message is too large.", "message_too_large")
        try:
            message = json.loads(raw)
        except (TypeError, ValueError):
            raise ViewProtocolError("The message is not valid JSON.") from None
        if not isinstance(message, dict):
            raise ViewProtocolError("The message must be an object.")

        kind = message.get("type")
        if kind == "ping":
            await self._send({"type": "pong"})
            return
        if kind == "mouse":
            await self._mouse(message)
            return
        if kind == "key":
            await self._key(message)
            return
        if kind == "text":
            await self._text(message)
            return
        raise ViewProtocolError("Unsupported view message type.")

    def _record_input(self) -> None:
        now = time.monotonic()
        while self._input_times and now - self._input_times[0] > 1.0:
            self._input_times.popleft()
        if len(self._input_times) >= self._input_per_second:
            raise ViewProtocolError(
                "Too many input events. Slow down.", "input_rate_limited"
            )
        self._input_times.append(now)

    async def _dispatch(self, method: str, params: dict[str, Any]) -> None:
        if self._cdp is None or self.closed:
            raise ViewProtocolError("The view is closed.", "view_closed")
        async with self._handle.interaction_lock:
            await self._cdp.send(method, params)
        self._handle.mark_user_input()
        self._record_input()

    def _point(self, message: dict[str, Any]) -> tuple[float, float]:
        x = _finite(message.get("x"), "x")
        y = _finite(message.get("y"), "y")
        if not (0.0 <= x <= self._width and 0.0 <= y <= self._height):
            raise ViewProtocolError("The pointer is outside the page viewport.")
        return x, y

    async def _mouse(self, message: dict[str, Any]) -> None:
        kind = message.get("event")
        if kind not in _MOUSE_EVENTS:
            raise ViewProtocolError("Unsupported mouse event.")
        x, y = self._point(message)
        modifiers = int(message.get("modifiers") or 0)
        if not 0 <= modifiers <= 15:
            raise ViewProtocolError("Invalid modifier state.")

        if kind == "move":
            await self._dispatch(
                "Input.dispatchMouseEvent",
                {
                    "type": "mouseMoved",
                    "x": x,
                    "y": y,
                    "button": "none",
                    "modifiers": modifiers,
                },
            )
            return
        if kind == "wheel":
            delta_x = _finite(message.get("deltaX", 0), "deltaX")
            delta_y = _finite(message.get("deltaY", 0), "deltaY")
            if abs(delta_x) > 4000 or abs(delta_y) > 4000:
                raise ViewProtocolError("The scroll delta is too large.")
            await self._dispatch(
                "Input.dispatchMouseEvent",
                {
                    "type": "mouseWheel",
                    "x": x,
                    "y": y,
                    "deltaX": delta_x,
                    "deltaY": delta_y,
                    "modifiers": modifiers,
                },
            )
            return

        button = message.get("button", "left")
        if button not in _BUTTONS:
            raise ViewProtocolError("Unsupported mouse button.")
        click_count = int(message.get("clickCount") or 1)
        if not 1 <= click_count <= 3:
            raise ViewProtocolError("Invalid click count.")
        await self._dispatch(
            "Input.dispatchMouseEvent",
            {
                "type": "mousePressed" if kind == "down" else "mouseReleased",
                "x": x,
                "y": y,
                "button": button,
                "buttons": (
                    {"left": 1, "right": 2, "middle": 4}[button]
                    if kind == "down"
                    else 0
                ),
                "clickCount": click_count,
                "modifiers": modifiers,
            },
        )

    async def _key(self, message: dict[str, Any]) -> None:
        key = message.get("key")
        if key not in _SPECIAL_KEYS:
            raise ViewProtocolError("Only navigation and editing keys are allowed.")
        code, virtual_key = _SPECIAL_KEYS[str(key)]
        phase = message.get("event", "press")
        if phase not in {"press", "down", "up"}:
            raise ViewProtocolError("Unsupported key phase.")
        modifiers = int(message.get("modifiers") or 0)
        if not 0 <= modifiers <= 15:
            raise ViewProtocolError("Invalid modifier state.")
        params: dict[str, Any] = {
            "key": str(key),
            "code": code,
            "windowsVirtualKeyCode": virtual_key,
            "nativeVirtualKeyCode": virtual_key,
            "modifiers": modifiers,
        }
        if phase in {"press", "down"}:
            down = dict(params, type="keyDown")
            if key == "Enter":
                down["text"] = "\r"
            await self._dispatch("Input.dispatchKeyEvent", down)
        if phase in {"press", "up"}:
            await self._dispatch("Input.dispatchKeyEvent", dict(params, type="keyUp"))

    async def _text(self, message: dict[str, Any]) -> None:
        text = message.get("text")
        if not isinstance(text, str) or not 1 <= len(text) <= 2000:
            raise ViewProtocolError("Text input must be 1-2000 characters.")
        if any(ord(char) < 32 and char not in "\n\t" for char in text):
            raise ViewProtocolError("Control characters are not allowed.")
        await self._dispatch("Input.insertText", {"text": text})

    # --- Teardown ------------------------------------------------------------
    async def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        current = asyncio.current_task()
        for task in self._tasks:
            if task is not current:
                task.cancel()
        for task in self._tasks:
            if task is current:
                continue
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks.clear()
        if self._cdp is not None:
            with contextlib.suppress(Exception):
                await self._cdp.send("Page.stopScreencast")
            with contextlib.suppress(Exception):
                self._cdp.remove_listener("Page.screencastFrame", self._on_frame)
            # The CDP session is owned by the persistent handle and reused by a
            # later view; only the screencast/listener is torn down here.
            self._cdp = None
        self._queue = asyncio.Queue(maxsize=1)


__all__ = ["LiveBrowserView", "ViewProtocolError"]
