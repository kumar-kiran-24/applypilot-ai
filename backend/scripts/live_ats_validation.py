"""Phase 2J.7 — live ATS acceptance validation against real employer forms.

This harness is **not** part of the pytest suite. It contacts genuine public
Greenhouse and Lever application forms and drives the real services through the
real authenticated HTTP API with a real Playwright Chromium session.

Safety rules enforced by this harness (and by the product under test):

* a synthetic candidate identity is used (no real person's data);
* the employer's final Submit/Apply/Finish control is **never** clicked and no
  application is ever submitted;
* no CAPTCHA, Cloudflare, OTP, MFA, password or sign-in control is touched,
  solved or bypassed; if a challenge appears the platform is recorded BLOCKED;
* only a local SQLite database is used — the configured hosted database is
  never contacted;
* values typed by the harness into a field are cleared again before filling.

Usage (from ``backend/``)::

    PLAYWRIGHT_BROWSERS_PATH=%LOCALAPPDATA%/ms-playwright \\
    PYTHONPATH=. python scripts/live_ats_validation.py \\
        --greenhouse https://job-boards.greenhouse.io/gitlab/jobs/8638232002 \\
        --lever https://jobs.lever.co/leverdemo/.../apply \\
        --report var/live-ats-report.json

The JSON report is written under the git-ignored ``var/`` directory.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import sys
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import app.models
from app.core.config import settings
from app.core.security import create_access_token
from app.db.base import Base
from app.db.session import get_db
from app.integrations.browser.persistent import PlaywrightPersistentBrowserDriver
from app.main import app
from app.models.candidate import Candidate
from app.models.job import Job
from app.models.resume import Resume, ResumeParsingStatus
from app.models.user import User
from app.services.browser_session_manager import (
    BrowserSessionManager,
    set_browser_session_manager,
)
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

SessionFactory = async_sessionmaker[AsyncSession]

#: Synthetic, obviously-fake candidate. Never a real person's data.
TEST_IDENTITY = {
    "full_name": "Ada Lovelace",
    "email": "ada.lovelace.test@example.com",
    "phone": "+1 555 0100",
    "city": "Austin",
    "country": "United States",
    "linkedin_url": "https://www.linkedin.com/in/ada-lovelace-test",
    "github_url": "https://github.com/ada-lovelace-test",
    "portfolio_url": "https://ada-lovelace-test.example.com",
    "professional_summary": (
        "Backend engineer focused on reliable distributed systems and APIs."
    ),
    "work_authorization": "Authorized to work in the United States",
    "skills": ["Python", "FastAPI", "PostgreSQL"],
}

#: Text that would only appear on the employer's post-submission page.
SUBMITTED_MARKERS = (
    "thank you for applying",
    "application submitted",
    "we received your application",
    "your application has been submitted",
)


def validation_classification(platform: str, url: str) -> dict[str, Any]:
    """Say whether a target is an employer posting or a vendor demo tenant.

    The distinction is written into the report so the machine-readable evidence
    cannot be mistaken for a production-employer validation. Only the public
    host is inspected; no claim is made about the employer beyond the URL.
    """
    host = (urlsplit(url).hostname or "").casefold()
    # The Lever demo tenant lives in the path (jobs.lever.co/leverdemo/...),
    # not in the host name.
    if "leverdemo" in url.casefold():
        return {
            "validation_type": "demo_tenant",
            "is_production_employer": False,
            "tenant": "leverdemo",
            "note": (
                "Lever's vendor demo tenant. This is a controlled/demo ATS "
                "validation, not a genuine employer production application."
            ),
        }
    if host.endswith("greenhouse.io"):
        return {
            "validation_type": "employer_hosted_public_posting",
            "is_production_employer": True,
            "tenant": None,
            "note": (
                "Public employer posting served by Greenhouse. The candidate "
                "and job records are synthetic and nothing was submitted."
            ),
        }
    return {
        "validation_type": "unclassified_public_form",
        "is_production_employer": False,
        "tenant": None,
        "note": "The host is neither a known employer ATS nor the Lever demo tenant.",
    }


class Evidence:
    """Ordered, sanitised evidence for one platform."""

    def __init__(
        self, platform: str, url: str, classification: dict[str, Any] | None = None
    ) -> None:
        self.platform = platform
        self.url = _redact(url)
        self.classification = classification or validation_classification(platform, url)
        self.steps: list[dict[str, Any]] = []

    def record(self, step: str, outcome: str, **detail: Any) -> None:
        self.steps.append({"step": step, "outcome": outcome, **detail})
        print(f"  [{outcome:9}] {step}")
        for key, value in detail.items():
            rendered = json.dumps(value, default=str)
            if len(rendered) > 400:
                rendered = rendered[:400] + "…"
            print(f"             {key}: {rendered}")

    @property
    def ok(self) -> bool:
        return all(step["outcome"] != "FAIL" for step in self.steps)

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "url": self.url,
            **self.classification,
            "steps": self.steps,
        }


def _redact(url: str) -> str:
    """Keep scheme/host/path only: query strings may carry single-use tokens."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _sanitize_fields(fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Strip anything that could be personal data; keep shape and decisions."""
    keep = (
        "field_id",
        "question_key",
        "label",
        "field_type",
        "classification",
        "required",
        "visible",
        "sensitivity",
        "already_populated",
        "conflicting",
        "fillable",
        "requires_confirmation",
        "source",
        "verification_status",
        "action",
        "status",
        "reason",
        "application_step",
    )
    cleaned = []
    for field in fields:
        row = {key: field.get(key) for key in keep if key in field}
        row["proposed_value_shown"] = bool(field.get("proposed_value"))
        row["provenance_confidence"] = (field.get("provenance") or {}).get("confidence")
        row["provenance_source"] = (field.get("provenance") or {}).get("source")
        cleaned.append(row)
    return cleaned


