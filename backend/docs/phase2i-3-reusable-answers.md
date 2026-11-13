---
noteId: "3a97c0b0df4411f1ab35dd3cff4bbf9e"
tags: []

---

# Phase 2I.3 - Reusable Answers & Candidate Knowledge Center

Scope: give saved application answers a dedicated home with search, categories,
honest trust levels, provenance, verification actions, safe reuse across
applications and a read-only review of candidate knowledge consistency.

The phase builds on the existing `application_answers` store. No duplicate
answer table, no second lifecycle and no new automation path were introduced.
ApplyPilot still never submits an application, never fills an employer form
without the existing approval workflow, and never handles CAPTCHA, OTP, MFA,
passwords, payment details or legal declarations automatically.

---

## 1. What was reused

| Existing capability | Reused for |
| --- | --- |
| `ApplicationAnswer` / `application_answers` | The only saved-answer store: values, `verification_status`, `answer_source`, `confidence`, `source_record_id`, `verified_at`, `last_confirmed_at`, `freshness_days` |
| `GET/PUT/DELETE /profile/answers` | Listing, editing, deleting and confirming answers |
| `POST /profile/answers/{key}/resolve` | Re-confirming, adopting the profile value or storing a corrected answer |
| `GET /profile/answers/review` | Stale/conflict detection (unchanged) |
| `ApplicationAnswerMatcher` | Canonical field classification, conflict detection, freshness rules, legal/sensitive categories |
| `answer_safety.is_forbidden_answer_storage` | Refusing credential/payment fields |
| `profile_answers()` / candidate profile | Verified evidence and conflict detection |
| `ApplicationService.update_question()` | Saving reused text with per-application confirmation |
| `CandidateService.completeness_for_user()` | Missing profile information in the knowledge report |
| BFF deny-by-default allowlist | Explicit routes for the new reads and the reuse lookup |

`app/services/answer_library.py` is a read/derive layer over that store. It adds
no persisted fields: category and trust are projections of the question wording
and the existing provenance columns.

## 2. Features implemented

### Answer library page (`/answers`)

* Saved answers across every application, newest data on refresh.
* Debounced server-side search over question text, question key and answer text.
* Category filter and trust-level filter, both validated by the API.
* Category counts and trust counts from the user's own answers.
* Per-answer detail panel: trust reason, answer source, confidence, last
  confirmation, stale reason, recorded source and related verified profile
  value, plus detected conflicts.
* Inline edit. Saving changed wording marks the answer unverified and clears its
  confirmation timestamps.
* Explicit "Confirm answer" action for user-written, AI-drafted or
  resume-derived answers.
* Copy to clipboard.
* Two-step delete (an explicit "Delete answer" confirmation).
* "Reuse" panel: choose one of the user's applications and one non-legal,
  non-file question, then insert the saved value as an unconfirmed draft.
  Answers that conflict with current verified information cannot be inserted.

### Candidate knowledge center

The same page shows a read-only report with four groups:

* **Missing information** - incomplete profile fields (with a link to the
  profile) and saved answers that still need a value.
* **Conflicts with verified data** - stored answers that disagree with the
  profile or with another answer; each can adopt the profile value or keep the
  stored value through the existing resolve endpoint.
* **Outdated confirmations** - verified answers past their freshness window.
* **Not verified** - AI drafts, resume suggestions and user-written answers that
  are not confirmed facts.

The report never changes a verified fact, never invents a missing value and
states that limitation in its own disclaimer.

### Reuse inside an application question

Text and textarea questions in the application workspace now show **Reuse saved
answer**. It loads ranked suggestions for that exact question, shows trust,
category, relevance, stale warnings and conflicts, and inserts the chosen text
into the local editor only. The existing **Save draft** and **Confirm answer**
controls remain responsible for persistence and confirmation.

## 3. Categories

Categories are derived from the question the answer belongs to. They are not
stored and no category is invented when the existing data does not support it.

| Category | Example triggers |
| --- | --- |
| Personal introduction | "Tell us about yourself", professional/personal summary, bio |
| Work experience | Work/professional experience, employment history, current role/company |
| Projects | Project, side project, portfolio |
| Technical skills | Skills, tech stack, technologies, tools, programming languages |
| Achievements | Achievement, accomplishment, award, recognition, certification |
| Strengths | Strength(s), greatest asset, "what are you good at" |
| Career goals | Career goal/objective/aspiration, "where do you see yourself", "why do you want" |
| Education | Education, degree, university, college, GPA, coursework |
| Other | Contact, address, availability, compensation, authorization, declarations, cover letter, anything unmatched |

A canonical key prefix (`experience.`, `projects.`, `skills.`, `education.`,
`certifications.`) wins before wording, so a saved `experience.summary` stays in
work experience even if its question text is terse. Literal prefixes such as
`contact.`, `address.` and `compensation.` deliberately map to **Other**.

## 4. Trust levels and provenance rules

