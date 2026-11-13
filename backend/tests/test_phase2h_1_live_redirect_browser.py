"""Phase 2H.1: real-Chromium validation of the Easy Apply hand-off.

The live Indeed SmartApply flow cannot be driven in CI: it needs a signed-in
session, may present CAPTCHA/MFA, and ApplyPilot must never automate those. This
suite therefore validates the *browser navigation lifecycle* with controlled
loopback origins standing in for the job board and the application platform:

    listing page --user clicks Easy Apply--> new tab (popup) --adopted-->
    controlled SmartApply-shaped form --> form analysis --> safe filling

Three distinct loopback hosts on one port are used deliberately, so this is a
genuine cross-origin hand-off, not a same-origin shortcut. The exact-host
SmartApply classification is covered by the pure tests in
``test_phase2h_smartapply_redirect.py``.

It is skipped by default because it launches a real browser. Run it explicitly:

    APPLYPILOT_RUN_BROWSER_TESTS=1 python -m pytest \\
        tests/test_phase2h_1_live_redirect_browser.py -m browser -q
"""

from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Awaitable, Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest

if os.environ.get("APPLYPILOT_RUN_BROWSER_TESTS") != "1":
    pytest.skip(
        "Set APPLYPILOT_RUN_BROWSER_TESTS=1 to run the real Easy Apply "
        "browser validation.",
        allow_module_level=True,
    )

from app.core.config import settings
from app.integrations.browser.field_actions import FieldAction, FieldActionKind
from app.integrations.browser.persistent import PlaywrightPersistentBrowserDriver
from app.services import application_url_security
from app.services.application_form_analyzer import ApplicationFormAnalyzer
from app.services.application_url_security import ApprovedOrigin, NavigationPolicy
from app.services.indeed_smartapply import SmartApplyStep, inspect_page

pytestmark = pytest.mark.browser

#: The page the live flow is expected to reach. Never fetched by these tests.
SMARTAPPLY_RESUME = (
    "https://smartapply.indeed.com/beta/indeedapply/form/"
    "resume-selection-module/resume-selection"
)

LISTING_HTML = """<!doctype html>
<html><body>
<h1>Associate AI Engineer</h1>
<p>Digital Green - Bengaluru, Karnataka</p>
<a id="easy-apply" href="{form}/apply" target="_blank" rel="noopener">Easy Apply</a>
<a id="same-tab-apply" href="{form}/apply">Apply on employer site</a>
<a id="external-apply" href="{external}/apply" target="_blank">Apply elsewhere</a>
</body></html>
"""

FORM_HTML = """<!doctype html>
<html><body>
<h1>Associate AI Engineer</h1>
<p>Digital Green</p>
<form id="application" onsubmit="event.preventDefault();">
  <h2>Resume selection</h2>
  <label for="resume">Upload resume</label>
  <input id="resume" name="resume" type="file" accept="application/pdf">

  <label for="name">Full name</label>
  <input id="name" name="full_name" type="text">

  <label for="email">Email address</label>
  <input id="email" name="email" type="email">

  <label for="phone">Phone number</label>
  <input id="phone" name="phone" type="tel">

  <label for="country">Country</label>
  <select id="country" name="country">
    <option value="">Select a country</option>
    <option>India</option>
    <option>United Kingdom</option>
  </select>

  <label for="education">University</label>
  <input id="education" name="university" type="text">

  <label for="experience">Years of experience</label>
  <input id="experience" name="years_experience" type="number">

  <label for="summary">Professional experience summary</label>
  <textarea id="summary" name="experience_summary"></textarea>

  <label for="motivation">Why do you want this role?</label>
  <textarea id="motivation" name="motivation" required></textarea>

  <fieldset>
    <legend>Work authorization</legend>
    <label><input id="authorized-yes" type="radio" name="authorized" value="yes">
      I am authorized to work</label>
    <label><input type="radio" name="authorized" value="no"> I am not</label>
  </fieldset>

  <label>
    <input id="consent" name="consent" type="checkbox">
    I certify the information is true and complete
  </label>

  <button id="submit" type="button"
    onclick="window.__submitted = true;
             document.getElementById('outcome').textContent = 'submitted';">
    Submit application
  </button>
  <div id="outcome">not-submitted</div>
</form>
</body></html>
"""

CUSTOM_RESUME_HTML = """<!doctype html>
<html><body>
<h1>Associate AI Engineer</h1>
<main>
  <h2>Resume selection</h2>
  <div role="radiogroup" aria-label="Resume selection">
    <div role="radio" id="card-1" aria-checked="false" tabindex="0"
         class="resume-card">ada-resume.pdf - Uploaded Mar 2026</div>
    <div role="radio" id="card-2" aria-checked="false" tabindex="0"
         class="resume-card">ada-resume-2025.pdf - Uploaded Jan 2026</div>
  </div>
  <button id="continue" type="button"
    onclick="window.__continued = true;">Continue</button>
  <div id="outcome">no-selection</div>
</main>
</body></html>
"""

