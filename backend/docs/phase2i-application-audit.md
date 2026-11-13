# Phase 2I - Application Completeness Audit (Stages 1-3)

Scope: make ApplyPilot AI a working product before deployment. **No deployment,
DNS, Nginx, systemd or infrastructure work was performed in this phase.**

Method: static trace of every path the browser can take, end to end -
frontend page -> hook -> client function -> BFF proxy allowlist -> FastAPI route
-> service -> model - plus a new executable contract test that proves the
frontend/backend route contract instead of eyeballing it.

Baseline test state at the start of this phase, re-executed (not carried over):

| Suite | Command | Result |
| --- | --- | --- |
| Backend | `pytest -q` (env `venv_rating`, Python 3.10) | **680 passed, 6 skipped, 0 failed** (JUnit report) |
| Frontend | `vitest run` | **79 passed** before the new test, 169 after |
| Types / lint | `tsc --noEmit`, `eslint` | clean |

---

## 1. The defect this audit actually found

The frontend never talks to FastAPI directly: every call goes through the BFF at
`app/api/backend/[...path]/route.ts`, which only forwards paths present in the
`PROXY_ROUTES` allowlist in `src/lib/constants.ts`. That allowlist had drifted
behind the client layer in four places, and **every component test mocked the
API layer, so nothing caught it.**

`frontend/tests/proxy-contract.test.ts` (new file, 90 assertions) enumerates
every `(method, path)` pair the clients call and asserts the allowlist accepts
it. It failed on exactly four real routes:

| Route | Client caller | Effect before the fix |
| --- | --- | --- |
| `GET applications/{id}/workflow` | `useApplicationWorkflow` (application workspace + redirect panel) | 404 -> the workflow/submission-phase panel could never load |
| `POST browser-sessions/by-application/{id}/detect-destination` | `useDetectApplicationDestination` (redirect panel) | 404 -> Glassdoor -> Indeed/Greenhouse handoff detection dead |
| `POST jobs/{id}/analyze` | `useAnalyzeJob` (job intelligence panel) | 404 -> "Analyze posting" button did nothing |
| `GET jobs/{id}/intelligence` | `useJobIntelligence` (job intelligence panel) | 404 -> cached intelligence never displayed |

All four backend routes exist and work; only the proxy policy was missing them.
**Fixed** in `src/lib/constants.ts` (allowlist only - no application/security
logic touched; the proxy still 404s anything unlisted).

Verified after the fix:

* `vitest run tests/proxy-contract.test.ts tests/proxy-resume-intelligence.test.ts`
  -> 94 passed (the pre-existing test still asserts `POST jobs/{id}/intelligence`
  and `GET metrics` stay rejected, so the allowlist did not become a blanket).
* Decorators re-read to confirm the allowlisted methods match the backend
  (`@router.get` workflow, `@router.post` detect-destination, `@router.post`
  analyze, `@router.get` intelligence).
* `tsc --noEmit` and `eslint` clean; full suite 169 passed.

## 2. Feature inventory

Statuses are assigned from traced behavior, not from the existence of a file.

