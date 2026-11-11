---
noteId: "phase2f24resumecomparison000000001"
tags: []
---

# Phase 2F.2.4 - Multi-Resume Comparison and Intelligent Recommendation

## 1. Scope

Phase 2F.2.4 compares every eligible resume the authenticated user owns against
one owned job and recommends the best one. It reuses the Phase 2F.2.3 evidence
engine and the existing `JobResumeMatch` cache; no new scoring algorithm, task
table or migration was added.

Implemented in this phase:

* `ResumeComparisonService` - resolves owned resumes, classifies each into an
  explicit availability state, reuses persisted matches, ranks them
  deterministically and reports partial results;
* `ResumeRecommendationService` - recommendation confidence, reasons, evidence,
  gaps, alternatives and close-score handling;
* two endpoints on the existing jobs router, with Pydantic-validated responses;
* 25 focused tests (service, ranking, cache, ownership and HTTP).

Not in this phase: further API expansion (2F.2.5), frontend (2F.2.6) and full
integration (2F.2.7).

## 2. Architecture

| Component | File | Responsibility |
| --- | --- | --- |
| Comparison workflow | `app/services/resume_comparison_service.py` | Ownership, resume states, ranking, partial results |
| Recommendation | `app/services/resume_recommendation_service.py` | Best resume, confidence, close calls, no-suitable handling |
| Response schema | `app/schemas/resume_comparison.py` | Pydantic response models |
| Serializer | `app/api/v1/serializers.py` | `resume_comparison_response` |
| Endpoints | `app/api/v1/endpoints/jobs.py` | `POST /jobs/{id}/compare-resumes`, `GET /jobs/{id}/recommendation` |
| Tests | `backend/tests/test_resume_comparison.py` | Synthetic-data coverage |

The comparison does not score anything itself. It calls
`MatchingService.ensure_job_matches`, which already selects owned resumes,
reuses or recomputes cached `JobResumeMatch` rows through the fingerprint cache
and calls `AdvancedMatchingService` / the 2F.2.3 engine. The comparison then
projects those rows onto the posting's required-skill coverage, ranks them and
delegates the recommendation.

## 3. Workflow

1. Resolve the job through `JobService.get_job`; another user's job is a 404.
2. Load all resumes where `resume.user_id == user.id` (resume deletion is a hard
   delete, so deleted resumes are absent).
3. Resolve the effective job requirements through `AdvancedMatchingService`
   (job intelligence fills gaps in the deterministic rules without overriding
   them).
4. Reuse or recompute persisted matches with `ensure_job_matches`.
5. Classify each resume (`READY`, `PARTIAL`, `ANALYSIS_REQUIRED`,
   `EXTRACTION_FAILED`, `INVALID`, `INACCESSIBLE`) with a human-readable reason.
6. Rank the comparable entries, call the recommendation service, and return
   ranked results plus explicit processing failures.

If the matching call fails unexpectedly, previously stored matches are used and
resumes without one are reported as `INACCESSIBLE`, so one request never loses
the whole comparison. A resume with failed extraction or failed intelligence is
reported and excluded from ranking, never raised.

## 4. Resume states

| State | Meaning | Ranked |
| --- | --- | --- |
| `READY` | Resume and job intelligence are current; the match state is `ready` | yes |
| `PARTIAL` | One input is stale/failed; the engine fell back to parsed data | yes |
| `ANALYSIS_REQUIRED` | No resume intelligence yet; parsed resume data was used | yes |
| `EXTRACTION_FAILED` | Resume intelligence failed and no usable content remains | no |
| `INVALID` | Stored resume content is unusable (e.g. parsing failed/OCR required) | no |
| `INACCESSIBLE` | No stored compatibility result could be linked to the resume | no |

Every non-ranked resume carries a reason and appears in `processing_failures`.
Uppercase states are the comparison API's states; the engine's lowercase
`processing_state` is returned separately as `match_processing_state` when a
match exists.

## 5. Ranking and tie-breaking

Ranking is computed per request from the stored results, in this order:

1. overall compatibility score (descending);
2. required-skill coverage (descending);
3. experience score (descending);
4. project score (descending);
5. education score (descending);
6. evidence confidence (`high` > `medium` > `low`);
7. resume creation date (oldest first);
8. resume id (lexicographic).

