"""Phase 2E Stage C: real Chromium integration test.

This file launches the installed Chromium through the existing persistent
Playwright driver, serves a controlled local HTML form and exercises the actual
interaction path: Stage B analysis, planning, text/email/tel/number/date/
textarea/select/radio/checkbox filling, resume upload and value verification.

It is skipped by default because it launches a real browser. Run it explicitly:

    APPLYPILOT_RUN_BROWSER_TESTS=1 python -m pytest \
        tests/test_phase2e_field_fill_browser.py -m browser -q
"""

from __future__ import annotations

import os
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

if os.environ.get("APPLYPILOT_RUN_BROWSER_TESTS") != "1":
    pytest.skip(
        "Set APPLYPILOT_RUN_BROWSER_TESTS=1 to run the real Chromium test.",
        allow_module_level=True,
    )

from app.core.config import settings
from app.integrations.browser.field_actions import (
    FieldAction,
    FieldActionKind,
    FieldActionResult,
)
from app.integrations.browser.persistent import (
    PlaywrightPersistentBrowserDriver,
)
from app.models.candidate import Candidate
from app.schemas.application_fill import FillAction, FillFieldStatus
from app.services.application_field_planner import (
    FieldExecutionPlanner,
    PlannedField,
)
from app.services.application_form_analyzer import ApplicationFormAnalyzer
from app.services.field_value_verifier import FieldValueVerifier
from app.services.playwright_field_interactor import PlaywrightFieldInteractor

pytestmark = pytest.mark.browser

FORM_HTML = """
<!doctype html>
<html><body>
<h1>Controlled application form</h1>
<form id="application" onsubmit="event.preventDefault();
  document.getElementById('submit-flag').textContent = 'submitted';">
  <label for="name">Full name</label>
  <input id="name" name="full_name" type="text" required>

  <label for="email">Email address</label>
  <input id="email" name="email" type="email" required>

  <label for="phone">Phone number</label>
  <input id="phone" name="phone" type="tel">

  <label for="city">Current location</label>
  <select id="city" name="city">
    <option value="">Choose...</option>
    <option value="austin">Austin</option>
    <option value="london">London</option>
  </select>

  <label for="years">Years of experience</label>
  <input id="years" name="years_experience" type="number">

  <label for="start">Available from</label>
  <input id="start" name="available_from" type="date">

  <label for="summary">Professional experience summary</label>
  <textarea id="summary" name="experience_summary"></textarea>

  <fieldset>
    <legend>Willing to relocate</legend>
    <label><input type="radio" name="relocate" value="yes"> Yes</label>
    <label><input type="radio" name="relocate" value="no"> No</label>
  </fieldset>

  <fieldset>
    <legend>Technical skills</legend>
    <label><input type="checkbox" name="skills" value="python"> Python</label>
    <label><input type="checkbox" name="skills" value="go"> Go</label>
  </fieldset>

  <label for="pref">Preferred name</label>
  <input id="pref" name="preferred_name" type="text" value="Ada">

  <label for="resume">Upload resume</label>
  <input id="resume" name="resume" type="file">

  <label for="agree">I certify that the information is true and complete</label>
  <input id="agree" name="certify" type="checkbox">

  <label for="otp">Verification code</label>
  <input id="otp" name="otp_code" type="text">

  <div class="g-recaptcha" data-sitekey="local-test-sitekey"></div>

  <button id="submit" type="submit">Submit application</button>
</form>
<div id="submit-flag">not-submitted</div>
</body></html>
"""


class _LocalPolicy:
    """Test-only policy pinned to the local controlled origin."""

    def __init__(self, origin: str) -> None:
        self.origin = origin

    def allows(self, url: str) -> bool:
        return url.startswith(self.origin)

    @property
    def allowed_origins(self) -> tuple[str, ...]:
        return (self.origin,)


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = FORM_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def _start_server() -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def _manual(
    field_id: str,
    label: str,
    action: FillAction,
    value: str,
    *,
    html_id: str | None = None,
    name: str | None = None,
    field_type: str = "text",
) -> PlannedField:
    from app.schemas.application_form import FieldCategory, FormFieldType

    return PlannedField(
        field_id=field_id,
        label=label,
        field_type=FormFieldType(field_type),
        classification=FieldCategory.EMAIL
        if field_type == "email"
        else FieldCategory.PERSONAL_NAME,
        required=True,
        action=action,
        status=FillFieldStatus.READY,
        proposed_value=value,
        html_id=html_id,
        name=name,
    )


