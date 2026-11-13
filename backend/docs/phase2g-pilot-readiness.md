---
noteId: "phase2gpilotreadiness000000000001"
tags: []
---

# Phase 2G - Controlled Pilot Readiness & Real-World Validation

## 1. Scope and outcome

Phase 2G validates the integrations and browser workflow that Phase 2F.2.7
deliberately left unexecuted: Google OAuth, Gmail read-only, Glassdoor email
discovery, live Groq job analysis, the Next.js browser workflow, realistic
employer form compatibility, and production-like reliability.

This phase adds **tests, test infrastructure and documentation only**. No
production endpoint, schema, migration, scoring rule or automation behaviour was
changed. One genuine data-integrity defect was found in the answer matcher and
fixed (Section 9), because leaving it would have filled employer and school
fields with the applicant's own name.

Result summary:

* The deterministic intelligence workflow, the Next.js UI and the controlled
  employer-form path are validated end to end.
* **Google OAuth and Gmail were not contacted** with a real account. They need
  a human to complete Google consent, so they are marked *not executed* with an
  exact manual procedure (Section 7).
* **Live Groq was attempted** with the configured key and the provider rejected
  the credential, so the live path is marked *not executed*; all deterministic
  fallbacks are validated (Section 6).

## 2. Repository recovery

`git status --short` before any change showed the expected uncommitted Phase 2F
work and the Phase 2F e2e scaffold, all preserved:

* modified: `frontend/package.json`, `frontend/package-lock.json`;
* untracked: `backend/docs/phase2f-2-6-*`, `backend/docs/phase2f-2-7-*`,
  `backend/tests/test_phase2f_2_7_e2e.py`, `deploy/`, `frontend/e2e/`,
  `frontend/playwright.config.ts`, `frontend/test-results/`.

Nothing was reset, cleaned, reverted or mass-formatted. Phase 2F documentation
was read (`phase2f-1-job-understanding.md`, `phase2f-2-3-advanced-matching.md`,
`phase2f-2-4-resume-comparison.md`, `phase2f-2-6-frontend-intelligence.md`,
`phase2f-2-7-end-to-end-validation.md`). Completed capabilities were not
recreated.

## 3. Integration-test gap analysis

Phase 2F.2.7 left these gaps, all addressed or explicitly closed by this phase:

| Gap | Status |
| --- | --- |
| No frontend Playwright dependency, configuration or browser tests | Closed. `@playwright/test` was already present; e2e infrastructure and 11 browser tests exist and pass |
| Google OAuth / Gmail never contacted | Manual-only; marked not executed with a procedure |
| Live Groq path never executed | Attempted; key rejected, marked not executed |
| Employer form compatibility limited to one controlled form | Closed with a Phase 2G ATS compatibility suite |
| Generic ATS "name" labels mis-resolved to the applicant name | Fixed with regression tests |
| Playwright dev-server flakiness on reload/navigation | Closed by serving e2e from a production build |

## 4. Test environment

* Windows (win32), bash (Git Bash), Chromium installed.
* Python 3.10.18 from the existing `venv_rating` Conda environment (no new
  environment created).
* Backend: FastAPI, SQLAlchemy, PostgreSQL (Neon), Alembic.
* Frontend: Next.js 15.5.29, React 19.1.0, TypeScript 5.7.2, TanStack Query
  5.62.11, Vitest 3.2.4, `@playwright/test` 1.65.
* Backend browser suite: real Chromium through the existing persistent
  Playwright driver.
* `backend/.env` (values never printed): `GOOGLE_CLIENT_ID`,
  `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`, `GMAIL_REDIRECT_URI` and
  `GROQ_API_KEY` are present.

## 5. External integration results

### 5.1 Google OAuth - NOT EXECUTED (requires a human at Google)

The OAuth client, redirect URIs, single-use login code exchange, state/nonce
handling, user provisioning, inactive-user rejection, and token-hash storage are
all covered by executed backend tests (`test_auth_google.py`, 30 tests). A live
login cannot be completed without a human granting consent, so the live flow was
not executed.

### 5.2 Gmail read-only - NOT EXECUTED (requires a human at Google)

Executed mechanically in the regression suite:

* incremental `gmail.readonly` consent with no write scope
  (`test_gmail_api.py::test_connect_returns_consent_url_with_incremental_scope`,
  and `test_google_client_requests_only_basic_scopes`);
* refresh-token encryption round-trip, randomisation, corrupt/empty ciphertext,
  separate key handling (`test_gmail_token_encryption.py`, 13 tests);
