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
import re
import uuid
from typing import Protocol
from urllib.parse import urlsplit

from app.core.config import Settings
from app.core.logging import get_logger
from app.integrations.browser.field_actions import (
    FieldAction,
    FieldActionKind,
    FieldActionResult,
)
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

    async def apply_field_action(self, action: FieldAction) -> FieldActionResult:
        """Apply one narrow Stage C field action and report what was observed.

        Implementations must refuse credential/verification controls and never
        click a submit, apply or navigation control.
        """
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


#: Controls that must never be touched by the fill engine.
_BLOCKED_FIELD_PATTERN = re.compile(
    r"pass|otp|one[-_\s]?time|mfa|2fa|captcha|verification[-_\s]?code|"
    r"security[-_\s]?code|auth(?:entication)?[-_\s]?code",
    re.IGNORECASE,
)

_FIND_TARGET_JS = """
(args) => {
  const [token, htmlId, name, label] = args;
  const norm = (value) => (value || '').replace(/\\s+/g, ' ').trim().toLowerCase();
  const labelFor = (el) => {
    let text = el.getAttribute('aria-label') || '';
    if (!text && el.id) {
      const explicit = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (explicit) text = explicit.innerText || explicit.textContent || '';
    }
    if (!text) {
      const wrapper = el.closest('label');
      if (wrapper) text = wrapper.innerText || wrapper.textContent || '';
    }
    if (!text) text = el.getAttribute('placeholder') || '';
    return norm(text);
  };
  const nodes = Array.from(document.querySelectorAll('input, select, textarea'))
    .filter((el) => {
      const type = (el.getAttribute('type') || '').toLowerCase();
      if (['hidden', 'submit', 'button', 'reset', 'image', 'password'].includes(type)) return false;
      if (el.disabled || el.readOnly) return false;
      const style = window.getComputedStyle(el);
      return style.display !== 'none' && style.visibility !== 'hidden';
    });
  let target = null;
  if (htmlId) target = nodes.find((el) => el.id === htmlId) || null;
  if (!target && name) target = nodes.find((el) => el.name === name) || null;
  if (!target && label) target = nodes.find((el) => labelFor(el) === norm(label)) || null;
  if (!target) return null;
  const marker = `apc-${token}`;
  target.setAttribute('data-applypilot-target', marker);
  return marker;
}
"""