| # | Feature | Status | Evidence / why |
| --- | --- | --- | --- |
| A1 | Google login, session, logout, expiry | **COMPLETE** | `auth.py` + BFF `exchange/logout/session` routes, HttpOnly cookie, revocation tests (`logout-revocation`, `session-cookie`, `unauthenticated-access`) |
| A2 | Per-user data isolation | **COMPLETE** | every service call takes `current_user`; ownership checks, e.g. `ResumeUploadHandler.ensure_owned` |
| A3 | Candidate profile CRUD (education, experience, projects, certifications, preferences, completeness) | **COMPLETE** | 20 `profile` routes, all allowlisted, `profile-editor.tsx` wired to all of them |
| B1 | Gmail OAuth (read-only), status, disconnect | **COMPLETE** | `gmail/status`, `gmail/disconnect`, `connection-panel.tsx` |
| B2 | Email -> job extraction, dedupe, sync status | **COMPLETE** | `jobs/discover` + `gmail/glassdoor/jobs`; dedupe by `UniqueConstraint(user_id, dedupe_key)`; `GlassdoorDiscovery` shows scan state and error copy |
| B3 | Expired/revoked token handling | **COMPLETE** | `gmail_oauth` refresh + error mapping surfaced via `discoveryErrorCopy` |
| C1 | PDF upload, validation, extraction, parsing | **COMPLETE** | magic-byte `%PDF-` check, 10 MB cap, content-type allow-list, ownership |
| C2 | **DOCX upload** | **NOT IMPLEMENTED** | zero `docx` references anywhere in the backend; client `accept=".pdf"` only. The brief asks for PDF *and* DOCX |
| C3 | Multiple resumes, delete, default, download | **COMPLETE** | `resumes` CRUD + `resume-manager.tsx` |
| C4 | Resume intelligence + caching | **COMPLETE** | `resumes/{id}/analyze`, `/intelligence`, cached rows, panel wired |
| D1 | Job intelligence (skills, seniority, requirements, employment type, remote) | **COMPLETE (now reachable)** | endpoints existed; UI was broken by the missing proxy route and is now fixed |
| D2 | Resume-to-job matching, evidence, confidence, missing skills | **COMPLETE** | `matching_engine`, `evidence-list`, `confidence-badge`, `score-meter`; explainability tests present |
| D3 | Multi-resume comparison + recommendation | **COMPLETE** | `jobs/{id}/compare-resumes`, `jobs/{id}/recommendation`, `resume-comparison-panel` |
| E1 | Job list UI | **COMPLETE** | `GlassdoorDiscovery` renders `useJobs({limit:50})` via `JobCard` |
| E2 | Search / filter / sort | **NOT IMPLEMENTED** | `list_jobs` takes only `limit`/`offset`; no query, stage or score filters in API or UI |
| E3 | Save / dismiss a recommendation | **NOT IMPLEMENTED** | no `saved`/`dismissed` field on `Job`, no endpoint, no UI action |
| E4 | Job detail page | **NOT IMPLEMENTED** | no `/jobs/[id]` route; intelligence is only reachable inline on a card |
| E5 | Recommendation history | **NOT IMPLEMENTED** | no event/audit table for recommendation decisions |
| F1 | Application creation, lifecycle, unique per (user, job) | **COMPLETE** | `analyze`/`inspect`, `application_state` legal transitions, `UniqueConstraint(user_id, job_id)`, `IntegrityError` adopted rather than duplicated |
| F2 | Status/stage, history, notes, retry/recovery | **COMPLETE** | `PATCH /stage` (applied/screening/interview/offer/rejected/withdrawn), `PATCH /cancel`, retry guards |
| F3 | Application list filtering/pagination | **NOT IMPLEMENTED** | list returns all rows, `total=len(...)`; fine at small scale, not at real scale |
| G1 | AI answer drafting for application questions | **NOT IMPLEMENTED** | `assistant/page.tsx` is an explicit "Not available yet" placeholder; no generation endpoint |
| G2 | Evidence-based reusable answers | **PARTIALLY COMPLETE** | `profile/answers` stores reusable answers; `missing_information` + `application_answer_matching` fill verified values; but no LLM drafting and **no user-facing answer library screen** |
| G3 | Confidence/provenance, no fabrication | **COMPLETE** | `answer_safety`, `field_value_verifier`; `mark-submitted` refuses without `confirmed: true` |
| H1 | Browser session, live streaming, ownership, expiry | **COMPLETE** | `browser_sessions` + `browser_view` ticket flow, single-worker assumption documented |
| H2 | Glassdoor handoff, Indeed SmartApply, Greenhouse | **COMPLETE (now reachable)** | `application_redirect_service`/`indeed_smartapply`/`application_platforms`; detection was 404ing through the proxy and now works |
| H3 | Form analysis, safe planning, safe filling, resume choice | **COMPLETE** | `application_form_analyzer`, `application_field_planner`, `application_field_fill_service`, `field_value_verifier` |
| H4 | Human-loop guardrails (no auto Easy Apply, no auto submit, manual CAPTCHA/OTP/MFA/password/legal) | **COMPLETE / unchanged** | no guardrail code was touched in this phase |
| I1 | Dashboard real metrics | **PARTIALLY COMPLETE** | stats come from real APIs (`useJobs`, `useMatchingSummary`, `useGmailStatus`) with honest em-dash error states - but only jobs/resumes/Gmail; **no applications-in-progress, completed, interview or rejected counts** |
| I2 | Navigation, loading/empty/error states | **COMPLETE** | skeletons, `EmptyState`, `error.tsx`, `not-found.tsx`, `global-error.tsx` across pages |
| I3 | Responsive layout | **NEEDS VALIDATION** | Tailwind breakpoints present; not visually verified in this phase |
| J1 | Profile / Google / Gmail / resume / application settings | **PARTIALLY COMPLETE** | profile editor, connection panel, auto-apply settings page exist; no single settings surface |
| J2 | Notification settings | **NOT IMPLEMENTED** | no notification model, endpoint or UI |
| J3 | Account management, data deletion / privacy controls | **NOT IMPLEMENTED** | no delete-account or data-export endpoint; settings page only renders account summary + logout |

