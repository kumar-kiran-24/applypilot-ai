# ApplyPilot AI — Master Architecture & Product Completion Audit

**Audit date:** 13 November 2026
**Repository:** `C:\Agentic_ai\applypilot-ai` (branch `main`, 18 commits)
**Auditor stance:** repository evidence first. Documentation claims are treated as claims, not proof.
**Code changes made by this audit:** none. One report file created. Two temporary test-log files were created and deleted. No commit, push, migration, database write, deployment action or environment change was performed.

---

## 0. Scope, method and repository state

### What was inspected

| Area | Inspected |
| --- | --- |
| Backend | 17 endpoint modules (142 routes), 55 services, 24 ORM model modules, 21 Alembic revisions, browser integration layer, auth/security core, 48 test files |
| Frontend | 18 pages, 5 route handlers (BFF, auth exchange/logout/session, Gmail connect), 55 components, all `features/*/api.ts` + hooks, types, constants, tests, Playwright E2E |
| Database | Models + full Alembic chain (read-only; no DB mutation) |
| Runtime | `backend/.env` key *presence only* (values never printed), settings resolution |

### Working-tree state (preserved, nothing touched)

- 38 tracked files modified, all uncommitted (Phase 2I/2J.1 work).
- Large untracked surface: `backend/app/services/job_import.py`, `backend/app/schemas/job_import.py`, `backend/app/core/event_loop.py`, `backend/scripts/run_server.py`, `deploy/`, `frontend/e2e/`, `frontend/src/app/apply/`, `frontend/src/app/(app)/jobs/[id]/`, `frontend/src/components/answers/`, `frontend/src/components/assistant/`, and the new Phase 2J.1 tests.
- Recent commits: `35d1fbf fix the bugs`, `7e1e38c update the readme`, `ee0880d build the field extracter using the playright`, …
- **No commit, reset, clean, stash or checkout was performed.**

### Commands actually executed in this audit

| Check | Command | Result |
| --- | --- | --- |
| Backend suite | `python -m pytest -q` (all tests, `venv_rating`) | **755 passed, 1 skipped, 0 failed** |
| Frontend unit/component | `npx vitest run` | **228 passed / 24 files** |
| TypeScript | `npx tsc --noEmit` | **exit 0, clean** |
| Backend lint | `python -m ruff check .` | **11 findings**, all pre-existing (Phase 2I files/tests) |
| Alembic head | `python -m alembic heads` | **`a7c4e18f5b93` (single head)** |
| Alembic chain | `python -m alembic history` | 21 revisions, linear, no branches/merges |
| BFF allowlist probe | temporary Vitest contract probe (created, run, deleted) | **5 client/backend routes rejected** (see §E.1) |

Not executed in this audit: Playwright E2E (mocked), `next build`, `eslint`, live Chromium sessions, live Google OAuth, live Glassdoor. Their previously reported results are recorded as *reported*, not re-verified.

Environment note: `pytest-timeout` is **not installed**; `pytest --timeout=…` fails with "unrecognized arguments". The suite runs without it. Python is 3.10.18 in `C:\Users\kiran\miniconda3\envs\venv_rating`.

---

## A. Product understanding

ApplyPilot AI is an **assisted job-application agent**, not a job board, not a resume checker, not a mass-apply bot.

The intended loop is: *sign in → get a job (Gmail discovery or pasted URL) → understand the posting → recommend the right resume → open the real employer form in a controlled browser → understand its fields → retrieve verified candidate evidence → draft evidence-grounded answers → surface missing/conflicting/sensitive items → user approves → fill safe fields + upload resume → user reviews → user submits → track status.*

The product's differentiator is **evidence and control**, not automation volume:

- Every proposed value carries a source (verified profile, resume, previously approved answer, AI draft, user input) and confidence.
- Legal declarations, work authorisation, demographic/EEO, salary, notice period, CAPTCHA, OTP/MFA, passwords and the final Submit/Apply/Finish control are **never automated**.
- Deterministic validation is authoritative for ownership, safety and sensitive fields; LLMs may draft but must not invent facts.

The objective is to remove repetitive typing while keeping the candidate's facts accurate and the final decision theirs.

---

## B. Current architecture

### Reality, in one line

A Next.js 15 App Router frontend talks only to a **back-end-for-frontend (BFF) allow-list proxy**; the BFF forwards to a FastAPI service that owns all authorisation, intelligence and a process-local Playwright browser pool, persisting to PostgreSQL (Neon).

### Diagram

```mermaid
flowchart TB
    subgraph U[User]
        B[Browser]
    end

    subgraph FE[Next.js 15 · React 19 · App Router]
        P[Pages: /apply · /dashboard · /jobs/[id] · /applications/[id] · /resumes · /profile · /answers · /assistant · /gmail · /missing-information · /auto-apply · /settings]
        C[Client feature layer: api.ts + TanStack Query hooks]
        BFF["/api/backend/[...path] · PROXY_ROUTES allow-list · HttpOnly ap_session cookie"]
        AUTH["/api/auth/exchange|logout|session · /api/gmail/connect"]
        P --> C --> BFF
        P --> AUTH
    end

    subgraph BE[FastAPI · /api/v1 · 142 routes]
        R[auth · jobs · applications · browser-sessions · browser_view · application_fill · application_human_loop · assistant · profile · candidates · resumes · matching · missing_information · auto_apply · gmail · health · metrics]
        S[Services: job_import · job_understanding · resume_intelligence · matching/comparison/recommendation · application_service · questionnaire · answer_assistant/library · redirect · form_analyzer · field_planner · field_fill · human_loop · workflow]
        G[Deterministic core: platform detection · URL/DNS gate · navigation policy · answer safety · approval fingerprint]
        BW[Persistent browser manager · Playwright Chromium · CDP screencast relay · single-use view tickets]
        R --> S --> G
        S --> BW
    end

    subgraph DATA[PostgreSQL · SQLAlchemy async · Alembic]
        T[users · candidates(+nested) · resumes · resume_intelligence · jobs · job_intelligence · job_states · job_resume_matches · applications · application_questions/answers · application_answers · missing_information · auto_apply · browser_sessions · form_analyses · field_executions · interventions · auth_codes · revoked_tokens · gmail_connections]
    end

    subgraph EXT[External]
        GO[Google OAuth + Gmail read-only]
        GR[Groq LLM]
        EMP[Employer sites: Greenhouse · Lever · Ashby · SmartRecruiters · Indeed SmartApply · Workday/iCIMS (manual)]
    end

    B --> P
    BFF --> R
    AUTH --> R
    BW <--> EMP
    S --> GR
    S --> GO
    S --> T
    B -. "ws(s) direct, ticket-authenticated" .-> BW
```

### Layer-by-layer

**Layer 1 — UI (Next.js).** 18 pages. The authenticated shell `(app)/layout.tsx` validates the JWT server-side and redirects unauthenticated users. `/apply` deliberately sits **outside** that layout so a pasted job URL survives Google sign-in (return path stored in a short-lived `ap_return_to` cookie, re-validated by `safeReturnPath`).

**Layer 2 — orchestration (FastAPI).** Routers are thin; business rules live in services; all SQL is scoped by `user_id` resolved from the JWT. A request-id/security-header/logging middleware, a per-principal rate limiter, a revocation denylist, and a background sweeper for expired browser sessions run in `app/main.py`.

**Layer 3 — intelligence.** Deterministic-first: platform detection, URL/SSRF gates, keyword/label classification, answer matching, score computation. Groq (`openai/gpt-oss-120b`) is optional enrichment, validated and provenance-tagged; job/resume intelligence is cached and versioned.

**Layer 4 — browser.** In-process `BrowserSessionManager` holding Playwright Chromium contexts keyed by session id + owner. Navigation is constrained by a `NavigationPolicy` (exact origins + platform domains + operator allow-list), DNS is resolved and pinned. A CDP screencast relay streams JPEG frames over an authenticated single-use-ticket WebSocket; input is narrowly validated. No CDP port is exposed.

**Layer 5 — data.** 24 model modules, 21 linear migrations, composite unique keys such as `(user_id, job_id)` and `(user_id, dedupe_key)`.

### Architectural seams that matter

1. **Two different field models exist side by side.** The *application questionnaire* (`build_questionnaire`) is `CORE_QUESTIONS` ∪ declared/inspected fields; the *live form analysis* (`ApplicationFormAnalyzer` → `ApplicationFormAnalysis`) is what the fill plan and the fill execution use. Approval is computed over the questionnaire; filling is computed over the analysis. Nothing reconciles the two.
2. **`analyze` inspects the real form only when automation is enabled, and then in a throwaway browser.** `ApplicationService._inspect` short-circuits to `adapter.inspect_application(url)` (declared questions) unless `APPLICATION_AUTOMATION_ENABLED` is true, `adapter.supports_automation`, and the URL is not a job board. When it does inspect, it opens its **own** `PlaywrightBrowser` — not the persistent session — so cookies/login state at inspection time can differ from the session the user later sees.
3. **The BFF is a second, hand-maintained API surface** that must be kept in sync with client calls. This audit shows it is already out of sync (§E.1, §F).

---

## C. Implementation inventory

Status vocabulary: **Implemented** (code exists and is coherent) · **Automated-test verified** · **Locally integration-tested** (real local process/DB, not external site) · **Live external verified** (a real third-party site was driven) · **Not implemented** · **Blocked externally**.

### C.1 Authentication & accounts

| Item | Files | APIs | Status |
| --- | --- | --- | --- |
| Google OAuth login, one-time code exchange, JWT in HttpOnly cookie | `api/v1/endpoints/auth.py`, `services/google_oauth.py`, `auth_codes.py`, `app/api/auth/exchange/route.ts` | `GET /auth/google/login`, `/auth/google/callback`, `POST /auth/login/exchange`, `GET /auth/me` | Implemented · Automated-test verified · Live Google OAuth **not** re-verified in this audit |
| Logout + JWT revocation (`jti` denylist, hourly purge) | `services/token_revocation.py`, `models/revoked_token.py`, `app/api/auth/logout/route.ts` | `POST /auth/logout` | Implemented · Automated-test verified |
| Session validation in the UI | `app/(app)/layout.tsx`, `lib/auth/session.ts`, `lib/auth/cookies.ts` | — | Implemented |
| Rate limiting (auth + API, per principal) | `core/rate_limit.py` | middleware | Implemented · Automated-test verified |

### C.2 Candidate profile & knowledge

| Item | Files | APIs | Status |
| --- | --- | --- | --- |
| Profile, education, experience, projects, certifications, preferences | `api/v1/endpoints/profile.py` (598 LOC), `candidate_service.py`, models | 22 `/profile…` routes | Implemented · Automated-test verified |
| Reusable answers with verification/provenance/freshness/categories/conflicts | `answer_library.py`, `application_answer_matching.py`, `answer_safety.py` | `GET/PUT/DELETE /profile/answers…`, `/resolve`, `/knowledge` | Implemented · Automated-test verified |
| Missing-information queue + conflict resolution | `missing_information_service.py` | 7 `/missing-information…` routes | Implemented · Automated-test verified |
| Candidate knowledge centre UI | `frontend/src/components/answers/answer-library.tsx`, `app/(app)/answers` | — | Implemented |

### C.3 Resumes

| Item | Files | APIs | Status |
| --- | --- | --- | --- |
| Upload (PDF only, size cap), local opaque storage, delete, default flag | `resume_service.py`, `resume_storage.py`, `endpoints/resumes.py` | `POST /resumes/upload`, `GET/DELETE /resumes/{id}`, `PATCH /resumes/{id}/default`, `GET /resumes/{id}/download` | Implemented · Automated-test verified |
| Parsing | `resume_parser.py` (611 LOC) | — | Implemented |
| Resume intelligence extraction + cache | `resume_intelligence_service.py`, `_extraction.py`, `models/resume_intelligence.py` | `POST /resumes/{id}/analyze`, `GET /resumes/{id}/intelligence` | Implemented · Automated-test verified |
| Multi-resume comparison + recommendation | `resume_comparison_service.py`, `resume_recommendation_service.py`, `advanced_matching_engine.py` | `POST /jobs/{id}/compare-resumes`, `GET /jobs/{id}/recommendation` | Implemented · Automated-test verified |
| Resume chosen for an application, manual override preserved across re-analysis | `application_service._select_resume`, `select_resume` | `PATCH /applications/{id}/resume` | Implemented · Automated-test verified |
| Upload handler used during filling (ownership + temp file + cleanup) | `resume_upload_handler.py` | — | Implemented · Automated-test verified |

Safety: storage keys are `uuid/uuid.pdf` and re-validated by regex before any filesystem access; `ensure_owned` compares `resume.user_id`; temp copies are deleted in `finally`. **No cross-user file path is reachable.**

### C.4 Job discovery, import & intelligence

| Item | Files | APIs | Status |
| --- | --- | --- | --- |
| Gmail read-only OAuth, encrypted refresh token | `gmail_oauth.py`, `gmail_service.py`, `gmail_token_encryption.py`, `endpoints/gmail.py` | connect/callback/status/disconnect | Implemented · Automated-test verified · Live Gmail **not** re-verified |
| Glassdoor alert extraction from email | `glassdoor_jobs.py` (864 LOC) | `GET /gmail/glassdoor/jobs`, `POST /jobs/discover` | Implemented · Automated-test verified |
| Direct job URL import (Glassdoor only, no fetching) | **`job_import.py` (new)**, `schemas/job_import.py` | `POST /jobs/import` | Implemented · Automated-test verified · **unreachable through the BFF** (§E.1) |
| Job list: search/filter/sort/paging, saved/dismissed state | `job_service.py`, `models/job_state.py` | `GET /jobs`, `/jobs/saved`, `/save`, `/dismiss`, `/restore` | Implemented · Automated-test verified · Live local DB verified per `runtime-issues-nov-13-2026.md` |
| Job intelligence (deterministic + optional Groq, cached, provenance) | `job_understanding_service.py`, `job_intelligence_extraction.py` | `POST /jobs/{id}/analyze`, `GET /jobs/{id}/intelligence` | Implemented · Automated-test verified · live Groq test skipped without network |
| Job↔resume matching | `matching_service.py`, `matching_engine.py`, `advanced_matching_*` | `POST /jobs/{id}/match`, `GET /jobs/{id}/matches`, `POST /matching/bulk`, `GET /matching/summary` | Implemented · Automated-test verified |

Honest limitation, correctly encoded in the code: a pasted Glassdoor URL cannot yield company/location/description without scraping, so `ImportExtraction.missing_fields` reports them as unknown.

### C.5 Application lifecycle

| Item | Files | APIs | Status |
| --- | --- | --- | --- |
| Prepare application, platform detection, questionnaire, resume recommendation | `application_service.py` (1583 LOC), `application_questionnaire.py` (817 LOC), `application_adapters.py` | `POST /applications/analyze`, `GET /applications/{id}` | Implemented · Automated-test verified |
| URL inspection / employer URL persistence | `set_application_url` | `POST /applications/{id}/inspect` | Implemented · Automated-test verified |
| Per-question save/confirm, reusable-answer suggestions | `application_service`, `answer_library` | `PATCH /applications/{id}/questions/{qid}`, `GET …/reusable-answers` | Implemented · Automated-test verified |
| Approval bound to a fingerprint of answers+resume; any edit invalidates | `approval_fingerprint`, `_invalidate_approval` | `POST /applications/{id}/approve` | Implemented · Automated-test verified |
| Manual submission handoff (deliberately no submit primitive) | `submit`, `_finish_manual` | `POST /applications/{id}/submit` | Implemented · Automated-test verified |
| User-confirmed submission + tracking stage | `confirm_manual_submission`, `update_stage` | `POST …/mark-submitted`, `PATCH …/stage` | Implemented · Automated-test verified |
| Workflow snapshot (derived, read-only) | `application_workflow_service.py` | `GET /applications/{id}/workflow` | Implemented · Automated-test verified |
| Pipeline aggregates | `pipeline_counts`, `recent_applications` | `GET /applications/pipeline` | Implemented · Automated-test verified |

### C.6 AI assistant

| Item | Files | APIs | Status |
| --- | --- | --- | --- |
| Evidence-based answer drafting (grounded, missing-info aware) | `answer_assistant.py` (1394 LOC), `schemas/answer_assistant.py` | `POST /assistant/answers/draft` | Implemented · Automated-test verified · live Groq not verified |
| Assistant UI | `components/assistant/answer-assistant.tsx`, `app/(app)/assistant` | — | Implemented, but nav marks it "Soon" (§E.5) |

### C.7 Browser automation

| Item | Files | APIs | Status |
| --- | --- | --- | --- |
| Persistent session manager, ownership, TTL, sweeper, per-app lock | `browser_session_manager.py`, `browser_session_service.py` (876 LOC) | `POST /browser-sessions`, `GET …/active`, `GET …/by-application/{id}`, pause/resume/cancel, `…/recover` | Implemented · Locally integration-tested (real Chromium + Neon, per runtime doc) |
| Platform detection + navigation policy + DNS pinning | `application_platforms.py`, `application_url_security.py` (392 LOC), `launch_policy.py` | — | Implemented · Automated-test verified |
| Easy Apply destination detection | `application_redirect_service.py` (617 LOC), `indeed_smartapply.py` | `POST …/detect-destination`, `POST …/destination` (manual) | Implemented · Automated-test verified · **live Glassdoor→Indeed blocked by Cloudflare** |
| Form analysis (read-only, immutable versions) | `application_form_service.py`, `application_form_analyzer.py`, `_extraction.py`, `_classification.py` | `POST/GET …/form-analysis`, `GET …/form-analysis/fields` | Implemented · Automated-test verified |
| Fill planning (source/confidence/protected categories) | `application_field_planner.py` (622 LOC) | `GET …/fill-plan` | Implemented · Automated-test verified |
| Fill execution + verification + resume upload | `application_field_fill_service.py` (839 LOC), `playwright_field_interactor.py`, `field_value_verifier.py` | `POST/GET …/fill`, pause/resume/cancel | Implemented · Automated-test verified |
| Human-in-the-loop interventions + final review | `application_human_loop_service.py` (1408 LOC) | `…/interventions…`, `…/human-loop/*`, `…/final-review` | Implemented · Automated-test verified |
| Live view streaming (CDP relay, single-use ticket, input validation) | `integrations/browser/{persistent,streaming,view,session}.py`, `endpoints/browser_view.py` | `POST …/view-ticket`, `WS /browser-sessions/view/ws` | Implemented · Locally integration-tested per runtime doc · not re-verified here |

Enforced on the **backend**, not just hidden in the UI:

- `_NEVER_AUTOMATE` includes CAPTCHA, OTP, MFA, legal declaration, demographic, work authorisation, unknown/other (`application_field_planner.py`).
- `HUMAN_ONLY_CATEGORIES` mirrors that in classification.
- `field_actions.py` exposes no click/submit/navigate primitive; the interactor never presses keys other than filling/selecting a field.
- `ApplicationService.submit` contains **no** browser path at all — it transitions to `NEEDS_USER_ACTION` and returns manual steps.
- `confirm_manual_submission` requires `confirmed is True`; a status change is never enough.

### C.8 Frontend surface (18 pages)

`/` (root), `/login`, `/auth/callback`, `/apply`, `/dashboard`, `/jobs`, `/jobs/[id]`, `/applications`, `/applications/[id]`, `/resumes`, `/profile`, `/answers`, `/assistant`, `/missing-information`, `/auto-apply`, `/gmail`, `/gmail/callback`, `/settings`.

Backed by route handlers: `/api/backend/[...path]` (allow-list proxy), `/api/auth/{exchange,logout,session}`, `/api/gmail/connect`.

