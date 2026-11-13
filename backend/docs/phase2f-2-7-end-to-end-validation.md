---
noteId: "phase2f27endtoendvalidation00000001"
tags: []
---

# Phase 2F.2.7 - End-to-End Intelligence Workflow Validation

## 1. Scope and outcome

Phase 2F.2.7 validates the complete intelligence workflow with the real
services, HTTP API, database, and frontend components:

```
Job discovery -> Job intelligence -> Resume upload -> Resume intelligence
-> Advanced matching -> Multi-resume comparison -> Recommendation
-> Application preparation -> Explicit resume selection -> Application review
```

This phase adds **tests and documentation only**. No production endpoint,
scoring rule, schema, migration, frontend component or automation behaviour was
changed.

Result: the deterministic workflow is verified end to end. **No genuine
Phase 2F defect was found**, so no production fix was required. Two validation
suites were added:

| File | Purpose | Tests |
| --- | --- | --- |
| `backend/tests/test_phase2f_2_7_e2e.py` | Backend workflow, cache, failure and security integration | 6 |
| `frontend/tests/intelligence-workflow.test.tsx` | Cross-panel UI workflow and persisted selection | 2 |

## 2. Repository recovery

`git status --short` before any change showed the expected Phase 2F.2.6 work
uncommitted:

* modified: application workspace, job card, resume card, jobs/resumes API and
  hooks, frontend types and constants;
* deleted: `frontend/src/components/jobs/match-panel.tsx` (superseded by the
  comparison panel, per the 2F.2.6 document);
* untracked: Phase 2F.2.6 intelligence components, tests, fixtures, the 2F.2.6
  document and the `deploy/` tree.

Every item was preserved. Nothing was reset, cleaned, reverted or mass-formatted.
The Phase 2F documents were read:

* `backend/docs/phase2f-1-job-understanding.md` (the requested
  `phase2f-2-1-job-understanding.md` name does not exist);
* `backend/docs/phase2f-2-3-advanced-matching.md`;
* `backend/docs/phase2f-2-4-resume-comparison.md`;
* `backend/docs/phase2f-2-6-frontend-intelligence.md`.

There is no standalone 2F.2.5 document; its application-resume integration is
described by `tests/test_application_resume_integration.py` and the 2F.2.6
document.

## 3. Workflow architecture

| Layer | Component | Verified responsibility |
| --- | --- | --- |
| Discovery storage | `job_discovery.store_glassdoor_jobs` | Owned posting, stable dedupe key, original listing URL preserved |
| Job intelligence | `JobUnderstandingService`, `job_intelligence_extraction` | Structured fields, provenance, confidence, deterministic cache and force refresh |
| Resume upload | `ResumeService`, `ResumeStorage` | PDF validation, parsing, OCR state, ownership, no storage internals returned |
| Resume intelligence | `ResumeIntelligenceService` | Skills, roles, projects, education, certifications, provenance, stale/missing/failed states, previous-success preservation |
| Advanced matching | `MatchingService`, `AdvancedMatchingService`, `advanced_matching_engine` | Five explainable dimensions, required-skill coverage, evidence, missing evidence, fingerprint cache |
| Comparison | `ResumeComparisonService` | Ownership, per-resume states, deterministic ranking, partial failures |
| Recommendation | `ResumeRecommendationService` | Recommended resume, alternatives, reasons, gaps, confidence, close scores, no-suitable state |
| Application preparation | `ApplicationService` | Reuses the same recommendation, persists resume/reason/score, never approves or submits |
| Explicit selection | `PATCH /applications/{id}/resume` | Manual choice persisted, ownership checked, approval invalidated |
| UI | Job/resume intelligence panels, comparison panel, application resume panel | Reads only on open, explicit writes, uncertainty and failure states |

The comparison does not score anything itself: it projects stored
`JobResumeMatch` rows, which already invalidate on posting, job-intelligence,
resume-content, resume-intelligence and algorithm-version changes. No new
table, cache or migration exists.

## 4. Test scenarios