async def test_real_chromium_interaction_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "APPLICATION_AUTOMATION_ENABLED", True)
    monkeypatch.setattr(settings, "APPLICATION_BROWSER_HEADLESS", True)

    server, origin = _start_server()
    driver = PlaywrightPersistentBrowserDriver(settings)
    handle = None
    try:
        handle = await driver.open(
            f"{origin}/apply",
            policy=_LocalPolicy(origin),  # type: ignore[arg-type]
            user_id="browser-test-user",
        )
        assert await handle.is_alive()

        candidate = Candidate(
            full_name="Ada Lovelace",
            email="ada@example.com",
            phone="5550100",
            phone_country_code="+1",
            city="Austin",
            skills=["Python", "Go"],
        )
        resume = SimpleNamespace(
            id=uuid.uuid4(), original_filename="ada-resume.pdf", user_id=uuid.uuid4()
        )

        analyzed = await ApplicationFormAnalyzer().analyze_page(
            handle, candidate=candidate
        )
        labels = {item.label for item in analyzed.fields}
        assert "Full name" in labels
        assert "Current location" in labels
        assert "I certify that the information is true and complete" in labels
        assert "Verification code" in labels

        plan = FieldExecutionPlanner().build(
            list(analyzed.fields),
            candidate=candidate,
            resume=resume,  # type: ignore[arg-type]
            resume_owned=True,
        )
        by_label = {item.label: item for item in plan.fields}
        assert by_label["Full name"].status is FillFieldStatus.READY
        assert by_label["Email address"].status is FillFieldStatus.READY
        assert by_label["Phone number"].status is FillFieldStatus.READY
        assert by_label["Current location"].action is FillAction.SELECT
        assert by_label["Technical skills"].action is FillAction.CHECK
        assert by_label["Preferred name"].status is FillFieldStatus.ALREADY_FILLED
        assert (
            by_label["I certify that the information is true and complete"].status
            is FillFieldStatus.NEEDS_HUMAN_INPUT
        )
        assert by_label["Verification code"].status is FillFieldStatus.NEEDS_HUMAN_INPUT
        assert by_label["CAPTCHA challenge"].status is FillFieldStatus.NEEDS_HUMAN_INPUT
        assert by_label["Upload resume"].status is FillFieldStatus.READY

        interactor = PlaywrightFieldInteractor()
        verifier = FieldValueVerifier()
        executed: list[str] = []
        for item in plan.ready_fields:
            if item.action is FillAction.UPLOAD:
                continue
            outcome = await interactor.execute(handle, item)
            assert outcome.ok, (item.label, outcome.reason)
            verified, reason = verifier.verify(item, outcome)
            assert verified, (item.label, reason)
            executed.append(item.label)

        assert "Full name" in executed
        assert "Email address" in executed
        assert "Phone number" in executed
        assert "Current location" in executed
        assert "Technical skills" in executed
        # Human-only fields were never touched.
        assert "I certify that the information is true and complete" not in executed
        assert "Verification code" not in executed
        assert "CAPTCHA challenge" not in executed

        # Number, date and textarea interactions.
        for item in (
            _manual(
                "manual-years",
                "Years of experience",
                FillAction.FILL,
                "8",
                html_id="years",
                name="years_experience",
                field_type="number",
            ),
            _manual(
                "manual-start",
                "Available from",
                FillAction.FILL,
                "2026-12-01",
                html_id="start",
                name="available_from",
                field_type="date",
            ),
            _manual(
                "manual-summary",
                "Professional experience summary",
                FillAction.FILL,
                "Backend engineer focused on reliable distributed systems.",
                html_id="summary",
                name="experience_summary",
                field_type="textarea",
            ),
            _manual(
                "manual-relocate",
                "Willing to relocate",
                FillAction.SELECT,
                "Yes",
                name="relocate",
                field_type="radio_group",
            ),
        ):
            outcome = await interactor.execute(handle, item)
            assert outcome.ok, (item.label, outcome.reason)
            assert verifier.verify(item, outcome)[0], item.label

        # Resume PDF upload through the real file input.
        pdf_path = tmp_path / "ada-resume.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n% controlled integration test resume\n")
        upload_item = _manual(
            "manual-resume",
            "Upload resume",
            FillAction.UPLOAD,
            "ada-resume.pdf",
            html_id="resume",
            name="resume",
            field_type="file",
        )
        upload = await interactor.execute(handle, upload_item, upload_path=str(pdf_path))
        assert upload.ok, upload.reason
        assert upload.observed_value == "ada-resume.pdf"
        assert verifier.verify(upload_item, upload)[0]

        # A field the user already populated is never overwritten.
        existing = _manual(
            "manual-pref",
            "Preferred name",
            FillAction.FILL,
            "Changed by automation",
            html_id="pref",
            name="preferred_name",
        )
        refused = await handle.apply_field_action(
            FieldAction(
                kind=FieldActionKind.FILL,
                field_id=existing.field_id,
                label=existing.label,
                html_id=existing.html_id,
                name=existing.name,
                text=existing.proposed_value,
            )
        )
        assert isinstance(refused, FieldActionResult)
        assert refused.ok is False
        assert "already contains" in refused.reason

        # Nothing submitted: the form stayed on the same URL and the submit
        # handler never ran.
        html = await handle.content()
        assert "not-submitted" in html
        assert await handle.current_url() == f"{origin}/apply"
    finally:
        if handle is not None:
            await handle.close()
        server.shutdown()
        server.server_close()
