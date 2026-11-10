"""Stage F real end-to-end test: one persisted application through one browser.

This is a live Chromium integration test, not a mock. It starts a controlled
local employer form, creates the persisted records, then drives the real
Stage A-E services through the authenticated HTTP API and the real WebSocket
view transport:

    browser session -> form analysis -> fill plan -> safe fill (incl. resume)
    -> missing-information pause -> live user input in the same page
    -> re-analysis -> safe resume -> final review -> explicit user confirmation

It asserts the application is never submitted by ApplyPilot: the controlled
page records a submission only when the test (acting as the user) clicks the
employer's button after the review.

Run separately:

    APPLYPILOT_RUN_BROWSER_TESTS=1 python -m pytest \
        tests/test_phase2e_stage_f_e2e_browser.py -m browser -q
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

if os.environ.get("APPLYPILOT_RUN_BROWSER_TESTS") != "1":
    pytest.skip(
        "Set APPLYPILOT_RUN_BROWSER_TESTS=1 to run the real Stage F E2E test.",
        allow_module_level=True,
    )

import uvicorn
import websockets
from app.core.config import settings
from app.core.security import create_access_token
from app.integrations.browser.persistent import PlaywrightPersistentBrowserDriver
from app.main import app
from app.models.application import Application, ApplicationStatus
from app.models.candidate import Candidate
from app.models.job import Job
from app.models.resume import Resume, ResumeParsingStatus
from app.models.user import User
from app.services.browser_session_manager import (
    BrowserSessionManager,
    set_browser_session_manager,
)
from app.services.browser_view_service import (
    BrowserViewService,
    get_browser_view_service,
    set_browser_view_service,
)
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

pytestmark = pytest.mark.browser

SessionFactory = async_sessionmaker[AsyncSession]

FORM_HTML = b"""<!doctype html>
<html><body>
<h1>Controlled employer application</h1>
<form id="application" onsubmit="event.preventDefault();">
  <label for="full_name">Full name</label>
  <input id="full_name" name="full_name" type="text" required>

  <label for="email">Email address</label>
  <input id="email" name="email" type="email" required>

  <label for="phone">Phone number</label>
  <input id="phone" name="phone" type="tel">

  <label for="city">Current location</label>
  <input id="city" name="city" type="text">

  <label for="resume">Resume</label>
  <input id="resume" name="resume" type="file" required>

  <label for="motivation">Why do you want this role?</label>
  <textarea id="motivation" name="motivation" required></textarea>

  <label>
    <input id="legal" name="legal" type="checkbox" required>
    I certify that the information provided is accurate.
  </label>

  <button id="submit" type="button"
    onclick="document.getElementById('outcome').textContent='submitted';
             window.__submitted = true;">Submit application</button>
  <div id="outcome">not-submitted</div>