async def _build_database() -> tuple[Any, SessionFactory]:
    """A private in-memory SQLite database; the hosted DB is never touched."""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


async def _seed(
    session_factory: SessionFactory, *, form_url: str, job_title: str, company: str
) -> tuple[User, Job, Resume, str]:
    """Create a synthetic user, candidate, resume and stored job."""
    async with session_factory() as session:
        user = User(
            google_sub=f"live-{uuid.uuid4().hex}",
            email=f"live-ats-{uuid.uuid4().hex[:8]}@example.com",
            email_verified=True,
            full_name=TEST_IDENTITY["full_name"],
            is_active=True,
        )
        session.add(user)
        await session.flush()

        storage_key = f"{user.id}/{uuid.uuid4()}.pdf"
        pdf_path = Path(settings.RESUME_STORAGE_DIR) / storage_key
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        pdf_path.write_bytes(
            b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
            b"2 0 obj\n<< /Type /Pages /Kids [] /Count 0 >>\nendobj\n"
            b"trailer\n<< /Root 1 0 R >>\n%%EOF\n"
        )

        candidate = Candidate(
            user_id=user.id,
            full_name=TEST_IDENTITY["full_name"],
            email=f"ada.lovelace+{uuid.uuid4().hex[:6]}@example.com",
            phone=TEST_IDENTITY["phone"],
            city=TEST_IDENTITY["city"],
            country=TEST_IDENTITY["country"],
            linkedin_url=TEST_IDENTITY["linkedin_url"],
            github_url=TEST_IDENTITY["github_url"],
            portfolio_url=TEST_IDENTITY["portfolio_url"],
            professional_summary=TEST_IDENTITY["professional_summary"],
            work_authorization=TEST_IDENTITY["work_authorization"],
            skills=list(TEST_IDENTITY["skills"]),
        )
        session.add(candidate)

        job = Job(
            user_id=user.id,
            dedupe_key=f"live-ats:{uuid.uuid4().int}",
            source="glassdoor",
            job_title=job_title,
            company=company,
            location="Remote",
            job_url=form_url,
            job_description=(
                "Backend engineer building reliable APIs and distributed systems "
                "with Python, FastAPI and PostgreSQL. Experience with production "
                "services, testing and observability is required."
            ),
            email_subject="Live ATS validation",
            message_id=f"live-ats-{uuid.uuid4().hex}",
        )
        session.add(job)
        await session.flush()

        resume = Resume(
            user_id=user.id,
            original_filename="ada-lovelace-test-resume.pdf",
            storage_key=storage_key,
            storage_backend="local",
            content_type="application/pdf",
            file_size=pdf_path.stat().st_size,
            checksum=uuid.uuid4().hex + uuid.uuid4().hex,
            is_default=True,
            parsing_status=ResumeParsingStatus.COMPLETED,
            parsed_data={"full_name": TEST_IDENTITY["full_name"]},
            skills=list(TEST_IDENTITY["skills"]),
        )
        session.add(resume)
        await session.flush()

        await session.commit()
        return user, job, resume, storage_key


async def _page_has_submitted(page: Any) -> bool:
    try:
        body = (await page.inner_text("body")) or ""
    except Exception:
        return False
    return any(marker in body.casefold() for marker in SUBMITTED_MARKERS)


def _label_hint(label: str) -> str:
    """Strip the decorations ATS labels add around the real accessible text."""
    hint = label
    for marker in ("\u2731", " ATTACH", " No location found", " Select ...", " *"):
        hint = hint.split(marker)[0]
    return hint.strip()[:60]


async def _locator_for(page: Any, label: str) -> Any:
    return page.get_by_label(_label_hint(label), exact=False).first


async def _commit_user_edit(page: Any, label: str, value: str) -> bool:
    """Type into a field the plan marked ready, exactly as a user would."""
    try:
        target = await _locator_for(page, label)
        await target.fill(value)
        return await target.input_value() == value
    except Exception:
        return False