### Backend (`tests/test_phase2f_2_7_e2e.py`)

| Step | Scenario | Assertions |
| --- | --- | --- |
| A | Store a posting through the production discovery path | Title, company, source and the **original listing URL** survive; the posting is owned by the authenticated user |
| B | Analyze the job, read the cache, force refresh | Title, required/preferred skills, 5 years, bachelor/CS, responsibilities, work location, listing URL; deterministic provenance and field confidence; `cached=false` then `true`; force returns `cached=false`; `llm_status=disabled` |
| C | Upload strong, moderate and scanned PDFs | Text PDFs parse and expose skills; the scanned PDF is `ocr_required`; all three are listed; no `storage_key`, `storage_backend` or `checksum` |
| D | Analyze each resume | Summary, skills, dated experience, projects, education, certifications, sections, provenance, field confidence; cache reuse; the scanned resume fails safely with no extracted fields |
| E | Compare all resumes | 3 total, 2 compared, `partial`; deterministic ranks; 4 required skills; skill/experience/title/education scores; evidence; missing required skills for the weaker resume; scanned resume reported as `EXTRACTION_FAILED`; repeated call identical; one match row per resume, no duplicates |
| F | Read the recommendation | Recommended strong resume, score equal to the ranked entry, confidence, reasons, supporting evidence, gaps, alternatives with point difference, threshold 3, disclaimer |
| G | Prepare an application | Selected resume equals the recommendation; filename/reason/score persisted; `documents.resume` matches; status `awaiting_user_information`; no approval/submission; legal declaration still user-controlled |
| H | Select the alternative, reanalyze, approve, change again | Selection persists across GET and reanalysis; manual reason is kept; explicit approval works after required answers; changing the resume clears `approved_at`, leaves `awaiting_approval`, and never submits |
| - | Equal resumes | Identical resumes rank deterministically across calls; `close_score`/`close_call` true; explanation present; confidence never `high`; one alternative within 3 points |
| - | Empty/unsuitable sets | `no_resumes`; `not_computed` before a comparison; then `no_suitable_resume` with no recommendation and an explicit failure entry |
| - | Cache invalidation | Changed posting invalidates job intelligence and the job fingerprint; changed resume content marks analysis `stale`; resume fingerprint changes; no duplicate match rows |
| - | Failed extraction | A forced extraction failure keeps the previous successful reading, fields and the single completed row, with a warning |
| - | Security | Cross-user job/resume read, analyze, compare, download, attach and select are 404; no storage internals in responses; no resume text in logs |

### Frontend (`tests/intelligence-workflow.test.tsx`)

The real panels are mounted together over one shared synthetic backend:

1. Job intelligence opens with "Not saved yet", analyzes and saves.
2. Resume intelligence distinguishes "Not analyzed yet" from "Analysis ready"
   and saves on click.
3. A scanned resume shows "OCR required before analysis" and performs no read.
4. The comparison panel asks for a comparison, then renders the recommendation,
   ranking, alternative and failures.
5. Close-call uncertainty is shown as a preference, not a winner.
6. Disclaimer, confidence and gaps are rendered from the API.
7. The application panel starts on the recommendation without any PATCH.
8. Selecting the alternative through the dropdown is an explicit PATCH.
9. A fresh `QueryClient` (page refresh) reloads the persisted manual selection.
10. API failures show safe copy and retry actions, with no internals.

Every case asserts no approve or submit request is made.

## 5. Backend integration results

Command (from `backend/`):

```powershell
& $PY -m pytest -m "not live and not browser" -p no:warnings --tb=short
```

```
642 passed, 4 skipped, 1 deselected in 164.95s (0:02:44)
```

The 4 skips are the browser modules gated by
`APPLYPILOT_RUN_BROWSER_TESTS=1`; the deselection is the existing live Groq test.

Phase 2F suites alone (`--collect-only`):

