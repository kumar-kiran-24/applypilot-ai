"""In-memory registry of live browser sessions.

The durable execution record is :class:`~app.models.browser_session.BrowserSession`.
The browser object itself cannot be persisted, so it lives here, keyed by the
session id, and never leaves the process:

* a session is owned by exactly one user and one application;
* a second live session for the same id is refused (duplicate execution
  prevention), while the service layer makes repeated starts idempotent;
* sessions expire after a short TTL and are closed, never silently reused;
* shutdown closes every browser.

Tests inject a fake :class:`PersistentBrowserDriver`, so no real browser is
launched in the automated suite.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from app.core.config import Settings, settings
from app.core.logging import get_logger
from app.core.metrics import record_counter
from app.integrations.browser.persistent import (
    PersistentBrowserDriver,
    PersistentBrowserHandle,
    PlaywrightPersistentBrowserDriver,
)
from app.services.application_url_security import NavigationPolicy

logger = get_logger(__name__)


class BrowserSessionConflictError(RuntimeError):
    """A live browser already exists for this session id."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class BrowserCapacityError(RuntimeError):
    """The process-wide live-browser ceiling has been reached.

    Each Chromium costs several hundred MB, so refusing a new session is safer
    than letting the host run out of memory and kill every browser at once.
    """

    def __init__(self, message: str = "Live browser capacity is exhausted.") -> None:
        self.message = message
        super().__init__(message)


@dataclass
class LiveBrowserSession:
    """One live handle plus its ownership and expiry metadata."""

    session_id: uuid.UUID
    user_id: uuid.UUID
    application_id: uuid.UUID
    handle: PersistentBrowserHandle
    expires_at_monotonic: float
    policy: NavigationPolicy


@dataclass
class BrowserSessionManager:
    """Process-local owner of live browser handles."""

    driver: PersistentBrowserDriver
    ttl_seconds: int = 900
    max_sessions: int = 8
    clock: Callable[[], float] = field(default=time.monotonic)
    _sessions: dict[uuid.UUID, LiveBrowserSession] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def acquire(
        self,
        *,
        session_id: uuid.UUID,
        user_id: uuid.UUID,
        application_id: uuid.UUID,
        url: str,
        policy: NavigationPolicy,
        pinned_address: str | None = None,
    ) -> LiveBrowserSession:
        """Open a browser for this session, unless one is already live."""
        async with self._lock:
            existing = self._sessions.get(session_id)
            if existing is not None and await self._is_usable(existing):
                raise BrowserSessionConflictError(
                    "A live browser session already exists for this application."
                )
            if existing is not None:
                await self._drop(existing)
                self._sessions.pop(session_id, None)
            # Capacity is checked under the lock so two simultaneous starts
            # cannot both pass the last slot.
            if len(self._sessions) >= self.max_sessions:
                record_counter(
                    "applypilot_browser_launch_failures_total", reason="capacity"
                )
                raise BrowserCapacityError(
                    "The server has reached its live browser capacity. Try again "
                    "when an open session has finished."
                )

        # Launch outside the registry lock so a slow browser start does not
        # block other users' status calls.
        try:
            handle = await self.driver.open(
                url,
                policy=policy,
                user_id=str(user_id),
                pinned_address=pinned_address,
            )
        except Exception:
            record_counter(
                "applypilot_browser_launch_failures_total", reason="launch_error"
            )
            raise
        entry = LiveBrowserSession(
            session_id=session_id,
            user_id=user_id,
            application_id=application_id,
            handle=handle,
            expires_at_monotonic=self.clock() + self.ttl_seconds,
            policy=policy,
        )
        async with self._lock:
            current = self._sessions.get(session_id)
            if current is not None:
                # Two concurrent starts raced; keep the first and close ours.
                await handle.close()
                raise BrowserSessionConflictError(
                    "A live browser session already exists for this application."
                )
            self._sessions[session_id] = entry
        record_counter("applypilot_browser_sessions_opened_total")
        return entry

    async def get(
        self, session_id: uuid.UUID, *, user_id: uuid.UUID | None = None
    ) -> LiveBrowserSession | None:
        """Return a live, unexpired session owned by ``user_id`` if given."""
        entry = self._sessions.get(session_id)
        if entry is None:
            return None
        if user_id is not None and entry.user_id != user_id:
            return None
        if await self._is_usable(entry):
            return entry
        await self.release(session_id)
        return None

    async def touch(self, session_id: uuid.UUID, *, user_id: uuid.UUID) -> bool:
        """Extend the TTL after a user action; False when no longer live."""
        entry = await self.get(session_id, user_id=user_id)
        if entry is None:
            return False
        entry.expires_at_monotonic = self.clock() + self.ttl_seconds
        return True

    def live_count(self) -> int:
        """Number of handles currently held by this process (no I/O)."""
        return len(self._sessions)

    async def sweep_expired(self) -> int:
        """Close handles that expired or whose browser/page died.

        This is the idle-path cleanup: a session that is never polled again
        would otherwise keep a Chromium process alive until shutdown.
        """
        async with self._lock:
            entries = list(self._sessions.values())
        stale: list[LiveBrowserSession] = []
        for entry in entries:
            if self.clock() >= entry.expires_at_monotonic:
                stale.append(entry)
                continue
            with contextlib.suppress(Exception):
                if not await entry.handle.is_alive():
                    stale.append(entry)
        removed = 0
        for entry in stale:
            async with self._lock:
                if self._sessions.get(entry.session_id) is entry:
                    self._sessions.pop(entry.session_id, None)
                    removed += 1
            await self._drop(entry)
        return removed

    async def release(self, session_id: uuid.UUID) -> bool:
        """Close and forget one session. Idempotent."""
        async with self._lock:
            entry = self._sessions.pop(session_id, None)
        if entry is None:
            return False
        await self._drop(entry)
        return True

    async def close_all(self) -> None:
        """Close every live browser, used on application shutdown."""
        async with self._lock:
            entries = list(self._sessions.values())
            self._sessions.clear()
        for entry in entries:
            await self._drop(entry)

    async def _is_usable(self, entry: LiveBrowserSession) -> bool:
        if self.clock() >= entry.expires_at_monotonic:
            return False
        with contextlib.suppress(Exception):
            return await entry.handle.is_alive()
        return False

    async def _drop(self, entry: LiveBrowserSession) -> None:
        with contextlib.suppress(Exception):
            await entry.handle.close()
        record_counter("applypilot_browser_sessions_closed_total")
        logger.info(
            "Browser session closed",
            extra={
                "stage": "browser_session_close",
                "session_id": str(entry.session_id),
            },
        )