| Trust level | Derived from | Reused as a fact? |
| --- | --- | --- |
| Verified candidate fact | `verification_status=verified` + `answer_source=profile` | Yes |
| User-reviewed answer | `verification_status=verified` (any other source) | Yes |
| Resume-derived suggestion | Not verified + `answer_source=resume` | No |
| AI-generated draft | Not verified + `answer_source=ai_suggestion` | No |
| User-written, not yet reviewed | Not verified + user input / previously approved | No |
| Requires additional information | No stored value, or `answer_source=needs_confirmation` | No |

Rules enforced in code:

1. An AI draft is never reported as a verified fact or silently promoted.
2. Saving an AI draft through `PUT /profile/answers/{key}` keeps
   `verification_status=unknown` unless the client explicitly confirms it.
3. Editing the answer text without an explicit `verification_status` resets the
   answer to `unknown`, clears `verified_at`/`last_confirmed_at`, sets
   `answer_source=user_input` (unless the client supplies another source) and
   lowers confidence to 0.5. A previously verified value cannot keep a stale
   "verified" label after its wording changed.
4. Confirming an answer records `answer_source=previously_approved`, a fresh
   `last_confirmed_at` and confidence 1.0.
5. Resolving a conflict keeps the existing rule: the resolved value is always
   freshly confirmed by the user.
6. Reusing an answer in an application records
   `answer_source=previously_approved` and
   `source_record_id=application_answer:{key}` on the application question. The
   provenance survives saving and confirming that application's answer; the
   reused lookup is scoped to the authenticated user's candidate, so a client
   cannot claim provenance for somebody else's answer.
7. Legal declarations are never stored for reuse, never resolved and never
   offered as suggestions. Credential/payment questions are refused at every
   save path.

## 5. Reuse behaviour

Matching is deterministic and explainable; no model is involved.

1. Exact/canonical relation: the saved answer's canonical field equals the new
   question's canonical field (for example both are `contact.phone`).
2. Exact question key.
3. Exact normalised question wording.
4. Same derived category (never **Other**) with word overlap.
5. Strong token overlap (Jaccard >= 0.5) for free-text behavioural questions.

The job title and extracted required skills add a small re-ranking bonus
(+0.05) only when the saved text already mentions them. They never create a
match from unrelated wording, and the reason string says so when the bonus
applies.

Every suggestion carries `requires_review` and `safe_to_insert`:

* `safe_to_insert=false` when the saved value conflicts with the current
  verified profile value or with another stored answer;
* `safe_to_insert=false` when the confirmation is outdated;
* only the user's own saved answers are considered;
* legal questions return no suggestions at all.

Insertion never writes to an employer's form. It produces an application
question with `status=suggested`, `requires_confirmation=true` and
`confirmed=false`.

## 6. API changes

### Extended

```
GET /api/v1/profile/answers?q=&category=&trust=
```

Returns `SavedAnswerResponse[]` (all previous fields plus `category`,
`category_label`, `trust_level`, `trust_label`, `trust_reason`, `needs_review`,
`needs_information`, `is_stale`, `freshness_reason`). `category` and `trust` are
validated against the supported values (unknown values return `422`).

```
PUT /api/v1/profile/answers/{question_key}
POST /api/v1/profile/answers/{question_key}/resolve
```

Now return the same enriched `SavedAnswerResponse`. Editing semantics are
unchanged except for rule 3 above.

```
PATCH /api/v1/applications/{application_id}/questions/{question_id}
```

Accepts an optional `reused_answer_key`. The key is looked up for the owning
candidate; a missing/foreign key returns `404`, and a legal or forbidden saved
answer returns `400`. When present, the question records
`answer_source=previously_approved` and the saved answer as `source_record_id`,
including after confirmation.

### New

```
GET /api/v1/profile/answers/categories
```

`{ total, categories: [{ category, label, count }], trust_levels: {} }` for the
user's own answers only.

```
GET /api/v1/profile/answers/knowledge
```

`CandidateKnowledgeResponse`: totals, `missing_information`, `conflicts`,
`outdated`, `unverified`, `summary`, fixed disclaimer.

```
GET /api/v1/profile/answers/{question_key}
```

`SavedAnswerDetailResponse`: the enriched answer, its evidence/source items and
any detected conflicts. Another user's answer returns `404`.

```
GET /api/v1/applications/{application_id}/questions/{question_id}/reusable-answers
```

`ReusableAnswerSuggestionsResponse`: application/job context, question, and up
to 8 ranked suggestions with trust, relevance, reason, review/safe flags,
freshness and conflicts. The application is always resolved through
`ApplicationService.get_application(user, id)`, so a foreign application returns
`404`.

## 7. Database changes

**None.** No model, column, table or migration was added. The Alembic head is
still `a7c4e18f5b93`.

## 8. Frontend

**New**
* `src/features/answers/api.ts`, `src/features/answers/hooks.ts`
* `src/components/answers/answer-library.tsx`
* `src/app/(app)/answers/page.tsx`
* `tests/answer-library.test.tsx`
* `e2e/answer-library.spec.ts`

