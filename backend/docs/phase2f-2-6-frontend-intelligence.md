---
noteId: "phase2f26frontendintelligence00000001"
tags: []
---

# Phase 2F.2.6 - Frontend Intelligence Dashboard & Resume Recommendation UI

## 1. Scope

Phase 2F.2.6 exposes the verified Phase 2F backend intelligence through the
existing Next.js application. It is frontend-only: no backend endpoint, scoring
rule, schema or migration was changed, and every number shown comes from the
API.

Implemented in this phase:

* job intelligence panel with analyze/refresh, provenance and explicit
  "not stated" reporting;
* resume intelligence panel with ready, missing, stale, failed and OCR states;
* multi-resume comparison panel with ranking, dimension scores, coverage,
  strengths, gaps, evidence and partial-failure reporting;
* recommendation summary with confidence, reasons, gaps, alternatives and
  close-score uncertainty;
* application resume panel showing the recommendation without ever replacing a
  manual selection, with explicit selection through the existing PATCH route;
* 30 focused Vitest tests and this document.

Not in this phase: Phase 2F.2.7 end-to-end integration testing.

## 2. Files

| File | Purpose |
| --- | --- |
| `frontend/src/types/api.ts` | Added job intelligence, resume intelligence and comparison types mirroring the Pydantic schemas |
| `frontend/src/features/jobs/api.ts` | Intelligence read/analyze, compare, recommendation clients and query keys |
| `frontend/src/features/jobs/hooks.ts` | Query/mutation hooks with derived-cache invalidation |
| `frontend/src/features/resumes/api.ts` | Resume intelligence read/analyze clients |
| `frontend/src/features/resumes/hooks.ts` | Resume intelligence hooks |
| `frontend/src/components/intelligence/confidence-badge.tsx` | Compact confidence indicator |
| `frontend/src/components/intelligence/score-meter.tsx` | Reusable score meter and formatting |
| `frontend/src/components/intelligence/evidence-list.tsx` | Explainable evidence rendering |
| `frontend/src/components/intelligence/job-intelligence-panel.tsx` | Job understanding UI |
| `frontend/src/components/intelligence/resume-intelligence-panel.tsx` | Resume understanding UI |
| `frontend/src/components/intelligence/resume-comparison-panel.tsx` | Comparison, recommendation, alternatives and failures |
| `frontend/src/components/applications/application-resume-panel.tsx` | Selected resume + recommendation + explicit selection |
| `frontend/src/components/jobs/job-card.tsx` | Mounts the job intelligence and comparison panels |
| `frontend/src/components/resumes/resume-card.tsx` | Mounts the resume intelligence panel |
| `frontend/src/components/applications/application-workspace.tsx` | Uses the extracted application resume panel |
| `frontend/src/components/jobs/match-panel.tsx` | Removed: superseded by the comparison panel (same backend ranking, richer data) |
| `frontend/tests/fixtures/intelligence.ts` | Synthetic API fixtures |
| `frontend/tests/*intelligence*.test.tsx`, `resume-comparison-panel.test.tsx`, `application-resume-panel.test.tsx` | New tests |

## 3. API integration

All calls go through the existing authenticated BFF proxy (`apiFetch` →
`/api/backend/...`). The allow-list already contained the four routes from
Phase 2F.2.5, so no proxy policy was changed.

| UI action | Request |
| --- | --- |
| Open a job card | `GET /jobs/{id}/intelligence` and `GET /jobs/{id}/recommendation` |
| Analyze and save / refresh a posting | `POST /jobs/{id}/analyze` (`force` on refresh) |
| Compare resumes / recompute | `POST /jobs/{id}/compare-resumes` (`force` on recompute) |
| Open a resume card | `GET /resumes/{id}/intelligence` |
| Analyze / save / refresh / retry a resume | `POST /resumes/{id}/analyze` (`force` per state) |
| Change the application resume | `PATCH /applications/{id}/resume` (existing route) |

Opening a job or resume only reads. Expensive persisted analysis and comparison
run only when the user presses a button. Resume and job analysis invalidate the
stored match/comparison caches in React Query, so downstream panels refetch
rather than showing stale evidence.

## 4. UI behaviour

### Job intelligence

Shows title/employer, required and preferred skills, certifications requested,
experience range and level, education requirement, employment type, location,
work mode and salary when stated. Missing fields are listed explicitly under
"Not stated in the posting" so absence is never presented as a negative
requirement. Contradictions and extraction warnings are surfaced verbatim.
Provenance and per-field confidence are available on demand.

### Resume intelligence