_manager: BrowserSessionManager | None = None


def _default_manager() -> BrowserSessionManager:
    return BrowserSessionManager(
        driver=PlaywrightPersistentBrowserDriver(settings),
        ttl_seconds=settings.APPLICATION_BROWSER_SESSION_TTL_SECONDS,
        max_sessions=settings.APPLICATION_MAX_ACTIVE_BROWSER_SESSIONS,
    )


def get_browser_session_manager() -> BrowserSessionManager:
    """Return the process-wide manager, creating it on first use."""
    global _manager
    if _manager is None:
        _manager = _default_manager()
    return _manager


def set_browser_session_manager(manager: BrowserSessionManager | None) -> None:
    """Replace the manager (tests) or clear it. Does not close the old one."""
    global _manager
    _manager = manager


def build_manager(
    driver: PersistentBrowserDriver,
    *,
    settings_override: Settings | None = None,
) -> BrowserSessionManager:
    """Create a manager with an injected driver."""
    effective = settings_override or settings
    return BrowserSessionManager(
        driver=driver,
        ttl_seconds=effective.APPLICATION_BROWSER_SESSION_TTL_SECONDS,
        max_sessions=effective.APPLICATION_MAX_ACTIVE_BROWSER_SESSIONS,
    )


async def sweep_browser_sessions() -> int:
    """Close expired/crashed handles; returns how many were closed."""
    if _manager is None:
        return 0
    return await _manager.sweep_expired()


def live_browser_count() -> int:
    """Number of live handles in this process, for readiness reporting."""
    if _manager is None:
        return 0
    return _manager.live_count()


async def shutdown_browser_sessions() -> None:
    """Close every live browser. Safe when nothing was ever started."""
    global _manager
    if _manager is None:
        return
    await _manager.close_all()


__all__ = [
    "BrowserCapacityError",
    "BrowserSessionConflictError",
    "BrowserSessionManager",
    "LiveBrowserSession",
    "build_manager",
    "get_browser_session_manager",
    "live_browser_count",
    "set_browser_session_manager",
    "shutdown_browser_sessions",
    "sweep_browser_sessions",
]
