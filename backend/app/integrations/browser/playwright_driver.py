"""Playwright implementation of the isolated browser session.

Safety properties:

* Playwright is imported lazily, so the backend runs without it.
* Every call gets a fresh browser context (no shared cookies) that is closed in
  a ``finally`` block.
* Password, OTP, MFA and CAPTCHA fields are never filled. If one is required to
  proceed, ``ManualInterventionRequired`` is raised and the user completes it.
* Screenshots mask credential/OTP inputs and never contain form values in the
  evidence returned to the API.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from app.core.config import Settings
from app.core.logging import get_logger
from app.integrations.browser.session import (
    AutomationSession,
    BrowserUnavailableError,
    ManualInterventionRequired,
    RawFormField,
)
from app.models.application import QuestionType

logger = get_logger(__name__)

MAX_ERRORS = 10
MAX_REFERENCE_LENGTH = 200

# Never touches credential or human-verification inputs.
_BLOCKED_INPUT_PATTERN = re.compile(
    r"pass|otp|one[-_\s]?time|mfa|2fa|captcha|verification[-_\s]?code|security[-_\s]?code",
    re.IGNORECASE,
)

_EXTRACT_FIELDS_JS = """
() => {
  const nodes = Array.from(
    document.querySelectorAll('input, select, textarea')
  );
  return nodes
    .filter((el) => {
      const type = (el.getAttribute('type') || '').toLowerCase();
      if (['hidden', 'submit', 'button', 'reset', 'image'].includes(type)) return false;
      if (el.disabled || el.readOnly) return false;
      const style = window.getComputedStyle(el);
      return style.display !== 'none' && style.visibility !== 'hidden' && el.offsetParent !== null;
    })
    .map((el, index) => {
      const tag = el.tagName.toLowerCase();
      const type = (el.getAttribute('type') || '').toLowerCase();
      const id = el.getAttribute('id');
      let label = el.getAttribute('aria-label') || '';
      if (!label && el.id) {
        const explicit = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
        if (explicit) label = explicit.innerText || explicit.textContent || '';
      }
      if (!label) {
        const wrapper = el.closest('label');
        if (wrapper) label = wrapper.innerText || wrapper.textContent || '';
      }
      if (!label) label = el.getAttribute('placeholder') || el.getAttribute('name') || '';
      const identifier = `apf-${index}`;
      el.setAttribute('data-applypilot-field', identifier);
      let fieldType = 'text';
      if (tag === 'textarea') fieldType = 'textarea';
      else if (tag === 'select') fieldType = 'single_choice';
      else if (type === 'email') fieldType = 'email';
      else if (type === 'tel') fieldType = 'phone';
      else if (type === 'number') fieldType = 'number';
      else if (type === 'date') fieldType = 'date';
      else if (type === 'url') fieldType = 'url';
      else if (type === 'file') fieldType = 'file';
      else if (type === 'checkbox') fieldType = 'boolean';
      else if (type === 'radio') fieldType = 'single_choice';
      const options = tag === 'select'
        ? Array.from(el.options)
            .map((o) => (o.textContent || '').trim())
            .filter(Boolean)
            .slice(0, 50)
        : [];
      return {
        identifier,
        label: (label || '').replace(/\\s+/g, ' ').trim().slice(0, 300),
        field_type: fieldType,
        required: el.required === true || el.getAttribute('aria-required') === 'true',
        options,
        input_type: type,
        name: el.getAttribute('name') || '',
      };
    });
}
"""

_LOGIN_JS = """
() => {
  const text = (document.body?.innerText || '').toLowerCase();
  const hasPassword = Boolean(document.querySelector('input[type="password"]'));
  const hasCode = Array.from(document.querySelectorAll('input')).some((el) => {
    const name = `${el.name} ${el.id} ${el.placeholder || ''}`.toLowerCase();
    return /otp|one[-_ ]?time|mfa|2fa|verification code/.test(name);
  });
  return {
    login: hasPassword || /sign in to (apply|continue)|log in to (apply|continue)/.test(text),
    verification: hasCode || /captcha|verify you are human/.test(text),
  };
}
"""

_VALIDATION_JS = """
(limit) => {
  const selectors = [
    '[aria-invalid="true"]',
    '.error-message',
    '.field-error',
    '[role="alert"]',
    '.error',
  ];
  const messages = [];
  for (const selector of selectors) {
    for (const el of document.querySelectorAll(selector)) {
      const text = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
      if (text && text.length <= 300 && !messages.includes(text)) messages.push(text);
      if (messages.length >= limit) return messages;
    }
  }
  return messages;
}
"""

_MASK_STYLE = """
input[type="password"],
input[type="tel"],
input[autocomplete*="one-time"],
input[name*="otp" i],
input[name*="ssn" i],
[data-applypilot-sensitive="true"] {
  color: transparent !important;
  text-shadow: 0 0 8px rgba(0, 0, 0, 0.9) !important;
  caret-color: transparent !important;
}
"""


def _field_type(raw: dict[str, object]) -> QuestionType:
    value = str(raw.get("field_type") or "text")
    try:
        return QuestionType(value)
    except ValueError:
        return QuestionType.TEXT


class PlaywrightSession:
    """One ephemeral browser page. Closed by :class:`PlaywrightBrowser`."""

    def __init__(self, page: object, settings: Settings, *, user_id: str) -> None:
        self._page = page
        self._settings = settings
        self._user_id = user_id

    async def inspect(self, selectors: object) -> tuple[list[RawFormField], bool]:
        page = self._page
        raw_fields = await page.evaluate(_EXTRACT_FIELDS_JS)  # type: ignore[attr-defined]
        flags = await page.evaluate(_LOGIN_JS)  # type: ignore[attr-defined]
        fields = [
            RawFormField(
                identifier=str(item.get("identifier") or ""),
                label=str(item.get("label") or ""),
                field_type=_field_type(item),
                required=bool(item.get("required")),
                options=tuple(str(option) for option in item.get("options") or []),
            )
            for item in raw_fields
        ]
        if flags.get("verification"):
            raise ManualInterventionRequired(
                "The form asks for a CAPTCHA, OTP or other human verification.",
                step="verification",
            )
        return fields, bool(flags.get("login"))

    async def fill(self, values: dict[str, str], selectors: object) -> list[str]:
        page = self._page
        filled: list[str] = []
        for identifier, value in values.items():
            if not re.fullmatch(r"apf-\d+", identifier):
                continue
            locator = page.locator(  # type: ignore[attr-defined]
                f'[data-applypilot-field="{identifier}"]'
            )
            if await locator.count() != 1:
                continue
            input_type = (await locator.get_attribute("type") or "").lower()
            descriptor = " ".join(
                filter(
                    None,
                    [
                        await locator.get_attribute("name"),
                        await locator.get_attribute("id"),
                        await locator.get_attribute("autocomplete"),
                        identifier,
                    ],
                )
            )
            if _BLOCKED_INPUT_PATTERN.search(descriptor):
                raise ManualInterventionRequired(
                    "The form requires a credential or verification code.",
                    step="credential",
                )
            tag = await locator.evaluate("(el) => el.tagName.toLowerCase()")
            if tag == "select":
                await locator.select_option(label=value)
            elif input_type == "checkbox":
                if value.strip().lower() in {"yes", "true", "1", "on"}:
                    await locator.check()
                else:
                    await locator.uncheck()
            elif input_type == "radio":
                continue
            else:
                await locator.fill(value)
            filled.append(identifier)
        return filled

    async def upload_file(self, path: str, selector: str) -> str | None:
        page = self._page
        locator = page.locator(selector).first  # type: ignore[attr-defined]
        if await locator.count() == 0:
            return None
        await locator.set_input_files(path)
        return selector

    async def validation_errors(self, selectors: object) -> list[str]:
        errors = await self._page.evaluate(_VALIDATION_JS, MAX_ERRORS)  # type: ignore[attr-defined]
        return [str(error)[:300] for error in errors]

    async def submit(self, selectors: object) -> None:
        page = self._page
        button = page.locator(selectors.submit).last  # type: ignore[attr-defined]
        if await button.count() == 0:
            raise ManualInterventionRequired(
                "No submit button was found on the inspected form.",
                step="submit",
            )
        button_text = ((await button.inner_text()) or "").lower()
        if button_text and any(word in button_text for word in ("login", "sign in", "verify")):
            raise ManualInterventionRequired(
                "The form needs the account owner to sign in or verify first.",
                step="authentication",
            )
        await button.click()

    async def confirmation_reference(self, selector: str) -> str | None:
        page = self._page
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=15_000)  # type: ignore[attr-defined]
            locator = page.locator(selector).first  # type: ignore[attr-defined]
            if await locator.count() > 0:
                text = (await locator.inner_text() or "").strip()
                if text:
                    return text[:MAX_REFERENCE_LENGTH]
            return page.url[:MAX_REFERENCE_LENGTH]  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover - confirmation is best effort
            return None

    async def screenshot(self) -> str | None:
        """Mask credential-like inputs, then capture a debugging screenshot."""
        directory = Path(self._settings.APPLICATION_SCREENSHOT_DIR)
        try:
            await asyncio.to_thread(directory.mkdir, parents=True, exist_ok=True)
            await self._page.add_style_tag(content=_MASK_STYLE)  # type: ignore[attr-defined]
            filename = f"application-{self._user_id}-{uuid.uuid4().hex[:12]}.png"
            await self._page.screenshot(path=str(directory / filename), full_page=True)  # type: ignore[attr-defined]
            return filename
        except Exception:  # pragma: no cover - screenshot must never block
            logger.warning(
                "Application screenshot failed",
                extra={"stage": "application_screenshot"},
            )
            return None


class PlaywrightBrowser:
    """Creates isolated, short-lived browser sessions."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @asynccontextmanager
    async def open(self, url: str, *, user_id: str) -> AsyncIterator[AutomationSession]:
        """Open ``url`` in a fresh context and always tear it down afterwards."""
        if not self._settings.APPLICATION_AUTOMATION_ENABLED:
            raise BrowserUnavailableError(
                "Browser automation is disabled (APPLICATION_AUTOMATION_ENABLED=false)."
            )
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:  # pragma: no cover - depends on optional install
            raise BrowserUnavailableError(
                "Playwright is not installed. Install it to enable automation."
            ) from exc

        timeout = self._settings.APPLICATION_BROWSER_TIMEOUT_MS
        async with async_playwright() as playwright:  # pragma: no cover - needs a browser
            browser = await playwright.chromium.launch(
                headless=self._settings.APPLICATION_BROWSER_HEADLESS
            )
            context = await browser.new_context(
                viewport={"width": 1280, "height": 900},
                locale="en-US",
            )
            try:
                page = await context.new_page()
                page.set_default_timeout(timeout)
                await page.goto(url, wait_until="domcontentloaded")
                yield PlaywrightSession(page, self._settings, user_id=user_id)
            finally:
                await context.close()
                await browser.close()


__all__ = ["PlaywrightBrowser", "PlaywrightSession"]