Shows summary, dated experience total, skills grouped by category, roles,
projects, education and certifications with the evidence snippet and confidence
for each fact. The analysis state is always visible:

| State | Presentation |
| --- | --- |
| `ready` | "Analysis ready"; refresh re-runs with `force=true` |
| `stale` | "Analysis out of date"; saving recomputes and stores |
| `missing` | "Not analyzed yet"; the response-only reading is labelled "not saved" |
| `failed` | Safe failure message plus the reason code, no extracted content |
| loading | Skeleton blocks, actions disabled |

A scanned PDF (`ocr_required`) never reaches the analysis panel: it shows an
"OCR required before analysis" warning and states that nothing was inferred.
Pending/failed parses get their own explanation instead of an empty success.

### Comparison and recommendation

The recommendation panel shows the recommended filename, overall compatibility,
confidence, reasons, important gaps, up to four supporting evidence items and up
to three alternatives with their point difference and distinguishing factors.
A disclaimer states scores are not hiring probabilities. Close calls
(`close_call`/`close_score`) render a "Scores are close" warning and describe the
result as a preference, never a clear winner. `no_suitable_resume` shows the
backend message without inventing a recommendation; `not_computed` asks for a
comparison; `no_resumes` links to the resume manager.

The ranking list preserves backend order (sorted by returned `rank` only) and
shows required-skill coverage, skills, experience, projects, education and title
scores. `null` renders as "Not evaluated". Each entry shows its analysis state
(`ready`, `partial`, `analysis_required`, `extraction_failed`, `invalid`,
`inaccessible`) with the backend reason, matched/missing required skills,
strengths derived from returned evidence, gaps and evidence. Resumes that could
not be compared appear in a separate failures list.

## 5. Application integration

The application workspace's resume card now:

1. shows the currently selected resume and why it was chosen;
2. reads the stored recommendation for the application's job;
3. explains the recommendation when it differs from the selection;
4. offers "Use recommended resume" as an explicit button;
5. keeps the existing dropdown for choosing any owned resume;
6. persists either choice through `PATCH /applications/{id}/resume`;
7. refreshes the application after a change (existing hook invalidation);
8. warns when the selected resume needs analysis and links to the resume manager.

A resume the user chose manually (backend reason marker `Resume selected
manually by you for this application.`) is labelled "Selected by you" and is
never replaced on load or refresh. Nothing in this flow approves or submits an
application; the approve/submit controls remain separate and explicit.

## 6. Loading, error and empty states

* Skeletons for job/resume intelligence and the recommendation.
* Safe error copy from `describeError` with a Retry action; no internals.
* Empty states for no comparison, no resumes and no suitable resume.
* All actions disable while a request is in flight.
* `null`/missing values are rendered as unknown, never as zero.

## 7. Security and safety

* Requests use the same-origin BFF; the JWT stays in the HttpOnly cookie and is
  never read by browser code.
* No allow-list entry was added, removed or widened.
* The UI renders only fields the backend returns; no storage keys, paths, raw
  PDF bytes or `parsed_data` are requested or displayed.
* Resume recommendations require an explicit click and a manual selection is
  preserved unless the user chooses otherwise.
* No automatic approval, submission, form filling or browser action was added.
* No Gmail or Glassdoor scraping behaviour was touched.

## 8. Tests

`frontend/tests/job-intelligence-panel.test.tsx` (7): field rendering, missing
information, provenance, analyze-and-save, no-skill state, warnings,
unavailable API, loading skeleton.

`frontend/tests/resume-intelligence-panel.test.tsx` (8): ready sections and
evidence, refresh with force, unsaved reading + explicit save, stale, failed,
OCR, loading, unavailable API.

`frontend/tests/resume-comparison-panel.test.tsx` (7): ranking/recommendation/
failures, entry switching, close-score uncertainty, no suitable resume,
not-computed → compare, unavailable API, recompute error.

`frontend/tests/application-resume-panel.test.tsx` (8): recommendation without
automatic change, manual selection preserved, current-is-recommended, comparison
on demand, no suitable resume, analysis-required notice, unavailable
recommendation, non-editable state. Every case asserts no approve/submit call.

## 9. Limitations

* The comparison panel is read-only apart from comparison/recompute; it does not
  create applications.
* Evidence snippets are shown in full and can be long for detailed resumes.
* Close-call wording depends on the backend explanation; the UI only falls back
  when the backend omits it.
* Per-entry ranking ignores dimension weights (the backend does not expose a
  weighted re-score, by design).
* No dedicated job-detail page was introduced; intelligence lives inside the
  existing expandable job card to avoid duplicating navigation.
