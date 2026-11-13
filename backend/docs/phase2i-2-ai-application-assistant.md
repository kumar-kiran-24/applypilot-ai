---
noteId: "f2178380df2411f1ab35dd3cff4bbf9e"
tags: []

---

# Phase 2I.2 - AI Application Assistant & Answer Drafting

Scope: replace the assistant placeholder with a working, evidence-grounded
answer workflow, and connect drafts to the existing application questionnaire
without creating a second answer store or a second lifecycle.

No deployment or infrastructure work was performed. ApplyPilot still never
submits an application, never fills an employer page without the existing
approval/safe-fill workflow, and never handles CAPTCHA, OTP, MFA, passwords,
payment details or legal declarations automatically.

---

## 1. What was reused

| Existing capability | Reused for |
| --- | --- |
| `ApplicationAnswer` / `application_answers` | Saved reusable answers, provenance, verification and freshness |
| `ApplicationQuestion` / `application_questions` | Per-application drafts and the association between a draft and its question |
| `ApplicationAnswerMatcher` | Question classification, sensitive/legal categories, conflict detection |
| `answer_safety` | Refusing passwords, one-time codes, cookies and payment data |
| `profile_answers()` / candidate profile | Verified facts, experience, projects, education, skills, authorization |
| `ResumeIntelligenceService` | Resume summary, roles, projects, skills with per-fact provenance |
| `JobUnderstandingService` | Required skills, qualifications, responsibilities (no extra LLM call) |
| `GroqLLMClient` | One guarded drafting call |
| `PATCH /applications/{id}/questions/{qid}` | Saving a draft on an application |
| `PUT /profile/answers/{key}` | Saving a reusable suggestion |
| `GET /profile/answers` | The reusable-answer section and answer matching |
| BFF deny-by-default allowlist | One new explicit route |

No new model, table or duplicate answer service was introduced. A generated
draft is returned by the API and persisted only when the user saves it through
the existing endpoints.

## 2. API changes

One backend route was added:

```
POST /api/v1/assistant/answers/draft
```

Request (`DraftAnswerRequest`):

| Field | Notes |
| --- | --- |
| `application_id` | Optional; supplies ownership scope, job and questions |
| `job_id` | Used when drafting without an application |
| `question_id` | Optional; must belong to `application_id`; its canonical key/text win |
| `question_key` | Optional canonical key for custom questions |
| `question_text` | Required for custom questions, 3-1000 characters |
| `answer_type` | Existing `AnswerType`, default `text` |
| `allow_llm` | Defaults true; false returns deterministic evidence only |

At least one of `application_id` / `job_id` is required. `question_id` requires
`application_id`, and a `job_id` that disagrees with the application is rejected
with `400`.

Response (`AnswerDraftResponse`) contains:

* `status`: `drafted`, `needs_confirmation`, `insufficient_information` or `unavailable`
* `draft_answer` (nullable) and `confidence` / `confidence_label`
* `evidence[]` with id, kind (`profile`, `resume`, `job`, `answer_bank`), label,
  detail, source, `verified` and `used`
* `missing_information[]` (field, question, reason, sensitive)
* `conflicts[]` (stored value vs profile/other answer)
* `inferred_claims[]`, `warnings[]`
* `save_reusable_allowed`, `is_sensitive`, `is_legal`
* `llm_status` (`used`, `disabled`, `not_configured`, `failed`), `llm_model`
* job/application context and a fixed disclaimer

The frontend BFF allowlist now explicitly permits
`POST assistant/answers/draft`; the contract test fails if the client call is
ever removed from the allowlist.

## 3. Database migrations

**None.** The assistant writes no new tables. Draft text is persisted through
the existing `application_questions.answer` / provenance columns, and reusable
suggestions through `application_answers` with `answer_source=ai_suggestion`
and `verification_status=unknown`. The current Alembic head remains
`a7c4e18f5b93` (Phase 2I.1).

## 4. How a draft is produced

1. **Context and ownership.** The application is loaded with
   `ApplicationService.get_application(user, id)` (user-scoped), or the job with
   `JobService.get_job(user, id)`. A selected question must belong to that
   application.
