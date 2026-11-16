"""Phase 2J.7: real-Chromium evidence for resume upload verification.

Live filling on Greenhouse showed a successful resume upload being reported as
``The employer's field rejected the value.`` Greenhouse swaps the
``<input type="file">`` node as soon as the file is accepted, so reading the
input back after ``set_input_files`` either detaches or returns an empty list.
The widget then renders the chosen filename as text.

The corrected verification accepts either the input's own file list or the
widget's rendering of the accepted filename, and still fails when the page
shows neither.

Run explicitly:

    APPLYPILOT_RUN_BROWSER_TESTS=1 python -m pytest \
        tests/test_phase2j7_upload_verification_browser.py -m browser -q
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

if __import__("os").environ.get("APPLYPILOT_RUN_BROWSER_TESTS") != "1":
    pytest.skip(
        "Set APPLYPILOT_RUN_BROWSER_TESTS=1 to run the real Chromium test.",
        allow_module_level=True,
    )

from app.core.config import settings
from app.integrations.browser.field_actions import FieldAction, FieldActionKind
from app.integrations.browser.persistent import PlaywrightPersistentBrowserDriver

pytestmark = pytest.mark.browser

#: The ATS shape: the input is replaced once a file is accepted, and the chosen
#: filename is shown as widget text.
SWAPPING_HTML = """
<!doctype html><html><body>
<form>
  <label for="resume">Resume</label>
  <input id="resume" name="resume" type="file">
</form>
<div id="shown"></div>
<script>
  const input = document.getElementById('resume');
  input.addEventListener('change', () => {
    if (!input.files || !input.files.length) return;
    document.getElementById('shown').textContent = input.files[0].name;
    const fresh = document.createElement('input');
    fresh.type = 'file';
    fresh.id = 'resume';
    fresh.name = 'resume';
    input.replaceWith(fresh);
  });
</script>
</body></html>
"""

#: A page that swallows the selection without showing any filename: the
#: verification must not claim success here.
SILENT_HTML = """
<!doctype html><html><body>
<form>
  <label for="resume">Resume</label>
  <input id="resume" name="resume" type="file">
</form>
<script>
  const input = document.getElementById('resume');
  input.addEventListener('change', () => {
    const fresh = document.createElement('input');
    fresh.type = 'file';
    fresh.id = 'resume';
    fresh.name = 'resume';
    input.replaceWith(fresh);
  });
</script>
</body></html>
"""


class _LocalPolicy:
    redirect_flow = False

    def __init__(self, origin: str) -> None:
        self.origin = origin

    def allows(self, url: str) -> bool:
        return url.startswith(self.origin)

    @property
    def allowed_origins(self) -> tuple[str, ...]:
        return (self.origin,)


class _Handler(BaseHTTPRequestHandler):
    body = ""

    def do_GET(self) -> None:  # BaseHTTPRequestHandler API
        payload = self.body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture()
def origin() -> str:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture()
def driver(monkeypatch: pytest.MonkeyPatch) -> PlaywrightPersistentBrowserDriver:
    monkeypatch.setattr(settings, "APPLICATION_AUTOMATION_ENABLED", True)
    monkeypatch.setattr(settings, "APPLICATION_BROWSER_HEADLESS", True)
    return PlaywrightPersistentBrowserDriver(settings)


@pytest.fixture()
def resume_file(tmp_path: Path) -> Path:
    path = tmp_path / "ada-lovelace-test-resume.pdf"
    path.write_bytes(b"%PDF-1.4\n%%EOF\n")
    return path


async def _upload(
    origin: str, driver: PlaywrightPersistentBrowserDriver, resume: Path
):  # type: ignore[no-untyped-def]
    handle = await driver.open(
        f"{origin}/apply",
        policy=_LocalPolicy(origin),  # type: ignore[arg-type]
        user_id="upload-verification-user",
    )
    try:
        return await handle.apply_field_action(
            FieldAction(
                kind=FieldActionKind.UPLOAD,
                field_id="resume",
                label="Resume",
                html_id="resume",
                name="resume",
                upload_path=str(resume),
            )
        )
    finally:
        await handle.close()


async def test_a_swapped_input_still_counts_as_an_accepted_upload(
    origin: str, driver: PlaywrightPersistentBrowserDriver, resume_file: Path
) -> None:
    _Handler.body = SWAPPING_HTML
    result = await _upload(origin, driver, resume_file)
    assert result.ok is True
    assert result.observed_value == resume_file.name


async def test_an_upload_that_shows_no_filename_is_reported_as_failed(
    origin: str, driver: PlaywrightPersistentBrowserDriver, resume_file: Path
) -> None:
    _Handler.body = SILENT_HTML
    result = await _upload(origin, driver, resume_file)
    assert result.ok is False
    assert result.observed_value is None