* OAuth state binding to the authenticated ApplyPilot user, mismatching/unknown
  state rejection, redirect hardening
  (`test_gmail_api.py`, `test_auth_google.py`);
* connection status with no token leakage, disconnect (with and without
  revocation success), reconnect replacing the single connection
  (`test_gmail_api.py`);
* Gmail client transport, pagination, query building, error mapping, revocation
  (`test_gmail_service.py`, 21 tests).

The live mailbox was not contacted, so read-only consent, disconnect/reconnect
and Glassdoor recommendation discovery are marked not executed.

### 5.3 Glassdoor email discovery - NOT EXECUTED live (deterministic coverage)

Gmail-independent extraction was executed:

* sender and look-alike host validation, display-name spoof rejection
  (`test_glassdoor_jobs.py`);
* safe URL validation (tracking stripped, job id preserved, unsafe and
  look-alike links rejected);
* multi-job layouts, duplicate handling within and across emails, plain-text
  fallback, malformed/absent links ignored, result limits.

The live "read Glassdoor alert emails from Gmail" path needs a connected mailbox
and is marked not executed.

### 5.4 Live Groq job analysis - NOT EXECUTED (credential rejected)

Command:

```bash
python -m pytest tests/test_job_analysis_agent.py::test_live_groq_job_description_analysis -m live -rs -q
```

Result:

```
SKIPPED [1] tests/test_job_analysis_agent.py:228:
Groq rejected the configured GROQ_API_KEY: Groq rejected the configured API key.
```

The key is configured but the provider returns an authentication error, so the
live path is not validated. Executed deterministic coverage that stands in for
the safety requirements:

* structured output validation, normalisation, dedupe, inverted-range rejection;
* unparseable response repair (one retry), code-fenced JSON, schema-violation
  rejection after repair;
* deterministic extraction takes precedence on disagreement; dropped LLM
  claims are recorded (`test_job_intelligence.py::test_merge_never_overwrites_deterministic_values`);
* LLM failure falls back safely and is retried
  (`test_job_intelligence.py::test_llm_failure_falls_back_and_is_retried`);
* missing key raises `LLMConfigurationError` before any network call;
* secrets/redaction (`test_logging_redaction.py`).

No LLM can fabricate job requirements or resume evidence: merged output keeps
deterministic provenance for overlapping fields and records unverified claims as
dropped.

## 6. Frontend Playwright browser results - EXECUTED

Infrastructure lives in `frontend/e2e/` with `frontend/playwright.config.ts`.
The suite runs against a **production Next.js build** (`next build && next
start`) and a local mock API; all data calls are fulfilled at the browser
boundary. Nothing contacts Google, Gmail, Groq or an employer.

Command (from `frontend/`):

```bash
npx playwright test
```

Result: **11 passed (5.7m)**.

| File | Tests | Coverage |
| --- | --- | --- |
| `application-workspace.spec.ts` | 2 | Explicit resume selection persists across reload, no PATCH on open, safe load error |
| `auth.spec.ts` | 4 | Google sign-in UI, signed-out/expired states, protected redirect, in-page 401 session clear |
| `gmail.spec.ts` | 2 | Connected read-only mailbox (scope shown, reconnect/disconnect), disconnected connect action |
| `intelligence.spec.ts` | 2 | Analyze + comparison + close-call uncertainty, safe API-failure copy |
| `resume-intelligence.spec.ts` | 1 | Explicit save, scanned PDF never analyzed |

Every workflow test asserts that no approve or submit request is issued and that
no storage internals appear in the UI.

Infrastructure changes made in this phase:

* `playwright.config.ts` now serves a production build (dev-server recompiles
  caused a blank shell after reload and an aborted navigation); timeouts raised
  to 90s/15s.
* `e2e/support.ts` fixture corrected to the real `ResumeMatchStat` shape
  (`is_default` instead of `best_score`); the production typecheck surfaced it.
* `e2e/intelligence.spec.ts` disclaimer locator scoped with `.first()` to
  resolve a strict-mode collision between two rendered panels.

## 7. Manual OAuth / Gmail procedure (not executed)

Use a dedicated Google test account. Do not use a personal mailbox.

1. In Google Cloud Console, register both redirect URIs exactly as configured:
   `http://127.0.0.1:8000/api/v1/auth/google/callback` and
   `http://127.0.0.1:8000/api/v1/gmail/callback` (development; HTTPS and
   `GMAIL_REDIRECT_URI` are required in production).
