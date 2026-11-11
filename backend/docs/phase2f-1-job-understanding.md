---
noteId: "phase2f1jobunderstanding0000000000001"
tags: []
---

# Phase 2F.1 - Intelligent Job Understanding Engine

## 1. Scope

Phase 2F.1 turns a stored Glassdoor posting into structured, explainable job
information. It builds on the existing deterministic `job_requirements` rules
and the existing Groq job-description agent; it does not replace matching,
resume parsing, eligibility, applications or browser automation.

Implemented in this phase:

* deterministic extraction of the requested fields, with provenance and
  confidence per field;
* optional, validated LLM enrichment that may only fill what the rules left
  empty and only with claims verified against the posting text;
* contradiction and warning reporting (no guessing);
* per-job result caching keyed by posting fingerprint and extractor version;
* `POST /api/v1/jobs/{job_id}/analyze` and
  `GET /api/v1/jobs/{job_id}/intelligence`.

Not in this phase (later 2F sub-phases): resume intelligence, advanced matching,
candidate context, answer generation, readiness, orchestrator and dashboard.

## 2. Architecture

| Component | File | Responsibility |
| --- | --- | --- |
| Deterministic extraction | `app/services/job_intelligence_extraction.py` | Pure rules, provenance, contradictions, LLM claim verification and merge |
| LLM enrichment + cache | `app/services/job_understanding_service.py` | Groq agent invocation, merge, caching, failure fallback, ownership |
| Model | `app/models/job_intelligence.py` | One cached extraction per job |
| Schemas | `app/schemas/job_intelligence.py` | Validated field/payload/response models |
| Endpoints | `app/api/v1/endpoints/jobs.py` | `analyze` and `intelligence` routes |
| Agent | `app/agents/job_analysis.py` | Existing structured LLM agent, extended with `required_certifications` |

Shared helpers `normalized_description` and `description_sections` were added to
`app/services/job_requirements.py` so extraction reuses the same heading rules as
matching. The "other section" heading list now recognises certification,
education, skills and technology headings so those lines no longer leak into
`responsibilities`.

## 3. Deterministic extraction

Fields produced (all with a source and confidence in `extraction.provenance`
and `extraction.field_confidence`):

| Field | Evidence used |
| --- | --- |
| `job_title`, `company`, `description`, `application_url` | stored posting (description preserved verbatim) |
| `required_skills`, `preferred_skills` | explicit posting sections, else prose mentions (lower confidence) |
| `experience_years`, `experience_years_max` | literal "N years" / "N-M years" |
| `education_level`, `education_field`, `education_requirement` | degree level and field named in the posting |
| `required_qualifications` | requirement-section lines that are not pure skill lists |
| `responsibilities` | responsibility section, excluding metadata lines |
| `employment_type`, `work_mode` | explicit phrasing; negation ("not remote") is ignored |
| `work_location` | stored location, else a `Location:`/`Based in` line |
| `salary_range` | the first stated numeric compensation statement, verbatim window |
| `experience_level` | seniority named in the title, else role-context lines |
| `required_certifications` | certification/licence section plus known credentials |
| `application_url` / `application_url_is_listing` | a direct ATS/careers link in the posting wins; otherwise the stored listing URL is flagged as a listing |

Sources are `job_record`, `deterministic`, `llm` and `not_stated`. A value is
`None`/`[]` only when the posting does not state it.

### Contradictions and warnings

Reported, never silently resolved:

* remote and on-site both stated without hybrid (work mode left unclassified;
  an "occasional visits" phrasing keeps it remote);
* a skill listed as both required and preferred (kept as required);
* an entry-level title with a 5+ year requirement;
* multiple education levels or employment types stated;
* compensation mentioned without a numeric range, or several statements;
* missing description.

## 4. LLM enrichment

* Uses the existing `JobDescriptionAnalysisAgent` (Groq, JSON mode, Pydantic
  validation, one JSON repair attempt, retry/timeout handled by
  `GroqLLMClient`).
