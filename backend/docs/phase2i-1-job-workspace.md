---
noteId: "4e211ce0df1b11f1ab35dd3cff4bbf9e"
tags: []

---

# Phase 2I.1 - Job Workspace & Application Pipeline

Scope: finish the P1 items from `phase2i-application-audit.md` - a searchable
job library, the `/jobs/[id]` workspace, saved/dismissed recommendation state,
and a dashboard pipeline aggregated from stored application rows.

No deployment, DNS, Nginx, systemd or infrastructure work was performed.
No automated final submission was added. CAPTCHA, OTP, MFA, passwords, legal
declarations and the final Submit/Apply button remain manual, and resume
selection plus approval remain explicit user actions.

---

## 1. What was already complete (verified, not rebuilt)

The previous session left the backend implemented. It was verified against the
running FastAPI application before any frontend work:

| Claim | Verification | Result |
| --- | --- | --- |
| Job search/filter/sort/pagination | `GET /api/v1/jobs` in `app.routes`, `JobService` query builder | Confirmed |
| Saved/dismissed state | `job_states` model, migration, service methods | Confirmed |
| Application pipeline aggregation | `GET /api/v1/applications/pipeline`, grouped status/stage queries | Confirmed |
| 14 new backend tests | `pytest tests/test_phase2i_1_job_workspace.py -q` | **14 passed** |

The execution log shown in the hand-off did not list the state routes, so they
were checked explicitly by iterating `app.routes` on the imported application
(141 route methods total). All were registered:

| Method | Route | Registered |
| --- | --- | --- |
| `GET` | `/api/v1/jobs` | yes |
| `GET` | `/api/v1/jobs/saved` | yes (declared before `/{job_id}`) |
| `GET` | `/api/v1/jobs/{job_id}` | yes |
| `POST` | `/api/v1/jobs/{job_id}/save` | yes |
| `DELETE` | `/api/v1/jobs/{job_id}/save` | yes |
| `POST` | `/api/v1/jobs/{job_id}/dismiss` | yes |
| `POST` | `/api/v1/jobs/{job_id}/restore` | yes |
| `GET` | `/api/v1/applications/pipeline` | yes |

No endpoint was reimplemented and no router registration needed changing.

## 2. Migration

`alembic/versions/a7c4e18f5b93_phase2i_1_job_states.py`

* revision `a7c4e18f5b93`, parent `c3f9a72d1e05` (Phase 2H redirects).
* `alembic heads` -> **single head `a7c4e18f5b93`**.
* Offline `alembic upgrade head --sql` completed and emitted the full chain,
  ending with:
  * `CREATE TABLE job_states`
  * `CONSTRAINT uq_job_states_user_id_job_id UNIQUE (user_id, job_id)`
  * foreign keys to `users.id` and `jobs.id`, both `ON DELETE CASCADE`
  * indexes `ix_job_states_user_id`, `ix_job_states_job_id`,
    `ix_job_states_user_id_is_saved`

The unique constraint is what makes save/dismiss idempotent: repeated calls
update one row, and the API tests assert `count(*) = 1` after repeated saves,
dismisses and restores.

A live `alembic current` could not run here: the configured Neon host is not
resolvable from this environment (`socket.gaierror`). The offline upgrade is the
migration check that was possible, and the backend suite runs against SQLite.

## 3. Application lifecycle used (no new lifecycle invented)

Statuses (`ApplicationStatus`, stored in `applications.status`):

`draft`, `analyzing`, `awaiting_user_information`, `ready_for_review`,
`awaiting_approval`, `submitting`, `submitted`, `failed`, `needs_user_action`,
`cancelled`.

Stages (`ApplicationStage`, stored in `applications.stage`):

`applied`, `screening`, `interview`, `offer`, `rejected`, `withdrawn`.

`GET /applications/pipeline` returns `total`, `in_progress` (the pre-submission
statuses), `submitted`, `failed`, `cancelled`, `by_stage` for all six stages and
`recent` (5 most recently updated). The dashboard renders those numbers; it does
not recompute or own a lifecycle.

## 4. Job deduplication