2. Start the backend and frontend and sign in through `/login`.
3. Verify the login consent screen requests only `openid email profile`.
4. Open `Gmail Integration`, click connect, and verify the second consent screen
   requests only `https://www.googleapis.com/auth/gmail.readonly` (no send,
   modify, delete or label scope).
5. Confirm the status page shows the test mailbox, `connected`, the read-only
   scope, and no token value.
6. Query the mailbox with a Glassdoor alert email; verify extracted jobs appear
   with original listing URLs and duplicates are skipped.
7. Disconnect; verify the stored refresh token is revoked and removed.
8. Reconnect; verify a single connection row is updated rather than duplicated.
9. Verify logs contain no authorization codes, tokens or email bodies.

Do not request write scopes and do not send, delete, modify or label email.

## 8. Employer form compatibility results - EXECUTED

New suite: `backend/tests/test_phase2g_ats_compatibility.py` (4 tests, local
HTML only). Combined with the existing Phase 2E browser and workflow suites.

| ATS pattern | Coverage | Result |
| --- | --- | --- |
| Standard text fields | Phase 2G contact step, real Chromium fill | Pass |
| Email and phone | Phase 2G + real Chromium | Pass |
| Select, radio, checkbox | Phase 2E form analysis + real Chromium | Pass |
| Resume upload | Real Chromium upload; planner queues human, never auto | Pass |
| Address block | Phase 2G step one (line 1, city, postal code, country) | Pass |
| Education | Phase 2G step two (school, degree, field of study) | Pass |
| Work experience | Phase 2G step two (employer, job title, summary) | Pass |
| Custom labels | Phase 2G unknown question stays human | Pass |
| Multi-step form | Phase 2G two independent steps, no field bleed | Pass |
| Form analysis / classification | Phase 2G + Phase 2E | Pass |
| Safe filling / existing values preserved | Phase 2G + real Chromium + planner | Pass |
| User intervention queue | Phase 2E human-loop suite | Pass |
| Reanalysis detects user-completed fields | Phase 2E human loop; Phase 2G re-read marks `already_filled` | Pass |
| Browser streaming | Phase 2E Stage E real Chromium stream | Pass |
| Final review accuracy | Phase 2E final-review checklist | Pass |

Nothing was submitted and no real employer account was used. CAPTCHA, OTP, MFA
and legal declarations remain user-only.

## 9. Bugs discovered and fixes applied

**1. ATS "name" fields could be filled with the applicant's name
(data-integrity defect).**

`ApplicationAnswerMatcher.identify_field_category` scored the one-word phrase
`name` for `personal.full_name`, and the specific categories also matched at the
same score. Dictionary order made `personal.full_name` win, so labels such as
`School name`, `Company name`, `Employer name` and `University name` resolved to
`Ada Lovelace` and the planner marked them `ready`.

Fix (`backend/app/services/application_answer_matching.py`): added specific
phrases (`school name`, `university name`, `college name`, `institution name`,
`company name`, `employer name`, plus `job title` and `position title` to the
current-title category). These longer phrases outscore the generic `name`, so
the correct profile value wins and the common "Job title" label now maps to the
current title. Regression tests:
`test_phase2g_ats_compatibility.py::test_name_labels_never_resolve_to_the_applicant_name`
and `test_education_and_work_history_use_profile_without_guessing`.

**2. Playwright suite was not runnable against the dev server.**

The initial run failed twice on Next.js dev-server behaviour (blank shell after
reload; `ERR_ABORTED` on navigation). These were infrastructure flakiness, not
product defects. Fixed by building once and serving the production app, raising
timeouts, correcting a fixture type (`is_default` vs `best_score`) and scoping a
strict-mode disclaimer locator. The suite then passed 11/11.

No other genuine defect was found. No production endpoint, schema, migration or
scoring rule was changed.

## 10. Security and reliability findings - EXECUTED

| Concern | Evidence | Result |
| --- | --- | --- |
| Expired sessions | Backend token tests; Playwright expired/signed-out/401 flows | Pass |
| Browser crashes | `test_phase2e_stage_g_hardening.py::test_sweep_closes_crashed_then_expired_browsers` | Pass |
| Navigation changes | `test_phase2e_browser_sessions.py::test_unsafe_navigation_stops_the_session` | Pass |
| Duplicate application creation | `test_applications.py::test_concurrent_preparation_reuses_the_existing_application` | Pass |
| Concurrent resume analysis | `test_resume_intelligence_persistence.py::test_concurrent_analysis_persists_one_row_and_extracts_once` | Pass |
| Cache invalidation | `test_phase2f_2_7_e2e.py` job/resume fingerprint and stale states | Pass |
| Unauthorized API access | Ownership tests across jobs, resumes, applications, browser sessions; Playwright protected redirect | Pass |
| Browser session cleanup | Session close, expiry, sweep and idempotent start tests | Pass |
| Safe error handling | Phase 2G safe-error UI tests; no internals in responses or logs | Pass |
| Resource cleanup | Browser `close`/`close_all`, process-wide ceiling, one browser per user | Pass |
| No secrets logged | `test_logging_redaction.py`; redaction and secret-fingerprint tests | Pass |
| No automatic submission | Every workflow/UI/browser test asserts no submit; `can_submit=false` before approval | Pass |

