---
noteId: "506f2590df5d11f1ab35dd3cff4bbf9e"
tags: []

---

# Phase 2J.1 — Direct Job URL Import & Application Workflow

**Status:** implemented, tested with mocked backends and focused backend tests.
**Not validated against a live Glassdoor session or a real employer form.**
Nothing is committed or deployed.

This phase adds the missing entry point between "I found a job on Glassdoor" and
the existing Phase 2H/2I application workspace:

```
Dashboard  ->  Import job URL  ->  Job workspace  ->  Open application
           ->  Detect destination (or enter it manually)
           ->  Analyse form  ->  Review fill preview  ->  Approve safe fill
           ->  Fill  ->  Human review  ->  Manual submission
```

The workflow deliberately reuses the existing job library, browser-session,
redirect, form-analysis, field-planner, answer and final-review services. It does
not add a second job/answer store or a second browser runtime.

---

## 1. Architecture

### Backend

| Concern | Reused / new |
| --- | --- |
| Job storage, ownership, dedupe key | Existing `jobs` table and `normalized_dedupe_key` |
| URL validation, DNS/public-address gate | Existing `application_url_security.validate_application_destination` |
| Glassdoor host/path recognition | Existing `glassdoor_jobs.is_glassdoor_job_url` / `canonicalize_job_url` |
| Destination detection / persistence | Existing `ApplicationRedirectService`; new manual-destination method |
| Browser ownership and lifecycle | Existing `BrowserSessionManager` / `BrowserSessionService`; new per-application lock and recovery method |
| Form analysis, field mapping, evidence, fill plan | Existing Phase 2H/2I planner and endpoints (unchanged contract) |
| Final review and submission handoff | Existing manual final-review surface |

New backend modules:

* `app/services/job_import.py` — validates and stores a pasted listing, derives
  a title from the URL, reuses an existing posting, and reports exactly which
  fields are unknown.
* `app/schemas/job_import.py` — request/response models.
* `POST /api/v1/jobs/import` — authenticated import endpoint.

Extended backend modules:

* `ApplicationRedirectService.set_manual_destination` and `manual_destination`
  on the redirect response.
* `BrowserSessionService.recover`, `SessionBlocker`, `SessionRecoveryOutcome`.
* `BrowserSessionManager.application_lock` and duplicate-row adoption.
* `application_platforms` look-alike host matching was tightened (an explicit
  TLD shape) because `glassdoor.com.evil.com` had been misclassified.
* `POST /browser-sessions/by-application/{id}/destination`
* `POST /browser-sessions/by-application/{id}/recover`

### Frontend

| Route / component | Responsibility |
| --- | --- |
| `src/components/dashboard/apply-job-card.tsx` | Dashboard "Apply to a Job" import card |
| `src/app/apply/page.tsx` | `/apply?url=...`, public so the URL survives sign-in |
| `src/components/applications/apply-return-gate.tsx` | Stores only the return path in a short-lived cookie, then opens login |
| `src/components/applications/apply-import-view.tsx` | Imports the URL for an authenticated visitor and redirects to `/jobs/{id}` |
| `src/features/jobs/import-url.ts` | Client-side pre-validation with the same host/path rules |
| `src/app/login/page.tsx`, `src/app/auth/callback/page.tsx`, `src/features/auth/login-callback-view.tsx` | Read, re-validate and consume the return hint |
| `src/lib/utils/url.ts` | `RETURN_TO_COOKIE`, `safeReturnPath` |
| `src/components/applications/employer-application-url-form.tsx` | `mode="destination"` saves the validated manual destination |
| `src/components/applications/application-browser-workspace.tsx` | Recovery panel, blocked-by explanation, forced recovery, destination mode |
| `src/components/applications/application-fill-panel.tsx` | Fill preview table (field, proposed value, source, status) rendered from the backend fill plan, plus the approve-and-fill controls |
| `src/features/applications/{api,hooks}.ts` | Recovery, destination and active-session calls |
| `src/types/api.ts` | Import, recovery, blocker and manual-destination response types |

---

## 2. API contract

### 2.1 `POST /api/v1/jobs/import`

Authenticated. Body:

```json
{ "url": "https://www.glassdoor.com/job-listing/senior-ai-engineer-JV_IC1.htm?jl=100" }
```

Success (`200`):

```json
{
  "job": { "id": "...", "job_title": "Senior AI Engineer", "company": null, "...": "..." },
  "created": true,
  "reused": false,
  "extraction": {
    "source": "glassdoor",
    "title_source": "url_slug",
    "description_available": false,
    "missing_fields": ["company", "location", "description"],
    "notes": [
      "Glassdoor was not scraped. The stored title was derived from the listing URL; open the original posting to confirm it.",
      "Missing details stay unknown rather than being guessed: company, location, description."
    ]
  }
}
```