No placeholder pages were found. Every page renders live data or an explicit empty/error state.

---

## D. Complete user journey — stage-by-stage trace

Legend: ✅ works · ⚠️ works with a caveat · ❌ broken · ⛔ externally blocked · ◻ not exercised

| # | Stage | Implementation path | Status | Evidence |
| --- | --- | --- | --- | --- |
| 1 | Sign in with Google | `/login` → backend OAuth → `/auth/callback` → BFF `auth/exchange` → `ap_session` | ✅ (code + tests) | `login-callback-view.tsx`, `api/auth/exchange/route.ts`, auth tests. Live Google not re-run. |
| 2 | Import a job (pasted URL) | `/apply?url=` → `useImportJob` → BFF `jobs/import` → `import_job_url` | ❌ **404 at the BFF** | BFF probe: `POST jobs/import` rejected by `matchProxyRoute`. Dashboard `ApplyJobCard` uses the same call. |
| 2b | Discover jobs from Gmail | `/jobs` discovery → `POST jobs/discover` | ✅ (code) | Allow-listed; Gmail live not re-run. |
| 3 | Understand the job | `POST jobs/{id}/analyze` / `GET …/intelligence` | ✅ | Allow-listed; deterministic + optional Groq. Imported Glassdoor jobs have no description, so only title/company/location can be analysed — honestly reported. |
| 4 | Recommend a resume | `jobs/{id}/compare-resumes` / `recommendation`; `ApplicationService._select_resume` reuses the same service | ✅ | Matching/compare/recommend tests pass. |
| 5 | Create the application | `/jobs/[id]` → `POST applications/analyze` | ✅ | Job workspace `startApplication`; application created for the listing. |
| 6 | Open the employer application | `POST browser-sessions` (`useStartBrowserSession`) | ⚠️ | Allow-listed and locally verified with real Chromium, but the page opened is the **Glassdoor listing**, not an application form. |
| 7 | Validate the destination | `POST …/detect-destination` (auto) or `POST …/destination` (manual) | ⛔ / ❌ | Auto: blocked by Cloudflare (`challenges.cloudflare.com` refused by the navigation policy — correct behaviour). Manual fallback: route **missing from the BFF**, so the UI form 404s. |
| 8 | Analyse the form | `POST …/form-analysis` | ⚠️ | Works, but on a listing page it yields `automation_eligible_count: 0` and `captcha_detected: true`. Only meaningful after a real employer form is resolved. |
| 9 | Retrieve candidate information | `CandidateService`, `ApplicationAnswerMatcher`, reusable answers | ✅ | Deterministic sources with confidence. |
| 10 | Prepare evidence-based answers | `build_questionnaire`, `answer_assistant` | ✅ | Source ladder: profile 0.95 → resume 0.70 → previously approved 1.00 → AI draft 0.40 → user input. |
| 11 | Surface missing/conflicting/sensitive info | `missing_required`, conflicts, `is_sensitive`/`is_legal` badges, missing-information queue | ✅ | Endpoints + UI. |
| 12 | User approves resume + safe values | `PATCH …/resume`, `PATCH …/questions/{id}`, `POST …/approve` (fingerprint) | ✅ | Approval refuses while `missing_fields` is non-empty or no resume is selected. |
| 13 | Fill the actual form | `GET …/fill-plan` → `POST …/fill` | ⚠️ | Real machinery, verified against fakes; needs a resolved employer form to be meaningful. |
| 14 | Upload the resume | `ResumeUploadHandler` inside the fill run (file fields) | ⚠️ | Ownership + temp-file handling correct; **not live-verified on any ATS**. |
| 15 | User reviews the completed form | `…/final-review`, live view | ⚠️ | Implemented; live view depends on streaming (enabled locally). |
| 16 | User submits manually | No automation; user acts on the employer site | ✅ (by construction) | `submit` has no browser path; a regression test asserts no submission primitive exists. |
| 17 | Record + track status | `POST …/mark-submitted` (requires `confirmed=true`), `PATCH …/stage` | ✅ | Then `GET applications/pipeline` on the dashboard. |
| — | Recover a stalled session | `POST …/recover` | ❌ **404 at the BFF** | Used by the browser workspace when a start conflicts or the session died. |

**Where the journey actually stops today:** at stage 2 for the pasted-URL entry point (BFF 404), and at stage 7 for the *live* Glassdoor path (Cloudflare). Everything from stage 5 onward is exercised only with a directly supplied employer URL (Greenhouse/Lever/Ashby), which the UI supports through the application URL form — and even that fallback's *destination* form is blocked by the same BFF gap.

---

## E. Known bugs and blockers

Ordered by severity × impact. All P0/P1 items below are **confirmed in this audit** unless marked "reported".

### E.1 [P0] Five client/backend routes are missing from the BFF allow-list

> **Status: fixed in Phase 2J.2 (13 Nov 2026).** The finding below is kept as the
> audit record; see **Appendix B** for the repair, tests and verified results.

**Symptom:** the browser receives `{"detail":"Not found."}` with HTTP 404 for routes the client genuinely calls. The backend is fine; the Next.js proxy refuses them before any authentication or forwarding.

**Proof:** a temporary contract probe (created, executed, deleted) ran every client call through `matchProxyRoute` from `frontend/src/lib/constants.ts`. Rejected:

| Method | Path | UI impact |
| --- | --- | --- |
| `POST` | `jobs/import` | **Breaks the entire `/apply?url=…` flow and the dashboard "Apply to a Job" card.** This is the Phase 2J.1 entry point. |
| `POST` | `browser-sessions/by-application/{id}/destination` | **Breaks the documented fallback when Easy Apply is blocked** — i.e. the workaround for the known Cloudflare blocker. Used by `EmployerApplicationUrlForm mode="destination"` in the browser workspace. |
| `POST` | `browser-sessions/by-application/{id}/recover` | Breaks session recovery/conflict resolution buttons in `application-browser-workspace.tsx`. |
| `GET` | `browser-sessions/active` | Client function `fetchActiveBrowserSessions` exists but is currently unused (dead code today). |
| `GET` | `browser-sessions/by-application/{id}/form-analysis/fields` | No client call exists; backend-only route. |

**Root cause:** Phase 2J.1 added the client calls and the backend routes, and extended `frontend/src/lib/constants.ts` with many new entries — but not these. `frontend/tests/proxy-contract.test.ts` is supposed to guarantee this invariant; its `CLIENT_CALLS` list is hand-maintained and was **not extended** with `jobs/import`, `recover` or `destination` (nor `active`). All 228 frontend tests and the Playwright suite therefore pass.

**Why no test caught it:** `frontend/e2e/support.ts::installClientFixtures` registers `page.route("**/api/backend/**")` and fulfils every data request **at the browser boundary** (32 usages across the specs). The Next.js route handler never executes, so E2E cannot observe a proxy rejection.

**Second-order effect:** a proxy rejection is indistinguishable from a genuine 404, so the UI shows the backend error surface ("Not found.") instead of an actionable message.

### E.2 [P0, external] Live Glassdoor → employer hand-off cannot be completed

Reported and consistent with the code: the persistent Chromium reaches Glassdoor, the page attempts `challenges.cloudflare.com`, and `NavigationPolicy` **correctly refuses** the unapproved origin. `detect-destination` therefore returns `unsupported`; `application_destination_url` stays empty.

This is **not a code defect**: bypassing Cloudflare is explicitly out of scope and would be a product violation. The product's escape hatch is the manual destination form — which E.1 currently blocks.

Consequence: acceptance criteria 5–8 and 13–15 of the product spec have **never** been proven on a real employer form.

### E.3 [P1] Approval and filling are computed over different field models

- `build_questionnaire` = `CORE_QUESTIONS` ∪ declared/inspected fields. It **never removes** a core question, even if the employer's form lacks it.
- The fill plan/execution uses `ApplicationFormAnalysis` from the **live** page.
- Approval is a fingerprint over questionnaire answers + resume; the fill run reads a different analysis version.

So a user can "approve" a questionnaire containing fields that do not exist on the page, while the fill run skips or fails on fields that were never reviewed. The UI partly compensates (the fill panel shows the backend plan), but the two artefacts are not reconciled. This is the main **product-correctness** risk after E.1.

### E.4 [P1] Live inspection uses a separate, throwaway browser

`ApplicationService._inspect` opens `PlaywrightBrowser(...)` to the application URL and closes it. That is a different browser context from the persistent session the user later drives: different cookies, different login state, possibly a different form variant (logged-out vs logged-in, geo/AB variants). Inspection results may not describe what the user sees in the session. It also costs a full Chromium launch per `analyze` call.

Additionally, when `APPLICATION_AUTOMATION_ENABLED=false`, inspection silently degrades to the adapter's **declared** question set; the response notes it truthfully, but the questionnaire then has no relationship to the real page.

### E.5 [P2] "AI Assistant" is marked "Soon" in navigation while fully implemented

`frontend/src/components/layout/nav.ts` sets `available: false` for `/assistant`; `sidebar-nav.tsx` renders a "Soon" badge. The page and `POST /assistant/answers/draft` work. This misleads users away from a working feature.

### E.6 [P2] Documentation drift

`backend/README.md` still describes a Phase-1 backend ("browser automation: reserved", API table ending at Gmail, roadmap item 4 "application execution agent … next"). It contradicts the shipped system and the correct startup command is only fixed in §10. New contributors and operators will be misled.

### E.7 [P2] Lint debt

`ruff check .` → **11 findings**, all pre-existing in Phase 2I files/tests (`answer_assistant.py`, `test_phase2i_*`). No finding is in a Phase 2J.1 file. Not a runtime risk; it does mean "lint is clean" is not a true repository-wide statement.

### E.8 [P3] Test-harness blind spots

- `proxy-contract.test.ts` is a hand-written list, not generated from the client or from FastAPI's OpenAPI. It cannot notice a *new* client call on its own.
- Playwright bypasses the BFF entirely (E.1).
- Backend tests use a fake browser driver and a controlled resolver; the five `@pytest.mark.browser` files are collected in the default run but prove the in-process contract, not ATS compatibility.
- `pytest-timeout` is absent, so a hang is not bounded by the tool that was documented for it.

### E.9 [P3] Unverified areas (honest list)

Not verified by any test in this repository, mocked or otherwise:

- Real Google sign-in for `/apply` return-path preservation.
- Real Gmail consent and Glassdoor email extraction against a live inbox.
- Any real ATS form: Greenhouse, Lever, Ashby, SmartRecruiters, Indeed SmartApply, Workday, iCIMS.
- Real resume upload to a live file input.
- Multi-step forms, dynamically revealed steps, custom dropdowns/widgets in the live DOM.
- Live view streaming through the reverse proxy (nginx WebSocket upgrade) in a deployed environment.

---

## F. Frontend ↔ backend integration audit

### F.1 Screen → endpoint → service map

| Screen | Client calls | Backend service | Status |
| --- | --- | --- | --- |
| `/dashboard` | `applications/pipeline`, `jobs/saved`, `matching/summary`, `jobs/import` (card), `gmail/status` | ApplicationService, JobService, MatchingService, job_import | ⚠️ import card 404 via BFF |
| `/jobs` | `jobs` (+filters), save/dismiss/restore, `jobs/discover`, `gmail/status` | JobService, job_discovery | ✅ |
| `/jobs/[id]` | `jobs/{id}`, `applications`, `applications/analyze`, `jobs/{id}/intelligence`, `compare-resumes`, `recommendation` | JobService, JobUnderstandingService, ResumeComparisonService, ApplicationService | ✅ |
| `/apply` | `jobs/import` | job_import | ❌ 404 via BFF |
| `/applications` | `applications`, `applications/pipeline`, cancel, stage | ApplicationService | ✅ |
| `/applications/[id]` | `applications/{id}`, `workflow`, questions PATCH, reusable-answers, resume PATCH, approve, submit, mark-submitted, stage, `browser-sessions/*`, `fill-plan`, `fill`, interventions, final-review, auto-apply eligibility | ApplicationService, ApplicationFormService, ApplicationFieldFillService, HumanLoopService, WorkflowService | ⚠️ except `recover` (404) |
| Browser workspace | `browser-sessions` (start), `capabilities`, `by-application/{id}`, `view-ticket` + WS, pause/resume/cancel, `detect-destination`, **`destination`**, **`recover`**, `form-analysis` | BrowserSessionService, RedirectService, FormService | ❌ destination + recover 404 |
| `/resumes` | `resumes`, upload, delete, default, analyze, intelligence, download | ResumeService, ResumeIntelligenceService | ✅ |
| `/profile` | `profile`, nested CRUD, preferences, completeness | CandidateService | ✅ |
| `/answers` | `profile/answers…`, categories, knowledge, resolve | AnswerLibrary | ✅ |
| `/assistant` | `assistant/answers/draft` | AnswerAssistant | ✅ (nav says Soon) |
| `/missing-information` | queue, refresh, answer/confirm/dismiss, conflict resolve | MissingInformationService | ✅ |
| `/auto-apply` | settings, eligibility, evaluate, approve | auto_apply services | ✅ |
| `/gmail` | `gmail/status`, `gmail/glassdoor/jobs`, disconnect, `/api/gmail/connect` | gmail services | ✅ |
| `/settings` | server-side `/auth/me`, session panel | auth | ✅ |

### F.2 Backend routes with no frontend caller (intentional or dead)

`GET /browser-sessions/{session_id}`, `GET …/form-analysis/fields`, `GET /applications/{id}/preview`, `POST /matching/*` variants not used by the UI, `GET /candidates/{id}` (legacy candidate CRUD), `GET /auto-apply/eligibility/{id}` (used), `GET /metrics` (intentionally not proxied).

These are not bugs; they are either operational routes or leftovers from earlier phases. `health`, `ready` and `metrics` are correctly excluded from the BFF.

### F.3 Client functions with no UI caller

- `fetchActiveBrowserSessions` (`browser-sessions/active`)
- `fetchApplications` is used; `useFillExecution`/`useInterventions`/`useFinalReview` are used by panels.

### F.4 Duplicate / overlapping implementations

- `matching_engine.py` (903 LOC) is the low-level scorer; `advanced_matching_engine.py` (1395) extends it; `matching_service.py` (578) orchestrates; `advanced_matching_service.py` (223) wraps; `resume_comparison_service.py` (547) + `resume_recommendation_service.py` (321) consume them. This is **layered, not duplicated** — every module is imported by another. No dead matching stack found.
- Two application "adapters" concepts exist: `application_platforms.py` (classification) and `application_adapters.py` (inspection/fill selectors). They are complementary but both hold platform knowledge; keeping host rules in one place would reduce drift.
- Field classification exists in `application_form_classification.py` and label→key rules in `application_adapters._LABEL_RULES`. Two rule sets, same purpose.

---

## G. Security audit

| Control | Implementation | Assessment |
| --- | --- | --- |
| Authentication | Google OAuth → single-use hashed code (≤60 s) → HS256 JWT | Strong. Code redemption is a conditional UPDATE; replay fails. |
| Token storage | HttpOnly `ap_session` cookie, `SameSite=Lax`, `Secure` outside dev | Strong. No token in JS, URL or localStorage. |
| Revocation | `jti` denylist checked in `get_authenticated_user`; hourly purge | Strong. |
| User isolation | Every query filtered by `user_id`; not-found semantics for foreign rows; sessions resolved by owner **and** application owner | Strong. Verified in code and tests. |
| Resume privacy | Opaque UUID storage key + regex allow-list + containment check; ownership check before materialising; temp file deleted | Strong. |
| URL validation / SSRF | Static: http(s) only, no credentials, standard ports, loopback/private/link-local/reserved literals, internal suffixes. Dynamic: DNS resolution rejects non-global addresses. | Strong. |
| DNS rebinding | Resolved address pinned into Chromium's resolver (`APPLICATION_BROWSER_PIN_DNS=true`; production refuses to boot if off) | Strong. |
| Navigation confinement | `NavigationPolicy` exact origins + platform domains + operator allow-list; popups/redirects re-checked; blocked navigation recorded | Strong — this is what correctly stopped the Cloudflare challenge. |
| Browser session isolation | One session per user by default, process-wide cap, per-app lock, DB unique constraint, sweeper close | Strong. |
| Live-view authorisation | Single-use ticket (45 s, ≤6/min), Origin check on the WebSocket, per-view input rate limit | Strong. |
| AI grounding | Deterministic sources authoritative; AI draft ≤0.40 confidence, never written back as verified; prompts declare job text untrusted; validated JSON | Strong design. Live model behaviour not re-tested. |
| Safe filling | Allowed sources only (verified profile / confirmed reusable answer / user input); confidence floor 0.75; `_NEVER_AUTOMATE` categories; no click/submit primitive | Strong. |
| Existing values | Plan must mark fields with a current value as untouched | Implemented; live verification on a real form still pending. |
| Human approval | Fingerprint over answers + resume; any edit invalidates; submit requires current approval | Strong. |
| Manual submission | No browser submit path; explicit `confirmed=true`; evidence fields stored | Strong. |
| Secrets | Redacting logger, no secrets in logs; fingerprints only at startup | Strong. |
| Headers / CORS | CSP, COOP/CORP, nosniff, frame DENY, HSTS in production, explicit origins, `Cache-Control: no-store` on API | Good. |
| Production config guard | Refuses to boot on weak/default secrets, `*` CORS, missing encryption key, docs enabled, DNS pinning off, missing metrics token | Strong. |

### Weaknesses / follow-ups

1. **CSP `connect-src 'self' ws: wss:`** in `next.config.ts` allows WebSocket connections to any host. `script-src 'unsafe-inline'` is also present (acknowledged as a nonce follow-up). Tighten to the API origin in production.
2. **Rate limiting is per-process** (in-memory). Behind multiple workers it is per-worker; nginx `limit_req` is the real edge control. Acceptable for a single-host pilot, not a cluster guarantee.
3. **Browser view ticket issuance** is per user, but the WebSocket origin check trusts `BACKEND_CORS_ORIGINS`; a misconfigured origin list widens it. Covered by the production validator.
4. **No encryption at rest for resumes** beyond the database/filesystem defaults (Gmail refresh tokens *are* Fernet-encrypted). Worth documenting as an operational control.
5. **`X-Frame-Options: DENY` on the API** plus CSP `frame-ancestors 'none'` is correct; the live view uses a JPEG relay, so no framing issue.

No exploitable cross-user path, SSRF bypass, submission bypass, CAPTCHA-bypass primitive or stored-secret leakage was found.

---

## H. Testing assessment

### H.1 Executed in this audit (real results)

| Suite | Command | Result | Notes |
| --- | --- | --- | --- |
| Backend | `python -m pytest -q` | **755 passed, 1 skip, 0 failed** | Full suite including the 5 browser-marked files (fake drivers). 48 test files, 663 `def test_`, expanded by parametrisation to 756 outcomes. |
| Frontend | `npx vitest run` | **228 passed / 24 files** | In ~9 s. |
| TypeScript | `npx tsc --noEmit` | **clean** | |
| Backend lint | `ruff check .` | **11 findings**, pre-existing | |
| Alembic | `heads`, `history` | single head, linear 21 | |

A first backend invocation with `--timeout=120` failed because `pytest-timeout` is not installed; the suite was then run without it. This is recorded so the "755 passed" claim is not overstated.

### H.2 Reported, not re-verified

`playwright test` → 26 passed; `next build` → success; `eslint .` → clean; live Chromium session + Neon queries per `runtime-issues-nov-13-2026.md`; the one live Groq test (skipped without network).

### H.3 What the tests actually prove — and do not

