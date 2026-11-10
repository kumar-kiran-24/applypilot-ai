"""Secure, short-lived view tickets and active-view registry.

The relay never accepts a browser-session id from the client. An authenticated
user first asks for a ticket through the normal API; ownership is checked against
the persisted application and the live in-memory browser. The ticket is:

* random, single use, and bound to that user, browser session and application;
* stored only as a SHA-256 hash in process memory;
* valid for a few seconds and never written to the database or logs;
* refused when a different view is already attached to the same page.

The actual frame stream lives in
:mod:`app.integrations.browser.streaming`; this module owns authorisation and
lifecycle only.
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.core.exceptions import AppError, ResourceNotFoundError
from app.integrations.browser.persistent import PersistentBrowserHandle
from app.services.browser_session_manager import (
    BrowserSessionManager,
    get_browser_session_manager,
)


class BrowserViewUnavailableError(AppError):
    """Streaming is disabled or the session is no longer live."""

    status_code = 503
    code = "browser_view_unavailable"
    message = "The live browser view is not available for this session."


class BrowserViewRateLimitedError(AppError):
    """Too many tickets were requested in a short period."""

    status_code = 429
    code = "browser_view_rate_limited"
    message = "Too many live-view requests. Wait a minute and try again."


class BrowserViewNotFoundError(ResourceNotFoundError):
    """No live browser is owned by this user for the application."""

    code = "browser_view_not_available"
    message = "Start or resume the browser session before opening the live view."


class BrowserViewTicketError(RuntimeError):
    """A ticket could not be exchanged for a view connection."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class IssuedViewTicket:
    ticket: str
    expires_at: datetime


@dataclass(frozen=True)
class ViewAttachment:
    view_id: uuid.UUID
    user_id: uuid.UUID
    session_id: uuid.UUID
    application_id: uuid.UUID
    handle: PersistentBrowserHandle


@dataclass
class _Ticket:
    user_id: uuid.UUID
    session_id: uuid.UUID
    application_id: uuid.UUID
    expires_at: float
    used: bool = False


class BrowserViewService:
    """Issues single-use tickets and limits attached views."""

    def __init__(
        self,
        manager: BrowserSessionManager,
        *,
        ticket_ttl_seconds: int | None = None,
        tickets_per_minute: int | None = None,
        clock=time.monotonic,
    ) -> None:
        self._manager = manager
        self._ttl = (
            ticket_ttl_seconds
            if ticket_ttl_seconds is not None
            else settings.APPLICATION_BROWSER_VIEW_TICKET_TTL_SECONDS
        )
        self._tickets_per_minute = (
            tickets_per_minute
            if tickets_per_minute is not None
            else settings.APPLICATION_BROWSER_VIEW_TICKETS_PER_MINUTE
        )
        self._clock = clock
        self._lock = asyncio.Lock()
        self._tickets: dict[str, _Ticket] = {}
        self._issuance: dict[uuid.UUID, deque[float]] = defaultdict(deque)
        self._active: dict[uuid.UUID, uuid.UUID] = {}

    async def issue(
        self,
        *,
        user_id: uuid.UUID,
        session_id: uuid.UUID,
        application_id: uuid.UUID,
    ) -> IssuedViewTicket:
        if not (
            settings.APPLICATION_AUTOMATION_ENABLED
            and settings.APPLICATION_BROWSER_STREAMING_ENABLED
        ):
            raise BrowserViewUnavailableError(
                "Live browser streaming is disabled on this deployment."
            )

        live = await self._manager.get(session_id, user_id=user_id)
        if live is None:
            raise BrowserViewNotFoundError()
        if live.application_id != application_id:
            raise BrowserViewNotFoundError()
        if live.handle.unsafe_navigation:
            raise BrowserViewUnavailableError("The browser left the approved employer site.")

        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        now = self._clock()
        async with self._lock:
            self._prune(now)
            events = self._issuance[user_id]
            while events and now - events[0] > 60.0:
                events.popleft()
            if len(events) >= self._tickets_per_minute:
                raise BrowserViewRateLimitedError()
            events.append(now)
            self._tickets[digest] = _Ticket(
                user_id=user_id,
                session_id=session_id,
                application_id=application_id,
                expires_at=now + self._ttl,
            )
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=self._ttl)
        return IssuedViewTicket(ticket=token, expires_at=expires_at)

    async def consume(self, token: str) -> ViewAttachment:
        if not token or len(token) > 512:
            raise BrowserViewTicketError("invalid_ticket", "The live-view ticket is invalid.")
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        now = self._clock()
        async with self._lock:
            self._prune(now)
            entry = self._tickets.get(digest)
            if entry is None:
                raise BrowserViewTicketError("invalid_ticket", "The live-view ticket is invalid.")
            if entry.used:
                raise BrowserViewTicketError(
                    "ticket_replayed", "This live-view ticket was already used."
                )
            if now >= entry.expires_at:
                raise BrowserViewTicketError("ticket_expired", "This live-view ticket has expired.")
            entry.used = True
            if entry.session_id in self._active:
                raise BrowserViewTicketError(
                    "view_already_attached",
                    "Another live view is already attached to this browser session.",
                )
            live = await self._manager.get(entry.session_id, user_id=entry.user_id)
            if live is None:
                raise BrowserViewTicketError(
                    "browser_session_closed",
                    "The browser session is no longer live.",
                )
            view_id = uuid.uuid4()
            self._active[entry.session_id] = view_id
            return ViewAttachment(
                view_id=view_id,
                user_id=entry.user_id,
                session_id=entry.session_id,
                application_id=entry.application_id,
                handle=live.handle,
            )

    async def release(self, session_id: uuid.UUID, view_id: uuid.UUID) -> None:
        async with self._lock:
            if self._active.get(session_id) == view_id:
                self._active.pop(session_id, None)

    async def is_live(self, *, session_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        """True while the session is unexpired, alive and still owned by user."""
        return await self._manager.get(session_id, user_id=user_id) is not None

    def active_views(self) -> int:
        return len(self._active)

    def _prune(self, now: float) -> None:
        # Retain for a minute after expiry so a late handshake can report
        # "expired"/"replayed" instead of a misleading "invalid".
        for digest, entry in list(self._tickets.items()):
            if now - entry.expires_at > 60.0:
                self._tickets.pop(digest, None)


_service: BrowserViewService | None = None


def get_browser_view_service() -> BrowserViewService:
    global _service
    if _service is None:
        _service = BrowserViewService(get_browser_session_manager())
    return _service


def set_browser_view_service(service: BrowserViewService | None) -> None:
    global _service
    _service = service


def active_browser_views() -> int:
    """Attached live views in this process, without creating the service."""
    if _service is None:
        return 0
    return _service.active_views()


__all__ = [
    "BrowserViewNotFoundError",
    "BrowserViewRateLimitedError",
    "BrowserViewService",
    "BrowserViewTicketError",
    "BrowserViewUnavailableError",
    "IssuedViewTicket",
    "ViewAttachment",
    "active_browser_views",
    "get_browser_view_service",
    "set_browser_view_service",
]