Re-importing the same posting returns the same `job.id` with `created: false`,
`reused: true`, and `title_source: "stored_record"`.

Errors:

| Status | Code | Meaning |
| --- | --- | --- |
| 400 | `unsupported_job_listing_source` | A recognised but unsupported board (for example LinkedIn) |
| 400 | `unsupported_job_listing_domain` | Not an exact supported Glassdoor host |
| 400 | `unsupported_job_url_shape` | Glassdoor, but not a job-listing path |
| 400 | `unsafe_application_url` / `unsafe_application_host` | Credentials, port, private IP literal or private DNS answer |
| 401 | — | No authenticated session |
| 422 | — | Missing/overlong URL |

### 2.2 `POST /api/v1/browser-sessions/by-application/{id}/destination`

Body:

```json
{ "destination_url": "https://boards.greenhouse.io/acme/jobs/123" }
```

The URL goes through the same public-address gate as the browser. Job boards,
private hosts and look-alikes are rejected. The response is the existing
`ApplicationRedirectResponse` with:

* `status: "validated"`, `destination_validated: true`;
* `manual_destination: true` so the UI never claims it was auto-detected;
* `listing_url` unchanged;
* `application_platform` / `platform_name` when recognised;
* `can_analyze` as determined by the existing step/platform rules.

The original `application_url` and `job_listing_url` are not overwritten. The
validated destination is stored in `application_destination_url`.

### 2.3 `POST /api/v1/browser-sessions/by-application/{id}/recover`

Optional body: `{ "force": true }`.

Behaviour:

1. Reconcile every session owned by the current user (a crashed/expired browser
   stops counting as live).
2. If a browser for this application is genuinely live, return it (idempotent).
3. If another application has a genuinely live browser and `force` is false,
   return `recovered: false`, `session: null`, and `blocked_by` naming the
   application/session. Nothing is closed.
4. With `force: true`, close only that user's blocking sessions, then start one
   new run for this application.

Response fields include `recovered`, `session`, `blocked_by`,
`closed_session_ids` and a human-readable `message`.

`GET /browser-sessions/by-application/{id}` continues to be the polling source
of truth; a dead browser is reported as `browser_status: "expired"` with
`failure_code: "browser_session_expired"` rather than being presented as live.

---

## 3. Database changes

**No migration is introduced by Phase 2J.1.**

The manual destination uses the columns already added for Phase 2H:

* `applications.application_destination_url`
* `applications.destination_platform`
* `applications.destination_validated`

The import uses the existing `jobs` table and its `(user_id, dedupe_key)`
uniqueness. A concurrent import that loses the unique-constraint race rolls
back and adopts the winning row instead of creating a duplicate.

The new per-application `asyncio.Lock` is process-local. It prevents duplicate
browsers within one backend worker; the database uniqueness constraint remains
the cross-worker guard.

---

## 4. Frontend workflow

1. **Dashboard** — the user pastes a Glassdoor listing URL. The client rejects
   obvious wrong hosts before calling the API; the server is still authoritative.
2. **Import** — `POST /jobs/import`. The UI shows whether the record was new or
   reused, plus the honest "details are unknown" notes.
3. **Workspace** — `/jobs/{id}` displays title, company/location when known, the
   original listing URL, the selected resume and matching score.
4. **Open Application** — the existing browser-session start flow. If a
   validated manual destination exists, that URL is what the browser opens.
5. **Destination** — the existing detection endpoint still covers same-tab,
   popup/new-tab and recognised platforms. When Glassdoor blocks automation or
   the destination is not detected, the workspace offers a manual URL form. The
   copy explicitly says the destination was supplied by the user, not detected.
6. **Analyse / preview / fill** — the existing form analysis and fill-run
   architecture. The preview is a table built row-for-row from the backend fill
   plan: `Field` (label, classification, reason), `Proposed value`,
   `Source` (`Verified profile`, `Confirmed answer`, `Your input`, `Resume`,
   `No source`) and `Status`. A proposed value is shown whenever the plan has
   one, including fields marked `Needs confirmation`; fields without a value
   say `Not available` rather than inventing text. The user must approve safe
   fill; the final Submit/Apply/Finish control is never clicked by ApplyPilot.

### Authentication return preservation

* `/apply` is intentionally outside the authenticated `(app)` layout.
* For a signed-out visitor, `ApplyReturnGate` writes `ap_return_to` with
  `Path=/; Max-Age=600; SameSite=Lax`. The value is only the encoded
  `/apply?url=...` path; it contains no token, code, profile or credential.