Unchanged. `app/models/job.py` and `app/services/job_discovery.py` were not
modified (confirmed with `git status`); dedupe remains
`UniqueConstraint(user_id, dedupe_key)` on `jobs`, with existing postings
updated rather than duplicated on rescans. Saved/dismissed decisions live in the
separate `job_states` table keyed by `(user_id, job_id)`, so a recommendation
decision can never alter a posting's identity or another user's view.

## 5. Frontend work completed

### API clients and hooks

* `fetchJobs(query)` now serialises `limit`, `offset`, `q`, `company`,
  `location`, `source`, `has_matches`, `state`, `sort` (empty values dropped).
* `fetchSavedJobs`, `fetchJob`, `setJobSaved` (POST/DELETE), `dismissJob`,
  `restoreJob`.
* `useJobs(query)`, `useSavedJobs`, `useJob`, and state mutations
  `useSaveJob` / `useUnsaveJob` / `useDismissJob` / `useRestoreJob`.
  Successful state changes write the returned job into the detail cache and
  invalidate every `jobs` query, so list membership and badges update.
* `fetchApplicationPipeline` + `useApplicationPipeline`, with
  `applicationKeys.pipeline`; existing application mutations already invalidate
  the whole `applications` prefix, which includes the pipeline.

### BFF proxy

The allowlist (`src/lib/constants.ts`) was already extended by the previous
session for `GET jobs` filters, `GET jobs/saved`, the four state routes,
`GET jobs/{id}` and `GET applications/pipeline`. The proxy remains
deny-by-default (unlisted paths still 404) and the contract test now also pins
the forwarded job-listing query keys.

### Job listing UI (`/jobs`)

`src/components/jobs/job-library.tsx` provides:

* free-text search (submitted, not per keystroke),
* company and location filters,
* source filter (Glassdoor - the only source stored today),
* match filter (with/without resume matches),
* sorting (received, title, company, recently saved),
* Active / Saved / Dismissed / All state tabs,
* server pagination with backend `total`,
* loading skeleton, empty states per tab, retryable error state.

`JobCard` now links its title to `/jobs/[id]` and adds Save/Unsave and
Dismiss/Restore actions with state badges and inline errors. The Glassdoor
discovery panel keeps the scan controls on `/jobs` and hands the listing to the
library (`showJobList={false}`); `/gmail` still renders its original full view.

### Job Workspace (`/jobs/[id]`)

`src/components/jobs/job-workspace.tsx` plus the route page shows:

* title, company, location, source, received/stored dates, original posting link,
* save/dismiss/restore,
* job description exactly as stored,
* requirements at a glance (required/preferred skills, experience, education,
  responsibilities, confidence),
* extracted job intelligence and provenance (existing `JobIntelligencePanel`),
* resume comparison, recommended resume, alternatives, evidence and gaps
  (existing `ResumeComparisonPanel`),
* the current application status/stage with an "Open application" link, or a
  "Start application" action that calls `POST applications/analyze` and opens
  the application workspace.

No new backend service was created; every panel reads existing endpoints.

### Dashboard pipeline

`src/components/dashboard/application-pipeline.tsx` renders total, in progress,
submitted, failed, cancelled, all six stage counts (including zeros), and the
five most recent applications as links into their workflows. It has loading,
empty and retryable error states and shows no counts when the pipeline is empty.

## 6. Files

**Backend (previous session, verified - not modified in this one)**
* `app/api/v1/endpoints/jobs.py`, `app/api/v1/endpoints/applications.py`
* `app/services/job_service.py`, `app/services/application_service.py`
* `app/models/job_state.py`, `app/models/__init__.py`, `app/api/v1/serializers.py`
* `app/schemas/matching.py`, `app/schemas/application.py`
* `alembic/versions/a7c4e18f5b93_phase2i_1_job_states.py`
* `tests/test_phase2i_1_job_workspace.py`

**Backend (new in this session)**
* `docs/phase2i-1-job-workspace.md`

