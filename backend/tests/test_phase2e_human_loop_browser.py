"""Phase 2E Stage D: real Chromium check for human-completed fields.

This launches the installed Chromium against a controlled local form. It does
not use mocks for browser interaction: it verifies that an existing user value is
detected by a fresh Stage B analysis, that the planner leaves it untouched, that
Stage C refuses to overwrite it, and that the Stage D diff reports the completion
without exposing the value. Legal and CAPTCHA controls remain manual, and the
form is never submitted.

Run separately:
``APPLYPILOT_RUN_BROWSER_TESTS=1 pytest tests/test_phase2e_human_loop_browser.py -m browser``
"""

from __future__ import annotations

import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

if os.environ.get("APPLYPILOT_RUN_BROWSER_TESTS") != "1":
    pytest.skip(
        "Set APPLYPILOT_RUN_BROWSER_TESTS=1 to launch the real Chromium test.",
        allow_module_level=True,
    )

from app.core.config import settings
from app.integrations.browser.field_actions import FieldAction, FieldActionKind
from app.integrations.browser.persistent import PlaywrightPersistentBrowserDriver
from app.integrations.browser.view import browser_view_capability
from app.models.candidate import Candidate
from app.schemas.application_fill import FillFieldStatus
from app.services.application_field_planner import FieldExecutionPlanner
from app.services.application_form_analyzer import ApplicationFormAnalyzer
from app.services.application_human_loop_service import ApplicationHumanLoopService
from app.services.playwright_field_interactor import PlaywrightFieldInteractor

pytestmark = pytest.mark.browser

FORM_HTML = """
<html><body>
<form id="application">
  <label for="years">Years of experience</label>
  <input id="years" name="years_experience" type="number" value="12" required>

  <label for="agree">I certify that the information is true and complete</label>
  <input id="agree" name="certify" type="checkbox">

  <div class="g-recaptcha" data-sitekey="test-site-key"></div>

  <button type="submit">Submit application</button>
</form>
<div id="outcome">not-submitted</div>
<script>
  document.getElementById("application").addEventListener("submit", (event) => {
    event.preventDefault();
    document.getElementById("outcome").textContent = "submitted";
  });
</script>
</body></html>
"""


class _LocalPolicy:
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


async def test_real_chromium_detects_user_completed_field_without_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del tmp_path
    monkeypatch.setattr(settings, "APPLICATION_AUTOMATION_ENABLED", True)
    monkeypatch.setattr(settings, "APPLICATION_BROWSER_HEADLESS", True)

    server, origin = _start_server()
    driver = PlaywrightPersistentBrowserDriver(settings)
    handle = None
    try:
        handle = await driver.open(
            f"{origin}/apply",
            policy=_LocalPolicy(origin),  # type: ignore[arg-type]
            user_id="human-loop-browser-user",
        )
        assert await handle.is_alive()

        analyzed = await ApplicationFormAnalyzer().analyze_page(
            handle,
            candidate=Candidate(
                full_name="Ada Lovelace",
                email="ada@example.com",
                skills=[],
            ),
        )
        fields = list(analyzed.fields)
        years = next(item for item in fields if item.label == "Years of experience")
        legal = next(
            item
            for item in fields
            if item.label == "I certify that the information is true and complete"
        )
        assert years.current_value == "12"
        assert legal.requires_human_input

        plan = FieldExecutionPlanner().build(fields, candidate=None, resume=None)
        planned_years = next(
            item for item in plan.fields if item.label == "Years of experience"
        )
        planned_legal = next(
            item
            for item in plan.fields
            if item.label == "I certify that the information is true and complete"
        )
        assert planned_years.status is FillFieldStatus.ALREADY_FILLED
        assert planned_years.proposed_value is None
        assert planned_legal.status is FillFieldStatus.NEEDS_HUMAN_INPUT

        # A direct fill attempt must be refused because the user already typed it.
        refused = await handle.apply_field_action(
            FieldAction(
                kind=FieldActionKind.FILL,
                field_id=planned_years.field_id,
                label=planned_years.label,
                html_id=planned_years.html_id,
                name=planned_years.name,
                text="99",
            )
        )
        assert refused.ok is False
        assert "already contains" in refused.reason
        assert await handle.content() is not None

        # Re-analysis still sees the user's value, not an automation value.
        reanalyzed = await ApplicationFormAnalyzer().analyze_page(handle)
        fields_again = list(reanalyzed.fields)
        years_again = next(
            item for item in fields_again if item.label == "Years of experience"
        )
        assert years_again.current_value == "12"

        # The Stage D diff reports completion using metadata only. Build a
        # previous-version snapshot with no value, as after the first run.
        previous = [
            item.model_copy(update={"current_value": None}) for item in fields_again
        ]
        changes = ApplicationHumanLoopService._diff(previous, fields_again)
        completed = ApplicationHumanLoopService._newly_completed(
            previous, fields_again
        )
        assert "Years of experience" in completed
        assert any(
            change.field_id == years_again.field_id and change.now_has_value
            for change in changes
        )
        assert "12" not in " ".join(change.model_dump_json() for change in changes)

        # No legal interaction or final submission occurred.
        interactor = PlaywrightFieldInteractor()
        refused_legal = await interactor.execute(handle, planned_legal)
        assert refused_legal.ok is False
        html = await handle.content()
        assert "not-submitted" in html
        assert "data-submitted" not in html
        assert await handle.current_url() == f"{origin}/apply"

        # Stage E registers the real CDP screencast provider; it is available
        # whenever automation and streaming are enabled for this deployment.
        capability = browser_view_capability()
        assert capability.available is True
        assert capability.user_can_interact is True
        assert capability.mode.value == "cdp_relay"
        assert capability.reason
    finally:
        if handle is not None:
            await handle.close()
        server.shutdown()
        server.server_close()


__all__: list[Any] = []
