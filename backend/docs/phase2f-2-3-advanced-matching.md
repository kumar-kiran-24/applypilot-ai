---
noteId: "phase2f23advancedmatching000000001"
tags: []
---

# Phase 2F.2.3 - Advanced Evidence-Based Job-Resume Matching

## 1. Scope

Phase 2F.2.3 upgrades the existing deterministic matcher to consume the Phase
2F structured intelligence, without replacing the matching architecture, adding
public endpoints or changing the documented weights.

Implemented in this phase:

* scoring from current `JobIntelligence` and `ResumeIntelligence` when both
  exist and are fresh, with a labelled fallback to rule-based requirements and
  stored parsed resume data otherwise;
* skill matching by normalized key and technology aliases, with the resume
  reference, confidence and match reason recorded for each match;
* five explainable category scores, an evidence list, missing evidence, gaps,
  strengths and a processing state;
* cache invalidation on posting change, job-intelligence change, resume content
  change, resume-intelligence version/content change and algorithm version
  change, with no schema migration;
* backward-compatible response fields on the existing match endpoints.

Not in this phase: multi-resume comparison and recommendations (2F.2.4), new
API endpoints (2F.2.5), frontend (2F.2.6) and full integration (2F.2.7).

## 2. Architecture

| Component | File | Responsibility |
| --- | --- | --- |
| Evidence engine | `app/services/advanced_matching_engine.py` | Alias normalization, category scoring, evidence, states (pure, deterministic) |
| Intelligence adapter | `app/services/advanced_matching_service.py` | Resolve owned job/resume intelligence, freshness, effective requirements |
| Persistence/caching | `app/services/matching_service.py` | Existing rows, ranking, fingerprint cache, ownership |
| Response schema | `app/schemas/matching.py`, `app/api/v1/serializers.py` | Backward-compatible evidence fields |
| Tests | `backend/tests/test_advanced_matching.py` | 30 focused engine and service tests |

`MatchingService` keeps its role: it selects owned resumes, decides whether a
cached row is reusable, calls the engine and ranks the rows. The engine is a
pure function of requirements, resume profile and intelligence payloads.

## 3. Scoring weights

The established weights are unchanged and are asserted in tests:

| Dimension | Weight | Evidence used |
| --- | --- | --- |
| Skills (required + preferred bonus) | 0.40 | Resume skill evidence, confidence-weighted |
| Experience | 0.25 | Dated roles, role relevance, internships flagged |
| Title alignment | 0.15 | Designations and summary language, project names at half credit |
| Project relevance | 0.10 | Project technologies and demonstrated skills |
| Education | 0.10 | Degree level, then field/specialization |

A dimension the posting states nothing about is not evaluated; its weight is
redistributed proportionally, as before.

**Preferred skills.** Preferred skills keep their existing 15% share *inside*
the skills dimension (85% required coverage + 15% preferred coverage when a
preference is stated). This preserves the published weights and does not
inflate the overall score for a candidate who matches every requirement.

**Certifications.** Certifications do not change any category score. They are
reported as supporting evidence when a posting certification appears on the
resume, and as missing evidence when it does not. Rationale: the weights are
fixed at 40/25/15/10/10 and there is no established certification dimension;
awarding points inside another dimension would silently change its meaning.

**No double counting.** A skill is credited once in the skills dimension; if it
appears in several resume sections the strongest reference wins (skills section
> role > project). The project dimension counts project evidence only, so a
skill listed in a project is not a second credit in the skills dimension.

## 4. Evidence model

Every result carries:

* `evidence[]` with `category`, `description`, `resume_reference`,
  `confidence` and `match_reason`;
* `missing_evidence[]` — facts the resume does not state, phrased as unknown
  rather than as a negative finding;
* `gaps[]` — a compact per-dimension coverage summary;
* `processing_state` and `details.inputs` describing which inputs were used;
* `algorithm_version` (`MATCHING_ALGORITHM_VERSION`).

Evidence references are human-readable resume sections (`skills section`,
`experience: Backend Engineer`, `project: Payments API`). Storage keys, file
paths and raw document bytes never enter the result.

## 5. Confidence and processing states

Skill credit is 1.0 for explicitly listed skills with high confidence, 0.9 for
explicit medium-confidence evidence, 0.8 for skills evidenced by a role, 0.75
for project evidence and 0.7 for implicit mentions.

The result confidence is `low` when there is no usable resume evidence, when
the posting has no description, or when the posting's own confidence is low;
`high` only when both intelligence sources are current, high-confidence and
required coverage reaches 60%; otherwise `medium`.

Processing states:

| State | Meaning |
| --- | --- |
| `ready` | Both intelligence records are current and were used |
| `partial` | One or both records are missing/stale/supplemented by fallback |
| `insufficient_evidence` | The resume has no usable structured evidence |
| `resume_extraction_failed` | Resume extraction failed and nothing is usable |
| `job_description_incomplete` | No description and no usable job intelligence |

A score is still produced in `partial` states because it is a deterministic
overlap measure, but the state and `details.inputs` make the missing inputs
explicit. Scores are never presented as hiring probabilities.

## 6. Skills, experience and other dimensions

* Aliases (`JS`/JavaScript, `ML`/Machine Learning, `Node`/Node.js, `TS`,
  `GCP`, `k8s`, `postgres`, `springboot`, ...) map to one comparison key.
  Unrelated technologies are not equated.
* Experience months come from dated role facts; an undated resume receives at
  most 40% of the experience dimension and no invented year count. Projects are
  never treated as employment. Internships are included as dated experience and
  flagged in the evidence.
* Title alignment gives full credit to designation tokens and to the
  professional summary's role language (preserving the previous engine's
  behaviour), and half credit to project-name tokens, so a project alone cannot
  claim full title alignment.
* Missing education is reported as unknown, not as proof of disqualification.

## 7. Cache invalidation

`job_fingerprint` now includes the matching algorithm version, the requirement
extractor version, the posting text and (when present) the job-intelligence
extractor version and posting fingerprint. `resume_fingerprint` includes the
algorithm version, the stored resume checksum/parse state and the
resume-intelligence version and content fingerprint.

Consequently a cached match is reused only while all of the following hold:

* the posting text is unchanged;
* the job intelligence (version and fingerprint) is unchanged;
* the resume content (checksum and parsed data) is unchanged;
* the resume intelligence (version and fingerprint) is unchanged;
* the matching algorithm version is unchanged.

No migration was required: fingerprint columns already exist.

## 8. API compatibility

`POST /jobs/{id}/match`, `GET /jobs/{id}/matches`, `POST /matching/bulk` and
`GET /matching/summary` are unchanged. `ResumeMatchResponse` gains defaulted
fields (`evidence`, `missing_evidence`, `gaps`, `processing_state`,
`algorithm_version`), so older cached rows and clients keep working.

## 9. Limitations

* Intelligence is never generated inside matching; callers must run the Phase
  2F.1 / 2F.2 analysis for a `ready` state. Until then the deterministic
  fallback and `partial` state apply.
* No semantic LLM analysis is used; matching stays deterministic and offline.
* `details.inputs` reports staleness but not the age of the stale record.
* Multi-resume comparison, ranking explanations across resumes and the
  recommendation workflow remain for Phase 2F.2.4.