**Modified**
* `src/components/applications/application-workspace.tsx` - "Reuse saved
  answer" picker; reused key forwarded through Save draft / Confirm answer.
* `src/features/applications/api.ts`, `hooks.ts` - `reused_answer_key` on the
  question PATCH.
* `src/features/profile/api.ts`, `hooks.ts` - enriched answer return type and
  answer-library cache invalidation.
* `src/components/layout/nav.ts` - "Answer Library" navigation entry.
* `src/lib/constants.ts` - explicit BFF routes for the new reads, the resolve
  action, the question suggestion lookup, and forwarded `q`/`category`/`trust`
  filters.
* `src/types/api.ts` - saved-answer, category, trust, evidence, knowledge and
  suggestion types.
* `tests/proxy-contract.test.ts` - new routes and filter forwarding.

The page uses the existing card, badge, button, alert, empty-state, skeleton and
spinner components. No new design system or dependency was added.

## 9. Test results

Commands were run from `backend/` with the project's `venv_rating` environment
and from `frontend/`.

| Check | Command | Result |
| --- | --- | --- |
| Phase 2I.3 backend tests | `pytest tests/test_phase2i_3_reusable_answers.py -q` | **26 passed** |
| Backend regression | `pytest -m "not live and not browser" -o addopts="" -q` | **725 passed, 5 skipped, 1 deselected**, exit 0 (140s) |
| Backend lint | `ruff check` on all changed files | clean |
| Alembic heads | `alembic heads` | one head: `a7c4e18f5b93` |
| Frontend unit/component | `vitest run` | **208 passed across 19 files** (6 new) |
| TypeScript | `tsc --noEmit` | clean |
| ESLint | `eslint .` | clean |
| Next.js build | `next build` | success; `/answers` 6.31 kB |
| Playwright | `playwright test` | **23 passed** (21 existing + 2 new) |

New backend coverage:

* category derivation, including the `other` fallback and key prefixes;
* trust levels, including AI drafts and resume suggestions never being facts;
* search, category and trust filtering, and invalid filter rejection;
* per-user isolation of list, detail, categories and knowledge;
* detail evidence plus profile conflicts;
* edit resets verification and clears confirmation timestamps;
* confirming restores `previously_approved` provenance;
* knowledge report: missing profile fields, conflicts, stale confirmations and
  unverified answers, with no mutation of the stored fact;
* reuse suggestions: canonical match, unrelated/legal exclusion, shared-word
  exclusion, conflict blocking and no writes;
* user-scoped suggestion lookup and reuse provenance surviving confirmation;
* rejection of a reused key the user does not own.

New frontend tests cover the answers page (trust/category badges, search and
filters, edit, confirm, delete confirmation, details/evidence) and reuse into an
application question. Playwright covers the page end-to-end and the workspace
reuse picker, asserting `confirmed:false`, `reused_answer_key`, and the absence
of any approve/submit call.

## 10. Security and safeguards

* Every endpoint resolves the candidate from the authenticated user; another
  user's answer, application or question returns `404`.
* Search and filters are server-side and scoped to the owned candidate.
* A reused answer key is re-checked against the owned candidate, not trusted
  from the client.
* Legal declarations remain impossible to store as reusable verified answers,
  are never suggested, never resolved and never reused.
* Passwords, OTPs, cookies and payment data remain refused by
  `is_forbidden_answer_storage` on create, update, resolve and reuse.
* AI drafts and resume suggestions are labelled as such and cannot be treated as
  facts without an explicit user confirmation.
* Editing text resets verification instead of preserving a misleading label.
* Reuse inserts only into ApplyPilot's own application record; the employer's
  form is untouched, and approval/submission flows are not called.
* No candidate information is fabricated: missing profile fields are reported,
  not filled.

## 11. Remaining limitations

* Categories are heuristic. Unusual wording falls back to **Other** by design,
  and the API never invents a category the existing question does not support.
* Reuse ranking is deterministic and wording-based. It does not use embeddings
  or an LLM, so a relevant answer phrased very differently may not be suggested;
  the user can still find it in the library and copy it.
* The job context influences ranking only through title and extracted required
  skills. It never changes the answer text.
* There is no server-side version history per saved answer. Editing replaces the
  value; the previous wording is not retained.
* Conflicts are surfaced, not auto-resolved. The user must choose "keep stored",
  "use profile" or edit the answer.
* The knowledge report's missing-information list mirrors the existing profile
  completeness rules; it does not assess whether an individual experience or
  project entry is stale.
* Freshness windows are the existing category defaults. A stale answer is shown
  and blocked from safe insertion, but it is not automatically refreshed.
* Playwright runs against the production build with mocked BFF responses; no
  live Google-authenticated session or real employer site was exercised.
* No deployment, infrastructure, DNS or SSL work was performed, and no commit or
  push was made.