`None` ranks below any present value, and keys never depend on request order, so
repeated comparisons return the same order. The compare service does not rewrite
the persisted `rank`/`is_recommended` columns (those remain the simpler
`MatchingService` ordering); the recommended resume in the response is the one
chosen by the recommendation service.

Required-skill coverage is a projection of the stored match: matched required
skills divided by the effective posting requirements. It is not a second scoring
pass.

## 6. Recommendation behaviour

`ResumeRecommendationService.recommend` considers only entries that are ranked,
have a positive overall score and whose match state is not
`insufficient_evidence` / `resume_extraction_failed` (the engine's explicit "no
usable resume evidence" states).

The result contains:

* recommended resume id and display filename;
* overall score and confidence;
* reasons (highest score, required-skill coverage, evidence confidence, partial
  input warning, up to two stored strengths);
* supporting evidence from the stored match (safely projected, never paths or
  file content);
* important gaps (per-dimension gaps plus missing required skills);
* up to three alternatives with their score difference and distinguishing
  factors.

Confidence is derived from evidence, not the score: it can never be `high` on a
close call, and stays `low` when the match's evidence confidence is low.

## 7. Close scores

The close-call threshold is an absolute overall-score difference of **3.0
percentage points**, configurable via
`ResumeRecommendationService(close_score_threshold=...)`. When the top two
qualifying resumes are within it:

* `close_score` is true and the recommendation carries `close_call` plus
  `close_call_explanation`;
* the first resume is described as a preference, not a clear winner;
* the explanation names the dimensions that differ (required-skill coverage,
  experience, project, education, title and/or evidence confidence);
* confidence drops to `medium` or `low`.

## 8. No suitable resume

If no resume has usable evidence, no recommendation is returned. The response
status is `no_suitable_resume`, all resumes are still listed with their states,
and the message explains why and suggests reviewing/updating resumes and running
resume analysis. No score or recommendation is invented.

## 9. Caching and idempotency

Comparison results are derived, not stored: there is no new table and no
comparison cache to invalidate. Each response is computed from the persisted
`JobResumeMatch` rows, whose existing fingerprints already invalidate when:

* the posting text changes;
* job intelligence changes (version or fingerprint);
* resume content/checksum or parse state changes;
* resume intelligence changes;
* the matching algorithm version changes.

Repeated requests therefore return the same ordering and the same scores and
never create duplicate `JobResumeMatch` rows (`(job_id, resume_id)` is unique and
the existing update path is reused). `force=true` forwards to the existing
recompute path only; it does not create new rows.

## 10. API

`POST /api/v1/jobs/{job_id}/compare-resumes`

```json
{ "force": false }
```

`GET /api/v1/jobs/{job_id}/recommendation`

The read variant never recomputes; with no stored matches it returns status
`not_computed` and a message asking for the compare request.

Both return the same `ResumeComparisonResponse`: job id, status, total resumes,
compared resumes, ranked entries, recommendation, close-score flag and
threshold, processing failures, algorithm version, generated timestamp,
disclaimer and message.

Status values: `completed`, `partial`, `no_suitable_resume`, `no_resumes`,
`not_computed`.

## 11. Database

No migration. The phase only reads existing tables (`resumes`, `jobs`,
`job_intelligence`, `resume_intelligence`, `job_resume_matches`).

## 12. Security

* Every job and resume lookup is filtered by the authenticated user; another
  user's job is a 404 and another user's resumes never enter a comparison.
* Resume display filenames are returned (already public in the resume/match
  APIs); storage keys, storage paths, raw PDF content and `parsed_data` are
  never returned or logged.
* Resume files are only read to build the existing match cache; this phase never
  modifies an uploaded resume and never submits an application.

## 13. Limitations

* `ANALYSIS_REQUIRED` resumes are ranked using the parsed-data fallback; their
  confidence is lower and the reason says so.
* The comparison is not persisted, so a recommendation read only reflects the
  matches that have already been computed.
* When `ensure_job_matches` fails unexpectedly the fallback is coarse: resumes
  missing a stored row become `INACCESSIBLE` rather than being retried
  individually.
* Per-resume ranking ignores dimension weights beyond the six documented keys;
  weighted re-scoring is deliberately not introduced here.
