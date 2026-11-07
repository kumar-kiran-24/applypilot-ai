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
from typing import Protocol, runtime_checkable


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


_REASON = (
    "This deployment runs the employer's page in a backend Playwright session. "
    "A backend browser is not visible in the ApplyPilot interface by itself. "
    "Interactive viewing requires a separate browser-streaming service; until "
    "that service is deployed, automation state and missing questions are shown "
    "in ApplyPilot, and the employer's page must be opened in your own browser "
    "for CAPTCHA, OTP, MFA, legal declarations and final submission."
)

_REQUIRED_COMPONENT = (
    "browser streaming service (CDP screencast relay, or noVNC + websockify "
    "in front of a headed browser)"
)


class UnavailableBrowserViewProvider:
    """Default provider: no streaming service is configured."""

    def capability(self) -> BrowserViewCapability:
        return BrowserViewCapability(
            available=False,
            mode=BrowserViewMode.NONE,
            user_can_interact=False,
            reason=_REASON,
            required_component=_REQUIRED_COMPONENT,
        )

    async def view_url(self, *, session_id: str, user_id: str) -> str | None:
        del session_id, user_id
        return None


_DEFAULT_PROVIDER: BrowserViewProvider = UnavailableBrowserViewProvider()


def get_browser_view_provider() -> BrowserViewProvider:
    """Return the active view provider (unavailable unless one is registered)."""
    return _DEFAULT_PROVIDER


def browser_view_capability() -> BrowserViewCapability:
    """Describe browser visibility without implying a working preview."""
    return get_browser_view_provider().capability()


__all__ = [
    "BrowserViewCapability",
    "BrowserViewMode",
    "BrowserViewProvider",
    "UnavailableBrowserViewProvider",
    "browser_view_capability",
    "get_browser_view_provider",
]