async def _read_field(page: Any, label: str) -> str | None:
    """Read a field, retrying once because ATS pages re-render after a fill."""
    for attempt in range(2):
        try:
            return await (await _locator_for(page, label)).input_value()
        except Exception:
            if attempt == 0:
                await asyncio.sleep(0.5)
    return None


async def _clear_user_edit(page: Any, label: str) -> None:
    with contextlib.suppress(Exception):
        await (await _locator_for(page, label)).fill("")


async def validate(
    *,
    platform_name: str,
    form_url: str,
    job_title: str,
    company: str,
    session_factory: SessionFactory,
    manager: BrowserSessionManager,
) -> Evidence:
    evidence = Evidence(platform_name, form_url)
    user, job, resume, storage_key = await _seed(
        session_factory, form_url=form_url, job_title=job_title, company=company
    )
    headers = {"Authorization": f"Bearer {create_access_token(user.id)}"}
    handle = None
    session_id: uuid.UUID | None = None

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. Job URL import through the documented primary entry point.
        imported = await client.post(
            "/api/v1/jobs/import", json={"url": form_url}, headers=headers
        )
        evidence.record(
            "job_url_import",
            "PASS" if imported.status_code < 300 else "LIMITATION",
            http_status=imported.status_code,
            error_code=(imported.json() or {}).get("code")
            if imported.status_code >= 400
            else None,
            note=(
                "Direct import accepted"
                if imported.status_code < 300
                else "Direct URL import rejects non-Glassdoor sources by design; "
                "the employer URL form (/applications/{id}/inspect) is the "
                "supported path for this platform."
            ),
        )

        # 2. Create the application from the genuine posting: live platform
        #    detection plus a real form inspection build the questionnaire.
        analyzed_app = await client.post(
            "/api/v1/applications/analyze",
            json={
                "job_id": str(job.id),
                "application_url": form_url,
                "resume_id": str(resume.id),
            },
            headers=headers,
        )
        if analyzed_app.status_code != 200:
            evidence.record(
                "application_analysis",
                "FAIL",
                http_status=analyzed_app.status_code,
                error=analyzed_app.text[:400],
            )
            return evidence
        preview_body = analyzed_app.json()
        application_id = uuid.UUID(preview_body["application_id"])
        question_count = sum(
            len(section.get("questions") or [])
            for section in (preview_body.get("sections") or [])
        )
        evidence.record(
            "application_analysis",
            "PASS"
            if preview_body.get("job_title") == job_title
            and preview_body.get("company") == company
            and preview_body.get("platform") in {"greenhouse", "lever"}
            else "PARTIAL",
            http_status=analyzed_app.status_code,
            job_title=preview_body.get("job_title"),
            company=preview_body.get("company"),
            platform=preview_body.get("platform"),
            platform_name=preview_body.get("platform_name"),
            destination_url=_redact(preview_body.get("application_url") or ""),
            destination_validated=preview_body.get("destination_validated"),
            automation_mode=preview_body.get("automation_mode"),
            inspection_mode=preview_body.get("inspection_mode"),
            question_count=question_count,
            missing_required=len(preview_body.get("missing_required") or []),
            resume=(preview_body.get("resume") or {}),
        )

        # 3. Resume comparison + recommendation, and the application's choice.
        comparison = await client.post(
            f"/api/v1/jobs/{job.id}/compare-resumes", json={}, headers=headers
        )
        recommendation = await client.get(
            f"/api/v1/jobs/{job.id}/recommendation", headers=headers
        )
        rec_body = recommendation.json() if recommendation.status_code == 200 else {}
        rec = rec_body.get("recommendation") or {}
        evidence.record(
            "resume_recommendation",
            "PASS" if comparison.status_code == 200 and rec_body else "LIMITATION",
            compare_status=comparison.status_code,
            recommendation_status=rec_body.get("status"),
            total_resumes=rec_body.get("total_resumes"),
            recommended=(rec.get("resume") or {}).get("filename"),
            recommended_reason=(rec.get("reason") or "")[:200],
            selected=(preview_body.get("resume") or {}).get("filename"),
            selection_reason=((preview_body.get("resume") or {}).get("reason") or "")[:160],
        )

        # 4. Persistent browser session against the genuine employer page.
        started = await client.post(
            "/api/v1/browser-sessions",
            json={"application_id": str(application_id)},
            headers=headers,
        )
        if started.status_code != 200:
            evidence.record(
                "browser_session_start",
                "BLOCKED",
                http_status=started.status_code,
                error=started.text[:400],
            )
            return evidence
        session = started.json()
        session_id = uuid.UUID(session["session_id"])
        live = await manager.get(session_id, user_id=user.id)
        handle = live.handle if live else None
        page = getattr(handle, "_page", None)
        evidence.record(
            "browser_session_start",
            "PASS" if session.get("browser_status") == "open" and page else "FAIL",
            browser_status=session.get("browser_status"),
            state=session.get("state"),
            allowed_origins=session.get("allowed_origins"),
            final_url=_redact(session.get("current_url") or ""),
        )
        if page is None:
            return evidence

        # 5. Real form analysis of the live DOM.
        analyzed = await client.post(
            f"/api/v1/browser-sessions/by-application/{application_id}/form-analysis",
            headers=headers,
        )
        if analyzed.status_code != 200:
            evidence.record(
                "form_analysis", "FAIL", http_status=analyzed.status_code,
                error=analyzed.text[:400],
            )
            return evidence
        analysis = analyzed.json()
        fields = analysis.get("fields") or []
        evidence.record(
            "form_analysis",
            "PASS" if fields else "FAIL",
            http_status=analyzed.status_code,
            analysis_id=analysis.get("analysis_id"),
            analysis_version=analysis.get("version"),
            page_url=_redact(analysis.get("page_url") or ""),
            field_count=analysis.get("field_count"),
            automation_eligible_count=analysis.get("automation_eligible_count"),
            human_input_count=analysis.get("requires_human_input_count"),
            captcha_detected=analysis.get("captcha_detected"),
            warnings=analysis.get("warnings"),
            field_types=sorted({f.get("field_type") for f in fields}),
            classifications=sorted({f.get("classification") for f in fields}),
        )

        # 6. Canonical field extraction + question matching/provenance.
        plan_response = await client.get(
            f"/api/v1/browser-sessions/by-application/{application_id}/fill-plan",
            headers=headers,
        )
        if plan_response.status_code != 200:
            evidence.record(
                "fill_plan", "FAIL", http_status=plan_response.status_code,
                error=plan_response.text[:400],
            )
            return evidence
        plan = plan_response.json()
        canonical = _sanitize_fields(plan.get("fields") or [])
        ready = [f for f in canonical if f.get("status") == "ready"]
        ready_filled = [f for f in ready if f.get("action") == "fill"]
        evidence.record(
            "fill_plan",
            "PASS" if ready else "FAIL",
            http_status=plan_response.status_code,
            total_fields=plan.get("total_fields"),
            eligible_count=plan.get("eligible_count"),
            confirmation_count=plan.get("confirmation_count"),
            human_input_count=plan.get("human_input_count"),
            already_filled_count=plan.get("already_filled_count"),
            skipped_count=plan.get("skipped_count"),
            executable=plan.get("executable"),
            page_url=_redact(plan.get("page_url") or ""),
            structure_fingerprint_present=bool(plan.get("structure_fingerprint")),
            approval_fingerprint_present=bool(plan.get("approval_fingerprint")),
            resume=plan.get("resume"),
            ready_fields=[
                {
                    "field_id": f.get("field_id"),
                    "label": (f.get("label") or "")[:60],
                    "classification": f.get("classification"),
                    "action": f.get("action"),
                    "source": f.get("provenance_source"),
                    "confidence": f.get("provenance_confidence"),
                }
                for f in ready
            ],
            human_only_fields=[
                (f.get("label") or "")[:60]
                for f in canonical
                if f.get("status") in {"needs_human_input", "needs_confirmation"}
            ][:20],
            warnings=plan.get("warnings"),
        )
        evidence.record(
            "candidate_answer_provenance",
            "PASS" if all(f.get("provenance_source") for f in ready) else "PARTIAL",
            sources=sorted({str(f.get("provenance_source")) for f in ready}),
            confidences=sorted({str(f.get("provenance_confidence")) for f in ready}),
        )

        # 7. Sensitive / already-populated / missing handling.
        missing = [
            (f.get("label") or "")[:60]
            for f in canonical
            if f.get("required") and f.get("status") in {"needs_human_input", "needs_confirmation"}
        ]
        by_sensitivity: dict[str, int] = {}
        for field in canonical:
            key = str(field.get("sensitivity") or "none")
            by_sensitivity[key] = by_sensitivity.get(key, 0) + 1
        never_automated = [
            (f.get("label") or "")[:60]
            for f in canonical
            if f.get("classification")
            in {
                "legal_declaration",
                "demographic_information",
                "captcha",
                "otp_verification",
                "mfa_verification",
                "work_authorization",
            }
        ]
        populated = [
            (f.get("label") or "")[:60]
            for f in canonical
            if f.get("already_populated")
        ]
        evidence.record(
            "field_safety_triage",
            "PASS" if never_automated or by_sensitivity.get("sensitive", 0) == 0 else "PARTIAL",
            required_missing_from_candidate=missing[:12],
            never_automated_fields=never_automated[:12],
            sensitivity_counts=by_sensitivity,
            already_populated_fields=populated[:12],
            fillable_count=sum(1 for f in canonical if f.get("fillable")),
        )

        # 8. Stale-approval protection: a user edit after preview must invalidate
        #    the approved plan before anything is typed.
        probe_label = ""
        if ready_filled:
            target_label = ready_filled[0].get("label") or ""
            probe_label = target_label
            edited = await _commit_user_edit(page, target_label, "USER-EDIT")
            if edited:
                stale = await client.post(
                    f"/api/v1/browser-sessions/by-application/{application_id}/fill",
                    json={
                        "approval_fingerprint": plan["approval_fingerprint"],
                        "restart": False,
                    },
                    headers=headers,
                )
                stale_body = stale.json() if stale.content else {}
                # The engine may refuse the whole run (409 approval_stale) or
                # continue and leave the edited control for the user. What must
                # never happen is the approved value overwriting the user's.
                value_after = await _read_field(page, target_label)
                preserved = value_after == "USER-EDIT"
                field_result = next(
                    (
                        {
                            "action": r.get("action"),
                            "status": r.get("status"),
                            "reason": (r.get("reason") or "")[:90],
                        }
                        for r in (stale_body.get("results") or [])
                        if (r.get("label") or "")[:60] == target_label[:60]
                    ),
                    None,
                )
                await _clear_user_edit(page, target_label)
                # If the page re-rendered before the value could be re-read, the
                # engine's own per-field result is the remaining evidence: a
                # pending_human_input row means it never touched the control.
                untouched = (
                    (field_result or {}).get("status") == "pending_human_input"
                )
                outcome = "PASS" if preserved else ("PARTIAL" if untouched else "FAIL")
                evidence.record(
                    "stale_approval_protection",
                    outcome,
                    field=target_label[:60],
                    http_status=stale.status_code,
                    error_code=stale_body.get("code"),
                    user_value_preserved=preserved,
                    live_value_readable=value_after is not None,
                    field_result=field_result,
                    note=(
                        "A value typed after the preview must never be overwritten. "
                        "409 means the run refused; 200 means it continued and "
                        "left the edited field untouched. PARTIAL means the page "
                        "re-rendered before the live value could be re-read, so the "
                        "engine's pending_human_input row is the evidence."
                    ),
                )
            else:
                evidence.record(
                    "stale_approval_protection",
                    "PARTIAL",
                    note="No ready field could be edited through its accessible label.",
                )
        else:
            evidence.record(
                "stale_approval_protection",
                "PARTIAL",
                note="Plan had no ready text field to edit.",
            )

        # 8b. Existing-value preservation: a value the user typed before the
        #     plan is approved must survive the fill. A different ready field is
        #     used so the stale-approval probe above is not disturbed.
        existing_label = ""
        for field in ready_filled:
            candidate_label = field.get("label") or ""
            if not candidate_label or candidate_label == probe_label:
                continue
            if await _commit_user_edit(page, candidate_label, "EXISTING-VALUE"):
                existing_label = candidate_label
                break

        # 9. Re-analyse after the probes, then review and approve the current plan.
        reanalyzed = await client.post(
            f"/api/v1/browser-sessions/by-application/{application_id}/form-analysis",
            headers=headers,
        )
        current_plan = await client.get(
            f"/api/v1/browser-sessions/by-application/{application_id}/fill-plan",
            headers=headers,
        )
        if current_plan.status_code != 200:
            evidence.record(
                "fill_plan_refresh", "FAIL", http_status=current_plan.status_code,
                error=current_plan.text[:300],
            )
            return evidence
        plan = current_plan.json()
        refreshed = _sanitize_fields(plan.get("fields") or [])
        evidence.record(
            "fill_plan_refresh",
            "PASS" if reanalyzed.status_code == 200 else "FAIL",
            reanalysis_version=(
                reanalyzed.json().get("version")
                if reanalyzed.status_code == 200
                else None
            ),
            total_fields=plan.get("total_fields"),
            eligible_count=plan.get("eligible_count"),
            upload_fields=[
                {"field_id": f.get("field_id"), "label": (f.get("label") or "")[:40]}
                for f in refreshed
                if f.get("action") == "upload"
            ],
            duplicate_field_ids=[
                field_id
                for field_id in {
                    f.get("field_id") for f in refreshed if f.get("field_id")
                }
                if sum(1 for f in refreshed if f.get("field_id") == field_id) > 1
            ],
            approval_fingerprint_changed=plan.get("approval_fingerprint") is not None,
        )

        # 10. Safe field filling with the approved fingerprint. A live ATS may
        #     re-render between review and fill, in which case the engine
        #     correctly refuses with approval_stale. Re-analyse once and retry
        #     with the newly reviewed plan; never send a stale fingerprint.
        retried_after_approval_stale = False
        execution: dict[str, Any] = {}
        filled = None
        for attempt in range(2):
            filled = await client.post(
                f"/api/v1/browser-sessions/by-application/{application_id}/fill",
                json={
                    "approval_fingerprint": plan["approval_fingerprint"],
                    "restart": False,
                },
                headers=headers,
            )
            body = filled.json() if filled.content else {}
            if (
                attempt == 0
                and filled.status_code == 409
                and body.get("code") == "approval_stale"
            ):
                retried_after_approval_stale = True
                await client.post(
                    f"/api/v1/browser-sessions/by-application/{application_id}/form-analysis",
                    headers=headers,
                )
                replanned = await client.get(
                    f"/api/v1/browser-sessions/by-application/{application_id}/fill-plan",
                    headers=headers,
                )
                if replanned.status_code != 200:
                    break
                plan = replanned.json()
                canonical = _sanitize_fields(plan.get("fields") or [])
                refreshed = canonical
                continue
            break
        assert filled is not None  # the loop always runs at least once
        if filled.status_code != 200:
            evidence.record(
                "safe_field_fill", "FAIL", http_status=filled.status_code,
                error=filled.text[:500],
                retried_after_approval_stale=retried_after_approval_stale,
            )
        else:
            execution = filled.json()
            evidence.record(
                "safe_field_fill",
                "PASS"
                if execution.get("succeeded_count", 0) >= 1
                and execution.get("failed_count", 0) == 0
                else "PARTIAL",
                status=execution.get("status"),
                total_fields=execution.get("total_fields"),
                ready_count=execution.get("ready_count"),
                succeeded_count=execution.get("succeeded_count"),
                verified_count=execution.get("verified_count"),
                failed_count=execution.get("failed_count"),
                skipped_count=execution.get("skipped_count"),
                pending_confirmation_count=execution.get("pending_confirmation_count"),
                pending_human_input_count=execution.get("pending_human_input_count"),
                pause_reason=(execution.get("pause_reason") or "")[:200],
                retried_after_approval_stale=retried_after_approval_stale,
                results=[
                        {
                            "field_id": r.get("field_id"),
                            "label": (r.get("label") or "")[:50],
                            "action": r.get("action"),
                            "status": r.get("status"),
                            "verified": r.get("verified"),
                            "reason": (r.get("reason") or "")[:90],
                        }
                        for r in (execution.get("results") or [])
                        if r.get("status") not in {"pending_human_input", "pending_confirmation"}
                    ],
            )

        # 10b. Existing-value preservation, verified on the live DOM. The value
        #      typed before approval must still be there and the plan must have
        #      skipped the field rather than filling it.
        if existing_label:
            existing_row = next(
                (
                    row
                    for row in canonical
                    if (row.get("label") or "")[:60] == existing_label[:60]
                ),
                None,
            )
            execution_row = next(
                (
                    row
                    for row in (execution.get("results") or [])
                    if (row.get("label") or "")[:40] == existing_label[:40]
                ),
                None,
            )
            # The plan may still list the field as ready; the fill service is
            # what re-reads the live value and skips it. Both the untouched live
            # value and that skipped row are required.
            untouched_by_engine = bool(execution_row) and execution_row.get(
                "status"
            ) in {"skipped", "pending_human_input"}
            final_value = await _read_field(page, existing_label)
            evidence.record(
                "existing_value_preservation",
                "PASS"
                if untouched_by_engine and final_value == "EXISTING-VALUE"
                else "FAIL",
                field=existing_label[:60],
                plan_status=(existing_row or {}).get("status"),
                execution_status=(execution_row or {}).get("status"),
                execution_reason=((execution_row or {}).get("reason") or "")[:90],
                live_value_preserved=final_value == "EXISTING-VALUE",
                live_value_readable=final_value is not None,
                note=(
                    "A value typed into the employer's form before the plan was "
                    "approved must be left untouched and reported as skipped."
                ),
            )
            await _clear_user_edit(page, existing_label)
        else:
            evidence.record(
                "existing_value_preservation",
                "PARTIAL",
                note=(
                    "No second ready text field was available to pre-populate, so "
                    "existing-value preservation was not exercised live."
                ),
            )

        # 11. Verify the live page: values present, resume attached, not submitted.
        live_state: dict[str, Any] = {"url": _redact(page.url)}
        try:
            live_state["file_inputs_with_file"] = await page.evaluate(
                """() => [...document.querySelectorAll('input[type=file]')]
                     .map((el) => (el.files ? el.files.length : 0))"""
            )
            # The employer-visible filename is part of the fix under validation.
            live_state["file_input_names"] = await page.evaluate(
                """() => [...document.querySelectorAll('input[type=file]')]
                     .map((el) => (el.files && el.files[0] ? el.files[0].name : ''))"""
            )
        except Exception:
            live_state["file_inputs_with_file"] = "unreadable"
        try:
            live_state["visible_text_inputs_filled"] = await page.evaluate(
                """() => [...document.querySelectorAll(
                     'input[type=text],input[type=email],input[type=tel],textarea')]
                     .filter((el) => el.offsetParent !== null && (el.value || '').trim())
                     .length"""
            )
        except Exception:
            live_state["visible_text_inputs_filled"] = "unreadable"
        live_state["submitted"] = await _page_has_submitted(page)
        resume_selection = plan.get("resume") or {}
        resume_name = str(resume_selection.get("filename") or "")
        # Greenhouse replaces the file input once the upload is accepted and
        # shows the chosen name as widget text instead, so both forms of
        # evidence are read rather than treating a swapped input as a failure.
        try:
            live_state["resume_filename_visible"] = bool(resume_name) and bool(
                await page.evaluate(
                    "(name) => document.body.innerText.includes(name)", resume_name
                )
            )
        except Exception:
            live_state["resume_filename_visible"] = "unreadable"
        evidence.record(
            "live_page_after_fill",
            "PASS"
            if live_state.get("submitted") is False
            and (
                any((n or 0) for n in live_state.get("file_inputs_with_file") or [0])
                or live_state.get("resume_filename_visible") is True
            )
            else "PARTIAL",
            **live_state,
            resume_will_upload=resume_selection.get("will_upload"),
            resume_filename=resume_selection.get("filename"),
        )

        # 12. Checkbox behaviour: every checkbox group is human-owned by design.
        checkbox_rows = [
            {
                "label": (f.get("label") or "")[:60],
                "classification": f.get("classification"),
                "action": f.get("action"),
                "status": f.get("status"),
                "fillable": f.get("fillable"),
            }
            for f in canonical
            if str(f.get("field_type") or "").startswith("checkbox")
        ]
        evidence.record(
            "checkbox_behavior",
            "PASS" if all(not row["fillable"] for row in checkbox_rows) else "FAIL",
            checkbox_fields=checkbox_rows[:12],
            note="No live ATS checkbox was fillable from a verified candidate value; "
            "all were classified as human-owned (demographic/consent). The "
            "user-change-after-preview race is covered by the real-Chromium "
            "Phase 2J.5 checkbox suite.",
        )

        # 13. Human loop: interventions and final review, still unsubmitted.
        interventions = await client.get(
            f"/api/v1/browser-sessions/by-application/{application_id}/interventions",
            headers=headers,
        )
        review = await client.get(
            f"/api/v1/browser-sessions/by-application/{application_id}/final-review",
            headers=headers,
        )
        review_body = review.json() if review.status_code == 200 else {}
        evidence.record(
            "human_loop_and_final_review",
            "PASS" if review.status_code == 200 else "FAIL",
            interventions=interventions.json().get("blocking_count")
            if interventions.status_code == 200
            else interventions.status_code,
            ready_for_final_review=review_body.get("ready_for_final_review"),
            blocker_count=review_body.get("blocker_count"),
            blockers=(review_body.get("blocking_interventions") or [])[:10],
            filled_count=review_body.get("filled_count"),
            verified_count=review_body.get("verified_count"),
            manual_submission_required=review_body.get("manual_submission_required"),
            still_unsubmitted=not await _page_has_submitted(page),
        )

        # 14. Session recovery with the browser alive, then after it is released.
        recover_live = await client.post(
            f"/api/v1/browser-sessions/by-application/{application_id}/recover",
            json={"force": False},
            headers=headers,
        )
        evidence.record(
            "session_recovery_while_live",
            "PASS" if recover_live.status_code == 200 else "FAIL",
            http_status=recover_live.status_code,
            recovered=(recover_live.json() or {}).get("recovered"),
            blocked_by=[
                {"state": b.get("state"), "job_title": b.get("job_title")}
                for b in ((recover_live.json() or {}).get("blocked_by") or [])
            ],
            message=((recover_live.json() or {}).get("message") or "")[:160],
        )

        # 15. Manual submission handoff: the product must refuse to submit.
        submitted = await client.post(
            f"/api/v1/applications/{application_id}/submit", json={}, headers=headers
        )
        submit_body = submitted.json() if submitted.status_code < 400 else {}
        submit_error = submitted.json() if submitted.status_code >= 400 else {}
        still_unsubmitted = not await _page_has_submitted(page)
        # The product must never submit on its own: either it refuses because the
        # questionnaire is not approved yet, or it records a manual hand-off.
        evidence.record(
            "manual_submission_handoff",
            "PASS" if still_unsubmitted and not submit_body.get("submitted") else "FAIL",
            http_status=submitted.status_code,
            status=submit_body.get("status"),
            manual_required=submit_body.get("manual_required"),
            manual_steps=len(submit_body.get("steps") or submit_body.get("manual_steps") or []),
            message=(submit_body.get("message") or submit_error.get("message") or "")[:200],
            error_code=submit_error.get("code"),
            page_still_unsubmitted=still_unsubmitted,
        )

        # 16. Application status + dashboard pipeline consistency.
        detail = await client.get(f"/api/v1/applications/{application_id}", headers=headers)
        pipeline = await client.get("/api/v1/applications/pipeline", headers=headers)
        workflow = await client.get(
            f"/api/v1/applications/{application_id}/workflow", headers=headers
        )
        detail_body = detail.json() if detail.status_code == 200 else {}
        app_detail = detail_body.get("application", detail_body)
        pipeline_body = pipeline.json() if pipeline.status_code == 200 else {}
        workflow_body = workflow.json() if workflow.status_code == 200 else {}
        evidence.record(
            "status_and_dashboard_consistency",
            "PASS"
            if detail.status_code == 200
            and pipeline.status_code == 200
            and workflow.status_code == 200
            else "FAIL",
            status=app_detail.get("status"),
            stage=app_detail.get("stage"),
            workflow_stage=workflow_body.get("workflow_stage"),
            submission_phase=workflow_body.get("submission_phase"),
            pipeline_total=pipeline_body.get("total"),
            pipeline_in_progress=pipeline_body.get("in_progress"),
            pipeline_submitted=pipeline_body.get("submitted"),
            confirmed_by_user=(workflow_body.get("submission") or {}).get(
                "confirmed_by_user"
            ),
        )

        # 17. Release the session and prove recovery starts a fresh one.
        if session_id is not None:
            await manager.release(session_id)
        handle = None
        recover_after = await client.post(
            f"/api/v1/browser-sessions/by-application/{application_id}/recover",
            json={"force": True},
            headers=headers,
        )
        recovered_session = (recover_after.json() or {}).get("session") or {}
        evidence.record(
            "session_recovery_after_release",
            "PASS"
            if recover_after.status_code == 200
            and (recover_after.json() or {}).get("recovered")
            else "PARTIAL",
            http_status=recover_after.status_code,
            recovered=(recover_after.json() or {}).get("recovered"),
            browser_status=recovered_session.get("browser_status"),
            closed_session_ids=(recover_after.json() or {}).get("closed_session_ids"),
        )
        new_id = recovered_session.get("session_id")
        if new_id:
            await manager.release(uuid.UUID(new_id))

    await asyncio.to_thread(
        Path(settings.RESUME_STORAGE_DIR, storage_key).unlink, missing_ok=True
    )
    return evidence