* Login and the OAuth callback re-validate the cookie with `safeReturnPath`,
  which accepts only the `/apply` path with an optional `url` parameter (no
  absolute/protocol-relative URLs, control characters, traversal or extra
  parameters).
* The callback clears the cookie after use.

---

## 5. Security properties

* **No fetching of pasted URLs.** The import performs static URL checks plus DNS
  resolution only. It never sends an HTTP request to Glassdoor, never follows a
  redirect and never scrapes.
* **SSRF / rebinding.** The URL must be `http(s)`, must not contain credentials
  or a non-default port, and its host must resolve only to public addresses.
  The browser layer repeats validation and pins the resolved address when it
  opens a destination.
* **Host allow-list.** Only the exact Glassdoor host shape and
  `/job-listing/...` paths are accepted. Look-alikes such as
  `glassdoor.com.evil.com` are rejected; the previous permissive regex was
  fixed.
* **Ownership.** Imports, applications and sessions are scoped to the current
  user. Cross-user access returns 404.
* **Manual destination is not detection.** The API and UI label it as
  user-supplied; the original listing URL is preserved separately.
* **No security bypass.** CAPTCHA, Cloudflare, sign-in, OTP/MFA, passwords and
  legal declarations remain manual. No final submission is automated.
* **Session cleanup.** Recovery re-checks real browser liveness before acting
  and never silently closes another user's session.

---

## 6. Test results

Environment: Windows, existing `venv_rating` Conda environment, branch `main`.
No new environment, deployment, commit or push.

| Check | Command | Actual result |
| --- | --- | --- |
| Phase 2J.1 focused backend | `pytest tests/test_phase2j1_direct_job_import.py` | **18 passed** |
| Affected backend suites | `pytest tests/test_phase2j1_direct_job_import.py tests/test_applications.py tests/test_application_url_workflow.py tests/test_phase2e_browser_sessions.py tests/test_phase2h_smartapply_redirect.py tests/test_phase2i_1_job_workspace.py` | **137 passed** |
| Backend regression (all files, three groups) | `pytest -m "not live and not browser" -p no:warnings` on 16 + 13 + 19 test files | **755 passed** (351 + 141 + 263). A single combined invocation did not finish inside the 500s command budget on this Windows host, so the same full set was run in three sequential groups. |
| Ruff (changed files) | `ruff check` on the new/modified backend files | **All checks passed** |
| Ruff (whole backend) | `ruff check .` | 11 findings, all in pre-existing files not touched by this phase (E501/F401/I001 in the answer-assistant service/schema and older tests) |
| Frontend unit/component | `vitest run` | **228 passed across 24 files** |
| TypeScript | `tsc --noEmit` | clean, exit 0 |
| ESLint | `eslint .` | clean, exit 0 |
| Next.js production build | `next build` | success; `/apply` built as a dynamic route. Also exercised by the Playwright webServer command, which runs `next build && next start`. |
| Playwright E2E | `playwright test` | **26 passed** (23 existing + 3 new), 3.9m |

New frontend tests:

* `tests/import-url.test.ts` — supported hosts/paths, look-alikes, unsafe
  schemes/credentials/ports/private IPs.
* `tests/apply-return-path.test.ts` — only `/apply?url=...` survives; open
  redirects and extra parameters are rejected.
* `tests/apply-job.test.tsx` — validation before the API call, successful
  import and workspace navigation, reused posting, backend rejection surface.
* `tests/submission-safeguard.test.ts` — submission uses the manual handoff
  endpoint and the confirmation flag is explicit.
* `tests/employer-application-url-form.test.tsx` — destination mode saves via
  `/destination` and never calls the inspect endpoint.
* `tests/application-browser-workspace.test.tsx` — recovery names a live
  blocker, ends nothing without `force`, then connects after forced recovery.
* `tests/application-fill-panel.test.tsx` — the preview renders the backend
  plan's field/value/source/status rows (including proposed values for
  `Needs confirmation` and `Not available` for missing legal answers), a fill
  run starts only after the explicit button click with `restart: false`, and no
  submit/apply control exists.

`tests/setup.ts` raises Testing Library's async-query timeout to 5s and
`vitest.config.ts` raises `testTimeout` to 15s. Under full-suite parallel load
on this host, three React Query/RTL suites intermittently exceeded the 1s/5s
defaults while every assertion still passed; running them individually always
passed. No assertion was removed or relaxed.

New Playwright journeys (`e2e/apply-job.spec.ts`):