| File | Tests |
| --- | --- |
| `test_job_intelligence.py` | 17 |
| `test_resume_intelligence.py` | 23 |
| `test_resume_intelligence_api.py` | 14 |
| `test_resume_intelligence_persistence.py` | 26 |
| `test_advanced_matching.py` | 30 |
| `test_resume_comparison.py` | 25 |
| `test_application_resume_integration.py` | 6 |
| `test_phase2f_2_7_e2e.py` | 6 |
| **Total** | **147** |

Lint:

```powershell
& $PY -m ruff check .
```

```
All checks passed!
```

No production source file was changed. Ruff initially flagged two list
concatenations in the new test only; both were rewritten as unpacked tuples.

## 6. Frontend integration results

Commands (from `frontend/`):

```powershell
npm test
npx tsc --noEmit
npx eslint .
npm run build
```

| Check | Result |
| --- | --- |
| `npm test` | **63 passed** across 12 files |
| `npx tsc --noEmit` | exit 0, no output |
| `npx eslint .` | exit 0, no output |
| `npm run build` | exit 0, 21 routes compiled |

Phase 2F UI tests:

| File | Tests |
| --- | --- |
| `job-intelligence-panel.test.tsx` | 7 |
| `resume-intelligence-panel.test.tsx` | 8 |
| `resume-comparison-panel.test.tsx` | 7 |
| `application-resume-panel.test.tsx` | 8 |
| `intelligence-workflow.test.tsx` | 2 |
| **Total** | **32** |

The remaining 31 frontend tests cover authentication, the BFF proxy, session
cookies, the browser workspace and employer URL handling; all pass.

## 7. Real-browser results

Backend Playwright/Chromium suite (Phase 2E browser automation, the only
real-browser suite in the repository):

```powershell
$env:APPLYPILOT_RUN_BROWSER_TESTS = "1"
& $PY -m pytest -m browser -p no:warnings --tb=short -q
```

```
5 passed
```

These tests drive a **controlled local employer form**; no real employer site,
account or submission is used.

Frontend real-browser testing was **not executed**: the frontend has no
`@playwright/test` dependency, no Playwright configuration and no browser test
files. Reporting a browser result for the Next.js UI would require adding that
infrastructure, which is outside this validation phase. The UI workflow is
covered by the jsdom integration test above.

## 8. Security and safety verification

| Requirement | Verification | Result |
| --- | --- | --- |
| Cross-user job access rejected | GET/analyze/compare/recommendation as another user | 404 `job_not_found` |
| Cross-user resume access rejected | GET/intelligence/analyze/download as another user | 404 `resume_not_found` |
| No unauthorized resume selection | Attach another user's resume during preparation and PATCH it onto an owned application | 404 `resume_not_found` |
| Storage paths/keys not exposed | Resume, intelligence, comparison and job responses scanned | No `storage_key`, `storage_backend`, `checksum`, storage directory or `users/` path |
| No PDF contents in logs | Unique marker and a resume line checked against captured INFO/WARNING logs | Absent |
| BFF cookie authentication | Existing `session-cookie.test.ts`, `proxy-resume-intelligence.test.ts`, `unauthenticated-access.test.ts` | Pass |
| API ownership enforced | Backend ownership tests and the e2e security test | Pass |
| No automatic approval | Preparation, comparison, selection and recommendation reads | `approved_at` stays null until explicit approval |
| No automatic final submission | Full workflow and UI tests | `submitted_at` null; no submit request; `can_submit=false` before approval |
| CAPTCHA/OTP/MFA and legal declarations user-controlled | Legal declaration is required and unconfirmed after preparation; Phase 2E human-loop tests | Pass |
| Employer form values not overwritten | Phase 2E field-fill/browser regression suite | Pass |
| Gmail read-only | Existing Gmail service, config and token tests | Pass |
| No direct Glassdoor scraping | Existing Glassdoor extraction tests; discovery reads alert emails only | Pass |

The workflow never automates the final employer submission. The user must
manually submit on the employer's website.

## 9. Bugs discovered and fixes applied

**No genuine Phase 2F defect was discovered.** No production code was changed
and no regression test for a fix was needed.

During authoring, three test expectations were corrected against documented
behaviour (not product defects):