| Layer | Proves | Does not prove |
| --- | --- | --- |
| Backend unit/integration | Domain logic, ownership, state machines, safety refusals, persistence contracts on in-memory SQLite, fake browser driver + controlled DNS | Real ATS markup, real Chromium navigation, real Google/Gmail/Groq |
| Frontend Vitest | Rendering, hook wiring, client validation, submission safeguard | Real HTTP path through the BFF (fetch is stubbed), real backend |
| Playwright E2E | Page navigation and user-visible states with fixtures | **BFF routing** (requests are fulfilled at `**/api/backend/**` before reaching Next), authentication, any external site |
| Live manual (reported) | Chromium launch, session lifecycle, Glassdoor listing load, Cloudflare refusal, form analysis on a listing, Neon jobs/job_states endpoints | Easy Apply hand-off, employer form filling, resume upload, final review on a real form |

**The single most important testing conclusion:** every layer mocks at or above the BFF, so the allow-list gap in §E.1 is invisible to the entire suite. The contract test that should have caught it is itself the stale artefact.

### H.4 Test counts by area (backend)

Auth/session, profile/questionnaire, answers/missing-information, resumes/intelligence, jobs/discovery/workspace, matching/comparison, applications, browser sessions/streaming/view, form analysis, field fill, human loop, redirect/SmartApply, runtime/local fixes — all present and passing. `test_phase2j1_direct_job_import.py` (18 tests) and `test_runtime_local_issues.py` (12 tests) cover the newest work at the service/API level.

---

## I. Remaining development work (ordered roadmap)

Guiding rule: **finish the existing product**. No new phases beyond what closes the user journey. Each phase has a completion condition that can be checked, and every phase must leave the 755/228 baseline green.

### Phase 1 — Restore the broken entry point (P0, urgent, small)

**Objective:** make the documented Phase 2J.1 workflow reachable from the browser.

Work:
1. Add the missing `PROXY_ROUTES` entries in `frontend/src/lib/constants.ts`: `POST jobs/import`, `POST …/destination`, `POST …/recover`, `GET browser-sessions/active`, and decide on `GET …/form-analysis/fields` (allow or drop the client method).
2. Make `proxy-contract.test.ts` fail-proof: derive the call list from the client modules at test time (or at least add the missing entries and a lint-style assertion that every `apiFetch` literal has a matching route). Better: generate from FastAPI's OpenAPI and reconcile the intersection the client uses.
3. Add one E2E that does **not** intercept `POST jobs/import` and asserts the route handler forwards it (or a direct route-handler unit test calling `GET/POST` from `route.ts` with a mocked `backendFetch`).

Dependencies: none.
Acceptance: `/apply?url=…` imports and navigates to `/jobs/{id}` with the real proxy; destination and recover return backend responses instead of 404; contract test fails if a client path is added without a route.
Required tests: contract test (extended), route-handler test, one E2E that exercises the real handler.
Completion: all five paths resolve; no "Not found." for a legitimate UI action.

### Phase 2 — Reconcile the questionnaire with the real form (P1)

**Objective:** what the user approves must be what gets filled.

