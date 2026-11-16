---
noteId: "93cd3830df4a11f1ab35dd3cff4bbf9e"
tags: []

---

# Runtime issues — 13 November 2026

Local Windows 11 investigation of two confirmed runtime failures in
`C:\Agentic_ai\applypilot-ai`, plus live verification against the real database
and a real Chromium session.

Scope: fix the two runtime failures only. No new product features, no deployment
work, no new environments, no data reset. All pre-existing work was preserved:
nothing was committed, pushed, stashed, checked out or cleaned by this
investigation. (During the session the repository gained one commit made outside
this work, `35d1fbf "fix the bugs"`, which contains the Phase 2I backend work;
none of the changes below are in it — they are still uncommitted.)

---

## 1. Issue 1 — `relation "job_states" does not exist`

### Root cause

The migration was **pending**, not missing and not pointed at the wrong
database.

- `backend/alembic.ini` holds only a placeholder URL; `backend/alembic/env.py`
  injects `settings.database_url` from the application settings, so Alembic and
  the backend read the same `backend/.env` (`DATABASE_URL`, asyncpg driver).
- The database was at revision `c3f9a72d1e05` while the expected head was
  `a7c4e18f5b93` (`a7c4e18f5b93_phase2i_1_job_states.py`), which creates
  `job_states`. That revision existed on disk but had never been applied.
- Because the table did not exist, `GET /api/v1/jobs` and
  `GET /api/v1/matching/summary` raised `asyncpg.exceptions.UndefinedTableError`.

Target confirmed before changing anything (no credentials printed):

| Check | Value |
| --- | --- |
| Driver | `postgresql+asyncpg` |
| Host | `ep-lively-lab-zaj9wpeg.c-2.eu-west-2.aws.neon.tech` |
| Database | `neondb` |
| Schema | `public` |
| Server | PostgreSQL 18.7 |
| `current_database()` | `neondb` |
| Tables before | 26 (no `job_states`) |
| `alembic_version` before | `c3f9a72d1e05` |

### Fix

Applied the **existing** committed migration only:

```powershell
cd C:\Agentic_ai\applypilot-ai\backend
conda activate venv_rating
python -m alembic current    # c3f9a72d1e05
python -m alembic heads      # a7c4e18f5b93 (head)
python -m alembic upgrade head
```

No table was created by hand, no duplicate migration was added, the database was
not stamped and nothing was dropped or recreated.

### Database revision

| | Before | After |
| --- | --- | --- |
| `alembic current` | `c3f9a72d1e05` | `a7c4e18f5b93 (head)` |
| `alembic heads` | `a7c4e18f5b93 (head)` | `a7c4e18f5b93 (head)` |
| `job_states` | absent | present |
| `users` rows | 2 | 2 |
| `jobs` rows | 380 | 380 |
| `applications` rows | 8 | 8 |

`job_states` now has the schema the revision defines: columns `id`, `user_id`,
`job_id`, `is_saved`, `is_dismissed`, `saved_at`, `dismissed_at`, `created_at`,
`updated_at`; primary key `pk_job_states`; unique constraint
`uq_job_states_user_id_job_id`; indexes `ix_job_states_user_id`,
`ix_job_states_job_id`, `ix_job_states_user_id_is_saved`. No user data changed.

---

## 2. Issue 2 — Playwright `NotImplementedError` on Windows

### Root cause

Uvicorn 0.38 chooses the event loop itself, and its Windows rule breaks
Playwright.

`uvicorn/loops/asyncio.py`:

```python
def asyncio_loop_factory(use_subprocess: bool = False):
    if sys.platform == "win32" and not use_subprocess:
        return asyncio.ProactorEventLoop
    return asyncio.SelectorEventLoop
```

`Config.use_subprocess` is `bool(reload or workers > 1)`. The documented start
command (`run.txt`, `README.md`) used `uvicorn app.main:app --reload`, so
Uvicorn selected a **SelectorEventLoop** on Windows. A selector loop does not
implement `asyncio.create_subprocess_exec`: its transport factory raises
`NotImplementedError` from `asyncio.base_events._make_subprocess_transport`.
That is exactly the reported trace through Playwright `_transport.py`. The
browser driver wrapped it as `BrowserUnavailableError`, the service persisted
`browser_automation_unavailable` and the endpoint returned **503**.