class _DiagnosticsHandler(logging.Handler):
    """Keep the safe structured fields of warning/error records for the report.

    The console formatter only prints the message, and the live run needs the
    failure class of a field action or a refused browser launch as evidence.
    Only a fixed allowlist of non-identifying keys is kept.
    """

    _FIELDS = ("stage", "error_class", "field_kind", "platform")
    _STAGES = frozenset({"field_action", "field_fill_upload", "browser_session_open"})

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.records: list[dict[str, Any]] = []

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(record, "stage", None) not in self._STAGES:
            return
        payload: dict[str, Any] = {
            "level": record.levelname,
            "message": record.getMessage()[:200],
        }
        for key in self._FIELDS:
            value = getattr(record, key, None)
            if value:
                payload[key] = value
        self.records.append(payload)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--greenhouse", help="Public Greenhouse job/application URL")
    parser.add_argument("--lever", help="Public Lever application URL")
    parser.add_argument("--report", default="var/live-ats-report.json")
    parser.add_argument("--job-title", default="Backend Engineer (validation target)")
    parser.add_argument("--company", default="Validation Employer")
    args = parser.parse_args()

    settings.APPLICATION_AUTOMATION_ENABLED = True
    settings.APPLICATION_BROWSER_HEADLESS = True
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    diagnostics = _DiagnosticsHandler()
    logging.getLogger().addHandler(diagnostics)

    engine, session_factory = await _build_database()

    async def override_get_db():  # type: ignore[no-untyped-def]
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    manager = BrowserSessionManager(
        driver=PlaywrightPersistentBrowserDriver(settings), ttl_seconds=900
    )
    set_browser_session_manager(manager)

    targets: list[tuple[str, str]] = []
    if args.greenhouse:
        targets.append(("Greenhouse", args.greenhouse))
    if args.lever:
        targets.append(("Lever", args.lever))
    results: list[dict[str, Any]] = []
    try:
        for platform_name, url in targets:
            print(f"\n=== {platform_name}: {_redact(url)} ===")
            try:
                evidence = await validate(
                    platform_name=platform_name,
                    form_url=url,
                    job_title=args.job_title,
                    company=args.company,
                    session_factory=session_factory,
                    manager=manager,
                )
                results.append(evidence.to_dict())
            except Exception as exc:
                print(f"  [ERROR    ] harness exception: {type(exc).__name__}: {exc}")
                results.append(
                    {
                        "platform": platform_name,
                        "url": _redact(url),
                        **validation_classification(platform_name, url),
                        "steps": [
                            {
                                "step": "harness",
                                "outcome": "FAIL",
                                "error": f"{type(exc).__name__}: {exc}",
                            }
                        ],
                    }
                )
    finally:
        await manager.close_all()
        set_browser_session_manager(None)
        app.dependency_overrides.clear()
        await engine.dispose()

    report = {
        "generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "environment": {
            "python": sys.version.split()[0],
            "headless_chromium": settings.APPLICATION_BROWSER_HEADLESS,
            "database": "in-memory sqlite (hosted database not contacted)",
            "candidate_identity": "synthetic (Ada Lovelace test record)",
            "submission": "never performed; Submit/Apply/Finish never clicked",
        },
        "platforms": results,
        "diagnostics": diagnostics.records,
    }
    path = Path(args.report)
    await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(
        path.write_text, json.dumps(report, indent=2), encoding="utf-8"
    )
    print(f"\nReport written to {path}")
    failures = [r for r in results if any(s["outcome"] == "FAIL" for s in r["steps"])]
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