2. **Deterministic evidence retrieval.** No model is involved:
   * verified profile summary, skills, experience, projects, education,
     certifications and contact/authorization facts;
   * the application's selected resume (or the default resume) through resume
     intelligence; unreadable files fall back to stored parsed data;
   * job description and extracted requirements/responsibilities;
   * related saved answers only when their words overlap the question.
3. **Guards before generation.**
   * credentials/payment questions return an explicit manual request;
   * legal declarations are never drafted;
   * sensitive or literal categories are answered only from a verified profile
     value (or a verified saved answer), and always require confirmation;
   * missing sensitive facts return a `missing_information` request;
   * no profile/resume/bank evidence returns a request for candidate evidence;
   * job-specific questions without posting evidence ask for job context.
4. **Guarded LLM drafting.** The model receives only the selected evidence as
   JSON, with a system prompt that forbids invented employers, titles, dates,
   skills, certifications, achievements, metrics and assumptions, and treats the
   posting/question as data rather than instructions.
5. **Validation of the model output.**
   * unknown evidence citations are dropped;
   * the draft is discarded if it contains a number that does not appear in the
     evidence (for example invented percentages);
   * no cited evidence lowers confidence and adds a warning;
   * confidence is capped when any used evidence is resume-derived/unverified;
   * inferred statements are returned separately and are never labelled facts.
6. **Provider failure.** A missing key returns `unavailable` /
   `not_configured`; an upstream failure returns `unavailable` / `failed` with
   the deterministic evidence so the user can write the answer. No canned answer
   is fabricated.

The endpoint never saves or submits anything.

## 5. Answer management

| Action | Implementation |
| --- | --- |
| Generate | `POST /assistant/answers/draft` |
| Regenerate | Same endpoint called again; no draft state is stored server-side |
| Edit | Editable textarea on the assistant page and in the application editor |
| Save to application | `PATCH /applications/{id}/questions/{qid}` with `confirmed: false`; the user still confirms separately |
| Save reusable | `PUT /profile/answers/{key}` with `answer_source=ai_suggestion`, unverified |
| Reuse | Reusable-answer sidebar loads an answer into the editor; the existing questionnaire matcher still governs automatic reuse |
| Copy | Clipboard button |
| Associate | `question_id` is stored in the response and used by the save action |
| Retrieve saved | `GET /profile/answers` |

A draft saved to an application is marked suggested/needs confirmation by the
existing service. It cannot become a verified answer without the user's explicit
confirmation, and legal questions are blocked from both save paths.

## 6. Frontend

### Assistant page (`/assistant`)

The placeholder was replaced by `AnswerAssistant` with:

* application selector (job title, company, stored status),
* job context card with links to `/jobs/{id}` and `/applications/{id}`,
* existing-question selector plus free-text custom question,
* generate/regenerate with loading and safe error states,
* editable draft, confidence label, LLM status/model and cited-evidence count,
* warnings, missing-information and conflict alerts, inferred claims,
* copy, save-to-application and save-to-reusable actions with success/error states,
* evidence sidebar showing kind, verified/unverified and used badges,
* reusable-answer sidebar with load-into-editor,
* explicit "never auto-submitted" and manual-sensitive-answer messaging.

It uses the existing card, badge, button, alert, empty-state, skeleton and
spinner components, and no new design system.

### Application workflow integration

Textarea questions in the existing application workspace now have a
**Draft with AI** action. It calls the same endpoint with `question_id`, inserts
the text into the local editor only, shows the confidence/evidence notice or the
missing-information request, and leaves the existing **Save draft** and
**Confirm answer** controls in charge. Nothing is written to the employer's site
and no submission or approval is triggered.

## 7. Files

**Backend (new)**
* `app/schemas/answer_assistant.py`
* `app/services/answer_assistant.py`
* `app/api/v1/endpoints/assistant.py`
* `tests/test_phase2i_2_answer_assistant.py`
* `docs/phase2i-2-ai-application-assistant.md`

**Backend (modified)**
* `app/api/v1/router.py` (register the assistant router)