Nothing in the application set an event loop policy (`asyncio.get_event_loop_policy()`
was never called), so the incompatible loop was purely a Uvicorn startup choice.
Starting the app loop first and changing policy afterwards cannot help — the
loop must be chosen before the application loop exists.

### Fix

1. **`backend/app/core/event_loop.py` (new)** — one place that decides the loop:
   - `windows_proactor_loop_factory()` returns a new `ProactorEventLoop` on
     Windows and a normal `SelectorEventLoop` elsewhere. It is written as a
     zero-argument *loop factory*, which is what Uvicorn's custom `loop` import
     path expects (`Config.get_loop_factory()` returns it unchanged).
   - `configure_event_loop_policy()` installs
     `asyncio.WindowsProactorEventLoopPolicy` on Windows before any loop exists
     and is a no-op on Linux (and on Python versions that drop policy objects).
   - `loop_supports_subprocess()` lets the driver detect the incompatible case
     deterministically.

2. **`backend/scripts/run_server.py` (new)** — the documented startup entry
   point. It configures the policy, then passes
   `loop="app.core.event_loop:windows_proactor_loop_factory"` to Uvicorn on
   Windows and `loop="auto"` on Linux. Because the loop factory is passed in the
   `Config`, it survives the reload supervisor's child process, so `--reload`
   works too. No second/background event loop is created: the application still
   runs on the single Uvicorn loop.

3. **`backend/app/integrations/browser/persistent.py`**:
   - Preflight in `open()`: on Windows, a selector loop now raises
     `BrowserUnavailableError` *before* Playwright starts, with an actionable
     message (`start the backend with 'python -m scripts.run_server' …`) and a
     `browser_session_open` warning naming `loop_class`. Previously the failure
     surfaced as an opaque `NotImplementedError` deep in Playwright.
   - Partial-launch cleanup: `_close_partial_launch()` closes the context, then
     the browser, then stops Playwright, innermost first, best-effort, so a
     context/page/`goto` failure after a successful launch cannot leave a
     Chromium child behind. Cleanup failures are logged, never mask the launch
     error.
   - Generic launch failures now log `error_class`, a 300-character `error`
     message and `platform`, instead of only the exception class.

4. **`backend/app/services/browser_session_service.py`** — the
   `browser_session_start` warning now includes the short exception message, so
   an operator can diagnose the cause from the log.

5. Docs updated to the corrected command: `backend/README.md` §10, `run.txt`,
   and the `app/main.py` module docstring.

No security behaviour changed: the persistent browser architecture, ownership,
per-user session isolation, navigation policy/DNS pinning, streaming ticket
model and human-submission safeguards are untouched. Playwright is still used
asynchronously only; there is no sync/async fallback.

### Correct backend startup command

From `C:\Agentic_ai\applypilot-ai\backend` in the existing `venv_rating`
environment:

```powershell
python -m scripts.run_server --reload --host 127.0.0.1 --port 8000
```

Use `--no-reload` (or omit `--reload`) for a run without auto-reload. Do **not**
use `uvicorn app.main:app --reload` on Windows. Linux keeps using
`loop="auto"`, so deployment behaviour is unchanged.

---

## 3. Live verification

All checks below ran against the real Neon database and a real Chromium, using
the existing application `5620e259-6c43-4445-8486-bc16b79d3fb1` (Glassdoor
listing, user `1a9a584c-…`). No application was submitted.

### Issue 1 endpoints

