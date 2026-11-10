---
noteId: "5e2974d0dc2211f1a81e4f0576e6a512"
tags: []

---

# Phase 2E Stage F - End-to-end workflow

Stage F closes the Phase 2E loop: one owned application is carried from an
approved record, through a real Chromium page, safe field filling, a
human-in-the-loop pause, live user input, re-analysis and a final review, and is
only recorded as submitted after the user explicitly confirms it.

Stage E already documents the live browser relay. Read
[`phase2e-stage-e-browser-streaming.md`](./phase2e-stage-e-browser-streaming.md)
for the CDP screencast, ticket and WebSocket protocol details; this document
covers the workflow that uses it and does not repeat them.

## 1. End-to-end architecture

The workflow is a chain of existing services, not a new orchestrator. Each
record is owned by the stage that created it, and Stage F only reads and derives.

```
Job / Application (Phase 2B-2D)
        │
        ▼
Browser session  ── app/services/browser_session_service.py   (Stage A)
        │            one persistent Playwright page, owner-scoped
        ▼
Form analysis    ── app/services/application_form_service.py   (Stage B)
        │            versioned, immutable snapshots
        ▼
Fill plan        ── app/services/application_field_fill_service.py (Stage C)
        │            safe fields only; resume is the only upload
        ▼
Intervention queue ─ app/services/application_human_loop_service.py (Stage D)
        │            missing information, confirmations, session expiry
        ▼
Live view        ── app/services/browser_view_service.py        (Stage E)
        │            single-use ticket, one attached view
        │            user types/clicks directly in the same page
        ▼
Re-analysis      ── Stage D reads the live DOM and diffs versions
        │            user-entered values are detected, never overwritten
        ▼
Final review     ── Stage D checklist + Stage F workflow snapshot
        │
        ▼
User submits on the employer's page
        │
        ▼
POST /applications/{id}/mark-submitted  {confirmed: true, ...}
                     explicit user evidence is stored
```

Key properties:

* **One browser.** The page shown in the UI, filled by Stage C and typed into
  by the user is the same Playwright context and page.
* **Live values.** `PersistentBrowserHandle.content()` clones the page and
  mirrors the current value of visible text, textarea, select, checkbox and
  radio controls into the detached clone before analysis. Password, hidden and
  file inputs are left untouched, and the employer's live DOM is not mutated.
  Re-analysis therefore sees what the user typed without a second browser.
* **Immutable analyses.** Every re-analysis stores a new version; old versions
  are never edited. Stage D diffs versions to report changes and completions.
* **Derived snapshot.** `GET /api/v1/applications/{id}/workflow` composes
  application status, the latest browser session, analysis, execution and
  intervention counts into `workflow_stage`, `submission_phase` and
  `next_actions`. Nothing is persisted, so it cannot become a second source of
  truth or drift from the owning state machines.

## 2. Implementation map

New in Stage F:

| File | Purpose |
| --- | --- |
| `app/services/application_workflow_service.py` | Read-only derived workflow snapshot and safe next actions. |
| `app/schemas/application_workflow.py` | Snapshot schemas; identifiers, counts, status strings and short reasons only. |
| `app/core/request_context.py` | Correlates `X-Request-ID` with logs. |
| `app/api/v1/endpoints/health.py` (extended) | `/health` and `/ready` probes with an honest single-process/browser-streaming report. |
| `alembic/versions/d4a7b2e9f613_phase2e_stage_d_interventions.py` | Intervention table from Stage D. |
| `tests/test_phase2e_stage_f_workflow.py` | Database/API tests for the snapshot, confirmation, ownership and probes. |
| `tests/test_phase2e_stage_f_e2e_browser.py` | Real Chromium end-to-end test (gated). |

Extended for Stage D/E/F:

* `app/services/application_human_loop_service.py` - intervention queue,
  reconciliation, final review, safe resume.
* `app/services/browser_view_service.py`, `app/api/v1/endpoints/browser_view.py`,
  `app/integrations/browser/streaming.py` - live view (see the Stage E doc).
* `app/integrations/browser/persistent.py` - detached live-value snapshot,
  interaction lock, user-activity timestamp, navigation epoch, internal CDP.
* `app/services/application_field_fill_service.py` - resumable states, pause
  when the user is typing or the page navigates, plan rebuild from a newer
  analysis, `READY_FOR_FINAL_REVIEW`.
* `app/services/application_service.py` - `confirm_manual_submission` now
  requires `confirmed: true` and sanitises evidence.
* `app/api/v1/endpoints/applications.py` - `/workflow`, `/mark-submitted`.
* Frontend: `application-workspace.tsx`, `features/applications/api.ts`,
  `features/applications/hooks.ts`, `types/api.ts` - workflow summary card and
  explicit confirmation flag.

## 3. Test coverage

Three layers, deliberately separated:

