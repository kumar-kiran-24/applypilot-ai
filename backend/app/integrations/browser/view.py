"""Boundary for showing a live browser session to the user.

A Playwright browser running inside the backend is **not** automatically visible
in a web page. Making it interactive requires a separate browser-streaming
component - typically a CDP screencast relay, or a headed browser exposed over
VNC with noVNC/websockify in front of it.

This module defines the integration contract without pretending that component
exists. The default provider reports ``available=False`` and names the missing
dependency. A future deployment can register a real provider (for example one
that returns a short-lived, authenticated websocket URL) without changing the
session API, the state machine or the persistence layer.

The provider contract deliberately never returns browser cookies, authentication
tokens, a CDP endpoint or a raw control socket: only an end-user view URL that a
separate streaming service authenticates for the owning user.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from importlib.util import find_spec
from typing import Protocol, runtime_checkable

from app.core.config import settings


class BrowserViewMode(str, Enum):
    """How (if at all) the live browser can be shown to the user."""

    NONE = "none"
    CDP_RELAY = "cdp_relay"
    VNC = "vnc"


@dataclass(frozen=True)
class BrowserViewCapability:
    """Honest description of the current deployment's browser visibility."""

    available: bool
    mode: BrowserViewMode
    user_can_interact: bool
    reason: str
    required_component: str | None = None

    def to_payload(self) -> dict[str, object]:
        return {
            "available": self.available,
            "mode": self.mode.value,
            "user_can_interact": self.user_can_interact,
            "reason": self.reason,
            "required_component": self.required_component,
        }


@runtime_checkable
class BrowserViewProvider(Protocol):
    """A separate service that renders a live browser for one user."""

    def capability(self) -> BrowserViewCapability:
        """Describe whether this provider can show a browser at all."""
        ...

    async def view_url(self, *, session_id: str, user_id: str) -> str | None:
        """Return a short-lived, user-authenticated view URL, or ``None``."""
        ...


_AUTOMATION_DISABLED_REASON = (
    "Browser automation is disabled on this deployment "
    "(APPLICATION_AUTOMATION_ENABLED=false), so there is no persistent employer "
    "page to show. Set APPLICATION_AUTOMATION_ENABLED=true to use the embedded "
    "live browser."
)

_STREAMING_DISABLED_REASON = (
    "Live browser streaming is disabled on this deployment "
    "(APPLICATION_BROWSER_STREAMING_ENABLED=false). The persistent employer page "
    "still runs, but it cannot be shown or controlled in ApplyPilot until "
    "streaming is enabled."
)

_PLAYWRIGHT_MISSING_REASON = (
    "Browser streaming is configured, but the Playwright package is not installed "
    "in this backend environment. Install Playwright and Chromium to show the "
    "persistent employer page in ApplyPilot."
)

_STREAMING_COMPONENT = "CDP screencast relay (built in; no extra service)"


class UnavailableBrowserViewProvider:
    """Provider used when streaming cannot run, with a precise reason."""

    def __init__(
        self,
        *,
        reason: str = _AUTOMATION_DISABLED_REASON,
        required_component: str | None = None,
    ) -> None:
        self._reason = reason
        self._required_component = required_component

    def capability(self) -> BrowserViewCapability:
        return BrowserViewCapability(
            available=False,
            mode=BrowserViewMode.NONE,
            user_can_interact=False,
            reason=self._reason,
            required_component=self._required_component,
        )

    async def view_url(self, *, session_id: str, user_id: str) -> str | None:
        del session_id, user_id
        return None


_STREAM_REASON = (
    "The employer's page is streamed live from the same backend Playwright "
    "session and accepts mouse, keyboard and scroll input from you. Input is "
    "forwarded only to your owned session over a short-lived, single-use view "
    "ticket; no CDP port, cookie or storage state is exposed. CAPTCHA, OTP, MFA, "
    "legal declarations and final submission remain yours to complete."
)


class CdpRelayBrowserViewProvider:
    """In-process CDP screencast relay for the existing persistent page.

    Unlike :class:`UnavailableBrowserViewProvider`, this provider is backed by
    :mod:`app.integrations.browser.streaming`, which attaches to the live page.
    The provider object itself never carries a URL; the owning user requests a
    single-use ticket from an authenticated endpoint.
    """

    def __init__(self, enabled: bool) -> None:
        self._enabled = enabled

    def capability(self) -> BrowserViewCapability:
        return BrowserViewCapability(
            available=self._enabled,
            mode=BrowserViewMode.CDP_RELAY if self._enabled else BrowserViewMode.NONE,
            user_can_interact=self._enabled,
            reason=_STREAM_REASON if self._enabled else _REASON,
            required_component=None if self._enabled else _REQUIRED_COMPONENT,
        )

    async def view_url(self, *, session_id: str, user_id: str) -> str | None:
        # The secure ticket endpoint issues transport details after checking
        # application and session ownership in the database.
        del session_id, user_id
        return None


def get_browser_view_provider() -> BrowserViewProvider:
    """Return the active provider, naming the exact disabled prerequisite."""
    if not settings.APPLICATION_AUTOMATION_ENABLED:
        return UnavailableBrowserViewProvider(
            reason=_AUTOMATION_DISABLED_REASON,
            required_component="APPLICATION_AUTOMATION_ENABLED=true",
        )
    if not settings.APPLICATION_BROWSER_STREAMING_ENABLED:
        return UnavailableBrowserViewProvider(
            reason=_STREAMING_DISABLED_REASON,
            required_component="APPLICATION_BROWSER_STREAMING_ENABLED=true",
        )
    if find_spec("playwright") is None:
        return UnavailableBrowserViewProvider(
            reason=_PLAYWRIGHT_MISSING_REASON,
            required_component="playwright",
        )
    return CdpRelayBrowserViewProvider(True)


def browser_view_capability() -> BrowserViewCapability:
    """Describe browser visibility truthfully for the current deployment."""
    return get_browser_view_provider().capability()


__all__ = [
    "BrowserViewCapability",
    "BrowserViewMode",
    "BrowserViewProvider",
    "CdpRelayBrowserViewProvider",
    "UnavailableBrowserViewProvider",
    "browser_view_capability",
    "get_browser_view_provider",
]