1. Dashboard import → `/jobs/{id}` with the expected `POST jobs/import` body.
2. Opening `/apply?url=...` authenticated imports and opens the workspace.
3. Signed-out `/apply?url=...` keeps only the path in `ap_return_to` and
   redirects to login.

### Mocked vs live

Everything above is deterministic/mocked or a local production build:

* Backend tests use a fake browser driver and a controlled DNS resolver; no
  browser process and no outbound network call are used.
* Playwright fulfils every BFF data call at the browser boundary and signs in
  with a mock `ap_session` cookie. It does **not** perform Google OAuth, open
  Glassdoor, or contact a real employer.
* The live Groq test is excluded by the documented `-m "not live and not
  browser"` command; a direct full run without that filter failed with
  `APIConnectionError`/`getaddrinfo failed`, which is an environment limitation.
* `tests/test_phase2e_stage_g_hardening.py::test_login_exchange_is_rate_limited_per_client`
  failed once during an initial unfiltered full run, passed when run alone, and
  passed in the documented full rerun. It is an existing timing/isolation flake,
  not reproducible on demand.

### Not validated live

No real browser was launched in this phase. In particular, the following are
implemented but **not live-verified**:

* Glassdoor accepting the user-initiated listing navigation;
* Easy Apply popup/same-tab hand-off on a real posting;
* the real employer form's field labels and the generated fill plan;
* Google OAuth returning to `/apply` with the cookie intact.

---

## 7. Remaining limitations

1. **Glassdoor only.** Direct import accepts exact Glassdoor listing URLs. Other
   boards get a clear error; adding one requires an explicit host/path rule and
   a review of its terms.
2. **No metadata fetch.** Company, location and description stay null after a
   direct import. The title is derived from the URL slug and can be imperfect;
   the response labels it as derived rather than extracted.
3. **No anti-bot bypass.** If Glassdoor blocks the controlled browser, ApplyPilot
   explains it and offers the manual destination form. It does not solve
   CAPTCHA, Cloudflare, MFA or sign-in.
4. **No live browser evidence.** Session recovery, destination opening and form
   analysis are covered with fake drivers and mocked responses only. A staging
   run with real Chromium and a real test employer form is still required.
5. **Single-process lock.** `asyncio.Lock` only serialises starts inside one
   worker. Multiple workers rely on the database uniqueness constraint and may
   still produce a conflict response under a race; they cannot create duplicate
   rows.
6. **Return-path scope.** `safeReturnPath` intentionally allows only `/apply`;
   other intended destinations fall back to the dashboard rather than becoming
   an open redirect.
7. **Existing lint debt.** Whole-repo Ruff still reports 11 pre-existing
   findings; this phase did not reformat unrelated files.
8. **Preview is only as good as the analysis.** The preview reflects the backend
   fill plan exactly, but if the employer changes the form after analysis the
   run fails safely and asks for a re-analysis rather than guessing.
9. **Full backend suite invocation.** The combined `pytest -m "not live and not
   browser"` run exceeded the 500s command budget on this host (all 755 tests
   pass in three groups). This is an environment/throughput limitation, not a
   test failure.

---

## 8. Running the checks

```bash
# Backend (Windows bash, existing Conda env)
PY=/c/Users/kiran/miniconda3/envs/venv_rating/python.exe
cd backend
$PY -m pytest tests/test_phase2j1_direct_job_import.py
$PY -m pytest -m "not live and not browser" -p no:warnings
$PY -m ruff check app/services/job_import.py app/services/application_redirect_service.py \
  app/services/browser_session_service.py app/services/application_platforms.py \
  app/api/v1/endpoints/jobs.py app/api/v1/endpoints/browser_sessions.py \
  app/schemas/job_import.py app/schemas/browser_session.py \
  tests/test_phase2j1_direct_job_import.py

# Frontend
cd frontend
npx vitest run
npx tsc --noEmit
npx eslint .
npx next build
npx playwright test
```

## 9. Manual live-validation checklist (not executed)

1. Sign in with a real Google account and open a Glassdoor listing URL through
   `/apply?url=...` while signed out; confirm the return to `/apply` and import.
2. Import the same listing twice; confirm one job row and `reused: true`.
3. Start a browser, click Easy Apply yourself, and confirm the destination is
   detected and labelled as detected.
4. Block or close the browser process, reload the workspace, and confirm the UI
   reports `expired`; recover and confirm exactly one browser opens.
5. With another application holding a live browser, recover without `force` and
   confirm the blocker is named; then use the explicit force action.
6. Enter a manual Greenhouse/Lever destination, open it, analyse the form, and
   compare the preview rows with the backend plan.
7. Approve safe fill and confirm existing values are untouched, sensitive/legal
   fields stay manual, and Submit/Apply is never clicked.
