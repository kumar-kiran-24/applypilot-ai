"""Long-lived Playwright sessions with an enforced navigation boundary.

This is intentionally separate from :mod:`playwright_driver`, which opens a
short-lived context per inspection/submission and closes it immediately. A
Phase 2E session must survive between HTTP requests so the user can answer a
missing question and let the agent continue.

Safety properties kept by this host:

* Playwright is imported lazily, so the API starts without the optional package.
* One fresh browser context per session; no storage state is ever loaded or
  exported, and the context is closed on release, expiry or shutdown.
* Every main-frame navigation request is checked against the session's
  :class:`~app.services.application_url_security.NavigationPolicy`; redirects
  and new tabs cannot escape the approved origins.
* Downloads are disabled and file/other non-http schemes are blocked.
* No CDP endpoint, cookie or authentication header leaves this module.

If the browser cannot start, :class:`~app.integrations.browser.session.BrowserUnavailableError`
is raised instead of pretending a session exists.
"""

from __future__ import annotations

import contextlib
from typing import Protocol
from urllib.parse import urlsplit

from app.core.config import Settings
from app.core.logging import get_logger
from app.integrations.browser.session import BrowserUnavailableError
from app.services.application_url_security import NavigationPolicy, UnsafeNavigationError

logger = get_logger(__name__)


class PersistentBrowserHandle(Protocol):
    """A live browser that outlives one request."""

    async def current_url(self) -> str:
        """Return the current main-frame URL."""
        ...

    async def content(self) -> str:
        """Return the current main-frame HTML for read-only form analysis.

        This is the only page-content capability exposed to Phase 2E services.
        It never executes JavaScript, reads cookie/storage state or returns a
        password/OTP/CAPTCHA value; the extractor filters those itself.
        """
        ...

    async def is_alive(self) -> bool:
        """Return True while the browser and page can still be used."""
        ...

    @property
    def unsafe_navigation(self) -> bool:
        """True when the page attempted to leave the approved origins."""
        ...

    async def close(self) -> None:
        """Close the context and browser; safe to call more than once."""
        ...


class PersistentBrowserDriver(Protocol):
    """Creates handles; injectable so tests never launch a real browser."""

    async def open(
        self,
        url: str,
        *,
        policy: NavigationPolicy,
        user_id: str,
    ) -> PersistentBrowserHandle:
        ...


class _PlaywrightHandle:
    """Owns one Chromium context and enforces the navigation policy."""

    def __init__(
        self,
        playwright: object,
        browser: object,
        context: object,
        page: object,
        policy: NavigationPolicy,
    ) -> None:
        self._playwright = playwright
        self._browser = browser
        self._context = context
        self._page = page
        self._policy = policy
        self._closed = False
        self._unsafe_navigation = False
        context.on("page", self._on_new_page)  # type: ignore[attr-defined]
        page.on("framenavigated", self._on_navigated)  # type: ignore[attr-defined]
        page.on("popup", self._on_popup)  # type: ignore[attr-defined]

    # --- guards -------------------------------------------------------------
    async def guard_route(self, route: object) -> None:
        request = route.request  # type: ignore[attr-defined]
        try:
            is_navigation = request.is_navigation_request()  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover - defensive
            is_navigation = False
        if not is_navigation:
            # Subresources (CSS, fonts, images) are not browser navigations and
            # do not create an SSRF path back into the API.
            await route.continue_()  # type: ignore[attr-defined]
            return
        url = request.url  # type: ignore[attr-defined]
        if self._is_allowed(url):
            await route.continue_()  # type: ignore[attr-defined]
            return
        self._unsafe_navigation = True
        logger.warning(
            "Blocked browser navigation outside the approved origins",
            extra={
                "stage": "browser_navigation_guard",
                "host": (urlsplit(url).hostname or "")[:200],
            },
        )
        await route.abort("blockedbyclient")  # type: ignore[attr-defined]

    async def _on_navigated(self, frame: object) -> None:
        page = self._page
        try:
            is_main = frame == page.main_frame  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover - defensive
            is_main = False
        if not is_main:
            return
        url = getattr(frame, "url", "")
        if not url or url == "about:blank":
            return
        if not self._is_allowed(url):
            self._unsafe_navigation = True
            logger.warning(
                "Browser session left its approved origin",
                extra={"stage": "browser_navigation_violation"},
            )

    async def _on_new_page(self, page: object) -> None:
        """Only the main page is allowed; popups are closed immediately."""
        if page is self._page:
            await page.route("**/*", self.guard_route)  # type: ignore[attr-defined]
            return
        self._unsafe_navigation = True
        with contextlib.suppress(Exception):
            await page.close()  # type: ignore[attr-defined]

    async def _on_popup(self, popup: object) -> None:
        self._unsafe_navigation = True
        with contextlib.suppress(Exception):
            await popup.close()  # type: ignore[attr-defined]

    def _is_allowed(self, url: str) -> bool:
        if url == "about:blank":
            return True
        return self._policy.allows(url)

    # --- handle contract ----------------------------------------------------
    async def current_url(self) -> str:
        try:
            return str(self._page.url)  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover - closed page
            return ""

    async def content(self) -> str:
        if self._closed:
            return ""
        try:
            return str(await self._page.content())  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover - page crashed or navigated away
            return ""

    async def is_alive(self) -> bool:
        if self._closed:
            return False
        try:
            if self._page.is_closed():  # type: ignore[attr-defined]
                return False
            return bool(self._browser.is_connected())  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover - browser crashed
            return False

    @property
    def unsafe_navigation(self) -> bool:
        return self._unsafe_navigation

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for target in (self._context, self._browser):
            with contextlib.suppress(Exception):
                await target.close()  # type: ignore[attr-defined]
        with contextlib.suppress(Exception):
            await self._playwright.stop()  # type: ignore[attr-defined]