AUTH_HTML = """<!doctype html>
<html><body>
<h1>Sign in to Indeed</h1>
<form onsubmit="event.preventDefault();">
  <label for="email">Email address</label>
  <input id="email" name="email" type="email">
  <label for="password">Password</label>
  <input id="password" name="password" type="password">
  <button id="sign-in" type="submit">Sign in</button>
</form>
</body></html>
"""

_PAGES = {
    "/job-listing": LISTING_HTML,
    "/apply": FORM_HTML,
    "/apply-cards": CUSTOM_RESUME_HTML,
    "/auth": AUTH_HTML,
}


class _PageHandler(BaseHTTPRequestHandler):
    """Serves the controlled pages; substitute origins into the listing links."""

    listing_origin = ""
    form_origin = ""
    external_origin = ""

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        template = _PAGES.get(path)
        if template is None:
            self.send_error(404)
            return
        body = template.format(
            form=self.form_origin,
            external=self.external_origin,
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def _start_server(host: str, port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer((host, port), _PageHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://{host}:{server.server_address[1]}"


def _approved_origin(url: str) -> ApprovedOrigin:
    parts = urlsplit(url)
    return ApprovedOrigin(
        scheme=parts.scheme.lower(),
        host=(parts.hostname or "").lower(),
        port=parts.port or 80,
    )


class _Origins:
    """Two or three live loopback origins plus a matching navigation policy."""

    def __init__(self) -> None:
        self.servers: list[ThreadingHTTPServer] = []
        try:
            # One port, three loopback hosts: the listing, the application form
            # and an unrelated destination are genuinely cross-origin without
            # relying on a non-standard port the navigation policy would refuse.
            listing_server, self.listing = _start_server("127.0.0.1")
            self.servers.append(listing_server)
            port = listing_server.server_address[1]
            form_server, self.form = _start_server("127.0.0.2", port)
            self.servers.append(form_server)
            external_server, self.external = _start_server("127.0.0.3", port)
            self.servers.append(external_server)
        except Exception:
            self.close()
            raise
        _PageHandler.listing_origin = self.listing
        _PageHandler.form_origin = self.form
        _PageHandler.external_origin = self.external

    @property
    def listing_url(self) -> str:
        return f"{self.listing}/job-listing"

    def policy(self, *, allow_external: bool = False) -> NavigationPolicy:
        extras = [_approved_origin(self.form)]
        if allow_external:
            extras.append(_approved_origin(self.external))
        return NavigationPolicy(
            start_origin=_approved_origin(self.listing),
            extra_origins=tuple(extras),
            redirect_flow=True,
        )

    def close(self) -> None:
        for server in self.servers:
            server.shutdown()
            server.server_close()


async def _wait_for(
    predicate: Callable[[], Awaitable[bool]],
    *,
    timeout: float = 20.0,
    what: str = "condition",
) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if await predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"Timed out waiting for {what}.")


@pytest.fixture
def origins(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "APPLICATION_AUTOMATION_ENABLED", True)
    monkeypatch.setattr(settings, "APPLICATION_BROWSER_HEADLESS", True)
    # The controlled pages are loopback; only this test bypasses the static
    # public-URL guard. Production always keeps it.
    monkeypatch.setattr(
        application_url_security, "normalize_application_url", lambda url: url
    )
    instance = _Origins()
    try:
        yield instance
    finally:
        instance.close()


async def _open(origins: _Origins, *, policy: NavigationPolicy | None = None):
    driver = PlaywrightPersistentBrowserDriver(settings)
    handle = await driver.open(
        origins.listing_url,
        policy=policy or origins.policy(),
        user_id="phase2h1-live-test",
    )
    return handle


async def test_easy_apply_new_tab_is_adopted_and_reaches_the_form(
    origins: _Origins, tmp_path: Path
) -> None:
    handle = await _open(origins)
    try:
        assert await handle.is_alive()
        listing_page = handle._page  # type: ignore[attr-defined]
        context = handle._context  # type: ignore[attr-defined]
        assert len(context.pages) == 1
        assert handle.navigation_epoch == 1

        # The user (this test) clicks Easy Apply; ApplyPilot never does. The
        # anchor opens a real second tab on a different origin. `noopener`
        # suppresses the opener's popup event, so this also proves the
        # context-level page event is what guards and adopts the new tab.
        await listing_page.click("#easy-apply")
        await _wait_for(
            lambda: _current_url_equals(handle, f"{origins.form}/apply"),
            what="the new tab to be adopted as the application page",
        )

        # Adoption is real: the handle follows the new tab, the original
        # listing tab stays open, and the session stays trusted.
        assert len(context.pages) == 2
        assert str(listing_page.url).startswith(origins.listing)
        assert handle.blocked_navigation is False
        assert handle.unsafe_navigation is False
        assert handle.navigation_epoch >= 2

        # Read-only analysis of the live page through the real snapshot.
        live_html = await handle.content()
        form = ApplicationFormAnalyzer().analyze(
            live_html, page_url=SMARTAPPLY_RESUME
        )
        labels = {field.label for field in form.fields}
        assert "Upload resume" in labels
        assert "Email address" in labels
        assert "Phone number" in labels
        assert "Full name" in labels
        assert "University" in labels
        assert "Years of experience" in labels
        assert "Professional experience summary" in labels
        assert "Why do you want this role?" in labels
        assert "I certify the information is true and complete" in labels
        legal = [field for field in form.fields if field.is_legal]
        assert legal and all(field.requires_human_input for field in legal)
        assert all(not field.automation_eligible for field in legal)

        # A required question with no verified answer is surfaced for the user
        # instead of being invented or silently skipped.
        motivation = next(
            field for field in form.fields if field.label == "Why do you want this role?"
        )
        assert motivation.required is True
        assert motivation.requires_human_input is True
        assert motivation.source.suggested_value is None

        # The live HTML satisfies the strict SmartApply resume-selection
        # detector; the loopback URL itself is not a live Indeed page.
        page = inspect_page(SMARTAPPLY_RESUME, live_html)
        assert page is not None
        assert page.step is SmartApplyStep.RESUME_SELECTION
        assert page.resume_upload_detected is True

        # A value the user typed is never overwritten.
        adopted = handle._page  # type: ignore[attr-defined]
        await adopted.fill("#name", "User Typed Name")
        blocked = await handle.apply_field_action(
            FieldAction(
                kind=FieldActionKind.FILL,
                field_id="form0-full-name",
                label="Full name",
                html_id="name",
                text="Ada Lovelace",
            )
        )
        assert blocked.ok is False
        assert "not overwritten" in blocked.reason
        assert await adopted.input_value("#name") == "User Typed Name"

        # Empty verified fields are filled and observed back.
        for html_id, label, value in (
            ("email", "Email address", "ada@example.com"),
            ("phone", "Phone number", "+1 555 0100"),
        ):
            result = await handle.apply_field_action(
                FieldAction(
                    kind=FieldActionKind.FILL,
                    field_id=f"form0-{html_id}",
                    label=label,
                    html_id=html_id,
                    text=value,
                )
            )
            assert result.ok, (label, result.reason)
            assert result.observed_value == value

        selected = await handle.apply_field_action(
            FieldAction(
                kind=FieldActionKind.SELECT,
                field_id="form0-country",
                label="Country",
                html_id="country",
                option_labels=("India",),
            )
        )
        assert selected.ok, selected.reason
        assert await adopted.input_value("#country") == "India"

        # Education and experience are plain verified values, not inferred.
        for html_id, label, value in (
            ("education", "University", "University of London"),
            ("experience", "Years of experience", "7"),
            (
                "summary",
                "Professional experience summary",
                "Seven years building Python services.",
            ),
        ):
            result = await handle.apply_field_action(
                FieldAction(
                    kind=FieldActionKind.FILL,
                    field_id=f"form0-{html_id}",
                    label=label,
                    html_id=html_id,
                    text=value,
                )
            )
            assert result.ok, (label, result.reason)

        # The unanswered required question is left exactly as the user left it.
        assert await adopted.input_value("#motivation") == ""

        # The approved resume is attached to the native file input.
        resume = tmp_path / "ada-resume.pdf"
        resume.write_bytes(b"%PDF-1.4\n%%EOF\n")
        uploaded = await handle.apply_field_action(
            FieldAction(
                kind=FieldActionKind.UPLOAD,
                field_id="form0-resume",
                label="Upload resume",
                html_id="resume",
                upload_path=str(resume),
            )
        )
        assert uploaded.ok, uploaded.reason
        assert (
            await adopted.evaluate(
                "() => document.getElementById('resume').files.length"
            )
            == 1
        )

        # The legal declaration and the final submit were never touched.
        assert await adopted.is_checked("#consent") is False
        assert await adopted.inner_text("#outcome") == "not-submitted"
        assert await adopted.evaluate("() => Boolean(window.__submitted)") is False

        # While the user is typing, automation yields instead of overwriting.
        handle.mark_user_input()
        typing = await handle.apply_field_action(
            FieldAction(
                kind=FieldActionKind.FILL,
                field_id="form0-email",
                label="Email address",
                html_id="email",
                text="other@example.com",
            )
        )
        assert typing.ok is False
        assert "interacting" in typing.reason
        assert await adopted.input_value("#email") == "ada@example.com"
    finally:
        await handle.close()


async def test_legitimate_same_tab_hand_off_is_followed(origins: _Origins) -> None:
    handle = await _open(origins)
    try:
        page = handle._page  # type: ignore[attr-defined]
        await page.click("#same-tab-apply")
        await _wait_for(
            lambda: _current_url_equals(handle, f"{origins.form}/apply"),
            what="the same-tab hand-off",
        )
        assert handle.blocked_navigation is False
        assert handle.unsafe_navigation is False
        assert handle.navigation_epoch >= 2
    finally:
        await handle.close()


async def test_unapproved_new_tab_is_closed_and_the_listing_stays_open(
    origins: _Origins,
) -> None:
    handle = await _open(origins)
    try:
        listing_page = handle._page  # type: ignore[attr-defined]
        context = handle._context  # type: ignore[attr-defined]
        await listing_page.click("#external-apply")

        await _wait_for(
            lambda: _blocked(handle), what="the popup to be refused"
        )
        # The refusal is scoped to the hand-off: the listing page is untouched,
        # the popup is gone and the session is not condemned as compromised.
        assert handle.unsafe_navigation is False
        assert await handle.is_alive() is True
        assert len(context.pages) == 1
        assert str(await handle.current_url()) == origins.listing_url
    finally:
        await handle.close()


async def test_custom_resume_cards_are_reported_and_never_selected(
    origins: _Origins,
) -> None:
    driver = PlaywrightPersistentBrowserDriver(settings)
    handle = await driver.open(
        f"{origins.form}/apply-cards",
        policy=NavigationPolicy(
            start_origin=_approved_origin(origins.form),
            redirect_flow=True,
        ),
        user_id="phase2h1-live-test",
    )
    try:
        html = await handle.content()
        form = ApplicationFormAnalyzer().analyze(html, page_url=SMARTAPPLY_RESUME)
        assert form.fields == ()
        assert any("custom widgets" in warning for warning in form.warnings)

        # There is no click primitive, and no field action can target a card.
        attempted = await handle.apply_field_action(
            FieldAction(
                kind=FieldActionKind.SELECT,
                field_id="form0-card-1",
                label="ada-resume.pdf - Uploaded Mar 2026",
                html_id="card-1",
                option_labels=("ada-resume.pdf - Uploaded Mar 2026",),
            )
        )
        assert attempted.ok is False
        page = handle._page  # type: ignore[attr-defined]
        assert await page.get_attribute("#card-1", "aria-checked") == "false"
        assert await page.get_attribute("#card-2", "aria-checked") == "false"
        assert await page.inner_text("#outcome") == "no-selection"
        assert await page.evaluate("() => Boolean(window.__continued)") is False
    finally:
        await handle.close()


async def test_sign_in_page_is_detected_and_passwords_are_refused(
    origins: _Origins,
) -> None:
    driver = PlaywrightPersistentBrowserDriver(settings)
    handle = await driver.open(
        f"{origins.form}/auth",
        policy=NavigationPolicy(
            start_origin=_approved_origin(origins.form),
            redirect_flow=True,
        ),
        user_id="phase2h1-live-test",
    )
    try:
        html = await handle.content()
        form = ApplicationFormAnalyzer().analyze(html)
        passwords = [
            field
            for field in form.fields
            if field.field_type.value == "password"
        ]
        assert passwords
        assert all(field.requires_human_input for field in passwords)
        assert all(not field.automation_eligible for field in passwords)

        refused = await handle.apply_field_action(
            FieldAction(
                kind=FieldActionKind.FILL,
                field_id="form0-password",
                label="Password",
                html_id="password",
                text="never-entered",
            )
        )
        assert refused.ok is False
        assert "never touched" in refused.reason
        page = handle._page  # type: ignore[attr-defined]
        assert await page.input_value("#password") == ""
    finally:
        await handle.close()


async def _current_url_equals(handle: object, expected: str) -> bool:
    return str(await handle.current_url()).rstrip("/") == expected.rstrip("/")  # type: ignore[attr-defined]


async def _blocked(handle: object) -> bool:
    return bool(handle.blocked_navigation)  # type: ignore[attr-defined]


__all__: list[str] = []
