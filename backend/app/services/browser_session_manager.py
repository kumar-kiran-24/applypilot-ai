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

        # Launch outside the registry lock so a slow browser start does not
        # block other users' status calls.
        handle = await self.driver.open(url, policy=policy, user_id=str(user_id))
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
    )


async def shutdown_browser_sessions() -> None:
    """Close every live browser. Safe when nothing was ever started."""
    global _manager
    if _manager is None:
        return
    await _manager.close_all()


__all__ = [
    "BrowserSessionConflictError",
    "BrowserSessionManager",
    "LiveBrowserSession",
    "build_manager",
    "get_browser_session_manager",
    "set_browser_session_manager",
    "shutdown_browser_sessions",
]