Existing security controls were preserved; none were weakened.

## 11. Final verification results

| Check | Command | Result |
| --- | --- | --- |
| Backend regression | `pytest -m "not live and not browser"` | **646 passed, 4 skipped, 1 deselected** in 172.71s |
| Phase 2G ATS suite | `pytest tests/test_phase2g_ats_compatibility.py` | **4 passed** |
| Ruff | `ruff check .` | **All checks passed** |
| Live Groq | `pytest ... -m live -rs` | **SKIPPED - API key rejected** (not executed) |
| Real Chromium backend suite | `APPLYPILOT_RUN_BROWSER_TESTS=1 pytest -m browser` | **5 passed** |
| Frontend unit tests | `npm test` | **63 passed** across 12 files |
| TypeScript | `npx tsc --noEmit` | exit 0 |
| ESLint | `npx eslint .` | exit 0 |
| Next.js production build | `npm run build` | exit 0, all routes compiled |
| Frontend Playwright | `npx playwright test` | **11 passed** (5.7m) |
| Alembic | `alembic current` / `heads` / `check` | `d2f7a1c96b04 (head)`; no new upgrade operations |

The 4 backend skips are the browser modules gated by
`APPLYPILOT_RUN_BROWSER_TESTS=1`; the deselection is the live Groq test. Running
the browser suite separately yields the 5 passes above.

## 12. Known limitations

* **Google OAuth and Gmail live flows were not executed.** All mechanics are
  covered by executed tests, but no real consent or mailbox was used.
* **Live Glassdoor discovery was not executed**; it depends on the Gmail path.
* **Live Groq was not executed** because the configured key was rejected by the
  provider. Deterministic extraction and fallback are validated.
* Contacts use in-memory SQLite (existing convention); Alembic is checked
  against PostgreSQL separately.
* `Graduation year` has no candidate source, so it stays with the user rather
  than being derived; this is deliberate and asserted.
* A multi-select skills control is not auto-filled from the comma-separated
  profile value; it is left for the user.
* The Playwright suite uses a local mock API; it validates the UI workflow, not
  live provider behaviour.
* All resumes, jobs, answers and employer forms are synthetic.

## 13. Manual setup instructions

```bash
# Backend (from backend/)
$PY = "C:\\Users\\kiran\\miniconda3\\envs\\venv_rating\\python.exe"

& $PY -m pytest -m "not live and not browser" -p no:warnings --tb=short
& $PY -m ruff check .
& $PY -m pytest tests/test_phase2g_ats_compatibility.py -p no:warnings -q

$env:APPLYPILOT_RUN_BROWSER_TESTS = "1"
& $PY -m pytest -m browser -p no:warnings --tb=short -q

& $PY -m alembic current
& $PY -m alembic heads
& $PY -m alembic check

# Frontend (from frontend/)
npm test
npx tsc --noEmit
npx eslint .
npm run build
npx playwright test
```

Required `backend/.env` keys for live integrations (values never logged):

* `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`
* `GOOGLE_REDIRECT_URI`, `GMAIL_REDIRECT_URI` (HTTPS in production)
* `GROQ_API_KEY` (must be accepted by the provider for the live test)
* Gmail refresh-token encryption key and OAuth session secret per config

## 14. Pilot readiness decision

**Conditionally ready.** The deterministic intelligence workflow, the Next.js
browser workflow, the controlled employer-form path, security and reliability
checks all pass, and the ATS name-mapping defect is fixed with regression tests.

The controlled pilot must **not** begin until Google OAuth and Gmail read-only
consent are validated once with a dedicated test account (Section 7) and a valid
Groq key is supplied. Until then, a pilot should be limited to manual job intake,
deterministic analysis, explicit resume selection and manual application work,
with Gmail discovery and live LLM enrichment disabled. No automated submission
exists, and none should be added for the pilot.