* required-skill provenance is `deterministic` for prose/section extraction;
* a recommendation read with no resumes is `no_resumes`; `not_computed` applies
  when resumes exist but no match has been computed;
* comparison failure states are uppercase (`EXTRACTION_FAILED`) while the
  engine's `match_processing_state` is lowercase, as documented in 2F.2.4.

One behaviour worth noting (pre-existing, not fixed): matching persists one
`JobResumeMatch` row per owned resume, including an unreadable one, while the
comparison correctly excludes and reports it. This can make the job list's
`matches_count` include a resume that the comparison did not rank. Changing it
would alter existing matching/counting behaviour outside this validation scope,
so it is recorded as a limitation.

## 10. Known limitations

* **Gmail was not contacted.** The posting is stored through the real
  `store_glassdoor_jobs` path, so the persisted record is production-shaped, but
  the external Gmail/live discovery path was not executed in this phase.
* **The live Groq path was not executed.** Job analysis used
  `use_llm=false` for deterministic, reproducible results. The existing live
  agent test remains deselected without credentials.
* **Frontend real-browser validation not available** (no Playwright dependency
  or configuration in `frontend/`). Component integration is covered in jsdom.
* **API tests use in-memory SQLite**, as the existing suite does. Alembic is
  checked separately against PostgreSQL; no data-path PostgreSQL integration
  was added.
* **The workflow test drives components in a harness**, not Next.js routing or
  the full page shell. It mounts the same panels used by the job card, resume
  card and application workspace.
* **Unreadable resumes still receive a match row**, as described above; the
  comparison response is correct, but job summaries can overstate how many
  resumes were compared.
* **Comparison failure states are uppercase** by API design; clients must use
  `match_processing_state` for the engine's lowercase state.
* All resumes, jobs and answers are synthetic. No real PDF, candidate, employer
  account or application was used.

## 11. Exact commands

```powershell
# Backend
cd C:\Agentic_ai\applypilot-ai\backend
$PY = "C:\Users\kiran\miniconda3\envs\venv_rating\python.exe"

& $PY -m pytest -m "not live and not browser" -p no:warnings --tb=short
& $PY -m ruff check .
& $PY -m pytest tests/test_phase2f_2_7_e2e.py -p no:warnings --tb=short

$env:APPLYPILOT_RUN_BROWSER_TESTS = "1"
& $PY -m pytest -m browser -p no:warnings --tb=short -q

& $PY -m alembic current
& $PY -m alembic heads
& $PY -m alembic check

# Frontend
cd C:\Agentic_ai\applypilot-ai\frontend
npm test
npx tsc --noEmit
npx eslint .
npm run build
```

Alembic status:

```
current: d2f7a1c96b04 (head)
heads:   d2f7a1c96b04 (head)
check:   No new upgrade operations detected.
```

## 12. Environment

* Python `3.10.18` in the existing `venv_rating` Conda environment (no new
  environment created);
* FastAPI, SQLAlchemy, PostgreSQL, Alembic;
* Next.js 15.5.29, React 19.1.0, TypeScript 5.7.2;
* TanStack Query 5.62.11, Vitest 3.2.4;
* backend Playwright/Chromium available for the Phase 2E browser suite.

## 13. Final readiness assessment

Phase 2F.2.7 is **complete**.

* The full intelligence workflow is validated end to end against real services,
  endpoints and the database.
* Ranking is deterministic; caching, force refresh and invalidation behave as
  documented.
* Partial extraction failures are reported without failing the workflow.
* Manual resume selection is persisted and never silently replaced; approval is
  invalidated when the selection changes.
* Ownership, exposure and no-auto-submission guarantees hold.
* Backend regression (642 passed), Phase 2F suites (147), frontend (63), build,
  lint, typecheck, Alembic and the real-browser backend suite all pass.

**Phase 2F is ready to be considered complete** for the deterministic
intelligence workflow, with the limitations above. The live Gmail/Groq paths
and a frontend browser suite require external credentials or new test
infrastructure and are explicitly marked not executed rather than claimed.