class PlaywrightPersistentBrowserDriver:
    """Launches Chromium and keeps it alive until the handle is closed."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def open(
        self,
        url: str,
        *,
        policy: NavigationPolicy,
        user_id: str,
    ) -> PersistentBrowserHandle:
        if not self._settings.APPLICATION_AUTOMATION_ENABLED:
            raise BrowserUnavailableError(
                "Browser automation is disabled (APPLICATION_AUTOMATION_ENABLED=false)."
            )
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise BrowserUnavailableError(
                "Playwright is not installed. Install it to enable live browser sessions."
            ) from exc

        playwright = await async_playwright().start()
        try:
            browser = await playwright.chromium.launch(
                headless=self._settings.APPLICATION_BROWSER_HEADLESS
            )
            context = await browser.new_context(
                viewport={"width": 1280, "height": 900},
                locale="en-US",
                accept_downloads=False,
                # No storage state is ever loaded: a session starts signed out.
                storage_state=None,
            )
            page = await context.new_page()
            page.set_default_timeout(self._settings.APPLICATION_BROWSER_TIMEOUT_MS)
            handle = _PlaywrightHandle(playwright, browser, context, page, policy)
            await page.route("**/*", handle.guard_route)
            try:
                await page.goto(url, wait_until="domcontentloaded")
            except Exception as exc:
                if handle.unsafe_navigation:
                    await handle.close()
                    raise UnsafeNavigationError(
                        "The employer's page redirected outside the approved site.",
                        code="navigation_blocked",
                    ) from exc
                raise
            if handle.unsafe_navigation:
                await handle.close()
                raise BrowserUnavailableError(
                    "The employer's page redirected outside the approved site."
                )
            logger.info(
                "Browser session opened",
                extra={
                    "stage": "browser_session_open",
                    "user_id": user_id,
                    "host": (urlsplit(url).hostname or "")[:200],
                },
            )
            return handle
        except (BrowserUnavailableError, UnsafeNavigationError):
            with contextlib.suppress(Exception):
                await playwright.stop()
            raise
        except Exception as exc:
            with contextlib.suppress(Exception):
                await playwright.stop()
            logger.warning(
                "Browser session could not be opened",
                extra={"stage": "browser_session_open", "error_class": type(exc).__name__},
            )
            raise BrowserUnavailableError(
                "The browser session could not be opened."
            ) from exc


__all__ = [
    "PersistentBrowserDriver",
    "PersistentBrowserHandle",
    "PlaywrightPersistentBrowserDriver",
]