**Frontend (new in this session)**
* `src/components/jobs/job-library.tsx`
* `src/components/jobs/job-workspace.tsx`
* `src/app/(app)/jobs/[id]/page.tsx`
* `src/components/dashboard/application-pipeline.tsx`
* `tests/job-library.test.tsx`, `tests/job-workspace.test.tsx`,
  `tests/application-pipeline.test.tsx`
* `e2e/job-workspace.spec.ts`

**Frontend (modified in this session)**
* `src/features/jobs/api.ts`, `src/features/jobs/hooks.ts`
* `src/features/applications/api.ts`, `src/features/applications/hooks.ts`
* `src/components/jobs/job-card.tsx`
* `src/components/gmail/glassdoor-discovery.tsx`
* `src/components/applications/application-list.tsx` (exported the existing
  status/stage labels for reuse)
* `src/app/(app)/jobs/page.tsx`, `src/app/(app)/dashboard/page.tsx`
* `tests/proxy-contract.test.ts` (job filter query-key coverage)
* `e2e/support.ts` (job fixture gained the required `user_state`)

## 7. Verified results

| Check | Command | Result |
| --- | --- | --- |
| Phase 2I.1 backend | `pytest tests/test_phase2i_1_job_workspace.py -q` | **14 passed** |
| Backend regression | `pytest -m "not live and not browser" -p no:warnings` | **688 passed, 5 skipped, 1 deselected**, exit 0 (304.52s) |
| Route registration | import `app.main`, enumerate `app.routes` | all 8 routes present, 141 route methods |
| Migration heads | `alembic heads` | one head: `a7c4e18f5b93` |
| Migration chain | `alembic upgrade head --sql` | exit 0, `job_states` DDL emitted |
| Frontend unit/component | `vitest run` | **190 passed across 17 files** |
| New frontend tests | job library 6, workspace 5, pipeline 3, proxy 1 | **15 passed** |
| TypeScript | `tsc --noEmit` | clean, exit 0 |
| ESLint | `eslint .` | clean, exit 0 |
| Next.js build | `next build` | success; `/jobs` and `/jobs/[id]` built |
| Playwright | `playwright test` | **19 passed** (17 existing + 2 new) |

The live Groq test (`tests/test_job_analysis_agent.py::test_live_groq_job_description_analysis`)
fails without network access (`APIConnectionError`) and is excluded by the
repository's documented `-m "not live and not browser"` command. One earlier
full Vitest run showed 5 timing failures while the backend suite was running
concurrently; the clean rerun after it finished passed 190/190.

## 8. Journey coverage

Job listing -> search/filter -> open job -> analyze -> compare resumes -> read
recommendation -> save -> start application -> application workflow -> dashboard
pipeline is exercised at three levels:

1. **Backend API tests** prove listing filters, state idempotency, ownership,
   pipeline counts and generated SQL.
2. **Vitest component tests** drive the real components against a mocked BFF,
   asserting the actual query strings and state endpoints.
3. **Playwright** drives a production build in Chromium: filters `/jobs`, opens
   `/jobs/{id}`, sees intelligence + recommendation, saves, starts an
   application and lands on `/applications/{id}`; a second test reads the
   dashboard pipeline and opens a recent application.

The BFF proxy contract test independently proves every new `(method, path)` is
allowlisted.

## 9. Remaining limitations

* No live Google/Gmail/Neon credentials were used. Playwright fulfils client
  calls at the browser boundary and backend tests use in-process SQLite, so this
  is not a run against a real authenticated account or PostgreSQL.
* `alembic current` / `alembic upgrade` against the configured Neon database
  could not run (DNS failure); only the offline SQL chain was verified.
* The job library shows no facet counts (companies, locations, sources); the
  backend does not return aggregates and none were invented in the UI.
* The source filter lists only Glassdoor, the only source stored today.
* Save/dismiss are not optimistic: the list refetches after the state response.
* Recommendation history (audit item E5) is still not implemented; the current
  saved/dismissed flags are the latest decision, not an event log.
* Application list filtering/pagination (audit item F3) is unchanged; the
  dashboard's recent applications are the five most recently updated.
* `frontend/test-results/` is Playwright output from local runs and remains
  untracked.