_MARK_OPTIONS_JS = """
(element, args) => {
  const [token, name] = args;
  const norm = (value) => (value || '').replace(/\\s+/g, ' ').trim().toLowerCase();
  const labelFor = (el) => {
    let text = el.getAttribute('aria-label') || '';
    if (!text && el.id) {
      const explicit = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (explicit) text = explicit.innerText || explicit.textContent || '';
    }
    if (!text) {
      const wrapper = el.closest('label');
      if (wrapper) text = wrapper.innerText || wrapper.textContent || '';
    }
    return norm(text);
  };
  return Array.from(document.querySelectorAll('input[type="radio"], input[type="checkbox"]'))
    .filter((el) => !name || el.name === name)
    .map((el, index) => {
      const marker = `apo-${token}-${index}`;
      el.setAttribute('data-applypilot-option', marker);
      return { marker, label: labelFor(el), checked: el.checked === true };
    });
}
"""


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

    # --- Stage C field actions ---------------------------------------------
    async def apply_field_action(self, action: FieldAction) -> FieldActionResult:
        """Fill, select, check or attach a file; never click or submit."""
        page = self._page
        if self._closed:
            return FieldActionResult(False, action.field_id, reason="The browser is closed.")
        descriptor = " ".join(
            filter(None, [action.label, action.name, action.html_id, action.field_id])
        )
        if _BLOCKED_FIELD_PATTERN.search(descriptor):
            return FieldActionResult(
                False,
                action.field_id,
                reason="Credentials and verification fields are never touched.",
            )
        if self._unsafe_navigation:
            return FieldActionResult(
                False,
                action.field_id,
                reason="The page left the approved employer site.",
            )

        token = uuid.uuid4().hex[:12]
        try:
            marker = await page.evaluate(
                _FIND_TARGET_JS,
                [token, action.html_id or "", action.name or "", action.label or ""],
            )
        except Exception:  # pragma: no cover - closed or navigated page
            return FieldActionResult(False, action.field_id, reason="The page changed.")
        if not marker:
            return FieldActionResult(
                False,
                action.field_id,
                reason="The field was not found on the current page.",
            )

        locator = page.locator(f'[data-applypilot-target="{marker}"]')  # type: ignore[attr-defined]
        if await locator.count() != 1:
            return FieldActionResult(
                False,
                action.field_id,
                reason="The field could not be located uniquely.",
            )

        try:
            if action.kind is FieldActionKind.UPLOAD:
                return await self._upload(locator, action)
            if action.kind is FieldActionKind.SELECT:
                return await self._select(locator, action, token)
            if action.kind is FieldActionKind.CHECK:
                return await self._check(locator, action, token)
            return await self._fill(locator, action)
        except Exception:  # pragma: no cover - employer page behaviour
            logger.info(
                "Field action could not be completed",
                extra={"stage": "field_action", "field_kind": action.kind.value},
            )
            return FieldActionResult(
                False,
                action.field_id,
                reason="The employer's field rejected the value.",
            )

    async def _fill(self, locator: object, action: FieldAction) -> FieldActionResult:
        tag = await locator.evaluate("(el) => el.tagName.toLowerCase()")  # type: ignore[attr-defined]
        input_type = (
            await locator.get_attribute("type") or ""  # type: ignore[attr-defined]
        ).lower()
        text = (action.text or "").strip()
        if not text:
            return FieldActionResult(False, action.field_id, reason="There is no safe value.")
        if tag in {"input", "textarea"} and input_type != "checkbox":
            existing = await locator.input_value()  # type: ignore[attr-defined]
            if (existing or "").strip():
                if (existing or "").strip() == text:
                    return FieldActionResult(True, action.field_id, observed_value=existing)
                return FieldActionResult(
                    False,
                    action.field_id,
                    observed_value=existing,
                    reason="The field already contains a value and was not overwritten.",
                )
        await locator.fill(text)  # type: ignore[attr-defined]
        observed = await locator.input_value()  # type: ignore[attr-defined]
        return FieldActionResult(True, action.field_id, observed_value=str(observed))

    async def _select(
        self, locator: object, action: FieldAction, token: str
    ) -> FieldActionResult:
        options = list(action.option_labels)
        if not options:
            return FieldActionResult(False, action.field_id, reason="No option was chosen.")
        wanted = options[0].strip().casefold()
        name = await locator.get_attribute("name")  # type: ignore[attr-defined]
        input_type = (
            await locator.get_attribute("type") or ""  # type: ignore[attr-defined]
        ).lower()

        if input_type == "radio":
            marked = await locator.evaluate(  # type: ignore[attr-defined]
                _MARK_OPTIONS_JS, [token, name or ""]
            )
            match = next(
                (item for item in marked if str(item.get("label")).casefold() == wanted),
                None,
            )
            if match is None:
                return FieldActionResult(
                    False,
                    action.field_id,
                    reason="The chosen option is not on the current page.",
                )
            if match.get("checked"):
                return FieldActionResult(
                    True, action.field_id, observed_value=str(match.get("label"))
                )
            option = self._page.locator(  # type: ignore[attr-defined]
                f'[data-applypilot-option="{match["marker"]}"]'
            )
            await option.check()
            observed = next(
                (
                    item
                    for item in await locator.evaluate(  # type: ignore[attr-defined]
                        _MARK_OPTIONS_JS, [uuid.uuid4().hex[:12], name or ""]
                    )
                    if item.get("checked")
                ),
                None,
            )
            return FieldActionResult(
                True,
                action.field_id,
                observed_value=str(observed.get("label")) if observed else None,
            )

        current = await locator.evaluate(  # type: ignore[attr-defined]
            "(el) => el.options && el.selectedIndex >= 0 "
            "? el.options[el.selectedIndex].textContent : ''"
        )
        selected_index = await locator.evaluate(  # type: ignore[attr-defined]
            "(el) => el.options ? el.selectedIndex : -1"
        )
        if selected_index > 0 and str(current or "").strip():
            if str(current).strip().casefold() == wanted:
                return FieldActionResult(True, action.field_id, observed_value=str(current))
            return FieldActionResult(
                False,
                action.field_id,
                observed_value=str(current),
                reason="The field already contains a value and was not overwritten.",
            )
        await locator.select_option(label=options[0])  # type: ignore[attr-defined]
        observed = await locator.evaluate(  # type: ignore[attr-defined]
            "(el) => el.options && el.selectedIndex >= 0 "
            "? el.options[el.selectedIndex].textContent : ''"
        )
        return FieldActionResult(
            True, action.field_id, observed_value=str(observed or "").strip()
        )

    async def _check(
        self, locator: object, action: FieldAction, token: str
    ) -> FieldActionResult:
        name = await locator.get_attribute("name")  # type: ignore[attr-defined]
        input_type = (
            await locator.get_attribute("type") or ""  # type: ignore[attr-defined]
        ).lower()
        wanted = [item.strip().casefold() for item in action.option_labels if item.strip()]
        if not wanted and action.text:
            wanted = [action.text.strip().casefold()]

        if input_type == "checkbox" and name:
            marked = await locator.evaluate(  # type: ignore[attr-defined]
                _MARK_OPTIONS_JS, [token, name]
            )
            if len(marked) > 1:
                matched = [
                    item for item in marked if str(item.get("label")).casefold() in wanted
                ]
                if not matched:
                    return FieldActionResult(
                        False,
                        action.field_id,
                        reason="The chosen options are not on the current page.",
                    )
                if all(item.get("checked") for item in matched):
                    return FieldActionResult(
                        True,
                        action.field_id,
                        observed_value=", ".join(str(item.get("label")) for item in matched),
                    )
                for item in matched:
                    option = self._page.locator(  # type: ignore[attr-defined]
                        f'[data-applypilot-option="{item["marker"]}"]'
                    )
                    if not item.get("checked"):
                        await option.check()
                observed = await locator.evaluate(  # type: ignore[attr-defined]
                    _MARK_OPTIONS_JS, [uuid.uuid4().hex[:12], name]
                )
                return FieldActionResult(
                    True,
                    action.field_id,
                    observed_value=", ".join(
                        str(item.get("label")) for item in observed if item.get("checked")
                    ),
                )

        affirmative = not wanted or wanted[0] in {"yes", "true", "1", "on"}
        if affirmative and await locator.is_checked():  # type: ignore[attr-defined]
            return FieldActionResult(True, action.field_id, observed_value="true")
        if affirmative:
            await locator.check()  # type: ignore[attr-defined]
        else:
            await locator.uncheck()  # type: ignore[attr-defined]
        checked = await locator.is_checked()  # type: ignore[attr-defined]
        return FieldActionResult(
            True, action.field_id, observed_value="true" if checked else "false"
        )

    async def _upload(self, locator: object, action: FieldAction) -> FieldActionResult:
        if not action.upload_path:
            return FieldActionResult(False, action.field_id, reason="No resume was selected.")
        input_type = (
            await locator.get_attribute("type") or ""  # type: ignore[attr-defined]
        ).lower()
        if input_type != "file":
            return FieldActionResult(
                False,
                action.field_id,
                reason="The field is not a file input.",
            )
        existing = await locator.evaluate(  # type: ignore[attr-defined]
            "(el) => el.files && el.files.length ? el.files[0].name : ''"
        )
        if existing:
            return FieldActionResult(
                True, action.field_id, observed_value=str(existing)
            )
        await locator.set_input_files(action.upload_path)  # type: ignore[attr-defined]
        observed = await locator.evaluate(  # type: ignore[attr-defined]
            "(el) => el.files && el.files.length ? el.files[0].name : ''"
        )
        return FieldActionResult(
            bool(observed),
            action.field_id,
            observed_value=str(observed) if observed else None,
        )

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