| Call | Result |
| --- | --- |
| `GET /api/v1/jobs?limit=3` | **200**, `total: 378` |
| `GET /api/v1/matching/summary` | **200**, `jobs_total: 378`, `jobs_matched: 7` |
| `POST /jobs/{id}/save` | **200**, `user_state.saved=true` |
| `POST /jobs/{id}/save` again | **200**, idempotent, `saved_at` refreshed |
| `DELETE /jobs/{id}/save` | **200**, `saved=false` |
| `POST /jobs/{id}/dismiss` | **200**, `dismissed=true`, `saved=false` |
| `POST /jobs/{id}/restore` | **200**, `dismissed=false` |
| `GET /api/v1/jobs?limit=50` | **200**, listing shows the same `user_state` |
| DB check | exactly **1** `job_states` row for `(user_id, job_id)`; total 1 row |

### Browser session (Issue 3)

| Step | Result |
| --- | --- |
| `GET /browser-sessions/capabilities` | **200**, `automation_enabled: true`, `view.available: true` (`cdp_relay`) |
| `POST /browser-sessions` (`--no-reload` server) | **200** in 20.0 s, run 4, `state=browser_ready`, `browser_status=open` |
| Chromium process check | Playwright `chromium_headless_shell-1248` running (4 processes) under the backend |
| `current_url` | `https://www.glassdoor.co.in/partner/jobListing.htm` |
| `GET /browser-sessions/by-application/{id}` | **200**, same live session |
| `GET /browser-sessions/{session_id}` | **200** |
| `GET /browser-sessions/active` | **200**, `total: 1` |
| `POST …/detect-destination` | **200** in 7.2 s, `status: "unsupported"` (see below) |
| `POST …/form-analysis` | **200** in 11.3 s, analysis v1 stored |
| `POST …/cancel` | **200**, `state=cancelled`, `browser_status=closed`, Chromium processes **0** |
| `POST /browser-sessions` (server started **with `--reload`**) | **200** in 15.7 s, run 5, `browser_ready`; cancel closed it again |

The `--reload` result is the decisive check: the exact command shape that used to
fail now launches Chromium successfully.

### Honest limitation: the real Glassdoor → Indeed destination was not validated

The Glassdoor listing page loaded, but the page attempted to navigate to
`challenges.cloudflare.com` (a Cloudflare challenge). The Phase 2H navigation
guard correctly aborted that unapproved origin:

```
"Blocked browser navigation outside the approved origins", host: "challenges.cloudflare.com"
```

Consequences, reported as they are:

- `detect-destination` returned `status: "unsupported"` — **not** a validated
  Glassdoor → Indeed/SmartApply destination. `application_destination_url` and
  `destination_platform` remain empty and `destination_validated=false`.
- Form analysis therefore ran on the **Glassdoor listing page**, not on a
  supported application form. It still behaved correctly and safely:
  `captcha_detected: true`, `requires_human_input_count: 1`,
  `automation_eligible_count: 0`, plus a warning that ApplyPilot never solves
  CAPTCHA.
- `applications.redirect_status` was persisted as `unsupported` by the detection
  attempt. A new browser run resets it to `awaiting_user_action` (existing
  behaviour), so the workspace is not permanently stuck.
- Passing mocked Playwright e2e tests (`easy-apply-redirect.spec.ts`) is **not**
  evidence that the live Glassdoor → Indeed hand-off works. Validating that
  needs a listing that is not challenge-gated, or a human completing the
  CAPTCHA/login in the live view.

Human-in-the-loop and manual-submission safeguards were exercised, not weakened:
the only clickable path remains the user's, CAPTCHA/OTP/MFA/legal declarations
are marked human-only (`automation_eligible_count: 0`), and the existing
`test_no_submission_primitive_exists_on_the_adapter_or_handle` regression test
still passes.

---

## 4. Exact changes made

| File | Change |
| --- | --- |
| `backend/alembic/versions/a7c4e18f5b93_phase2i_1_job_states.py` | unchanged; applied to the database |
| `backend/app/core/event_loop.py` | **new**: Proactor loop factory, policy setup, selector-loop detection |
| `backend/scripts/run_server.py` | **new**: documented startup entry point used by README/run.txt |
| `backend/app/integrations/browser/persistent.py` | selector-loop preflight with actionable error; `_close_partial_launch()` cleanup; richer launch-failure logging |
| `backend/app/services/browser_session_service.py` | launch-failure log includes the short error message |
| `backend/app/main.py` | module docstring points at the correct startup command |
| `backend/tests/test_runtime_local_issues.py` | **new**: 12 regression tests (below) |
| `backend/README.md`, `run.txt` | corrected startup command and the reason |