</form>
</body></html>
"""


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = FORM_HTML
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def _start_server() -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


async def _start_api(port_holder: list[int]) -> tuple[uvicorn.Server, asyncio.Task[None]]:
    config = uvicorn.Config(
        app, host="127.0.0.1", port=0, log_level="warning", lifespan="off"
    )
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
    task = asyncio.create_task(server.serve())
    for _ in range(200):
        if server.started and server.servers:
            break
        await asyncio.sleep(0.025)
    assert server.started and server.servers
    port_holder.append(server.servers[0].sockets[0].getsockname()[1])
    return server, task


async def _recv_until(
    socket: Any, kind: str, *, timeout: float = 20.0
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    seen: list[dict[str, Any]] = []
    while loop.time() < deadline:
        raw = await asyncio.wait_for(socket.recv(), timeout=deadline - loop.time())
        message = json.loads(raw)
        seen.append(message)
        if message.get("type") == kind:
            return message, seen
    raise AssertionError(f"No {kind!r} message arrived within {timeout}s")


def _ws_url(port: int, ticket: str) -> str:
    return (
        f"ws://127.0.0.1:{port}/api/v1/browser-sessions/view/ws"
        f"?ticket={ticket}"
    )


async def _seed(
    session_factory: SessionFactory, origin: str
) -> tuple[User, Application, Resume, str]:
    async with session_factory() as session:
        user = User(
            google_sub=f"google-{uuid.uuid4().hex}",
            email=f"stage-f-{uuid.uuid4().hex[:8]}@example.com",
            email_verified=True,
            full_name="Ada Lovelace",
            is_active=True,
        )
        session.add(user)
        await session.flush()
        # The storage key format is <user uuid>/<file uuid>.pdf.
        storage_key = f"{user.id}/{uuid.uuid4()}.pdf"
        pdf_path = Path(settings.RESUME_STORAGE_DIR) / storage_key
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        pdf_path.write_bytes(
            b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<<>>\n%%EOF\n"
        )
        candidate = Candidate(
            user_id=user.id,
            full_name="Ada Lovelace",
            email=user.email,
            phone="+1 555 0100",
            city="Austin",
            country="United States",
            skills=["Python", "FastAPI"],
            work_authorization="Authorized to work in the US",
        )
        session.add(candidate)
        job = Job(
            user_id=user.id,
            dedupe_key=f"stage-f:{uuid.uuid4().int}",
            source="glassdoor",
            job_title="Senior Backend Engineer",
            company="Acme",
            location="Remote",
            job_url="https://www.glassdoor.com/job-listing/backend-JV_1.htm",
            job_description="Python, FastAPI and PostgreSQL.",
            email_subject="Jobs for you",
            message_id="msg-stage-f",
        )
        session.add(job)
        await session.flush()
        resume = Resume(
            user_id=user.id,
            original_filename="ada-resume.pdf",
            storage_key=storage_key,
            storage_backend="local",
            content_type="application/pdf",
            file_size=pdf_path.stat().st_size,
            checksum=uuid.uuid4().hex + uuid.uuid4().hex,
            is_default=True,
            parsing_status=ResumeParsingStatus.COMPLETED,
            parsed_data={"full_name": "Ada Lovelace"},
            skills=["Python", "FastAPI"],
        )
        session.add(resume)
        await session.flush()
        application = Application(
            id=uuid.uuid4(),
            user_id=user.id,
            job_id=job.id,
            candidate_id=candidate.id,
            platform="company_hosted",
            platform_name="Company career site",
            application_url=f"{origin}/apply",
            status=ApplicationStatus.READY_FOR_REVIEW,
            resume_id=resume.id,
            resume_filename="ada-resume.pdf",
            resume_reason="Selected for this test.",
            notes=[],
            missing_fields=[],
            automation_details={},
        )
        session.add(application)
        await session.commit()
        return user, application, resume, storage_key


async def test_real_e2e_application_never_submits_itself(
    session_factory: SessionFactory,
) -> None:
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(settings, "APPLICATION_AUTOMATION_ENABLED", True)
    monkeypatch.setattr(settings, "APPLICATION_BROWSER_HEADLESS", True)
    monkeypatch.setattr(settings, "APPLICATION_BROWSER_STREAMING_ENABLED", True)
    # The controlled form is served on loopback; only this test bypasses the
    # public-destination guard. Production never does.
    async def _allow_local(url: str, *, resolver: object = None) -> tuple[str, tuple[str, ...]]:
        del resolver
        return url, ("127.0.0.1",)

    monkeypatch.setattr(
        "app.services.browser_session_service.validate_application_destination",
        _allow_local,
    )
    # The controlled page uses loopback and an ephemeral port. Test-only URL
    # validation bypass; production keeps both guards.
    monkeypatch.setattr(
        "app.services.application_platforms.normalize_application_url",
        lambda url: url,
    )
    monkeypatch.setattr(
        "app.services.application_adapters.normalize_application_url",
        lambda url: url,
    )
    monkeypatch.setattr(
        "app.services.application_url_security.normalize_application_url",
        lambda url: url,
    )

    form_server, origin = _start_server()
    manager = BrowserSessionManager(
        driver=PlaywrightPersistentBrowserDriver(settings), ttl_seconds=900
    )
    set_browser_session_manager(manager)
    set_browser_view_service(BrowserViewService(manager))
    api_server: uvicorn.Server | None = None
    api_task: asyncio.Task[None] | None = None
    storage_key = ""
    try:
        user, application, _resume, storage_key = await _seed(session_factory, origin)
        headers = {"Authorization": f"Bearer {create_access_token(user.id)}"}

        async def override_get_db():  # type: ignore[no-untyped-def]
            async with session_factory() as session:
                yield session

        from app.db.session import get_db

        app.dependency_overrides[get_db] = override_get_db
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            # 1. Open the persistent employer page through the real Stage A API.
            started = await client.post(
                "/api/v1/browser-sessions",
                json={"application_id": str(application.id)},
                headers=headers,
            )
            assert started.status_code == 200, started.text
            session_body = started.json()
            session_id = uuid.UUID(session_body["session_id"])
            assert session_body["browser_status"] == "open"
            live = await manager.get(session_id, user_id=user.id)
            assert live is not None
            handle = live.handle
            page = handle._page  # type: ignore[attr-defined]

            # 2. Analyze the real page.
            analyzed = await client.post(
                f"/api/v1/browser-sessions/by-application/{application.id}/form-analysis",
                headers=headers,
            )
            assert analyzed.status_code == 200, analyzed.text
            analysis = analyzed.json()
            analysis_id = analysis["analysis_id"]
            assert analysis["field_count"] >= 6

            # 3. Build the fill plan and fill only safe fields.
            plan = await client.get(
                f"/api/v1/browser-sessions/by-application/{application.id}/fill-plan",
                headers=headers,
            )
            assert plan.status_code == 200, plan.text
            plan_body = plan.json()
            assert plan_body["eligible_count"] >= 3
            assert plan_body["human_input_count"] >= 1

            filled = await client.post(
                f"/api/v1/browser-sessions/by-application/{application.id}/fill",
                json={"restart": False},
                headers=headers,
            )
            assert filled.status_code == 200, filled.text
            fill = filled.json()
            assert fill["succeeded_count"] >= 3
            assert fill["verified_count"] >= 3

            # Values reached the same page, and nothing was submitted.
            assert await page.locator("#full_name").input_value() == "Ada Lovelace"
            assert await page.locator("#email").input_value() == user.email
            assert await page.locator("#outcome").inner_text() == "not-submitted"
            resume_files = await page.locator("#resume").evaluate(
                "(el) => el.files ? el.files.length : 0"
            )
            assert resume_files == 1

            # 4. The missing-information item paused the run.
            interventions = await client.get(
                f"/api/v1/browser-sessions/by-application/{application.id}/interventions",
                headers=headers,
            )
            assert interventions.status_code == 200
            queue = interventions.json()
            assert queue["blocking_count"] >= 1
            labels = " ".join(item["field_label"] for item in queue["interventions"])
            assert "Why do you want this role" in labels

            # 5. Attach the live view, receive a real frame, and complete the
            #    missing field + legal declaration through the same session.
            issued = await get_browser_view_service().issue(
                user_id=user.id,
                session_id=session_id,
                application_id=application.id,
            )
            api_server, api_task = await _start_api([])
            port = api_server.servers[0].sockets[0].getsockname()[1]

            async with websockets.connect(
                _ws_url(port, issued.ticket),
                origin="http://127.0.0.1:3000",
                max_size=4 * 1024 * 1024,
            ) as socket:
                ready, _ = await _recv_until(socket, "ready")
                assert ready["can_interact"] is True
                frame, _ = await _recv_until(socket, "frame")
                assert frame["data"]

                motivation = await page.locator("#motivation").bounding_box()
                assert motivation is not None
                x = motivation["x"] + motivation["width"] / 2
                y = motivation["y"] + motivation["height"] / 2
                for event in ("down", "up"):
                    await socket.send(
                        json.dumps(
                            {"type": "mouse", "event": event, "x": x, "y": y}
                        )
                    )
                await socket.send(
                    json.dumps(
                        {
                            "type": "text",
                            "text": "I build reliable, safe backend systems.",
                        }
                    )
                )
                await asyncio.sleep(0.3)
                assert "reliable" in await page.locator("#motivation").input_value()

                legal = await page.locator("#legal").bounding_box()
                assert legal is not None
                lx = legal["x"] + legal["width"] / 2
                ly = legal["y"] + legal["height"] / 2
                for event in ("down", "up"):
                    await socket.send(
                        json.dumps(
                            {"type": "mouse", "event": event, "x": lx, "y": ly}
                        )
                    )
                await asyncio.sleep(0.3)
                assert await page.locator("#legal").is_checked() is True
                assert await page.locator("#outcome").inner_text() == "not-submitted"

            # 6. Re-analyze the same page; the completed field is detected and a
            #    new immutable analysis version is stored.
            reconciled = await client.post(
                f"/api/v1/browser-sessions/by-application/{application.id}"
                "/human-loop/reanalyze",
                headers=headers,
            )
            assert reconciled.status_code == 200, reconciled.text
            reconcile = reconciled.json()
            assert reconcile["analysis_id"] != analysis_id
            assert reconcile["analysis_version"] > analysis["version"]
            completed = " ".join(reconcile["newly_completed_fields"]).casefold()
            assert "role" in completed or "motivation" in completed

            # 7. Resume safe filling from the newest analysis.
            resumed = await client.post(
                f"/api/v1/browser-sessions/by-application/{application.id}"
                "/human-loop/resume",
                headers=headers,
            )
            assert resumed.status_code == 200, resumed.text
            assert resumed.json()["status"] in {
                "ready_for_final_review",
                "needs_user_input",
                "completed",
            }

            # 8. Final review is ready and still nothing was submitted.
            review = await client.get(
                f"/api/v1/browser-sessions/by-application/{application.id}"
                "/final-review",
                headers=headers,
            )
            assert review.status_code == 200, review.text
            review_body = review.json()
            assert review_body["ready_for_final_review"] is True, review_body
            assert review_body["blocker_count"] == 0, review_body
            assert await page.locator("#outcome").inner_text() == "not-submitted"

            workflow = await client.get(
                f"/api/v1/applications/{application.id}/workflow", headers=headers
            )
            assert workflow.status_code == 200, workflow.text
            workflow_body = workflow.json()
            assert workflow_body["workflow_stage"] == "ready_for_final_review"
            assert workflow_body["submission_phase"] == "ready_for_final_review"
            assert workflow_body["submission"]["confirmed_by_user"] is False

            # 9. The user submits on the employer's page (the test acts as the
            #    user). ApplyPilot itself never clicked it.
            submit_box = await page.locator("#submit").bounding_box()
            assert submit_box is not None
            await page.locator("#submit").click()
            assert await page.locator("#outcome").inner_text() == "submitted"

            # 10. Explicit confirmation records the submission and evidence.
            confirmed = await client.post(
                f"/api/v1/applications/{application.id}/mark-submitted",
                json={"confirmed": True, "reference": "LOCAL-REF-1"},
                headers=headers,
            )
            assert confirmed.status_code == 200, confirmed.text
            assert confirmed.json()["status"] == "submitted"

            final_workflow = (
                await client.get(
                    f"/api/v1/applications/{application.id}/workflow",
                    headers=headers,
                )
            ).json()
            assert final_workflow["workflow_stage"] == "submitted"
            assert final_workflow["submission_phase"] == "submission_confirmed"
            assert final_workflow["submission"]["reference"] == "LOCAL-REF-1"

        # No duplicate application was created for the same job.
        async with session_factory() as session:
            rows = (
                await session.execute(
                    Application.__table__.select().where(
                        Application.user_id == user.id
                    )
                )
            ).all()
            assert len(rows) == 1

        # Cancelling/releasing closes the browser and the view registry.
        await manager.release(session_id)
        assert await handle.is_alive() is False
        assert get_browser_view_service().active_views() == 0
    finally:
        if api_server is not None:
            api_server.should_exit = True
        if api_task is not None:
            try:
                await asyncio.wait_for(api_task, timeout=10)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                api_task.cancel()
        await manager.close_all()
        set_browser_view_service(None)
        set_browser_session_manager(None)
        form_server.shutdown()
        form_server.server_close()
        if storage_key:
            path = Path(settings.RESUME_STORAGE_DIR) / storage_key
            path.unlink(missing_ok=True)
        app.dependency_overrides.clear()
        monkeypatch.undo()


__all__: list[Any] = []