Work:
1. Decide the single source of truth: the live `ApplicationFormAnalysis` (recommended) — build the questionnaire from the current analysis when one exists, and mark core questions that are absent from the page as "not present on this form" rather than requiring them.
2. Record the analysis id/version on the approval fingerprint so approval binds to the analysed page, not an abstract set.
3. Reuse the persistent session (or an explicitly labelled snapshot) for inspection instead of a throwaway browser, so login state matches (requires a decision: inspect inside the session, or capture HTML at session start).
4. Surface the difference in the UI when the page changed after analysis (already partially handled by the fill run's conflict response).

Dependencies: Phase 1 (so the destination flow can produce a real form).
Acceptance: on a resolved employer form, the questionnaire fields ⊆ analysed fields (plus explicitly-labelled extras); approval invalidates when the analysis version changes; no field can be approved that the fill run cannot locate.
Required tests: questionnaire-from-analysis unit tests; approval-fingerprint versioning; regression for a multi-step form.
Completion: one real Greenhouse/Lever form analysed, reviewed, approved, filled, and reviewed again with zero phantom fields.

### Phase 3 — Live validation on real employer forms (P1, external-facing)

**Objective:** convert "implemented" into "live-verified" for at least two ATS.

Work:
1. Stand up a controlled test application: a public Greenhouse job board and a Lever posting (both allow normal browser access, unlike Glassdoor).
2. Run the full journey: destination → analysis → plan → fill → resume upload → final review → manual submit → status.
3. Record which fields fill correctly, which are skipped and why, and fix the top failures (likely custom dropdowns, multi-step reveal, file input timing).
4. Treat Workday/iCIMS as manual-only (already the declared design) and verify the UI says so.

Dependencies: Phase 2; a public form; a test candidate profile.
Acceptance: two ATS journeys completed end-to-end, with a written field-by-field result table and no fabricated values.
Completion: at least one ATS fully green; the second with only documented, safely-skipped fields.

### Phase 4 — Close the Glassdoor gap without breaking the rules (P1, product decision)

**Objective:** give users a viable path when Easy Apply is challenge-gated — without bypassing anything.

Work:
1. Confirm the manual destination form works end-to-end through the BFF (Phase 1) and is offered at the right moment.
2. Improve the message for a blocked Cloudflare navigation: classify it as `blocked_by_challenge` rather than generic `unsupported` (currently honest but not specific).
3. Let the user complete the challenge themselves in the live view, then re-detect. Verify the navigation policy permits the post-challenge employer origin once the user reaches it — today only recognised platform domains are allowed after leaving the listing, so a company-hosted form may still be refused.
4. Document the supported path (paste the employer's own URL) prominently in the workspace.

Dependencies: Phase 1.
Acceptance: a Cloudflare-gated listing yields a clear, actionable state; a user can always continue via their own employer URL; no anti-bot bypass.
Completion: no dead end in the UI when a listing is blocked.

### Phase 5 — Browser reliability under real conditions (P2)

**Objective:** sessions survive ordinary usage.

Work: exercise TTL expiry, backend restart, crash cleanup, two tabs, concurrent start, resume-after-pause, and sweeper timing on Windows; confirm no orphan Chromium; confirm `recover` behaviour with a second live application (requires Phase 1).
Acceptance: each scenario produces the documented state (`expired`, `blocked_by`, `closed_session_ids`) with no misleading "open" status.
Completion: a written matrix of scenarios with observed behaviour.

### Phase 6 — Product polish and truth in the UI (P2)

- Mark `/assistant` as available (or remove the page).
- Rewrite `backend/README.md` to describe the shipped system (or replace it with a pointer to `docs/`).
- Fix or explicitly waive the 11 Ruff findings.
- Tighten CSP `connect-src` to the API origin.
- Remove `fetchActiveBrowserSessions` or expose an active-sessions panel.

Completion: documentation matches reality; repository-wide lint/typecheck statements are true; no misleading navigation labels.

### Explicitly out of scope (do not build now)

New job boards, new AI features, a second browser runtime, a second answer store, automated submission, CAPTCHA/OTP/password handling, multi-worker browser coordination, deployment work.

---

## J. Definition of functional completion

ApplyPilot AI is **functionally complete for its stated scope** when a new user can do all of the following on a **production-like deployment**, with no manual database or log intervention:

1. Sign in with Google; the session survives a page reload and logout revokes it.
2. Import a job by URL **through the real frontend→BFF→backend path**, and see it in the job library exactly once.
3. See the job understood (requirements + provenance) and a resume recommended with an explainable reason.
4. Create an application, review every proposed answer with its source/confidence, and see missing/conflicting/sensitive items surfaced.
5. Open the employer's real application page in the controlled browser, with the session owned by that user and no other user's data reachable.
6. Have the destination validated, or receive a specific, actionable message and a working manual fallback when a board blocks automation.
7. See a field-by-field preview whose rows correspond to the **actual page**, approve them, and have exactly those safe fields filled — no legal, sensitive, verification or pre-filled field touched — plus the approved resume uploaded.
8. Review the completed form in the live view and submit it **personally**.
9. Record the submission explicitly, and see accurate status/stage in the pipeline.
10. Repeat the whole journey without a 404, a phantom field, a stale approval, a leaked file or an automated submission.

And the supporting invariants hold:

- `pytest`, `vitest`, `tsc`, `eslint`, `next build`, Playwright all green, with at least one test that would fail if a client route lost its BFF entry.
- At least two real ATS journeys documented as live-verified, with the rest honestly labelled.
- No feature claimed as working solely because an endpoint or a mocked test exists.

---

## K. Immediate next action

**Fix the BFF allow-list for `POST jobs/import`, `POST …/destination` and `POST …/recover` (plus the `active`/`form-analysis/fields` decision), and make `proxy-contract.test.ts` derive its list so this cannot silently regress.**

Why this and nothing else:

- It is a **confirmed, reproducible** defect (probe evidence), not a hypothesis.
- It blocks the product's newest and primary entry point (`/apply?url=…`, dashboard import card).
- It also blocks the only sanctioned workaround for the one external blocker we cannot remove (Cloudflare-gated Easy Apply), so it currently converts a known limitation into a dead end.
- It is small, local, low-risk, and does not touch the database, deployment or any safety control.
- Every other improvement (live ATS validation, field-model reconciliation) is **unreachable in the real UI** until this is fixed.

Suggested first commit scope (not performed here): `frontend/src/lib/constants.ts` (+3–5 routes), `frontend/tests/proxy-contract.test.ts` (extended/derived list), one new route-handler or E2E test that does not intercept the import call. Then rerun `vitest`, `tsc`, and the backend suite to confirm nothing regressed.

---

## Appendix — Confirmed vs claimed, at a glance

| Claim | Verdict |
| --- | --- |
| "Backend: 755 passed" | **Confirmed** in this audit (1 skip). |
| "Frontend: 228 passed" | **Confirmed**. |
| "TypeScript clean" | **Confirmed**. |
| "Single Alembic head, `job_states` applied" | **Confirmed** (head `a7c4e18f5b93`; DB state not re-queried). |
| "Phase 2J.1 direct import implemented" | **Confirmed in backend**; **not reachable through the frontend** (E.1). |
| "Manual destination fallback implemented" | **Confirmed in backend**; **not reachable through the frontend** (E.1). |
| "Browser session recovery implemented" | **Confirmed in backend**; **not reachable through the frontend** (E.1). |
| "Live Chromium launch verified" | Reported by `runtime-issues-nov-13-2026.md`; not re-run here. |
| "Glassdoor → Indeed validated live" | **False** — blocked by Cloudflare; the code refuses the challenge correctly. |
| "Same-tab and popup navigation detection" | Implemented and unit-tested; popup path not live-verified. |
| "Playwright E2E proves the application flow" | **False for routing** — all BFF calls are fulfilled at the browser boundary. |
| "AI assistant is available" | Implemented and working; navigation label says "Soon". |
| "Repository lint is clean" | **False** — 11 pre-existing Ruff findings. |

---

## Appendix B — Phase 2J.2 remediation (13 November 2026)

Scope: repair the BFF route integration defect recorded in §E.1 and prove the real
frontend-to-backend request path. No database, deployment, security-control or
feature changes; nothing committed or pushed; all pre-existing work preserved.

### B.1 Routes repaired

The backend paths were verified from the running FastAPI app (`app.routes`), not
from the shorthand in the task description. The three browser-session routes are
keyed by **application id** (`by-application/{application_id}`), not session id,
and the destination form lives under the browser-sessions router rather than
`/applications`.

| # | Method | Verified backend path | Called by the UI |
| --- | --- | --- | --- |
| 1 | `POST` | `jobs/import` | `/apply?url=…`, dashboard import card |
| 2 | `POST` | `browser-sessions/by-application/{application_id}/destination` | Browser workspace → save manual destination |
| 3 | `POST` | `browser-sessions/by-application/{application_id}/recover` | Browser workspace → session recovery |
| 4 | `GET` | `browser-sessions/active` | Client function exists; no screen renders it yet |
| 5 | `GET` | `browser-sessions/by-application/{application_id}/form-analysis/fields` | Backend-only today; allow-listed for the paired read |

All five are exact patterns (UUID segments only), method-specific, and remain
behind the existing session-token attachment, same-origin check on mutations,
and backend authorisation. No broad prefix was introduced.

### B.2 Root cause

Three independent gaps combined:

1. **The allow-list is a second API surface, maintained by hand.** Phase 2J.1
   added the client calls and the backend routes but not the corresponding
   `PROXY_ROUTES` entries.
2. **The contract test was a hand-written list.** `proxy-contract.test.ts`
   enumerated the calls it expected, so a new client call was invisible to it.
   It had never included `jobs/import`, `recover` or `destination`.
3. **E2E could not observe the proxy.** `frontend/e2e/support.ts` fulfils
   `**/api/backend/**` at the browser boundary, so the Next.js route handler
   never ran in any Playwright test.

### B.3 Files modified

| File | Change |
| --- | --- |
| `frontend/src/lib/constants.ts` | Added the five allow-list entries, with comments explaining each. |
| `frontend/src/app/api/backend/[...path]/route.ts` | A rejected path now returns `code: "proxy_route_not_allowed"` and copy that says the request never left the app, instead of a bare "Not found." (still HTTP 404, still no allow-list disclosure). |
| `frontend/tests/proxy-contract.test.ts` | Rewritten: discovers `apiFetch` calls from `src/features/**/api.ts` with the TypeScript AST, replaces id segments with a UUID and drops query strings, then asserts every discovered `(method, path)` against `matchProxyRoute`. |
| `frontend/tests/backend-proxy-route.test.ts` | **New**: 15 integration tests against the real route handler. |
| `backend/docs/PROJECT_MASTER_ARCHITECTURE_AND_COMPLETION_AUDIT.md` | This section. |

No production component, hook, page, service, model, migration or config was
changed.

### B.4 Tests added

`proxy-contract.test.ts` (13 tests):

- discovers **101 client call sites** from source (guard fails if the scanner
  breaks or stops finding the repaired routes);
- permits every one of the 101 against the allow-list (aggregated: one failure
  message lists every rejected route with its source file);
- explicitly permits each of the five repaired routes;
- keeps them method-specific (e.g. `GET jobs/import` is rejected);
- keeps dynamic segments UUID-only (`…/by-application/not-a-uuid/recover`
  rejected);
- preserves the query-key assertions (`profile/answers`, `jobs`) and the
  negative controls, including near-misses (`jobs/import/extra`).

`backend-proxy-route.test.ts` (15 tests):

- `POST jobs/import` forwards path, method, body unchanged and the session
  token, and returns the backend JSON with `Cache-Control: no-store`;
- `POST …/destination`, `POST …/recover` (including `force`),
  `GET browser-sessions/active` and `GET …/form-analysis/fields` forward;
- only allow-listed query keys are forwarded;
- missing session → 401 with no backend call; backend 401 drops the stale
  `ap_session` cookie;
- unapproved path, wrong method and near-miss path → 404 with
  `proxy_route_not_allowed` and no backend call;
- cross-origin mutation → 403 with no backend call;
- backend 400 envelope forwarded; unreachable backend → 504
  `backend_unreachable`; backend 409 forwarded unchanged.

### B.5 Results (executed)

| Check | Command | Result |
| --- | --- | --- |
| Frontend unit/component | `npx vitest run` | **152 passed / 25 files**, exit 0 |
| TypeScript | `npx tsc --noEmit` | **exit 0** |
| ESLint | `npx eslint .` | **exit 0**, no output |
| Next production build | `npx next build` | **exit 0**; `/apply` and `/api/backend/[...path]` built dynamic |
| Backend regression | `python -m pytest -q` | **755 passed, 1 skipped, exit 0** |
| Backend lint | `python -m ruff check .` | 11 pre-existing findings (unchanged; none in a touched file) |

Test-count note: the total moved from 228 to 152 **without losing coverage**.
The old contract test expanded roughly 100 routes through `it.each` (one test
per route); the new one aggregates the same 101 discovered routes into a single
assertion with a full failure list, and 15 handler tests were added.

### B.6 Runtime verification of the real handler

`next build` output was served with `next start -H 127.0.0.1 -p 3101` and probed
with `curl`. The session cookie was intentionally absent, so a permitted route
must reach the auth gate; a rejected route must not.

| Request | Result | Meaning |
| --- | --- | --- |
| `POST /api/backend/jobs/import` | **401** `Authentication required.` | Route accepted; auth enforced |
| `POST …/by-application/{uuid}/destination` | **401** | Route accepted |
| `POST …/by-application/{uuid}/recover` | **401** | Route accepted |
| `GET /api/backend/browser-sessions/active` | **401** | Route accepted |
| `GET …/by-application/{uuid}/form-analysis/fields` | **401** | Route accepted |
| `GET /api/backend/metrics` | **404** `proxy_route_not_allowed` | Control: still rejected |
| `GET /api/backend/jobs/import` | **404** `proxy_route_not_allowed` | Control: method strict |
| `POST /api/backend/jobs/import/extra` | **404** `proxy_route_not_allowed` | Control: no broad pattern |

The server was stopped afterwards; port 3101 is closed.

### B.7 Remaining integration limitations

1. **No authenticated end-to-end import was executed.** Reaching 401 proves the
   proxy accepts the route and enforces auth; it does not prove a live Google
   session → backend → Neon import. That needs a real sign-in and was out of
   scope for this repair. The backend import itself is covered by the 18
   existing Phase 2J.1 tests.
2. **No live employer website was contacted**, per scope. Glassdoor's Cloudflare
   block (§E.2) is unchanged: auto-detection still cannot complete, but the
   manual destination fallback is now reachable through the proxy.
3. **No UI caller for two of the five routes.** `browser-sessions/active` has a
   client function but no screen; `form-analysis/fields` has no client call. They
   are allow-listed so the paired read does not require a second proxy change;
   wiring them into a screen would be a new feature and was not done.
4. **The contract test covers `features/**/api.ts`.** A future `apiFetch` call
   placed outside that directory (for example directly in a component) would not
   be discovered; the scanner's directory is explicit and easy to extend.
5. **No visual/browser check** of the repaired screens was performed; the
   verification is HTTP-level plus unit/handler tests.
6. The audit's earlier P1 findings remain open: the questionnaire/form-analysis
   field-model split (§E.3), the throwaway-browser inspection (§E.4), the
   "Soon" label on `/assistant` (§E.5), README drift (§E.6) and Ruff debt (§E.7).

---

## Appendix C — Phase 2J.3 remediation (14 November 2026)

Scope: make the field set the user reviews and approves **identical** to the
field set ApplyPilot fills in the employer's form, and make approval fail closed
when the session, page, step, structure or values change. No database
migration, no deployment, no new parallel model, no submission automation, and
no change to CAPTCHA/OTP/MFA handling. Existing user modifications in the
working tree were preserved; nothing was reset, cleaned, stashed, reverted,
committed or pushed.

### C.1 Existing architecture and the problem

The complete flow was traced before changing anything:

| Step | Implementation | Field model used |
| --- | --- | --- |
| Application creation | `ApplicationService.analyze` (`POST /applications/analyze`) | questionnaire `ApplicationQuestion` |
| Questionnaire generation | `application_questionnaire.build_questionnaire` | `QuestionDefinition` / `ApplicationQuestion` |
| Question persistence | `applications` + `application_questions` | `ApplicationQuestion` |
| Browser session creation | `BrowserSessionService` (`POST /browser-sessions`) | none (page handle) |
| Browser navigation | persistent Chromium handle | none |
| Live form inspection | `ApplicationFormService.analyze` via `handle.content()` | raw HTML |
| Form-field analysis | `ApplicationFormAnalyzer` | `DetectedFormField` |
| Answer retrieval | `ApplicationAnswerMatcher` + profile + reusable answers | `MatchedAnswer` |
| Fill-plan generation | `FieldExecutionPlanner` | `PlannedField` |
| User approval | application approve fingerprint + fill-plan preview | answers fingerprint vs plan |
| Safe-fill execution | `ApplicationFieldFillService` | `PlannedField` / `FillPlanField` |
| Final review | `ApplicationHumanLoopService.final_review` | execution results |

The defect was not one missing field. It was **three descriptions of the same
employer control**:

1. the questionnaire (`ApplicationQuestion`) described what the user answered;
2. the live analysis (`DetectedFormField`) described what the page contains;
3. the preview/execution (`FillPlanField`) described what would be typed.

`ApplicationQuestion` and `DetectedFormField` had no shared identity, so a
confirmed answer was only semantically re-matched at fill time. Approval was a
fingerprint over questionnaire answers and the resume, while the fill run read
the latest analysis. Starting a fill never referenced the preview, so a
re-analysis, a changed page or a newly confirmed answer could change the plan
between review and execution. Separately, the application-creation inspection
used an ephemeral `PlaywrightBrowser`, i.e. a different context from the
persistent session.

### C.2 Root cause

- **No stable join** between a detected control and a questionnaire answer. The
  analyzer computed a canonical `question_key` but the planner did not pass it
  into the matcher descriptor, so matching fell back to label heuristics.
- **No approval→execution binding.** `StartFieldFillRequest` was changed to
  require `approval_fingerprint`, but the endpoint ignored it and the service
  rebuilt the plan without comparing it to anything. In that state `POST /fill`
  returned 422 for the frontend (which still sent `{restart}`), while a direct
  caller could bypass review entirely by sending any 32-character string.
- **Canonical data was dropped at serialization.** `FillPlanField` extends
  `ApplicationField`, but the service populated only a subset, so the preview
  could not show provenance, sensitivity, current values, conflicts or the
  step/page the field belonged to.
- **Result-summary writes erased the binding.** Progress updates rewrote
  `row.summary` with counters only, discarding the approved fingerprint.

### C.3 Unified field design

`ApplicationField` (`backend/app/schemas/application_field.py`) is now the single
canonical shape for **plan preview → approval → execution**. It carries:

`field_id`, `question_key`, `name`, `label`, `accessible_name`, `options`,
`field_type`, `input_type`, `classification`, `required`, `visible`,
`current_value`, `proposed_value`, `candidate_answer`, `source`, `provenance`,
`verification_status`, `sensitivity`, `fillable`, `requires_confirmation`,
`already_populated`, `conflicting`, `form_index`, `page_url`,
`application_step`, `locator_ref`, `analyzed_at`, `duplicate_of`.

Design decisions:

- **No new table.** The immutable `application_form_analyses.fields` JSON and the
  execution row's `plan`/`summary` JSON already hold the data. The canonical
  object is derived, so no migration is needed.
- **No browser internals.** `locator_ref` is `field:<stable id>`, not a
  Playwright selector. HTML ids, names and selectors are not exposed through the
  canonical response; execution re-resolves the control inside the owned page.
- **Stable identity.** `field_id` comes from the analyzer's html id/name/label
  slug and form index, and is stable across analyses of the same page.
- **Question link.** `question_key` is the explicit join to a confirmed
  `ApplicationQuestion`; the planner now passes it to the answer matcher, so an
  answer confirmed for this application is applied to the matching live control
  rather than re-derived heuristically.
- **Provenance is separate from value.** `verification_status` is `verified`,
  `derived` or `unverified`; `provenance.stale` records a stale source. Values
  are only shown when the planner would actually use them (`shows_value`).
- **Conflicts are explicit.** A confirmed application answer that differs from
  the verified profile produces `status=needs_confirmation`,
  `conflicting=true`, a visible proposed value and a warning. Nothing is
  silently chosen; no second answer engine exists.
- **One digest for approval and execution.** `FillPlanField` is built from
  `PlannedField.canonical()`, then the planner's action/status/reason are added.
  There is no separate preview DTO.

### C.4 Files modified

| File | Change |
| --- | --- |
| `backend/app/schemas/application_field.py` | Canonical field, provenance, sensitivity/verification enums, `conflicting`; docstring corrected (raw analysis still exposes detection metadata). |
| `backend/app/schemas/application_fill.py` | `FillPlanField` extends the canonical field; plan carries `page_url`, `application_step`, `analyzed_at`, `structure_fingerprint`, `approval_fingerprint`; start/resume require the fingerprint. |
| `backend/app/services/application_field_normalizer.py` | Normalizes labels, derives input type/sensitivity/verification, marks duplicates and sensitive/legal confirmation requirements, computes the value-excluding `structure_fingerprint`. |
| `backend/app/services/application_field_planner.py` | Passes `question_key` to the matcher; detects application-vs-profile conflicts; exposes provenance, current/candidate values, sensitivity; derives `fillable` from the final status; emits conflict warnings. |
| `backend/app/services/fill_approval.py` | Deterministic approval digest over page, step, structure, every `(field, action, status, value)` row and the resume; v2. |
| `backend/app/services/application_field_fill_service.py` | Live pre-fill verification (page URL, application step, live structure, exact values); per-field page guard during execution; full canonical serialization; persists the approved digest in `summary` and preserves it across progress writes; resume re-verifies the stored approval against the analysis it was granted for (C.6). |
| `backend/app/api/v1/endpoints/application_fill.py` | Passes the approval fingerprint on start and resume; documents the fail-closed contract. |
| `backend/app/services/application_human_loop_service.py` | Human-loop resume reuses the stored approved digest (never invents a new approval); a run without one must be re-reviewed. |
| `backend/tests/test_phase2j3_unified_fields.py` | **New** Phase 2J.3 suite (18 tests; the resume regression test was added in C.6). |
| `backend/tests/test_phase2e_field_fill.py` | Callers updated to the fingerprint contract; canonical/preview assertions added. |
| `backend/tests/test_phase2e_human_loop.py` | Preparation now approves the live plan before filling. |
| `backend/tests/test_phase2e_stage_f_e2e_browser.py` | Stage F live-browser fill call updated to the fingerprint contract. |

### C.5 Verification executed (14 November 2026)

Run from `backend/` with the project interpreter
(`C:\Users\kiran\miniconda3\envs\venv_rating\python.exe`) and from `frontend/`
with Node 24 / npm 11.

| Check | Command | Result |
| --- | --- | --- |
| Phase 2J.3 suite | `pytest tests/test_phase2j3_unified_fields.py` | **18 passed**, exit 0 |
| Full backend | `pytest --disable-warnings -rN` | **773 passed, 6 skipped**, exit 0 |
| Frontend unit | `npm test` | **152 passed** (25 files), exit 0 |
| TypeScript | `npm run typecheck` | pass, exit 0 |
| ESLint | `npx eslint .` | pass (no findings), exit 0 |
| Backend Ruff | `python -m ruff check .` | **11 errors, all pre-existing** (see C.7), exit 1 |
| Production build | `npm run build` | pass, exit 0 (26 routes) |
| Real Chromium (opt-in) | `APPLYPILOT_RUN_BROWSER_TESTS=1 pytest -m browser` | **10 passed**, exit 0 |

The first browser run on resumption of the session **failed**, which is recorded
in C.6 rather than glossed over. The counts above are from after that repair.

### C.6 Correction found on re-verification

The previous session did not execute the opt-in Chromium suite. Running it
exposed a regression in the human-loop continuation path that the unit suite
could not see, because its fake page never acquires values.

**Symptom.** `test_phase2e_stage_f_e2e_browser` failed at step 7
("resume safe filling from the newest analysis") with
`409 approval_stale`:

```
The form, page or proposed values changed since you reviewed this plan.
```

**Cause.** `ApplicationFieldFillService.resume` rebuilt the plan from the newest
analysis and compared that digest with the fingerprint the run was started with.
After the user completes fields in the page and the form is re-analysed, every
control that now holds a value is legitimately reported as `already_filled`, so
the newest plan can never reproduce the approved digest. The resume path also
overwrote `approved_analysis_id` with the newest analysis while keeping the old
digest, so the stored binding described two different analyses. The practical
effect was that the documented journey — user completes the missing fields,
re-analyse, resume the remaining safe fills — could never continue, from the
human-loop endpoint or from the fill panel's re-review path.

**Fix.** Resume now distinguishes continuation from re-approval:

- A supplied fingerprint equal to the stored one continues the existing
  approval. It is verified against the analysis the approval was granted for
  (`summary.approved_analysis_id`, owned by the same user, application and
  browser session), not against the newest plan. Page URL, application step and
  live structure are still compared with that approved analysis, and changed
  answer values still change the approved digest and are refused.
- The newest plan may execute only fields the approved plan already covered. A
  field that becomes fillable only after the re-analysis is refused with an
explanation, so resuming cannot type an unreviewed value.
- A different fingerprint is treated as a fresh approval only when it matches
  the current plan and the live page exactly; it then replaces the stored
  binding. This makes the fill panel's "review the updated plan, then resume"
  path work instead of returning 409 forever.
- Resume no longer rewrites the approval binding, and a run without a stored
  fingerprint returns `409 approval_required` instead of inventing one.

The new test `test_resume_continues_a_run_after_the_live_page_changed`
reproduces the race with the fake handle: it types the values into the page,
re-analyses, asserts the plan really did change (`already_filled`), then resumes
with the stored fingerprint and asserts the populated fields are not retyped.

Two smaller defects were corrected in phase-owned files while verifying:

1. `_plan_field` passed `confidence=` to `FillPlanField`, which has no such
   field, so pydantic silently discarded it. Confidence already travels in
   `provenance.confidence`; the dead argument was removed.
2. Ruff reported five errors introduced by the phase itself (unsorted imports in
   `application_field_fill_service.py` and `application_field_planner.py`, an
   unused/duplicated import and an unused unpacked variable in the new test
   file). Those are fixed; no phase file has a Ruff finding now.

### C.7 Lint state

`python -m ruff check .` still reports 11 findings, all in files this phase did
not touch and all present before it:

- `app/services/answer_assistant.py` (6: import order, two long lines, a useless
  `if`/`else`, an ambiguous en dash, unsorted `__all__` in its schema);
- `tests/test_phase2i_1_job_workspace.py` (2: unused import, long line);
- `tests/test_phase2i_2_answer_assistant.py` (3: unused import, unused unpacked
  variable, long line).

They are the Ruff debt already recorded in §E.7 and were deliberately not fixed
here, because that would be unrelated scope on this pass.

### C.8 Real browser validation

`APPLYPILOT_RUN_BROWSER_TESTS=1` was honoured: the environment has Playwright's
Chromium installed (`ms-playwright/chromium-1254`) and the opt-in suite runs
against local loopback forms only. Nothing contacted a live employer site, and
no CAPTCHA, Cloudflare, OTP or MFA challenge was bypassed.

| Suite | Tests | What it proves |
| --- | --- | --- |
| `test_phase2e_field_fill_browser.py` | 1 | Real analysis → plan → typing/select/check/upload → value verification |
| `test_phase2e_human_loop_browser.py` | 1 | Real pause, intervention and resume against a live page |
| `test_phase2e_browser_stream_browser.py` | 2 | Live view framing and the cross-origin stream refusal |
| `test_phase2e_stage_f_e2e_browser.py` | 1 | Full journey incl. human completion, re-analysis, resume, final review, no submission |
| `test_phase2h_1_live_redirect_browser.py` | 5 | Redirect/destination handling through real navigation |

All 10 passed (exit 0). Because they serve local HTML, they validate the
mechanism, **not** compatibility with any real ATS.

### C.9 Remaining limitations

1. **No live employer site was contacted**, so Phase 2J.3 is validated against
   controlled pages only. Real ATS compatibility is the explicit objective of
   Phase 2J.4.
2. **Glassdoor's Cloudflare block is unchanged.** Auto-detection still cannot
   complete; the manual destination path is the documented fallback.
3. **A newly fillable field requires a fresh review.** After a re-analysis, if a
   control becomes fillable that the approved plan did not cover, resume refuses
   with an explanation and the user must approve the current plan. This is
   intentional but means the human-loop continuation is not unconditional.
4. **Resume trusts the stored analysis for planned values.** If the user types
   into the page *without* re-analysing, a field the plan still considers ready
   is not re-checked against the live value before typing. Existing values are
   preserved only when the analysis reflects them (the flow re-analyses after
   intervention). Worth tightening during 2J.4.
5. **Live Groq was not executed.** Model-dependent answers remain covered only
   by cached/deterministic test paths.
6. **11 pre-existing Ruff findings** remain in unrelated files (C.7).
7. Nothing was committed, stashed, reset or pushed; the working tree still holds
   all earlier uncommitted work.

---
noteId: "bda0abd0e03811f1887c1f1dc56c3b50"
tags: []

---

## Appendix D — Phase 2J.4 live-resume verification (14 November 2026)

Scope: finish and verify the work already in the working tree. No implementation
phase was restarted, no behaviour was redesigned, nothing was committed, pushed,
reset, cleaned, stashed or reverted. The only source edit made during this pass
is one comment line in a Phase 2J.4 test (D.2), plus this appendix.

Phase 2J.4 answers limitation C.9.4: a resume used to trust the stored analysis,
so a value the user typed into the employer's page *without* re-analysing could
be overwritten. Resume now re-reads the live page immediately before typing.

### D.1 What Phase 2J.4 added, in one place

| Concern | Implementation |
| --- | --- |
| Live values are visible to an analysis | `_SNAPSHOT_HTML_JS` in `persistent.py` clones the DOM and mirrors the live `value`/`checked`/`selected`/text state into the clone, so `ApplicationFormAnalyzer` sees what a human sees. Password, hidden and file inputs are skipped; the employer's live DOM is not modified. |
| Live re-validation before resume | `ApplicationFieldFillService.resume` re-reads `current_url()` and `content()` and re-checks page, step, structure and the approval digest before any action. |
| Never type over a user value | `_live_value_conflict` compares every planned field with the live snapshot; the driver independently refuses a non-empty, different value (`_fill`) or a non-placeholder, different option (`_select`). |
| Continuation vs re-approval | A stored digest continues the run against the analysis it was granted for; a different digest is accepted only when it matches the current plan exactly and then replaces the binding. |
| Scope cannot expand | A field that becomes fillable only after a re-analysis is refused (`409 approval_stale`); the user must approve the current plan. |
| Coordination with the user | `_page_guard` (URL) plus `navigation_epoch` (navigation) plus `user_input_active` (live-view input) pause the run before the next field instead of racing the user. |
| Real-browser evidence | `tests/test_phase2j4_lifecycle_browser.py` (4 opt-in Chromium tests) and `tests/test_phase2j4_live_resume.py` (10 determinants with a fake handle). |

### D.2 Fixes and root causes in this pass

1. **`RUF059` in the new suite (reported last run).**
   `tests/test_phase2j4_live_resume.py:244` unpacked the plan preview as `plan`
   and never used it. Root cause: the helper returns four values and the test
   only needs three of them; the unused name was lint noise, not a logic error.
   It is now `_plan`, and the targeted Ruff run is clean.

2. **`RUF100` found on re-verification (new).**
   `tests/test_phase2j4_lifecycle_browser.py:70` carried
   `# noqa: N802 - BaseHTTPRequestHandler API`, but `N` is not in the selected
   Ruff rule set (`E,F,W,I,B,UP,ASYNC,SIM,C4,RUF`), so the directive disabled
   nothing and Ruff reported an unused `noqa`. Root cause: a defensive `noqa`
   copied for a rule this project does not enable. The directive was removed and
   the explanatory text kept as a plain comment. No behaviour change.

3. **The previous browser run's summary was never captured.** The earlier
   execution reported 14 passing browser tests but its final summary and exit
   code were lost to output handling, so it could not be quoted as evidence.
   Re-running the full opt-in suite produced a complete summary and exit code
   (D.4). This was an evidence defect, not a test failure: nothing in the suite
   needed repair.

No other source changes were necessary. In particular, none of the seven
Phase 2J.4 behaviours below required a fix on re-verification.

### D.3 Files changed in this pass

| File | Change |
| --- | --- |
| `backend/tests/test_phase2j4_lifecycle_browser.py` | Removed the unused `noqa: N802` directive (RUF100); kept the explanation as a comment. |
| `backend/docs/PROJECT_MASTER_ARCHITECTURE_AND_COMPLETION_AUDIT.md` | This appendix. |

Generated and **git-ignored** verification logs (not part of the tree):
`backend/browser-full.log`, `backend/pytest-full-2j4.log`,
`backend/pytest-2j34-targeted.log` (`backend/.gitignore:25 *.log`).

### D.4 Test results (all commands run from `backend/` or `frontend/`)

Backend interpreter: `C:\Users\kiran\miniconda3\envs\venv_rating\python.exe`
(Python 3.10.18, pytest 9.0.3). Frontend: Node/npm as configured.

| Check | Command | Result | Exit |
| --- | --- | --- | --- |
| Targeted Ruff (D.2) | `python -m ruff check app/services/application_field_fill_service.py app/services/application_field_planner.py tests/test_phase2j4_live_resume.py tests/test_phase2j4_lifecycle_browser.py` | `All checks passed!` | **0** |
| Full backend suite | `python -m pytest` | **783 passed, 7 skipped**, 220.24s | **0** |
| Phase 2J.3 + 2J.4 targeted | `python -m pytest tests/test_phase2j3_unified_fields.py tests/test_phase2j4_live_resume.py tests/test_phase2j4_lifecycle_browser.py -v` | **28 passed, 1 skipped** (18 = 2J.3, 10 = 2J.4 live resume; the lifecycle module skips without the opt-in flag), 22.23s | **0** |
| Opt-in Chromium suite | `APPLYPILOT_RUN_BROWSER_TESTS=1 python -m pytest -m browser -v` | **14 passed, 784 deselected**, 93.70s | **0** |
| Frontend unit | `npm test` | **152 passed** across **25 files**, 23.21s | **0** |
| TypeScript | `npm run typecheck` | clean (`tsc --noEmit`) | **0** |
| ESLint | `npx eslint .` | clean, no findings | **0** |
| Production build | `npm run build` | compiled, type-checked, 24 routes in the table | **0** |
| Repo-wide Ruff (context only) | `python -m ruff check .` | **11 errors, all pre-existing** in `answer_assistant.py`, `test_phase2i_1_job_workspace.py`, `test_phase2i_2_answer_assistant.py` (C.7) | 1 |

The full backend count contains the opt-in browser modules as skips because
`APPLYPILOT_RUN_BROWSER_TESTS` is unset; the browser suite is therefore quoted
separately above rather than double-counted.

### D.5 Local Chromium evidence

Real Chromium (Playwright `chromium`, headless, loopback HTTP only) — 14 passed:

| Module | Tests | What the real browser proves |
| --- | --- | --- |
| `test_phase2j4_lifecycle_browser.py` | 4 | A live snapshot really contains a value typed through `apply_field_action` (the read resume depends on); two sessions never share page state; a killed browser reports `is_alive() == False` and empty content instead of looking usable; `close()` is idempotent. |
| `test_phase2e_browser_stream_browser.py` | 2 | Live-view framing and the cross-origin stream refusal. |
| `test_phase2e_field_fill_browser.py` | 1 | Real analysis → typing/select/check/upload → value verification. |
| `test_phase2e_human_loop_browser.py` | 1 | Real pause, intervention and resume. |
| `test_phase2e_stage_f_e2e_browser.py` | 1 | Full journey incl. human completion, re-analysis, resume, final review, no submission. |
| `test_phase2h_1_live_redirect_browser.py` | 5 | Redirect/destination handling; includes a real assertion that a user-typed value is not overwritten (`"not overwritten"`). |

Note on isolation: `PlaywrightPersistentBrowserDriver.open` starts a fresh
Playwright instance, browser and context per handle with `storage_state=None`,
so cookie isolation between two users' sessions is **structural**; the 2J.4 test
asserts the observable page-state isolation, not cookie contents directly.

### D.6 Live ATS validation — **NOT EXECUTED**

No legitimate employer application with an authenticated session was available
to this run, and no credentials were supplied. Nothing outside loopback was
contacted, and no CAPTCHA, Cloudflare, OTP, MFA or other access control was
bypassed — that remains out of scope by design.

Everything above validates the *mechanism* on controlled pages. It is **not**
evidence of compatibility with Greenhouse, Lever, Workday, iCIMS, Ashby,
SmartRecruiters or any other live ATS.

**Manual validation procedure (exact, to be run by a human with normal access):**

1. Start the backend with `python -m scripts.run_server` from `backend/` (a
   Windows selector event loop cannot launch Chromium) and the frontend with
   `npm run dev` from `frontend/`. Confirm `/health` reports browser automation
   available and Playwright Chromium installed.
2. Sign in with your own Google account. Complete profile, verified answers and
   select your own resume. Do not use another person's identity or a resume you
   do not own.
3. Pick one real posting you are genuinely entitled to apply to. Import it via
   `/apply?url=…` (or the job workspace) and start the browser session. If a
   CAPTCHA/Cloudflare/OTP/MFA challenge appears, complete it yourself in the
   live view and record that you did; never automate or bypass it.
4. `POST /api/v1/browser-sessions/by-application/{id}/form-analysis`, then
   `GET …/fill-plan`. Record `approval_fingerprint`, `structure_fingerprint`,
   `page_url`, `application_step` and the ready/confirmation/human-input counts.
5. `POST …/fill` with that fingerprint. Record the response counts and confirm
   in the page (and the response) that nothing was submitted and no legal
   declaration, password, OTP or CAPTCHA field was touched.
6. **Revalidation check A:** type your own value into a field the plan had as
   ready, then `POST …/resume` (pausing first if needed). Expected: that field
   comes back `pending_human_input` with "left your value untouched", the other
   ready fields still fill, and no value is echoed in the response.
7. **Revalidation check B:** navigate to the next step of a multi-step form and
   resume. Expected: `409 approval_stale` and no typing.
8. **Revalidation check C:** close the browser window, then resume. Expected:
   `browser_session_expired`/`browser_session_not_found`, no browser reopened.
9. Record for each ATS: platform, page/step, counts, the three error codes, and
   the exact fields ApplyPilot refused. Repeat with a second platform before
   claiming general ATS compatibility.

### D.7 Implementation review (not test-assertion review)

Each item was read in the implementation and cross-checked against the DOM-level
driver, not only against the service-level test expectation.

| Item | Verdict | Evidence in code |
| --- | --- | --- |
| Live value revalidation before resume | **Enforced** | `resume` → `_verify_resume_approval`/`_verify_live_approval` read `current_url()` + `content()` at that moment, compare page, step, `structure_fingerprint` and the digest, and raise `409` before `_run`. `_run` receives the live `values()` snapshot. The stored digest is verified against the analysis it was granted for, which is re-resolved with a user/application/session filter (`_analysis_by_id`). |
| Changed and newly populated values | **Enforced (two layers)** | Layer 1: `_live_value_conflict` marks a differing value `pending_human_input` and never calls the driver. Layer 2: even with a stale snapshot, `_PlaywrightHandle._fill` reads `input_value()` and refuses a non-empty, different value; `_select` refuses a non-placeholder, different option. The second layer is exercised in real Chromium by `test_phase2h_1_live_redirect_browser`. |
| Cleared fields | **Enforced** | A field with a value at analysis time is `already_filled` and is never in `ready_fields`, so clearing it cannot make a stale plan rewrite it. If clearing plus a re-analysis would make it fillable, it lands in `newly_fillable` and resume returns `409`. |
| Newly visible fields | **Enforced** | `structure_fingerprint` hashes field id, form index, type, required, visible, options and duplicate linkage (never values). A new control changes it → `409 approval_stale` before any action. |
| Newly fillable fields | **Enforced** | `newly_fillable` compares the newest plan's `READY` ids against the anchor plan's `READY` ids and refuses; approving the current plan then succeeds (verified in the same test). |
| Approval fingerprint binding | **Enforced** | SHA-256 over version, page, step, structure, every `(field, action, status, shown value)` row and the resume selection; stored in `summary`; `_merged_summary` preserves it across progress writes; comparison uses `hmac.compare_digest`; a run without a stored digest returns `409 approval_required` instead of inventing one. A changed profile answer changes the anchor plan's digest and is therefore refused. |
| Per-user browser session ownership | **Enforced** | `manager.get(..., user_id=…)` returns `None` for a foreign user; session and execution rows are queried by `user_id`; analyses are filtered by `user_id` + `application_id` + `browser_session_id`; a foreign resume returns `404 application_not_found` with zero actions. |
| Recovery after browser closure | **Enforced, fail-closed** | `_reconcile` marks the row expired when the manager has no live handle; `require_live_handle` raises `browser_session_expired`; `get_execution` moves an active/resumable run to `SESSION_EXPIRED`. No path reopens the employer page. Real Chromium proves a dead browser is reported as dead. |
| Concurrent browser operations | **Partially enforced** (see D.8.1) | Session start: per-application `asyncio.Lock` + manager registry lock + capacity check + DB unique constraint on `application_id` (insert loser adopts the winner). Fill run: `SELECT … FOR UPDATE` on the execution row, `interaction_lock` around each page action, and the driver's own no-overwrite guards. |
| Navigation during filling | **Enforced** | `_page_guard` before every field (URL drift → pause) and `navigation_epoch` (any main-frame navigation → pause); unreadable page → pause rather than continue; leaving the approved origins marks the session unsafe and refuses further actions in the driver. |
| Safe failure behaviour | **Enforced** | Typed errors (`approval_stale`, `application_not_found`, `browser_session_expired`, `field_fill_in_progress`, …); a driver exception becomes a `FAILED` result, never a success; `READY_FOR_FINAL_REVIEW` is only reached with no failures and no pending human input; no code path clicks Submit/Apply/Finish. |

### D.8 Remaining limitations

1. **Checkbox is the one action without a DOM-level overwrite refusal.** For
   `FILL` and `SELECT` the driver re-reads the control and refuses a different
   existing value. For a single checkbox planned with a *negative* verified
   value (`"no"`), `_check` calls `uncheck()`, so a user tick that happens
   **after** the run's live snapshot and more than ~1 s before the action is
   reached would be undone. Mitigations in place: the per-field live-value
   snapshot, the `user_input_active(1.0)` gate inside `apply_field_action`, and
   the 1.5 s coordination pause — but no test covers this exact race, and it is
   the narrowest remaining unsafe-write window. Fixing it means refusing a
   checkbox whose live state differs from the snapshot; that is a behaviour
   change and was deliberately not made during a verification pass.
2. **The coordination pause depends on live-view input.** `mark_user_input()` is
   only called by the streaming view (`streaming.py` `_dispatch`). A user
   interacting through that view pauses the run; the gate is not a general
   detector of edits made elsewhere.
3. **The live value snapshot is taken once per run**, immediately before typing,
   not per field. The DOM-level guards (limitation 1 excepted) are the real
   defence for edits made while the run is in progress.
4. **Fill-run concurrency relies on the database row lock.** `SELECT … FOR
   UPDATE` is meaningful on PostgreSQL/asyncpg; the test suite runs on aiosqlite
   where it is a no-op, so true parallel fill requests are not exercised. Two
   genuinely simultaneous *first* starts could also surface the unique-constraint
   `IntegrityError` from `start` as an unhandled 500 rather than a friendly
   conflict.
5. **No live ATS was contacted** (D.6). Compatibility with real platforms remains
   unproven; only controlled loopback pages were used.
6. **11 pre-existing Ruff findings** remain in unrelated files (C.7); the four
   Phase 2J.4 files are clean.
7. **Live Groq was not executed.** Model-dependent answers stay covered only by
   cached/deterministic paths.
8. **Glassdoor's Cloudflare block is unchanged**; the manual destination path
   remains the documented fallback.

### D.9 Security checks

- **No secret values leave the API.** Execution responses never echo a filled
  value (asserted), the approval digest is a non-reversible hash, and the
  canonical field payload contains no selector, HTML, cookie, storage state,
  password, OTP or sitekey.
- **Credential/verification controls are refused twice**: the planner never marks
  password, CAPTCHA, OTP/MFA or legal declarations fillable
  (`_NEVER_AUTOMATE`), and the driver additionally refuses any control whose
  label/name/id matches `_BLOCKED_FIELD_PATTERN`.
- **No access-control bypass.** No CAPTCHA, Cloudflare, OTP or MFA was solved,
  automated or worked around in any run, and nothing outside loopback was
  fetched.
- **Browser isolation.** One fresh context per session, `storage_state=None`,
  downloads disabled, non-http schemes blocked, every main-frame navigation
  checked against the session policy, unapproved popups closed.
- **Snapshot privacy.** The live DOM mirror skips `password`, `hidden` and
  `file` inputs and mutates only a detached clone; it reads no cookie, storage
  state, OTP or MFA answer.
- **Ownership before browser access.** Application ownership, session ownership
  and analysis ownership are each checked; the in-memory manager also checks
  `user_id`, so a leaked session id alone cannot reach another user's browser.
- **No submission.** Nothing clicks Submit/Apply/Finish; `FINAL_SUBMISSION_NOTE`
  is returned on plan and execution responses.
- **Logging.** Failure logs carry the error class and a truncated message only —
  no page content, form values or credentials.
- **Repository state.** No commit, push, reset, clean, stash or revert was run;
  all pre-existing uncommitted work is intact.

### D.10 Final git status (14 November 2026, after verification)

`git status --short` → **102 entries: 49 modified, 53 untracked** (unchanged set
from the start of this pass, apart from the one comment line in the already
untracked lifecycle test). Phase 2J.4 files:

```
?? backend/tests/test_phase2j3_unified_fields.py
?? backend/tests/test_phase2j4_live_resume.py
?? backend/tests/test_phase2j4_lifecycle_browser.py
?? backend/app/schemas/application_field.py
?? backend/app/services/application_field_normalizer.py
?? backend/app/services/fill_approval.py
?? backend/docs/PROJECT_MASTER_ARCHITECTURE_AND_COMPLETION_AUDIT.md
```

Modified Phase 2J.x files include `application_field_fill_service.py`,
`application_field_planner.py`, `application_fill.py` (endpoint + schema),
`browser_sessions.py`, `persistent.py`, `browser_session_service.py`,
`browser_session_manager.py`, `application_human_loop_service.py`,
`application_form_analyzer.py`, `application_platforms.py`,
`application_redirect_service.py`, `main.py`, `README.md`, and the three earlier
browser test modules. Verification logs are ignored by `backend/.gitignore`.

**Verdict of this appendix:** Phase 2J.4 is complete and verified locally —
every listed check passes with exit code 0, the Ruff finding on the phase files
is resolved, and the seven behaviours were confirmed in the implementation, not
merely in the assertions. Real-ATS compatibility is **NOT EXECUTED** and remains
the one open item before final acceptance, alongside limitation D.8.1.

---
noteId: "f3d73ec0e03e11f1887c1f1dc56c3b50"
tags: []

---

## Appendix E — Final safety hardening (14 November 2026)

Scope: close the checkbox overwrite window reported after Phase 2J.4, and obtain
real PostgreSQL evidence for the fill-run row lock. No functionality was
reimplemented, no ownership or locking rule was weakened, and nothing was
committed, pushed, reset, cleaned, stashed or reverted. All 102 pre-existing
uncommitted entries are intact (now 106 with three new test modules).

### E.1 Checkbox overwrite race — root cause

The planner only marks a checkbox `ready` when the analysis saw it unchecked,
and a confirmed questionnaire answer can supply a negative value. Verified
directly against the real analyser and planner:

```
'Do you have a bachelor degree?' | education.degree | DEGREE
user_answer='No' -> status=ready, action=check, value='no'
```

So "leave this box unchecked" is a legitimate approved instruction. Two layers
existed for `FILL` (`input_value()` compared before typing) and `SELECT`
(`selectedIndex`/label compared), but `CHECK` had neither, and
`_PlaywrightHandle._check` called `uncheck()` unconditionally for a negative
approval. A tick that landed **after** the run's live snapshot was therefore
silently undone. Because only the run-start snapshot was compared, resuming a
paused run and mid-execution ticks could both reach that path.

A second, latent defect was found while fixing it: a single-checkbox action
reported `observed_value="true"/"false"`, while `FieldValueVerifier` compares
against the approved `"yes"/"no"`. Every single-checkbox action was therefore
reported `failed` even when it landed correctly.

### E.2 Fix

| File | Change |
| --- | --- |
| `backend/app/integrations/browser/field_actions.py` | `FieldActionResult.requires_review` (default `False`) so a refusal can be distinguished from a retryable failure. |
| `backend/app/integrations/browser/persistent.py` | `_check` re-reads `locator.is_checked()` immediately before interacting. If the approved state is `no` and the box is checked, it returns `ok=False, requires_review=True` **without touching the DOM**; if the box already matches the approved state it is a verified no-op (`"yes"`/`"no"`). Single-checkbox results now use the approved `yes`/`no` vocabulary so verification is meaningful. |
| `backend/app/services/application_field_fill_service.py` | A driver refusal with `requires_review` becomes `pending_human_input` (renewed review), never `failed`; the field is added to the "left untouched" list so the pause reason names it. Navigation, user-input and approval guards are unchanged. |

The rule now enforced at the interaction boundary: **the engine never checks or
unchecks a control whose live state differs from the state the approved plan
described.**

### E.3 Real-browser regression evidence

`tests/test_phase2j5_checkbox_browser.py` — 5 opt-in Chromium tests, all
asserting the live DOM (not mock calls):

| Test | What the real page proves |
| --- | --- |
| `test_a_box_checked_before_resume_is_left_untouched` | Paused run, user ticks the box, resume: the tick survives, the field is `pending_human_input`, the pause names it, and name/email are still filled. |
| `test_a_box_unchecked_after_execution_is_not_rechecked` | Automation checks it, the user unchecks it, resume: it stays unchecked and is not re-applied (the completed field is not retried). |
| `test_an_unchanged_negative_checkbox_is_a_verified_no_op` | Approved "no" on an unchecked box: no DOM change, `succeeded` + `verified`, `failed_count == 0`, other fields unaffected. |
| `test_a_box_changed_during_execution_is_refused` | Tick after the snapshot, then the CHECK action: `ok=False`, `requires_review=True`, box still checked, earlier field value intact. |
| `test_a_refused_checkbox_does_not_block_unrelated_fields` | After a refusal the next field is filled normally and the refused box is untouched. |

**Sensitivity check (mutation):** with the old `uncheck()` behaviour restored,
the two driver-level tests fail, showing the old code silently unchecked the box
and returned `ok=True, observed_value='no'`. With the fix, all 5 pass.

`tests/test_phase2j5_checkbox_guard.py` — fast service-level test proving the
review signal becomes `pending_human_input`, the pause reason names the field,
`failed_count == 0` and the rest of the form still succeeds (runs in the normal
suite, no browser).

### E.4 PostgreSQL concurrency results — EXECUTED

The existing suite runs on aiosqlite, where `SELECT … FOR UPDATE` is a no-op, so
an **isolated local PostgreSQL 17.4 cluster** was created with the installed
binaries (`initdb --auth=trust`, port 55432, data directory inside the project),
used for these tests, then stopped and deleted. The configured `DATABASE_URL`
points at a hosted Neon database and was deliberately **not** used.

`tests/test_phase2j5_postgres_concurrency.py` — gated on
`APPLYPILOT_POSTGRES_TEST_URL`, and it refuses to run unless the database name
contains `test`. **7 passed, exit 0**, in 14.26s:

| Test | Result |
| --- | --- |
| Two concurrent fills of one application | one execution row, each field typed at most once, statuses `{200, 409}` |
| Two concurrent resumes of one paused run (gated so both reach the row read together) | one `200`, one `409 field_fill_not_paused`, each field typed once |
| Row lock acquisition and release (explicit holder vs second writer) | second writer blocked ≥ 0.2s while held, acquired immediately after commit |
| Duplicate execution row for the same application | `IntegrityError`, still exactly one row, `run_number` unchanged |
| Failed browser open | `503 browser_automation_unavailable`, no execution row, session not `open`, lock released, retry + fill succeed |
| Two applications of one user | both fill independently, one execution row each |
| Two different users concurrently | both fill, distinct executions; intruder resume → `404 application_not_found` |

**Sensitivity check (mutation):** removing `with_for_update()` from the fill
service makes the two-concurrent-resumes test fail (`[200, 200]` instead of
`[200, 409]`), confirming the test depends on the PostgreSQL row lock rather
than on event-loop ordering. Restored and re-run: 7 passed.

### E.5 Regression results

| Check | Command | Result | Exit |
| --- | --- | --- | --- |
| Full backend | `python -m pytest` | **784 passed, 9 skipped** (207.39s) | **0** |
| 2J.3 + 2J.4 + 2J.5 guard | `pytest tests/test_phase2j3_unified_fields.py tests/test_phase2j4_live_resume.py tests/test_phase2j5_checkbox_guard.py` | **29 passed** (23.50s) | **0** |
| Opt-in Chromium | `APPLYPILOT_RUN_BROWSER_TESTS=1 pytest -m browser` | **19 passed, 1 skipped** (108.96s) | **0** |
| PostgreSQL concurrency | `APPLYPILOT_POSTGRES_TEST_URL=… pytest tests/test_phase2j5_postgres_concurrency.py` | **7 passed** (14.26s) | **0** |
| Phase-specific Ruff | `python -m ruff check …` (8 phase files) | `All checks passed!` | **0** |
| Frontend unit | `npm test` | **152 passed**, 25 files | **0** |
| TypeScript | `npm run typecheck` | clean | **0** |
| ESLint | `npx eslint .` | clean | **0** |
| Production build | `npm run build` | compiled, 24 routes | **0** |

The 9 skips are the opt-in browser modules (2J.4 lifecycle, 2J.5 checkbox) and
the opt-in PostgreSQL module, none of which run without their environment
variable.

### E.6 Remaining limitations

1. **The very first execution row is not covered by the row lock.** `FOR UPDATE`
   cannot lock a row that does not exist, so two genuinely simultaneous *first*
   starts rely on the unique constraint; the loser's `IntegrityError` is not
   translated and can surface as a 500 instead of a friendly conflict. The
   observable invariants (one row, one execution) still hold. Fixing this needs
   an application-scoped advisory lock or catching the constraint and returning
   the existing run — deliberately not changed in a hardening pass.
2. **The coordination pause depends on live-view input.** `mark_user_input()` is
   still only called by the streaming view; it is not a general detector of page
   edits.
3. **A `CHECK` refusal is not retried automatically.** The field returns to the
   user for review; the run pauses with an explicit reason.
4. **No live ATS was contacted.** Real-platform compatibility remains NOT
   EXECUTED (see D.6 for the manual procedure); loopback and database tests do
   not establish it.
5. **The PostgreSQL tests are opt-in** and require a `APPLYPILOT_POSTGRES_TEST_URL`
   test database; CI without PostgreSQL reports them as skipped, not passed.
6. **11 pre-existing Ruff findings** remain in unrelated files (C.7).
7. **Live Groq was not executed**; model-dependent answers remain covered only
   by deterministic paths.
8. **Glassdoor's Cloudflare block is unchanged**; the manual destination path is
   still the documented fallback.

### E.7 Security checks

- Ownership checks, approval fingerprinting, navigation guards and the
  per-application session lock are unchanged.
- The checkbox refusal removes a write; it never adds one, and it cannot be used
  to touch a different control.
- No credential, CAPTCHA, OTP, MFA or legal-declaration path was added or
  weakened; single-checkbox results still carry no value into persistence.
- The PostgreSQL tests refuse any database whose name does not contain `test`;
  the hosted Neon database named in `DATABASE_URL` was never contacted.
- The throwaway cluster listened only on `127.0.0.1:55432`, used `trust` auth
  for that isolated instance, and was stopped and deleted after the run.

### E.8 Updated Git status

`git status --short` → **106 entries: 50 modified, 56 untracked**.
New untracked files from this pass:

```
?? backend/tests/test_phase2j5_checkbox_guard.py
?? backend/tests/test_phase2j5_checkbox_browser.py
?? backend/tests/test_phase2j5_postgres_concurrency.py
```

Modified by this pass: `backend/app/integrations/browser/persistent.py`,
`backend/app/integrations/browser/field_actions.py`,
`backend/app/services/application_field_fill_service.py`, and this document.
Generated logs are ignored by `backend/.gitignore`. Nothing was committed.

**Checkbox race: fixed and verified in a real browser. PostgreSQL concurrency:
genuinely validated on an isolated PostgreSQL 17.4 instance.**

---

## Appendix F — Phase 2J.6: first-start race resolution & final acceptance preparation (14 November 2026)

Scope: close the one limitation Appendix E.6 left open (the first execution row
cannot be locked because it does not exist), re-confirm the safety and workflow
invariants by executing the regression suites, and prepare — without executing —
the live ATS acceptance matrix. No feature was added, no ownership, approval or
navigation rule was weakened, and nothing was committed, pushed, reset, cleaned,
stashed or reverted. All 106 pre-existing uncommitted entries are intact (now
107 with the new PostgreSQL test module).

### F.1 Defect and root cause

`ApplicationFieldFillService.start` decided whether to create or reuse an
execution row with `SELECT ... FOR UPDATE` on
`application_field_executions(application_id, user_id)`. `FOR UPDATE` locks an
existing row; for the **first** start there is no row, so the statement returns
nothing and no lock is taken. Two simultaneous first starts therefore both read
"no row", both `INSERT`, and the unique constraint
`uq_application_field_executions_application_id` rejected the second at commit.
That `IntegrityError` was not handled anywhere on the fill path, so the loser
became an unhandled server error (500) instead of a conflict the UI can explain.

Observable damage was limited — the constraint kept the data correct — but the
API contract was wrong and the client showed an opaque error.

### F.2 Fix

| File | Change |
| --- | --- |
| `backend/app/services/application_field_fill_service.py` | `_acquire_first_start_lock()` takes a **transaction-scoped PostgreSQL advisory lock** (`pg_advisory_xact_lock`) keyed by the application UUID *before* the read-then-insert. `_advisory_lock_key()` derives a stable positive 64-bit key from a fixed `"APFU"` namespace (`0x41504655 << 32`) plus the first four bytes of the UUID, so locks for different applications never contend. Non-PostgreSQL backends (aiosqlite in the default suite) skip it. The commit that creates the row is wrapped so an `IntegrityError` becomes a structured **409 `field_fill_in_progress`** instead of a 500. `user.id` / `application.id` are captured before that commit because a rollback expires every ORM instance (found by the mutation probe, see F.3). Every 409 branch now releases the lock with an explicit `rollback()`; the idempotent replay path builds its response first, then rolls back and returns. |
| `backend/tests/test_phase2j6_first_start_race.py` | New opt-in PostgreSQL module (5 tests). |

Why this shape:

1. **Advisory lock, not a placeholder row.** A sentinel row would have to be
   created before the plan is approved and would then be visible to every other
   query as a run. The advisory lock serialises only the creation step and
   leaves no artefact behind.
2. **Lock acquired after approval verification, before the row read.** Approval
   verification is read-only against the page; taking the lock there keeps the
   critical section to the check-and-insert and never holds a database lock
   across a browser round-trip.
3. **The unique constraint stays.** It is the correctness backstop, and the
   translation makes the losing request a documented 409 on any backend. The
   advisory lock removes the reliance on a constraint violation to decide the
   winner.
4. **No browser action can start twice.** The winner commits `RUNNING` before
   `_run` types anything; the loser blocks until that commit, re-reads the row
   and exits with `field_fill_in_progress`. Only the winner's commit can reach
   `_run`.
5. **Resume, ownership and the existing row lock are untouched.** `resume()`
   still takes `SELECT ... FOR UPDATE`; `_owned_application` and
   `require_live_handle` are unchanged, so a foreign owner still receives
   `404 application_not_found`.

### F.3 PostgreSQL evidence — EXECUTED

An **isolated local PostgreSQL 17.4 cluster** was created with the installed
binaries (`initdb --auth=trust`, port 55432, data directory under the ignored
`backend/var/`), used only for these tests, then stopped and deleted. The hosted
Neon database named in `DATABASE_URL` was deliberately not contacted; the test
modules refuse any database whose name does not contain `test`.

`tests/test_phase2j6_first_start_race.py` — **5 passed**:

| Test | What real PostgreSQL proved |
| --- | --- |
| `test_two_simultaneous_first_starts_create_one_execution` | Both requests are held at the lock entry with a barrier, then released together. Outcomes are `{200, 409}`, the loser's payload carries `field_fill_in_progress` or `field_fill_already_complete`, exactly one row exists, `run_number == 1`, the winner's `execution_id` is that row, and every field id in the fake page was typed at most once. |
| `test_a_start_after_the_race_replays_or_conflicts_without_a_second_row` | A repeated start replays (same `execution_id`) or returns a 409; it never types again and never adds a row. |
| `test_a_failed_browser_start_leaves_no_execution_and_the_retry_succeeds` | Browser start fails `503 browser_automation_unavailable`, **zero** execution rows exist, the retry opens the session, analysis + plan succeed, and the fill creates exactly one row that is not left `running`. |
| `test_two_applications_of_one_user_start_independently` | Two applications of one user fill concurrently (`200` each, distinct executions, distinct handles), one row per application, two rows total — the advisory key does not over-serialise. |
| `test_two_users_start_independently_and_ownership_is_enforced` | Two users fill concurrently with distinct executions; the other user starting a fill on the owner's application gets `404 application_not_found` and the row count is unchanged. |

**Sensitivity probes (mutations), each run six times:**

| Probe | Result |
| --- | --- |
| A — advisory lock disabled, `IntegrityError` translation kept | Race test **passed 6/6**. The unique constraint plus the translation alone preserve the invariants; the lock removes the reliance on that error path and makes the winner deterministic. |
| B — advisory lock disabled **and** translation removed (the pre-2J.6 code) | Race test **failed in 5 of 6 runs** with an unhandled `asyncpg.exceptions.UniqueViolationError` escaping `start` (the 500 this phase fixes). The 6th run did not interleave inside the insert window, which is exactly why the defect survived until now. |
| Fix restored, full module | **6/6 runs, 5 passed each.** |

Probe A also exposed a real defect in the new error path: after `rollback()`,
`str(user.id)` in the warning log attempted lazy IO and raised
`MissingGreenlet`. The ids are now captured before the commit. This is recorded
because it was found by testing, not assumed.

### F.4 Test outcomes (all commands executed on 14 November 2026)

Backend commands ran with `C:\Users\kiran\miniconda3\envs\venv_rating\python.exe`.

| Check | Command | Result | Exit |
| --- | --- | --- | --- |
| Full backend suite | `python -m pytest` | **784 passed, 9 skipped, 1 failed** (196.92s) — the failure is the live Groq test hitting `getaddrinfo failed` (DNS), see below | **1** |
| Full backend suite, live tests excluded | `python -m pytest -m "not live"` | **784 passed, 9 skipped, 1 deselected** (126.95s) | **0** |
| Live Groq module in isolation | `python -m pytest tests/test_job_analysis_agent.py` | **11 passed, 1 skipped** (the live test skips when the API cannot be reached) | **0** |
| Phase 2J.3/2J.4/2J.5/2J.6 targeted | `pytest tests/test_phase2j3_unified_fields.py tests/test_phase2j4_lifecycle_browser.py tests/test_phase2j4_live_resume.py tests/test_phase2j5_checkbox_guard.py tests/test_phase2j6_first_start_race.py` | **29 passed, 2 skipped** (20.68s) — skips are the two opt-in modules | **0** |
| PostgreSQL concurrency (2J.5 + 2J.6) | `APPLYPILOT_POSTGRES_TEST_URL=… pytest tests/test_phase2j5_postgres_concurrency.py tests/test_phase2j6_first_start_race.py` | **12 passed** (7 existing + 5 new, 15.29s) | **0** |
| Opt-in Chromium | `APPLYPILOT_RUN_BROWSER_TESTS=1 pytest -m browser` | **19 passed, 2 skipped, 785 deselected** (117.14s) | **0** |
| Phase-specific Ruff | `ruff check` over 13 phase files (services, browser integration, 2J.3–2J.6 tests) | `All checks passed!` | **0** |
| Frontend unit/component | `npm test` | **152 passed / 25 files** (26.33s) | **0** |
| TypeScript | `npm run typecheck` | clean | **0** |
| ESLint | `npx eslint .` | clean | **0** |
| Production build | `npm run build` | compiled, **24 routes** | **0** |

The 9 skips in the default run are the opt-in browser modules (2J.4 lifecycle,
2J.5 checkbox), the opt-in PostgreSQL modules (2J.5, 2J.6) and pre-existing
credential/network-gated tests. The one failure in the unfiltered run is
`test_live_groq_job_description_analysis`, which performs a real Groq request;
the captured traceback is `httpcore.ConnectError: [Errno 11002] getaddrinfo
failed`. It is unrelated to this phase's change (job-analysis agent, untouched
file), and the same module passes/skips cleanly in isolation. It is reported as
an **environmental failure, not a regression**: the honest headline is 784
passed with one live-network failure, not "all green".

### F.5 Security and workflow re-confirmation

Re-confirmed against the code and the suites that now pass:

| Invariant | Evidence |
| --- | --- |
| Existing values are never overwritten silently | `_live_value_conflict` compares the live page value with the approved one: identical ⇒ `skipped`, different ⇒ `pending_human_input`, empty ⇒ proceed. The planner also leaves any field with a current value untouched. Covered by 2J.3 tests. |
| Checkbox state changes require renewed approval | `persistent.py::_check` re-reads `is_checked()` immediately before interacting; a mismatch returns `ok=False, requires_review=True` without touching the DOM, which the service turns into `pending_human_input`. 5 real-Chromium tests plus the fast guard test still pass. |
| Approval fingerprints remain bound to the reviewed plan | `fill_approval.approval_fingerprint` covers answers, resume, page URL, application step and the analysed structure; `_verify_live_approval` re-analyses the live page and raises `approval_stale`. New 2J.6 tests only ever pass the fingerprint the plan issued. |
| CAPTCHA, OTP, MFA, passwords and legal declarations stay manual | `_NEVER_AUTOMATE` = CAPTCHA, OTP, MFA, legal declaration, demographic, work authorisation, other, unknown; `_human()` returns no action for them. Visible in the 2J.6 run output: the legal, OTP and CAPTCHA rows are `pending_human_input`. |
| Final Submit / Apply / Finish is never automated | `field_actions.py` exposes no click/submit primitive; `ApplicationService.submit` has no browser path, requires `AWAITING_APPROVAL`, and moves to `NEEDS_USER_ACTION` with manual steps. Unchanged by this phase. |
| Resume ownership is enforced | `ResumeUploadHandler.materialize` → `ensure_owned`; storage key regex plus containment check. Unchanged. |
| Browser sessions cannot cross user boundaries | `require_live_handle`/`get_session` scope by `user_id` **and** application owner; the manager keys handles by session id + owner. F.3 test 5 asserts `404` for a foreign application. |
| Browser recovery fails closed | `recover()` reconciles stale rows first, never silently ends another live session without `force`, and `force` closes only that user's sessions. Unchanged. |
| No access-control bypass introduced | The only new database statement is `SELECT pg_advisory_xact_lock(:key)`; it neither reads nor writes application data and is keyed by a value the request already resolved through an ownership-checked lookup. The new tests assert the 404 path directly. |

### F.6 Real ATS acceptance matrix — prepared, NOT EXECUTED

LIVE VALIDATION: **NOT EXECUTED**. No authenticated employer application was
available, no CAPTCHA or anti-bot control was approached, and no external site
was contacted in this phase. The matrix below defines what must be captured
before any ATS may be called supported; a row may only be marked PASS with the
evidence named.

| Evidence required (per ATS) | Greenhouse | Lever | Workday | Other employer-hosted form |
| --- | --- | --- | --- | --- |
| **Page detection** — platform identified, URL/origin recorded, navigation policy allowed it | ☐ | ☐ | ☐ | ☐ |
| **Form analysis** — analysis version, field count, `automation_eligible_count`, `captcha_detected` captured from the real DOM | ☐ | ☐ | ☐ | ☐ |
| **Field classification** — per-field label/type/category, plus the count of `never automate` rows (CAPTCHA/OTP/MFA/legal/demographic/work authorisation) | ☐ | ☐ | ☐ | ☐ |
| **Existing-value preservation** — at least one pre-filled field left `skipped` with its original value still on the page | ☐ | ☐ | ☐ | ☐ |
| **Approval preview** — plan shown, `approval_fingerprint` issued, one field edited afterwards to prove `approval_stale` | ☐ | ☐ | ☐ | ☐ |
| **Safe filling** — per-field result table (succeeded/verified/skipped/pending), proof that no sensitive field was touched and nothing was overwritten | ☐ | ☐ | ☐ | ☐ |
| **Resume upload** — file input received the selected owned PDF and the filename was visible on the page | ☐ | ☐ | ☐ | ☐ |
| **Session recovery** — pause/resume, stale-session reconcile, `recover` outcome, no duplicate typing after resume | ☐ | ☐ | ☐ | ☐ |
| **Human-controlled final submission** — the user pressed Submit/Apply, `mark-submitted` requires `confirmed=true`, and no automation reached the control | ☐ | ☐ | ☐ | ☐ |
| **Declared support** | Prep only | Prep only | Prep only — Workday/iCIMS are manual-only by design, no automated support is claimed | Prep only |

Procedure to run it (unchanged from D.6): use a real posting a candidate has
already applied to, drive the persistent session end to end, and record the
actual per-field outcome rather than a screenshot of a plan. Any challenge page
(CAPTCHA, Cloudflare, MFA) is handed to the user; refusing to bypass it is a
pass, not a failure.

### F.7 Remaining limitations

1. **Live ATS validation is still outstanding** (F.6). Everything above is
   loopback, in-process or database evidence.
2. **The PostgreSQL tests remain opt-in.** CI without PostgreSQL reports two
   skipped modules, not passed ones.
3. **The advisory lock is PostgreSQL-specific.** On any other backend the
   guarantee is the unique constraint plus the 409 translation, which F.3 probe
   A shows is sufficient for correctness but decides the winner by exception.
4. **`mark_user_input()` is still driven only by the live view**, so the
   coordination pause is not a general detector of page edits (E.6.2).
5. **A refused `CHECK` is not retried automatically** (E.6.3).
6. **11 pre-existing Ruff findings** remain in unrelated files; the 13 phase
   files are clean.
7. **Live Groq is network-dependent** and produced one environmental failure in
   the unfiltered run (F.4).
8. **Glassdoor's Cloudflare block is unchanged**; the manual destination path
   remains the documented fallback.

### F.8 Final Git status

`git status --short` → **107 entries: 50 modified, 57 untracked** (Appendix E
reported 106; the only addition is the new test module). Pre-existing work from
Phases 2I–2J.5 is untouched.

```
 M backend/app/services/application_field_fill_service.py
 M backend/docs/PROJECT_MASTER_ARCHITECTURE_AND_COMPLETION_AUDIT.md
?? backend/tests/test_phase2j6_first_start_race.py
```

The isolated PostgreSQL cluster was stopped and its data directory deleted; test
logs were written under the gitignored `backend/var/`. Nothing was committed,
pushed, reset, cleaned, stashed or reverted.

**First-start race: fixed on PostgreSQL and verified there. Live ATS validation:
NOT EXECUTED.**

---

## Appendix G — Phase 2J.7: live ATS acceptance (16 November 2026)

Scope: finish the live acceptance work Phase 2J.6 prepared and left unexecuted,
prove the resume-filename correction in a real employer form, and run the full
regression. No feature was added, no URL allow-list was extended, no navigation,
ownership, approval or submission rule was weakened, and nothing was committed,
pushed, reset, cleaned, stashed or reverted. All 109 pre-existing uncommitted
entries are intact.

### G.1 What was already in place, and where work stopped

`backend/scripts/live_ats_validation.py` drives the real product services through
an in-process ASGI client, seeds a synthetic candidate (Ada Lovelace) and a
synthetic job record, opens a persistent Chromium session on a public ATS URL and
records one row per stage into `backend/var/live-ats-report.json`. It never
clicks Submit, Apply or Finish.

The 10:46 run had completed Greenhouse analysis, planning, nine verified safe
fields (including the five core identity fields), stale-approval protection,
session recovery, human review, manual handoff and status consistency, but
recorded:

* `resume upload` → `failed` for both Greenhouse file inputs
  (`TimeoutError` in `field_action`, upload); and
* `live_page_after_fill` → `PARTIAL`.

Diagnosis: Greenhouse replaces the `<input type="file">` node the moment it
accepts a file, so reading the input back after `set_input_files` detached or
returned an empty list and a **successful** upload was reported as rejected.
The fix (`persistent.py::_accepted_upload_name`, `_UPLOAD_VERIFY_TIMEOUT_MS = 2_000`)
accepts either the input's own file list **or** the widget's rendering of the
chosen filename, and still fails when the page shows neither. It was written at
10:51 — **after** the last live run at 10:49 — which is exactly where the work
stopped. The filename fix itself (`_safe_upload_name`) was already in
`resume_upload_handler.py` at 10:13.

This appendix re-ran the live matrix with that verification fix in place.

### G.2 Greenhouse validation matrix — real employer-hosted form

Target: `https://job-boards.greenhouse.io/gitlab/jobs/8638232002` — a genuine
public GitLab posting served by Greenhouse. The **candidate and job record are
synthetic**; ``company`` is seeded as "Validation Employer" so the report never
claims to be GitLab's own record. No application was submitted.

Final run: `2026-11-16T12:00:29`, headless Chromium, in-memory SQLite.

| # | Stage | Outcome | Evidence |
| --- | --- | --- | --- |
| 1 | Job URL import | **LIMITATION** | `400 unsupported_job_listing_domain`; direct import is Glassdoor-only by design (G.5). The employer-URL form (`POST /applications/{id}/inspect`) is the supported path. |
| 2 | Application analysis | **PASS** | `200`; platform `greenhouse`, `automation_mode: automated`, `inspection_mode: automated`, 44 questions, 9 missing required. The form itself was inspected live. |
| 3 | Resume recommendation | **PASS** | `compare 200`, recommendation `completed`, 1 resume, selected `ada-lovelace-test-resume.pdf` (manual selection). |
| 4 | Browser session start | **PASS** | `state: browser_ready`, allowed origin `https://job-boards.greenhouse.io:443`. |
| 5 | Form analysis | **PASS** | 22 fields, 11 automation-eligible, 9 human-input, `captcha_detected: false`; 4 unclassified fields warned and routed to human review. |
| 6 | Fill plan | **PASS** | 22 fields, 11 eligible, structure **and** approval fingerprints present, `executable: true`. |
| 7 | Answer provenance | **PASS** | Every ready field carries `confirmed_reusable_answer` (0.96) or `none` for uploads (0.94–0.98); no invented answers. |
| 8 | Missing / sensitive triage | **PASS** | 5 required fields missing from the candidate stay `needs_human_input`; 3 demographic fields (`Gender`, `Are you Hispanic/Latino?`, `Veteran Status`) are never automated. Sensitivity split: 19 public, 3 demographic. |
| 9 | Stale approval protection | **PASS** | A value typed after approval was left untouched; contract exercised in both shapes across runs (`409 approval_stale` refusal, and `200` with a `pending_human_input` row). |
| 10 | Plan refresh | **PASS** | Re-analysis `200`, new approval fingerprint issued, no duplicate field ids. |
| 11 | Safe field fill | **PASS** | 10 succeeded / 10 verified / 0 failed / 18 skipped / 14 pending human input; run paused `needs_user_input`. |
| 12 | Existing-value preservation | **PASS** | `Last Name` pre-typed before approval: plan said `ready`, the fill service re-read the live value, reported `skipped` — "The field already has a value; it was not overwritten" — and the live value was still the user's. |
| 13 | Resume upload + filename | **PASS** | Both file inputs accepted the PDF; `live_page_after_fill` shows `resume_filename_visible: true`. Greenhouse had swapped the inputs again, hence `file_input_names: []`; either evidence form is accepted (G.4). |
| 14 | Live DOM after fill | **PASS** | `submitted: false`, 5 visible text inputs carrying values, resume name visible in the widget. |
| 15 | Checkbox behaviour | **PASS** | No checkbox was fillable from a verified value; all human-owned (demographic/consent). The post-preview race is covered by the 2J.5 real-Chromium suite. |
| 16 | Human review | **PASS** | 5 blocking interventions (visa sponsorship, prior GitLab work, employment-agreement restriction), `ready_for_final_review: false`, `manual_submission_required: true`, `still_unsubmitted: true`. |
| 17 | Session recovery while live | **PASS** | `200`, reattached the existing session, `blocked_by: []`. |
| 18 | Manual submission handoff | **PASS** | `409 approval_required`; no Submit/Apply control was reached; page still unsubmitted. |
| 19 | Status / dashboard consistency | **PASS** | `awaiting_user_information`, workflow `paused_for_user`, pipeline 1 in progress / 0 submitted, `confirmed_by_user: false`. |
| 20 | Session recovery after release | **PASS** | `200`, browser `open`, nothing force-closed. |

### G.3 Lever validation matrix — demo tenant, not an employer posting

Target: `https://jobs.lever.co/leverdemo/ad208490-4052-4f91-a57e-433f2d1e484b/apply`.

**This is Lever's `leverdemo` vendor tenant. It is a controlled/demo ATS
validation, not a genuine employer production application.** No employer-hosted
Lever posting was available to this pass, so no production Lever claim is made.
The report records the synthetic candidate and "Validation Employer" and never
labels the tenant as an employer.

| Stage | Outcome | Evidence |
| --- | --- | --- |
| Job URL import | **LIMITATION** | Same `400 unsupported_job_listing_domain`; employer-URL form used instead. |
| Application analysis | **PASS** | `200`, platform `lever`, live inspection, 72 questions, 3 missing required. |
| Resume recommendation | **PASS** | `compare 200`, recommendation `completed`, selected synthetic PDF. |
| Browser session start | **PASS** | `browser_ready`, origin `https://jobs.lever.co:443`. |
| Form analysis | **PASS** | 40 fields, 8 eligible, 32 human-input, **`captcha_detected: true`** (hCaptcha widget); 6 unclassified fields warned. |
| Field classification | **PASS** | 8 safe categories mapped (name, email, phone, location, LinkedIn, GitHub, portfolio, resume); demographic/EEO/survey/CAPTCHA rows human-owned. |
| Answer provenance | **PASS** | All ready rows `confirmed_reusable_answer`, confidence 0.96; upload row `none`. |
| Safe field filling | **PASS** | 3 succeeded / 3 verified / 0 failed / 37 skipped / 32 pending human input; paused `needs_user_input`. |
| Existing-value preservation | **PASS** | `Email` pre-typed before approval: execution row `skipped` — "already has a value; it was not overwritten" — live value preserved. |
| Resume upload + filename | **PASS** | `input[type=file]` holds the file; `file_input_names: ["ada-lovelace-test-resume.pdf"]`. |
| CAPTCHA safeguard | **PASS** | hCaptcha was detected, classified, and left to the user; no solve/bypass was attempted and it is a blocker in the human loop. |
| Approval fingerprint | **PASS** | Structure + approval fingerprints present; stale-approval probe preserved the user value. |
| Manual review | **PASS** | 1 blocking intervention (`Current company`), `ready_for_final_review: false`, `still_unsubmitted: true`. |
| Session recovery | **PASS** | Reattach `200` while live; reattach `200` after release, nothing force-closed. |
| Manual submission handoff | **PASS** | `409 approval_required`, page still unsubmitted. |
| Status / dashboard consistency | **PASS** | `awaiting_user_information`, `paused_for_user`, 1 in progress / 0 submitted. |

### G.4 Resume filename correction — verified four ways

| Layer | Evidence | Result |
| --- | --- | --- |
| Unit — `_safe_upload_name` | `tests/test_phase2j7_acceptance_fixes.py`, 7 parametrised cases: `ada-resume.pdf`, `Ada Resume (final).pdf` → `Ada Resume _final_.pdf`, `../../etc/passwd` → `passwd.pdf`, `resume.PDF` (extension case kept), `""` → `resume.pdf`, `strange\x00name.pdf` → `strange_name.pdf`, and a 134-character name → 120 characters ending in `.pdf`. | **PASS** |
| Defect found while verifying the above | The first version truncated the finished name to 120 characters, which could cut the `.pdf` extension off an unusually long filename. Fixed by truncating only the stem and appending the original extension; covered by the long-name case. No live run used a name long enough to hit it. | **FIXED** |
| Unit — materialisation | The temporary copy is named `Ada Lovelace - Resume.pdf`, starts with `%PDF`, lives in an `applypilot-upload-*` directory, and both file and directory are gone after the `async with` block. A foreign resume is refused with `resume_not_owned`. | **PASS** |
| Real Chromium — swap | `tests/test_phase2j7_upload_verification_browser.py`: an ATS that replaces the file input on accept is reported as **accepted** with the correct name; a page that silently swallows the file is still reported **failed**. 2 passed with `APPLYPILOT_RUN_BROWSER_TESTS=1`. | **PASS** |
| Real employer form | Greenhouse live run: both file inputs accepted; the page rendered `ada-lovelace-test-resume.pdf` (`resume_filename_visible: true`). Lever demo: `file_input_names: ["ada-lovelace-test-resume.pdf"]`. No `applypilot-upload-` name and no temporary path appeared in either report. | **PASS** |

Security properties retained: the path is always chosen by ApplyPilot from the
resume's own basename (never a user-supplied path), path components are stripped
(`os.path.basename`), unsafe characters are replaced, the PDF extension is kept,
and the directory is removed in a `finally` block.

### G.5 Why job import reports LIMITATION

`app/services/job_import.py` defines `SUPPORTED_IMPORT_SOURCES = frozenset({"glassdoor"})`
and rejects everything else with an explicit `400 unsupported_job_listing_domain`
("Only public Glassdoor job listing URLs can be imported"). It then resolves (never
fetches) the destination and refuses private/internal addresses before storing
anything. Cross-ATS direct import is **not implemented**; this is an explicit
product limitation, not a bug in the ATS support. The allow-list was **not**
extended and the URL/SSRF checks were **not** weakened. Direct Greenhouse/Lever
applications enter through the employer-URL form (`POST /applications/{id}/inspect`),
which is what both matrices above exercised.

`destination_validated: false` in the reports is expected for a direct employer
form: that flag tracks the Glassdoor→ATS redirect hand-off, and a direct employer
URL is already the destination.

### G.6 Real-browser evidence and what changed to obtain it

The live runs contact the real public pages over the network with headless
Chromium. Attempts, in order:

| Run | Result | What it showed |
| --- | --- | --- |
| 10:46 / 10:49 (pre-fix) | Upload `failed` (TimeoutError), live page `PARTIAL` | Successful Greenhouse upload misreported after the input swap. |
| 11:30 (post-fix) | All PASS except import LIMITATION | Filename fix works live; 6 fields skipped as already valued. |
| 11:56 | Greenhouse `stale_approval_protection` / `safe_field_fill` FAIL | The page re-rendered between review and fill; the product correctly returned `409 approval_stale` / a `pending_human_input` row, but the harness treated the safeguard as a failure and its read-back raced the re-render. |
| 12:00 (final) | All PASS except import LIMITATION | Harness hardened (below); no product safeguard changed. |

Harness changes only (`scripts/live_ats_validation.py`): re-read a field once
after the page settles; treat `pending_human_input` as evidence when the live
value cannot be re-read (recorded as PARTIAL, never silently PASS); re-analyse
and retry **once** on a genuine `approval_stale`; and add an explicit
existing-value stage. No service, planner, approval or submission code changed
in this pass.

### G.7 Regression results (16 November 2026)

Backend commands used `C:\Users\kiran\miniconda3\envs\venv_rating\python.exe`
with `PYTHONPATH=.` where the script requires it.

| Check | Command | Result | Exit |
| --- | --- | --- | --- |
| Backend, non-live | `pytest -m "not live" -v` | **793 passed, 11 skipped, 1 deselected** (460.11s; re-run in full after the filename edit) | **0** |
| Phase 2J.7 focused | `pytest tests/test_phase2j7_acceptance_fixes.py -v` | **9 passed** (8 before the long-name case was added; the module was re-run after every edit) | **0** |
| Upload verification, real Chromium | `APPLYPILOT_RUN_BROWSER_TESTS=1 pytest tests/test_phase2j7_upload_verification_browser.py -m browser` | **2 passed** | **0** |
| Opt-in Chromium, all | `APPLYPILOT_RUN_BROWSER_TESTS=1 pytest -m browser` | **23 passed, 2 skipped, 793 deselected** (117.53s) | **0** |
| PostgreSQL concurrency | isolated PG 17.4, `127.0.0.1:55432`, DB `applypilot_test`; `pytest tests/test_phase2j5_postgres_concurrency.py tests/test_phase2j6_first_start_race.py` | **12 passed** (23.80s) | **0** |
| Frontend unit/component | `npm test -- --run` | **152 passed / 25 files** (20.97s) | **0** |
| TypeScript | `npx tsc --noEmit` | clean | **0** |
| ESLint | `npx eslint .` | clean | **0** |
| Production build | `npm run build` | compiled successfully, 3 static pages generated, full route table | **0** |
| Ruff, changed files (41 Python files) | `ruff check …` | **All checks passed!** | **0** |

Ruff initially reported **19 findings** across the three Phase 2J.7 files
(unsorted imports, unused `noqa`, two long lines, `try/except/pass`,
blocking `Path` calls in async functions, one blind `pytest.raises(Exception)`).
They were fixed in the files, not suppressed; the 41 changed Python files are now
clean. The **11 pre-existing repository-wide findings** recorded in F.7 remain in
unrelated Phase 2I files and were not touched.

Environment separation: everything above is local/loopback except the two live
ATS runs, which are network-dependent by design. No network, DNS, credential or
CAPTCHA failure occurred in these runs. Live Groq tests were excluded by
`-m "not live"`.

The lint edits were made to `tests/test_phase2j7_acceptance_fixes.py` and
`tests/test_phase2j7_embedded_frames_browser.py` after the first full backend
run; both affected modules were re-run afterwards (23 passed for the Chromium
suite). The long-name fix in `app/services/resume_upload_handler.py` was made
after that, so the **full non-live suite was run again** on the final code:
793 passed, 11 skipped, exit 0. No other production module was edited.

### G.8 Remaining defects and limitations

1. **Cover-letter file input receives the resume (new finding, not fixed).**
   `FillPlan.resume.file_field_ids` is `["form0-resume", "form0-cover-letter"]`
   for Greenhouse and both uploads are marked succeeded. The planner treats any
   `action: upload` field as a resume target, so the resume PDF is attached to
   the employer's *cover letter* slot. This is not fabrication (the file is the
   candidate's own), but it is the wrong document for that control. A cover
   letter belongs in human hands until a cover-letter document exists in the
   product. **Greenhouse cover-letter handling: PARTIAL.**
2. **Cross-ATS direct import is Glassdoor-only** (G.5). Greenhouse and Lever
   listings must enter via the employer-URL form.
3. **Lever production support is unproven.** The only available target was the
   `leverdemo` tenant; the matrix above is a controlled/demo validation.
4. **Greenhouse metadata is synthetic** (job title/company are validation
   values) though the form is a real GitLab posting; no employer application was
   submitted, so end-to-end employer acceptance remains unverified.
5. **Live ATS behaviour varies between runs.** Whether the stale-approval probe
   leaves the form filled decides which fields are later skipped. Both shapes are
   valid product behaviour and both are recorded; the harness now handles either.
6. **PostgreSQL tests stay opt-in**, as in F.7; CI without PostgreSQL sees skips.
7. **11 pre-existing Ruff findings** remain in unrelated files.
8. **No cover letter, OTP/MFA, or authentication-gated application** was
   exercised; those paths remain human-only by design.

### G.9 Verdict

| Area | Verdict |
| --- | --- |
| Resume filename correction | **PASS** — unit, real-Chromium and live-DOM evidence |
| Greenhouse: analysis → safe fill → upload → review → manual handoff | **PASS** — real employer-hosted form, never submitted |
| Greenhouse cover-letter document handling | **PARTIAL** — resume is attached to the cover-letter slot (G.8.1) |
| Lever (demo tenant) controlled validation | **PASS** — clearly labelled demo, not production |
| Lever employer production validation | **BLOCKED** — no employer-hosted posting available |
| Cross-ATS job import | **LIMITATION** — Glassdoor-only by design, unchanged |
| Regression (backend, browser, PostgreSQL, frontend, lint, build) | **PASS** |

**Phase 2J.7 is complete for its stated scope.** The live matrices are executed,
not prepared; every stage is PASS except the three honest non-passes above
(import LIMITATION, Lever production BLOCKED, cover-letter PARTIAL).

**Release readiness: not ready for general release; suitable for a limited
Greenhouse beta with manual submission.** Before a broader release, fix the
cover-letter attachment, validate one genuine employer-hosted Lever application,
and decide whether cross-ATS direct import is in scope. The absolute safeguards
held in every run: no Submit/Apply/Finish was clicked, no CAPTCHA was solved,
no candidate data was invented, no user-entered value was overwritten, and no
security or concurrency control was weakened.

---

## Appendix L — Phase 2J.7 completion pass (16 November 2026, evening)

This appendix continues Appendix G from the point it stopped. It records what was
re-tested, what was corrected, and what remains. Where it differs from G.8 and
G.9, this appendix supersedes them; G is left intact as the record of that run.

No feature was added. No URL allow-list was extended. No navigation, ownership,
approval, submission or concurrency rule was weakened. Nothing was committed,
pushed, reset, stashed, cleaned or git-reverted, and every pre-existing
uncommitted change is still present. (One prototype edit of this pass was undone
by hand; L.4.)

### L.1 What was verified before changing anything

The final 12:00 report was re-read row by row. It was internally consistent
except for one real product defect that G.8.1 had recorded but not fixed: the
Greenhouse fill plan contained **two** upload targets,
`resume.file_field_ids = ["form0-resume", "form0-cover-letter"]`, so the resume
PDF was attached to the employer's **cover-letter** slot as well as the resume
slot.

Root cause (confirmed in code, not inferred):

* `_action_for()` returned `FillAction.UPLOAD` for **every** `FormFieldType.FILE`;
* `_human()` preserved that `UPLOAD` action for any field sent to human review;
* `plan()` collected every `UPLOAD` row into `resume.file_field_ids`.

A cover-letter-classified file control therefore entered the resume upload list.
The same path would have caught any generic/unknown file input.

### L.2 Correction made

`app/services/application_field_planner.py`:

* `_action_for()` now returns `UPLOAD` only when the file control is classified
  `RESUME_UPLOAD`; every other file control gets `NONE`;
* `_human()` clears `UPLOAD` unless the row is a resume upload, so a later
  human-review branch cannot reintroduce the action.

Regression test added to `tests/test_phase2j7_acceptance_fixes.py`:
`test_only_the_resume_slot_receives_the_resume_file`. It plans a resume slot, a
cover-letter slot and a generic file slot and asserts that only the resume slot
is uploaded and listed in `resume.file_field_ids`.

Live verification: in all five post-fix runs, `fill_plan_refresh.upload_fields`
contained only `form0-resume`. The pre-fix 12:00 run contained both
`form0-resume` and `form0-cover-letter`.

`scripts/live_ats_validation.py` now writes a machine-readable classification for
each target (`validation_type`, `is_production_employer`, `tenant`, `note`) so the
JSON evidence itself cannot present the Lever demo tenant as an employer
production validation.

### L.3 Greenhouse validation matrix — final run

Target: `https://job-boards.greenhouse.io/gitlab/jobs/8638232002`, a real public
GitLab posting. Candidate and job record are synthetic ("Ada Lovelace", "Validation
Employer"); nothing was submitted. Final run: `2026-11-16T21:25:28`, headless
Chromium, in-memory SQLite. Classification: `employer_hosted_public_posting`.

| # | Stage | Outcome | Evidence |
| --- | --- | --- | --- |
| 1 | Job URL import | **LIMITATION** | `400 unsupported_job_listing_domain`; Glassdoor-only by design (L.7). |
| 2 | Application analysis | **PASS** | `200`, platform `greenhouse`, live inspection, 44 questions, 9 missing required. |
| 3 | Resume recommendation | **PASS** | `compare 200`, recommendation `completed`, 1 resume selected. |
| 4 | Browser session start | **PASS** | `browser_ready`, allowed origin `https://job-boards.greenhouse.io:443`. |
| 5 | Form analysis | **PASS** | 22 fields, 11 automation-eligible, 9 human-input, `captcha_detected: false`. |
| 6 | Fill plan | **PASS** | 22 fields, 11 eligible, structure + approval fingerprints, `executable: true`. |
| 7 | Answer provenance | **PASS** | `confirmed_reusable_answer` (0.94–0.98); no invented answers. |
| 8 | Missing / sensitive handling | **PASS** | 5 required fields stay `needs_human_input`; demographic/EEO fields never automated. |
| 9 | Stale approval protection | **PASS** | `409 approval_stale`; the typed value was preserved, never overwritten. |
| 10 | Plan refresh | **PASS** | Re-analysis version 2; **only** `form0-resume` in upload fields; no duplicate ids. |
| 11 | Safe field fill | **PASS** | 9 succeeded / 9 verified / 0 failed / 19 skipped / 15 pending human input; paused `needs_user_input`. |
| 12 | Existing-value preservation | **PASS** | `Last Name` pre-typed before approval: execution `skipped`, live value still the user's. |
| 13 | Resume upload + filename | **PASS (this run)** | Resume slot succeeded and verified; page rendered `ada-lovelace-test-resume.pdf`. Intermittent across runs — L.4. |
| 14 | Live DOM after fill | **PASS (this run)** | `submitted: false`, 5 visible text inputs carrying values, resume name visible in the widget. The file input was swapped again, so `file_input_names` is empty by design. |
| 15 | Checkbox behaviour | **PASS** | No checkbox was fillable from a verified value; all human-owned. |
| 16 | Human review | **PASS** | 5 blocking interventions (visa sponsorship, prior GitLab work, employment agreement), `ready_for_final_review: false`. |
| 17 | Session recovery while live | **PASS** | `200`, existing session reattached, `blocked_by: []`. |
| 18 | Manual submission handoff | **PASS** | `409 approval_required`; no Submit/Apply control was reached; page unsubmitted. |
| 19 | Status / dashboard consistency | **PASS** | `awaiting_user_information`, workflow `paused_for_user`, pipeline 1 in progress / 0 submitted. |
| 20 | Session recovery after release | **PASS** | `200`, browser `open`, nothing force-closed. |

Final run totals: **18 PASS, 1 LIMITATION, 0 PARTIAL, 0 FAIL**.

### L.4 Greenhouse resume-upload intermittency — PARTIAL

The resume upload is **not stable on Greenhouse**. Five runs were executed on
the same fixed code:

| Run (UTC) | Stale-approval retry | Resume upload | Live filename | Overall Greenhouse |
| --- | --- | --- | --- | --- |
| 21:12:21 | yes | **failed** | not shown | 16 PASS / 2 PARTIAL |
| 21:14:55 | no | succeeded | shown | 18 PASS |
| 21:16:35 | yes | **failed** | not shown | 16 PASS / 2 PARTIAL |
| 21:20:35 | yes | **failed** | not shown | 16 PASS / 2 PARTIAL |
| 21:25:28 (final) | yes | succeeded | shown | 18 PASS |

Three of five runs failed the upload; two (including the final run) succeeded.
The failure is reported honestly as `failed` ("The interaction did not complete.")
and `live_page_after_fill` returns PARTIAL; it is **not** reported as success.
Nothing was silently attached. The trigger is Greenhouse replacing/re-rendering
the hidden file input after the stale-approval refresh; `set_input_files()`
returns without error while neither the input nor the widget shows a filename.

A single re-locate-and-retry was prototyped in `_upload()` and **rejected**: it
made the real-Chromium fixture that deliberately swallows a file report success,
which would be a false-positive integrity failure. The prototype was fully
reverted (`persistent.py` is unchanged from the start of this pass) and the
negative browser test passes again. This is left as a real remaining defect, not
papered over.

**Greenhouse resume upload: PARTIAL.** The filename correction itself is correct
when the upload is accepted (L.6); the acceptance is intermittent.

### L.5 Lever validation matrix — demo tenant

Target: `https://jobs.lever.co/leverdemo/ad208490-4052-4f91-a57e-433f2d1e484b/apply`.

**This is Lever's `leverdemo` vendor tenant. It is a controlled/demo ATS
validation, not a genuine employer production application.** No employer-hosted
Lever posting was available, so no production Lever claim is made. The report
now records this explicitly: `validation_type: demo_tenant`,
`is_production_employer: false`, `tenant: leverdemo`.

| Stage | Outcome | Evidence |
| --- | --- | --- |
| Job URL import | **LIMITATION** | Same `400 unsupported_job_listing_domain`; employer-URL form used instead. |
| Application analysis | **PASS** | `200`, platform `lever`, live inspection, 72 questions, 3 missing required. |
| Resume recommendation | **PASS** | `compare 200`, recommendation `completed`, synthetic PDF selected. |
| Browser session start | **PASS** | `browser_ready`, origin `https://jobs.lever.co:443`. |
| Form analysis | **PASS** | 40 fields, 8 eligible, 32 human-input, `captcha_detected: true` (hCaptcha). |
| Field classification | **PASS** | Name/email/phone/location/LinkedIn/GitHub/portfolio/resume mapped; demographic/EEO/survey/CAPTCHA human-owned. |
| Answer provenance | **PASS** | All ready rows `confirmed_reusable_answer`; upload row `none`. |
| Safe field filling | **PASS** | 3 succeeded / 3 verified / 0 failed / 37 skipped / 32 pending human input. |
| Existing-value preservation | **PASS** | `Email` pre-typed before approval: `skipped`, live value preserved. |
| Resume upload + displayed filename | **PASS** | `input[type=file]` holds the file; `file_input_names: ["ada-lovelace-test-resume.pdf"]`. |
| Approval fingerprint | **PASS** | Structure + approval fingerprints present; stale probe preserved the user value. |
| CAPTCHA safeguard | **PASS** | hCaptcha detected, classified, left to the user; no solve/bypass attempted. |
| Manual review | **PASS** | 1 blocking intervention (`Current company`), `ready_for_final_review: false`. |
| Session recovery | **PASS** | Reattach `200` while live and after release; nothing force-closed. |
| Manual submission handoff | **PASS** | `409 approval_required`; page still unsubmitted. |
| Status / dashboard consistency | **PASS** | `awaiting_user_information`, `paused_for_user`, 1 in progress / 0 submitted. |

Final run totals: **18 PASS, 1 LIMITATION**. Lever held steady across all five
runs; the only Lever change in this pass is the accurate demo classification.

**Lever controlled/demo validation: PASS. Lever employer production validation:
BLOCKED** — no authorized employer-hosted posting was available.

### L.6 Resume filename correction — verified

| Layer | Evidence | Result |
| --- | --- | --- |
| Unit — `_safe_upload_name` | 7 parametrised cases: safe name preserved; `Ada Resume (final).pdf` → `Ada Resume _final_.pdf`; `../../etc/passwd` → `passwd.pdf`; `resume.PDF` keeps its extension; empty → `resume.pdf`; NUL byte → `strange_name.pdf`; 134-char name → 120 chars ending `.pdf`. | **PASS** |
| Unit — materialisation | Temp copy uses the original name, starts with `%PDF`, lives in `applypilot-upload-*`, and both file and directory are gone after the context; a foreign resume is refused `resume_not_owned`. | **PASS** |
| Real Chromium — swap and silent discard | `tests/test_phase2j7_upload_verification_browser.py`: swapped input is accepted with the correct name; a page that shows no filename is still **failed**. 2 passed. | **PASS** |
| Real employer form | Greenhouse final run: widget rendered `ada-lovelace-test-resume.pdf` (`resume_filename_visible: true`). Lever: input holds `ada-lovelace-test-resume.pdf`. | **PASS when accepted** |
| Path exposure | `applypilot-upload`, `C:\Users` and temporary paths occur **0** times in the final report and run log. | **PASS** |

Focused module: `tests/test_phase2j7_acceptance_fixes.py` — **10 passed** (the
new cover-letter regression included).

### L.7 Job import limitation — unchanged

`app/services/job_import.py` still defines `SUPPORTED_IMPORT_SOURCES =
frozenset({"glassdoor"})` and rejects other domains with `400
unsupported_job_listing_domain`. The allow-list was **not** extended and the
URL/SSRF checks were **not** weakened. Greenhouse and Lever entered through
`POST /applications/{id}/inspect`. Cross-ATS direct import is **not implemented**
and is reported as an explicit product limitation, not an ATS-support failure.

### L.8 Real-browser evidence and safeguards

All live evidence is headless Chromium against the real public pages, in-process
ASGI, synthetic candidate/job records, and a local SQLite database. The report
records `validation_type` for each target (L.2). Across every run:

* Submit / Apply / Finish was never clicked; `submitted`/`still_unsubmitted`
  evidence is present in each run.
* No CAPTCHA, Cloudflare, OTP, MFA, password or sign-in control was solved,
  read or bypassed. Lever's hCaptcha was detected and left to the user.
* No candidate information was invented; every filled row carries a provenance
  source.
* No user-entered value was overwritten; stale-approval and existing-value
  probes preserved the typed value in every run.
* Every temporary upload copy was removed, and no temporary path appeared in any
  report.

### L.9 Regression results (final code)

| Check | Command | Result | Exit |
| --- | --- | --- | --- |
| Backend, non-live | `pytest -m "not live"` | **794 passed, 11 skipped, 1 deselected** in 670.63s | **0** |
| Phase 2J.7 focused | `pytest tests/test_phase2j7_acceptance_fixes.py` | **10 passed** | **0** |
| Upload verification, real Chromium | `APPLYPILOT_RUN_BROWSER_TESTS=1 pytest tests/test_phase2j7_upload_verification_browser.py -m browser` | **2 passed** | **0** |
| Opt-in Chromium, all | `APPLYPILOT_RUN_BROWSER_TESTS=1 pytest -m browser` | **23 passed, 2 skipped, 795 deselected** in 122.04s | **0** |
| PostgreSQL concurrency | fresh local PG 17 cluster on `127.0.0.1:55432`, DB `applypilot_test`; `pytest tests/test_phase2j5_postgres_concurrency.py tests/test_phase2j6_first_start_race.py` | **12 passed** | **0** |
| Frontend unit/component | `npm test -- --run` | **152 passed / 25 files** | **0** |
| TypeScript | `npx tsc --noEmit` | clean | **0** |
| ESLint | `npx eslint .` | clean | **0** |
| Production build | `npm run build` | compiled successfully, full route table | **0** |
| Ruff, changed files (41 Python files) | `ruff check` | **All checks passed** | **0** |

Environment notes, separated from code failures:

* The first full Chromium sweep had **one** failure:
  `test_an_embedded_cross_origin_frame_keeps_the_session_healthy`. The frame list
  was `['http://127.0.0.1:54098/apply', '']` — the local iframe had not loaded
  when the test sampled it; the test's wait loop checks markup text, not frame
  presence. The module then passed **2 / 2**, and the full sweep passed **23 / 23**
  on re-run. This is a test timing flake, not a product regression.
* The PostgreSQL cluster was started fresh for this pass and stopped afterwards.
  Without it the two test modules skip, as before.
* No DNS, network, credential or CAPTCHA failure occurred in the live runs.

### L.10 Remaining defects and limitations

1. **Greenhouse resume upload is intermittent — PARTIAL.** Three of five
   post-fix runs failed to attach the resume after a stale-approval refresh
   (L.4). The product reports the failure honestly and leaves the field to the
   user; it never claims a false success. This is release-blocking for an
   unattended Greenhouse flow.
2. **Lever employer production validation is BLOCKED.** Only the `leverdemo`
   tenant was available; the demo validation must not be presented as employer
   production evidence.
3. **Cover-letter files are not supported.** The wrong-document defect is fixed:
   the cover-letter slot is now left to the user instead of receiving the resume.
   ApplyPilot has no cover-letter document to attach, so this remains an explicit
   product limitation, not a silent behaviour.
4. **Cross-ATS direct import is Glassdoor-only** (L.7). Greenhouse/Lever listings
   must enter through the employer-URL form.
5. **One Chromium test is timing-flaky** (embedded-frame wait condition); the
   product code passed on re-run.
6. **11 pre-existing repository-wide Ruff findings** remain in unrelated Phase 2I
   files; the 41 changed Python files are clean.
7. **PostgreSQL tests stay opt-in.**
8. **Metadata is synthetic** (candidate and job record) though the Greenhouse
   form is a genuine public employer posting. No application was submitted, so
   end-to-end employer acceptance remains unverified.

### L.11 Final acceptance verdict

| Area | Verdict |
| --- | --- |
| Greenhouse analysis → planning → safe fill → review → manual handoff | **PASS** |
| Greenhouse existing-value preservation, stale approval, session recovery, status consistency | **PASS** |
| Greenhouse resume upload + employer-visible filename | **PARTIAL** — correct when accepted, intermittent after a page re-render (L.4) |
| Greenhouse cover-letter handling | **PASS for safety** — resume is no longer attached there; cover-letter documents remain **unsupported** |
| Lever controlled demo validation | **PASS** — clearly labelled `demo_tenant` in the report |
| Lever employer production validation | **BLOCKED** — no employer-hosted posting available |
| Cross-ATS job import | **LIMITATION** — Glassdoor-only by design, unchanged |
| Resume filename correction and temp-file hygiene | **PASS** |
| Regression: backend, Chromium, PostgreSQL, frontend, lint, build | **PASS** |

**Phase 2J.7 acceptance is complete for its scope, but the Greenhouse resume
upload is not reliable enough for general release.**

**Release readiness: NOT READY for general release.** A supervised, manual-submission
Greenhouse pilot is defensible only if the user confirms the resume is attached
before submitting; Lever production remains unvalidated and cross-ATS import is
out of scope. Before release, make the Greenhouse upload deterministic (and add a
real-Chromium regression for the re-render case that does not weaken the
silent-discard check), validate one authorized employer-hosted Lever application,
and decide whether cover-letter documents and cross-ATS import are in scope.

### L.12 Git state

No commit, push, reset, stash, clean or git-revert was performed. The working tree
still contains all pre-existing uncommitted changes plus this pass's edits:

* `backend/app/services/application_field_planner.py` — resume-only upload action
* `backend/tests/test_phase2j7_acceptance_fixes.py` — cover-letter regression
* `backend/scripts/live_ats_validation.py` — demo/employer classification metadata
* `backend/var/live-ats-report.json` — final live evidence, plus archived runs and
  logs under `backend/var/live-ats-coverletter-fix*`

`backend/app/integrations/browser/persistent.py` was returned to its pre-pass
content after the rejected retry prototype (L.4).

## Appendix M — Phase 2J.7.1 runtime recovery (16 November 2026, late evening)

Appendix L records the 2J.7 field-fill work and is left intact. This appendix
covers only the two runtime errors a user saw in the Intelligent Form Filling
panel, and the local UI verification after they were fixed. No feature, URL
allow-list entry, navigation rule, ownership check, approval fingerprint or
submission rule was changed.

### M.1 Root cause of "The ApplyPilot API could not be reached."

The error is the BFF's 504 `backend_unreachable` envelope, so the Next.js server
could not complete `backendFetch`. Two independent runtime faults produced it
locally, both reproduced before any edit:

1. **The backend worker was dead.** It accepted TCP connections on 8000 and never
   answered `GET /api/v1/health` (worker RSS 30 MB: it never finished starting).
   Every proxied route therefore failed. Restarting it restored the API.
2. **The Next.js dev build was corrupt.** `/.next` raised
   `Cannot find module './897.js'` from `webpack-runtime.js`, so every
   `/api/backend/*` call returned HTTP 500 with an HTML error page. Restarting
   the dev server with a clean `.next` fixed it.

Two error-handling defects made these failures indistinguishable and misleading:

* the BFF mapped **timeouts** and **connection failures** to the same 504 with
  the same copy, so a slow live-page action on a healthy backend was reported as
  "could not be reached";
* `getCurrentUser()` collapsed a 401 and an outage to `null`, so the protected
  layout redirected an actively signed-in user to `/login?expired=1` — claiming
  an expired session and discarding the workspace — whenever the API was down.

### M.2 Root cause of "The browser session expired or was closed."

The message is the honest `EXPIRED_MESSAGE` for a row whose `browser_status` is
`expired`: the persisted row survived a backend restart but the in-process
Playwright handle did not, because handles live in process memory. The recorded
session (`app 2e43b483`, `run 1`) shows `last_activity_at 11:11:49`,
`expires_at 11:23:56` and `stopped_at 12:49:22` — marked expired by a later
process, not by the user.

That record also exposed a real defect: **`expires_at` was pinned to
`started_at + TTL` while `last_activity_at` kept moving.** Nothing renewed the
TTL of a session the user was using, and the periodic sweeper closed the browser
15 minutes after it opened. A plan + fill on a real form (8 s per plan, 12 s per
fill on a trivial page) routinely ran past that under normal use, so a working
browser was killed mid-form and then truthfully reported as expired. The same
gap applied to the live view, which may run for 3600 s but never refreshed a
900 s session TTL.

### M.3 Corrections made

* `app/services/browser_session_service.py` — `_reconcile()` now calls a new
  `_renew()`: every authenticated read that reconciles a live row (status,
  recovery, analysis, plan, fill, view ticket) pushes `expires_at` and the
  manager's monotonic TTL forward. An abandoned session still expires on
  schedule; a dead browser is never renewed.
* `app/services/browser_view_service.py` — `is_live()`, called while a view is
  attached, refreshes the same idle window: watching the employer page is use.
* `frontend/src/app/api/backend/[...path]/route.ts` — live-browser routes
  (`detect-destination`, `destination`, `recover`, `form-analysis`, `fill-plan`,
  `fill`) get a 150 s budget instead of 30 s; abort/timeout now returns 504
  `backend_timeout` with timeout-specific copy, connection failure keeps
  `backend_unreachable` and names the fix; both log one sanitised line
  (`[bff] METHOD path failed after Nms (reason)`) with no token or body.
* `frontend/src/lib/auth/session.ts` + `app/(app)/layout.tsx` — new
  `probeSession()` returns `authenticated | unauthenticated | unavailable`. Only
  a real 401/403 redirects to `/login?expired=1`; an outage renders a "cannot
  reach its API" screen that states the sign-in is still valid.
* `frontend/src/lib/api/client.ts`, `lib/api/errors.ts` — a failed same-origin
  BFF fetch now says the web app could not be reached (not the backend), and a
  bodyless 5xx names the app server instead of "Something went wrong".

Regression tests added:

* `tests/test_phase2e_browser_sessions.py::test_activity_renews_the_idle_window_but_idleness_still_expires`
  (fails without `_renew()`, verified by temporarily disabling it);
* `frontend/tests/backend-proxy-route.test.ts` — timeout vs unreachable, plus the
  150 s live-browser budget;
* `frontend/tests/session-probe.test.ts` (new) and
  `tests/unauthenticated-access.test.ts` — outage is not an expired sign-in.

### M.4 Local UI verification

Backend `python -m scripts.run_server` on 8000, frontend `next dev` on 3000,
Chromium driven through the real UI (Google OAuth replaced by a locally minted
session cookie — the only shortcut taken). Verified in order: workspace load;
start browser session; the employer page visible in the live stream (frames
captured); form analysis; field-preview table with fingerprint; approve & fill;
resume selection state; human-loop blockers; final submission marked manual;
reload and navigation with reattachment. Evidence: `backend/var/shots/01`-`10`.

Failure modes were then reproduced deliberately: with the backend stopped the UI
shows "ApplyPilot cannot reach its API" and keeps the session (screenshot 08);
after restart the session is reported expired and the employer page is reopened
only by clicking "Recover browser" (09, 10); the database gained exactly one new
run at that click (`run 8`), and its `expires_at` tracks `last_activity_at`
(17:21:00 -> 17:36:00) rather than `started_at`.

### M.5 Results and remaining limitations

80 passed, 3 skipped across the eight relevant backend suites (browser sessions,
view, field fill, form analysis, human loop, upload verification, browser stream,
embedded frames); 158 frontend tests passed; `tsc --noEmit` and `eslint .` clean;
`ruff` clean on every file touched here (the 11 pre-existing findings are all in
Phase 2I `answer_assistant` files and were left alone).

Limitations: `restart=true` is still not exposed in the fill UI, so approving a
run that is already `ready_for_final_review` returns the honest 409
`field_fill_already_complete` instead of offering a redo; the assertion that the
live-view TTL renewal keeps a watched session alive is covered by unit-level
behaviour rather than a 15-minute live wait; and `--reload` still destroys
in-process browsers on every backend edit, so acceptance runs should not use it.
No commit, push, reset, stash, clean or revert was performed; all pre-existing
uncommitted work is intact. Local-only driver scripts and screenshots are in
`backend/var/` (git-ignored).