New tests (`backend/tests/test_runtime_local_issues.py`):

- Proactor factory on Windows / selector factory elsewhere;
- selector loop accepted on Linux, rejected on Windows;
- Uvicorn `--reload` config produces a Proactor loop factory;
- `run_server.main()` passes the loop factory, port and reload flag;
- Proactor policy installed on Windows, no-op on Linux;
- partial launch (context failure) closes browser **and** stops Playwright;
- launch failure stops Playwright and chains the cause;
- Windows selector loop fails with the actionable `scripts.run_server` message;
- Alembic history has exactly one head (`a7c4e18f5b93`) that creates `job_states`;
- `job_states` table/constraints/indexes match the model.

Session creation, retrieval, destination detection, save/dismiss/restore and the
security restrictions were already covered by
`test_phase2e_browser_sessions.py`, `test_phase2h_smartapply_redirect.py` and
`test_phase2i_1_job_workspace.py`; they were re-run rather than duplicated.

---

## 5. Test results

| Check | Command | Result |
| --- | --- | --- |
| New regression tests | `python -m pytest tests/test_runtime_local_issues.py` | **12 passed** |
| Focused backend | `pytest tests/test_runtime_local_issues.py tests/test_phase2e_browser_sessions.py tests/test_phase2h_smartapply_redirect.py tests/test_phase2i_1_job_workspace.py` | **84 passed** |
| Full backend regression | `python -m pytest -q` | **exit 0**, 738 test outcomes, no `F`/failure markers |
| Ruff lint (changed files) | `python -m ruff check app/core/event_loop.py scripts/run_server.py app/integrations/browser/persistent.py app/services/browser_session_service.py tests/test_runtime_local_issues.py` | **All checks passed** |
| Ruff lint (repository) | `python -m ruff check .` | 11 pre-existing errors, all in unrelated Phase 2I files (`answer_assistant.py`, `test_phase2i_*.py`); none in a file changed here |
| Frontend unit | `npx vitest run` | **208 passed** (19 files) |
| TypeScript | `npx tsc --noEmit` | clean, exit 0 |
| ESLint | `npx eslint .` | clean, exit 0 |
| Next.js build | `npx next build` | succeeded, exit 0 |
| Playwright (browser workspace + redirect + job workspace) | `npx playwright test e2e/application-workspace.spec.ts e2e/easy-apply-redirect.spec.ts e2e/job-workspace.spec.ts` | **10 passed** (1.8 m) |

Notes:

- `ruff format --check` is not clean repository-wide on untouched pre-existing
  files (documented since Phase 2E Stage F). The new files are formatted; no
  mass reformat was performed to avoid unrelated churn.
- The pytest terminal summary line is suppressed in this environment; the full
  run was verified from its progress output and exit status (0).

---

## 6. Remaining blockers

1. **Live Glassdoor → Indeed destination validation.** Blocked by Cloudflare's
   challenge on the stored listing URL. The session itself now works, but the
   hand-off cannot be validated end-to-end without a human completing the
   challenge in the live view (or a listing that is not challenge-gated). This
   is an external-site restriction, not a code defect fixed here.
2. **Misleading `unsupported` status after a challenge.** When the guard blocks
   `challenges.cloudflare.com`, `detect-destination` reports `unsupported`
   (honest about the block, but not specific about "the page was behind a
   challenge"). Changing that wording/classification would be a product change
   and was intentionally not made.
3. **The backend is currently running** (started with `--reload` on
   `127.0.0.1:8000`, health 200). Stop it with Ctrl+C in its terminal or
   `taskkill /PID <pid> /T`. No Playwright Chromium is left running.
4. **Pre-existing lint debt** (11 `ruff check` findings) in Phase 2I files — out
   of scope for this fix.