1. **Pure unit tests (no browser, no database).** Field planning, response
   validation, diffing, state transitions.
2. **API/database tests (mock browser).** `tests/test_phase2e_*` suites except
   the `*_browser.py` files. They prove ownership, validation, pause/resume
   rules and confirmation semantics but are **not** evidence of live browser
   behaviour.
3. **Real Chromium integration tests (gated).**

   | File | What it proves |
   | --- | --- |
   | `tests/test_phase2e_browser_stream_browser.py` | JPEG frames, click, text, Backspace, wheel, navigation, foreign-Origin refusal, ticket expiry/replay, cross-user denial, cleanup. |
   | `tests/test_phase2e_field_fill_browser.py` | Real safe filling and resume upload, no submit click. |
   | `tests/test_phase2e_human_loop_browser.py` | Live value capture, re-analysis, user-set values preserved. |
   | `tests/test_phase2e_stage_f_e2e_browser.py` | One persisted application through the whole workflow (below). |

The Stage F end-to-end test builds a controlled employer form on loopback and
drives the real services through authenticated HTTP and the real WebSocket:

```
browser session -> form analysis -> fill plan -> safe fill (incl. resume)
-> missing-information pause -> live user input in the same page
-> re-analysis -> safe resume -> final review -> explicit user confirmation
```

It asserts:

* filled values really reached the page and the resume input holds one file;
* the page still shows `not-submitted` after filling, re-analysis and review;
* the live view delivers a frame and forwards mouse/text input;
* re-analysis creates a new version and reports the user-completed field;
* the workflow snapshot reports `ready_for_final_review`, blocking count 0 and
  `confirmed_by_user: false` before confirmation;
* the test (acting as the user) clicks the employer's button, then
  `mark-submitted` records `submitted` and the reference;
* no duplicate application row was created, and releasing the session closes
  the browser and the view registry.

Test-only loopback bypasses of the public-destination/URL guards exist **only**
inside that test. Production keeps those guards.

## 4. Local development and browser tests

Use the existing Conda environment. Do not create another virtualenv.

```powershell
conda activate venv_rating
cd backend
python -m pytest -q                     # full suite; browser tests skip
python -m pytest tests/test_applications.py -q
python -m ruff check .
```

Real Chromium suites are opt-in because they launch a browser and bind local
ports:

```powershell
$env:APPLYPILOT_RUN_BROWSER_TESTS = "1"
conda run -n venv_rating python -m pytest `
  tests/test_phase2e_browser_stream_browser.py `
  tests/test_phase2e_field_fill_browser.py `
  tests/test_phase2e_human_loop_browser.py `
  tests/test_phase2e_stage_f_e2e_browser.py -m browser -q
```

They need Playwright's Chromium in the environment
(`python -m playwright install chromium`), no display (headless works), and free
loopback ports. They do not need a real employer site, credentials or the LLM.

Frontend:

```powershell
cd frontend
npm run typecheck
npm run lint
npm run build
npm run dev   # http://127.0.0.1:3000
```

## 5. Required configuration

Backend (see `backend/.env.example` for the annotated template):

| Variable | Stage F relevance |
| --- | --- |
| `APPLICATION_AUTOMATION_ENABLED` | Master switch. When false, `/submit`-style automation and the live view are unavailable and the UI is told so. |
| `APPLICATION_BROWSER_HEADLESS` | Headless Chromium still produces frames. |
| `APPLICATION_BROWSER_STREAMING_ENABLED` | Required for the live view; streaming/ticket endpoints return 503 otherwise. |
| `APPLICATION_BROWSER_SESSION_TTL_SECONDS` | Live browser lifetime; expiry drives `session_expired` and recovery hints. |
| `APPLICATION_BROWSER_VIEW_TICKET_TTL_SECONDS` | Single-use ticket lifetime (default 45s). |
| `APPLICATION_BROWSER_*` input/ticket limits | View input and issuance rate limits. |
| `BACKEND_CORS_ORIGINS` | Also the WebSocket `Origin` allow-list. Must list the exact frontend origin(s). |
| `DATABASE_URL`, `JWT_SECRET_KEY`, `RESUME_STORAGE_DIR` | Normal API/DB/auth/resume requirements; the workflow snapshot and confirmation read/write them. |

Frontend: `NEXT_PUBLIC_API_BASE_URL` must be an absolute API base (for example
`http://127.0.0.1:8000/api/v1`) so the browser workspace can derive the `ws://`
or `wss://` URL. The ticket is the only query value and is single use.

## 6. Deployment requirements

1. **One backend worker.** In-memory browser sessions, view tickets and active
   views are process-local. `--workers 1` is required today.
2. Chromium system libraries for the Playwright version:
   `python -m playwright install --with-deps chromium`.
3. Bind the API to loopback behind a TLS reverse proxy. Never expose port 8000,
   Chromium's remote-debugging port or any CDP listener; the app opens none.