Counts: 20 COMPLETE, 4 PARTIALLY COMPLETE, 8 NOT IMPLEMENTED, 1 NEEDS VALIDATION,
0 remaining BROKEN (the 4 broken proxy routes were the only BROKEN items found
and are fixed).

## 3. Frontend / backend / database gaps

**Frontend gaps**
* `assistant` page is a placeholder (G1) - the one place the UI admits it.
* No job detail route (E4); no answer library UI (G2); no application filters (F3).
* Dead but harmless: `useApplicationPlatforms` is exported and proxied yet no
  component uses it.
* The assistant placeholder text is stale - it claims job analysis "is not
  exposed through an API endpoint yet", but `jobs/{id}/analyze` and
  `/intelligence` exist (and were just unblocked).

**Backend gaps**
* No DOCX parsing path (C2) - would need a parser dependency; not added in this
  phase because it changes `requirements.txt` and the upload contract.
* No search/sort on `list_jobs`, no `saved/dismissed` on `Job` (E2/E3/E5).
* No answer-generation endpoint (G1), no notification model, no account/data
  deletion (J2/J3).

**Database gaps**
* `Job` has no user-state columns (saved/dismissed) and no recommendation
  history table - E3/E5 need a migration.
* `Application` correctly carries `status` + `stage`; interview/rejection
  tracking already exists in the schema and only needs dashboard aggregation.

## 4. Security review of what I touched

* Change surface: one allowlist array in `src/lib/constants.ts` + one new test.
* The proxy remains **deny-by-default**: unlisted paths still 404, and the
  regression test pins that.
* Nothing weakened: single-worker architecture, no auto-submit, manual
  CAPTCHA/OTP/MFA/password/legal declarations, no overwrite of existing form
  values, explicit resume approval - all untouched.
* Upload security reviewed and left as-is: magic-byte sniffing, size cap,
  content-type allow-list, ownership check, temp file deleted in `finally`.

## 5. Prioritized roadmap (Stages 4-8)

**P0 - done this phase**
1. BFF allowlist drift -> 4 routes unblocked, guarded by a contract test. ✅

**P1 - highest product value, no new infra**
2. **E4 + E2/E3**: job detail page (`/jobs/[id]`) hosting intelligence,
   comparison and recommendation; add `q`/`sort`/`stage` query params to
   `list_jobs`, and `saved`/`dismissed` columns + endpoint (one Alembic revision).
3. **I1**: application pipeline on the dashboard - counts by `stage`
   (interview/rejected/offer) from an aggregate endpoint, no new tables.
4. **G2 (UI only)**: surface the existing reusable-answer library so users can
   review/edit what the filler will use.

**P2 - feature completion**
5. **G1**: answer drafting grounded in profile + resume with provenance, routed
   through `answer_safety`; replace the assistant placeholder.
6. **C2**: DOCX support (parse to text, keep PDF for the actual upload).

**P3 - product completeness**
7. **J2/J3**: notification settings, account deletion and data export with an
   explicit confirmation flow.
8. **E5**: recommendation history once E3 exists.

**P4 - validation**
9. Full journey test with seeded fixtures (login -> profile -> Gmail sync ->
   match -> application -> browser -> form analysis -> human review -> manual
   submit confirmation), plus a visual pass for I3.

Recommended next phase: **2I.1 - "job workspace" (P1 items 2-4)**. It is the
largest remaining usability gap, it reuses endpoints that already exist, and it
is the smallest change that makes the recommendation -> application path feel
like one product.

## 6. What this audit could not prove

No live browser or real Google/Neon credentials were used, so behavior is
proven at the route/contract/service layer and by the existing suites, not by a
manual click-through against live Google or Gmail. That remains part of Stage 6.