* Runs only when `use_llm` is true, the description has at least 50 characters
  and `GROQ_API_KEY` is configured.
* `merge_llm_understanding` fills only empty fields. Deterministic values are
  never overwritten. Disagreements on stated facts (work mode, employment type,
  seniority, minimum years) are appended to `contradictions` and the explicit
  wording is kept.
* Every model value must be grounded in the posting text. The check requires an
  exact phrase or >= 60% of the value's tokens, and years must appear as
  `N years`. Ungrounded values are dropped and listed in
  `extraction.dropped_llm_claims`.
* A failed LLM call never fails the request: deterministic analysis is returned
  with `llm_status=failed` and a safe warning (exception class only). The next
  explicit analyze request retries.

`llm_status` values: `used`, `disabled`, `not_configured`, `failed`,
`skipped_no_description`, `not_analyzed` (read-only response for an unsaved
computation).

## 5. Caching

`job_intelligence` stores one row per job with `extractor_version`,
`job_fingerprint` (SHA-256 over extractor version, title, company, location,
job URL and description), `confidence`, LLM status/model and the validated
payload. A cached row is reused while the fingerprint matches; `force=true`
recomputes. A failed LLM row is retried when LLM use is requested; a
`not_configured` row is retried once a key exists. `GET` never writes and never
calls the LLM: with no cache it returns a deterministic-only analysis marked
`cached=false`.

## 6. API

Base: `/api/v1/jobs`. Both routes require the bearer JWT and enforce job
ownership (another account's job is `404 job_not_found`).

`POST /{job_id}/analyze`

```json
{ "force": false, "use_llm": true }
```

`GET /{job_id}/intelligence` - no body.

Both return:

```json
{
  "job_id": "uuid",
  "fields": { "...": "structured understanding" },
  "extraction": {
    "confidence": "high",
    "provenance": { "required_skills": "deterministic" },
    "field_confidence": { "required_skills": "high" },
    "contradictions": [],
    "warnings": [],
    "llm_used": true,
    "llm_status": "used",
    "llm_model": "openai/gpt-oss-120b",
    "extractor_version": "2f.1.1",
    "dropped_llm_claims": [],
    "supplemented_fields": ["preferred_skills"],
    "computed_at": "2026-11-10T12:00:00Z",
    "cached": false
  },
  "disclaimer": "...not a hiring probability...",
  "message": null
}
```

The disclaimer is explicit that scores are not hiring probabilities and that
missing values mean the posting did not state them.

## 7. Security

* Text inside `<job_description>` is treated as untrusted data by the existing
  agent prompt; the service adds a grounding gate before storing any model value.
* No secret, token or credential is sent to or returned from the model; only the
  posting text and its title/company.
* LLM failures expose only the exception class, never the upstream message.
* Extraction results are owner-scoped; the raw stored description is only
  returned to its owner.
* Nothing here triggers a browser, a submission or a click.

## 8. Verification

```powershell
conda run -n venv_rating python -m pytest -q -m "not live and not browser"
conda run -n venv_rating python -m ruff check app tests
conda run -n venv_rating python -m alembic heads
conda run -n venv_rating python -m alembic current
conda run -n venv_rating python -m alembic check
```

`tests/test_job_intelligence.py` covers deterministic extraction, missing
information, contradictions, salary without numbers, required/preferred overlap,
LLM grounding and disagreement handling, authentication, ownership, caching,
`force`, description-change invalidation and LLM failure recovery.

## 9. Known limitations

* Extraction quality depends on the posting text; Glassdoor alert emails often
  carry no description, in which case only stored metadata is analysed.
* Work-mode and seniority detection are keyword heuristics with a small negation
  window; unusual phrasing may be left unclassified rather than guessed.
* Salary is recorded verbatim from one window and never normalised to a number
  or currency; multiple statements keep the first and warn.
* The fresh-data path (LLM) is exercised with an injected agent in tests; the
  live Groq test remains in the existing agent suite.
* `GET` returns an unsaved deterministic view, so a dashboard must call
  `POST /analyze` to persist an enriched analysis.