**Frontend (new)**
* `src/features/assistant/api.ts`, `src/features/assistant/hooks.ts`
* `src/components/assistant/answer-assistant.tsx`
* `tests/answer-assistant.test.tsx`
* `e2e/answer-assistant.spec.ts`

**Frontend (modified)**
* `src/app/(app)/assistant/page.tsx` (placeholder replaced)
* `src/components/applications/application-workspace.tsx` (question-level AI draft)
* `src/lib/constants.ts` (BFF allowlist)
* `src/types/api.ts` (draft/evidence types and answer provenance fields)
* `src/features/profile/api.ts`, `src/features/profile/hooks.ts` (reusable upsert)
* `src/features/applications/hooks.ts` (`useApplication` optional enablement)
* `tests/proxy-contract.test.ts` (new route contract)

## 8. Verified results

| Check | Command | Result |
| --- | --- | --- |
| Phase 2I.2 backend tests | `pytest tests/test_phase2i_2_answer_assistant.py -q` | **11 passed** |
| Backend regression | `pytest -m "not live and not browser" -o addopts="" -q` | **699 passed, 5 skipped, 1 deselected**, exit 0 (160s) |
| Route registration | enumerate `app.main.app.routes` | `POST /api/v1/assistant/answers/draft` present |
| Alembic heads | `alembic heads` | one head: `a7c4e18f5b93` |
| Frontend unit/component | `vitest run` | **196 passed across 18 files** |
| New frontend tests | assistant 5 + proxy contract 1 | **6 passed** |
| TypeScript | `tsc --noEmit` | clean |
| ESLint | `eslint .` | clean |
| Next.js build | `next build` | success; `/assistant` 7.55 kB |
| Playwright | `playwright test` | **21 passed** (19 existing + 2 new) |

The new browser tests cover:

* assistant generation from a selected application question, evidence display,
  editing, saving to the application, saving to reusable answers, and the
  absence of any approve/submit call;
* **Draft with AI** inside the application editor inserting into the local
  textarea and requiring the existing Save/Confirm controls.

## 9. Security safeguards

* Every read is authenticated and user-scoped; another user's application or job
  returns `404`, and a question from another application is rejected with `400`.
* No credential, OTP, cookie or payment field can be drafted or stored.
* Legal declarations are never generated, saved for reuse, or reused.
* Work authorization, sponsorship, salary, notice period and relocation are
  never inferred: they come only from verified profile/bank values and require
  fresh confirmation; otherwise the API asks for the detail.
* Evidence is retrieved deterministically. The model cannot introduce job
  requirements, candidate facts or numbers that are not in the supplied
  evidence; unsupported numbers invalidate the draft.
* Missing evidence produces a request for information, not a generic answer.
* Conflicts between a stored answer and the profile are surfaced rather than
  silently resolved.
* Provider failures degrade to evidence-only responses; no fallback text is
  invented.
* Saving is explicit. The draft endpoint persists nothing, the application
  answer stays unconfirmed, and the reusable answer stays unverified.
* The approval/safe-fill workflow is untouched; final submission remains manual
  and is never called by the assistant.

## 10. Remaining limitations

* Live Groq generation was not exercised because no API key/network is
  configured in this environment. Tests use a real service stack with a fake LLM
  client and a real failure client. The `live` test remains excluded by the
  project's documented marker.
* Factual verification beyond citations and numeric tokens is heuristic: the
  prompt forbids invented prose, but the system cannot prove every sentence is
  evidence-backed. This is mitigated by evidence-only prompts, citation
  filtering, confidence caps, separate inferred claims and mandatory user review.
* Drafts have no server-side version history. Regeneration replaces the returned
  draft; only explicitly saved question/reusable answers persist.
* The assistant UI requires an existing application, so its application selector
  is empty for a user with no prepared applications. The API can draft from a
  `job_id` alone, but there is no standalone job picker in the page yet.
* Resumes whose file is not readable fall back to stored parsed data, which is
  marked unverified and lowers confidence.
* Reusable answers saved by the assistant start unverified by design; they are
  not auto-reused until the existing matcher and confirmation rules allow it.
* No live Google-authenticated browser validation was performed; Playwright
  runs against the production build with mocked BFF responses.