4. Route the WebSocket upgrade correctly and disable proxy buffering, or frames
   arrive late and input feels broken.
5. Serve the frontend over HTTPS and set `BACKEND_CORS_ORIGINS` to those exact
   origins so the `Origin` check passes.
6. Run `alembic upgrade head` before starting; `/ready` reports database
   readiness and an honest single-process/browser-streaming status.
7. Send `SIGTERM` on deploy/restart so the lifespan shutdown closes every live
   browser and view.

Nginx WebSocket location (adjust hostnames/certificates; the ticket must not be
logged):

```nginx
location /api/v1/browser-sessions/view/ws {
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_buffering off;
    proxy_read_timeout 3600s;
    access_log off;
}

location /api/v1/ {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

**Do not claim production readiness while sessions and tickets are in process
memory.** A multi-worker or multi-host deployment needs sticky routing plus a
shared registry, or a remote browser worker service. Neither is implemented
here.

## 7. Security boundaries

* **No automated submission in the Stage C/D/E/F flow.** Filling stops before
  Submit/Apply/Finish. The final action is the user's click on the employer's
  page.
* **Explicit confirmation only.** `POST /applications/{id}/mark-submitted`
  rejects anything but `confirmed: true` with `409 submission_not_confirmed`.
  Navigation, a vanished form or a frontend status change is never evidence.
* **Evidence is sanitised.** The confirmation URL is reduced to origin + path
  (query strings and fragments are dropped), the reference is truncated and the
  message is capped. Logs record only booleans/ids, never values.
* **No automated submission anywhere (Stage G).** The legacy opt-in path was
  removed: `POST /applications/{id}/submit` now only hands off the manual steps,
  and the adapter/driver click primitives no longer exist. Approval never moves
  an application to `SUBMITTED`. See
  [`phase2e-stage-g-production-hardening.md`](./phase2e-stage-g-production-hardening.md).
* **Nothing sensitive is automated or stored.** CAPTCHA, OTP, MFA, passwords
  and legal declarations are never solved, typed or marked resolved by
  ApplyPilot. Only a fresh analysis or a verified user action can close them.
* **User values are never overwritten.** Filling pauses when the user interacted
  in the last ~1.5s, the page's navigation epoch is re-checked before each
  field, and a newer analysis makes Stage C rebuild its plan instead of reusing
  stale targets. A value the user typed satisfies a missing-information item.
* **Ownership and tickets stay enforced.** Application/session ownership is
  checked before ticket issuance; tickets are hashed, single use, bound to one
  user/session/application, rate limited and short lived. One view per session.
* **Live values stay on the server.** Re-analysis returns labels, metadata and
  change flags, not the typed text or secrets; passwords, hidden and file inputs
  are excluded from the snapshot.

## 8. Known unsupported interactions

* Audio, clipboard, file-picker, drag-and-drop and native browser dialogs are
  not forwarded. Resume upload still uses the Stage C path.
* CAPTCHA, OTP and MFA must be completed by the user on the page; no solver,
  relay or code entry is provided.
* Multi-tab sharing, per-session viewport resizing and multi-worker browsers are
  unsupported. One page per session, fixed 1280x900 viewport.
* A cross-origin popup, download or hard navigation outside the approved
  origins is not followed; the session pauses and asks for re-analysis.
* Sites with strict CSP or unusual shadow-DOM/iframe forms may not be analysable
  or fillable. The failure is reported, not guessed.
* The frontend live viewport has not been visually exercised against a real
  employer site in this environment; it is typechecked, linted, built, and its
  transport is covered by the real integration test.

## 9. Verification commands and status

Stage F was verified on the existing `venv_rating` environment:

| Check | Command | Result |
| --- | --- | --- |
| Full backend suite | `conda run -n venv_rating python -m pytest` | 443 passed, 5 skipped, exit 0 |
| Application tests | `python -m pytest tests/test_applications.py -q` | 27 passed, exit 0 |
| Real Chromium suites | gated four-file command above | 4 passed, exit 0 |
| Ruff lint | `python -m ruff check .` | all checks passed |
| Ruff format check | `python -m ruff format --check .` | fails on 84 pre-existing files (see below) |
| Alembic | `alembic current`, `heads`, `check` | `d4a7b2e9f613 (head)`, no new operations |
| Frontend | `npm run typecheck`, `lint`, `build` | all exit 0 |

`ruff format --check` is not clean on this repository: 84 files, including files
untouched by Stage F (for example `app/models/job_match.py`), would be
reformatted. The baseline is pre-existing, so Stage F did not mass-reformat the
tree; `ruff check` (lint) is clean.

## 10. Remaining work

* Shared/distributed browser session and ticket registry, or a remote browser
  worker, to allow more than one API process.
* Visual QA of the live viewport against real employer pages.
* Persist richer submission evidence (for example a stored confirmation
  screenshot) if audit requirements demand it.
