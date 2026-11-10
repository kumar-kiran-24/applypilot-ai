---
noteId: "85e69600dc2811f1a81e4f0576e6a512"
tags: []

---

# Phase 2E Stage G - Production hardening

Stage G hardens the Stage A-F system without changing the product surface:
ApplyPilot now has **no automated final submission anywhere**, stricter
production configuration, deterministic browser cleanup, request-level
observability and a deployment runbook. This document records what changed, how
it was verified and what is still not production-ready.

Stage E streaming details live in
[`phase2e-stage-e-browser-streaming.md`](./phase2e-stage-e-browser-streaming.md);
the Stage F workflow lives in
[`phase2e-stage-f-end-to-end.md`](./phase2e-stage-f-end-to-end.md).

## 1. Architecture changes

| Area | Change |
| --- | --- |
| Submission | Removed `ApplicationPlatformAdapter.submit_application`, `AutomationSession.submit`, `PlaywrightSession.submit`, the submit/confirmation selectors and `_submit_automated`. `POST /applications/{id}/submit` is now a manual handoff only. |
| Application state | Approval never transitions to `SUBMITTING`/`SUBMITTED`. `SUBMITTED` is reachable only through `confirm_manual_submission(confirmed=True)`. |
| Preview | `submission_mode` is always `manual`; `manual_steps` are always returned; every preview warns that ApplyPilot never clicks Submit/Apply/Finish. |
| Production config | `ENVIRONMENT=production` rejects missing/wildcard/non-HTTPS/loopback CORS origins in addition to the existing secret and redirect checks. |
| HTTP responses | All responses carry `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`; production adds HSTS. |
| Browser lifecycle | `BrowserSessionManager.sweep_expired()` closes handles whose TTL elapsed or whose page/browser died; a 30-second background sweep runs for the process lifetime. |
| Chromium | Launch adds `--disable-dev-shm-usage`; the page gets an explicit navigation timeout. |
| Readiness | `/ready` now reports `active_browser_sessions` alongside database and streaming capability. |
| Observability | One structured `http_request` line per API call with method, path, status, duration and request id; health/readiness are excluded. |
| Frontend | Review card and confirmation copy no longer imply ApplyPilot can submit; the action is "Continue to manual submission" and the handoff card records the user's own submission. |

No migration was added: Stage G changes behaviour and configuration, not the
database schema.

## 2. Submission policy (authoritative)

1. ApplyPilot fills safe fields only. It has no click/submit primitive in the
   field-action allow-list, the adapter contract or the Playwright driver.
2. Approval is explicit and fingerprint-bound. Editing an answer or the resume
   invalidates it.
3. `POST /applications/{id}/submit` validates approval and returns the manual
   steps; it never performs a browser action. The status becomes
   `needs_user_action`.
4. `POST /applications/{id}/mark-submitted` requires `confirmed: true`
   (otherwise `409 submission_not_confirmed`). Only this call records
   `SUBMITTED`, `stage=applied` and `submitted_at`.
5. Navigation, a disappearing form, or a status change is never submission
   evidence.
6. Evidence is sanitised: the confirmation URL keeps origin+path only, the
   reference is truncated, and logs contain booleans/ids only.
7. CAPTCHA, OTP, MFA, passwords and legal declarations stay manual.

## 3. Security boundaries and audit results

**Authentication / OAuth (reviewed, existing behaviour confirmed):**

* Google login uses Authlib's authorization-code flow with a signed,
  HttpOnly, `SameSite=Lax`, short-lived state cookie; `MismatchingStateError`
  is rejected and only a fixed error identifier is forwarded to the frontend.
* The callback never renders a JWT. It issues a 256-bit single-use code stored
  only as SHA-256 with a <=60s TTL; redemption is one conditional `UPDATE`.
* JWT is HS256 with required `exp`/`sub`, a `type=access` check, and a
  `WWW-Authenticate` 401 envelope. Expired/malformed/wrong-type tokens are
  rejected before any data access.
* Production refuses the development JWT secret, requires a 32+ char
  `OAUTH_SESSION_SECRET`, and requires HTTPS redirect URIs.

**CSRF / cookies:** backend auth is a bearer token, not a cookie, so classic
CSRF does not apply to the API. Browser mutations go through the Next.js BFF,
which rejects non-GET requests whose `Origin` host differs from `Host`, and the
session cookie is `HttpOnly`, `SameSite=Lax`, `Secure` in production. The
signed OAuth state cookie is separate and single-flow.

**Authorization / IDOR:** every application, resume, job, missing-information,
intervention, browser-session and workflow route resolves records through the
authenticated user. Cross-user access returns 404 (not 403) to avoid confirming
existence; this is covered by the application, browser-session, human-loop,
workflow and resume suites.

**CORS:** explicit origins only, credentials enabled, and production now
requires HTTPS origins that are not loopback/wildcard. The same list is the
WebSocket `Origin` allow-list.

**Errors and logs:** unexpected exceptions return
`{"detail": "Internal server error.", "code": "internal_error"}`; validation
errors never echo input values; JSON logs redact key names containing
token/secret/password/authorization and redact `code|state|token` query values
(including uvicorn access lines). Request logging excludes query strings and
bodies.

**Browser isolation (reviewed, existing behaviour confirmed):**

* Application URL is validated statically (public http(s), standard port, no
  credentials/private/loopback/link-local/reserved hosts) and dynamically
  (every resolved address must be global) before Chromium opens.
* Every navigation request passes the origin policy; popups/new pages are
  checked too; leaving the employer/platform boundary marks the session failed
  and closes the browser.
* Sessions are one-per-application and one-live-per-user
  (`APPLICATION_BROWSER_MAX_ACTIVE_PER_USER`), owned by user + application,
  expire after the TTL, and are closed on pause/cancel/expiry/shutdown.
* Closing the view releases the ticket attachment but never cancels the
  browser; the session remains owned and reconciled.
* Tickets are hashed, single use, bound, TTL-limited, issuance rate-limited and
  consumed atomically. No CDP endpoint, cookie, storage state, URL or path is
  exposed.

**Residual browser risk:** DNS is validated before launch, but Chromium resolves
the host again. A determined rebinding attack between validation and navigation
is not fully eliminated; the navigation policy still bounds *URLs*, not resolved
IPs. Mitigate with egress firewall rules and a resolver you control. This is
documented rather than silently claimed as solved.

## 4. Browser session reliability

* **Idle cleanup:** a 30s sweep closes expired/dead handles even if no request
  touches the row; `/ready` exposes the live count.
* **Crash detection:** `is_alive()` checks page closure and browser connection;
  the sweep closes the handle, and the next API read reconciles the persisted
  row to `expired`/`failed`.
* **Restart:** in-memory handles and tickets are gone after a restart. Persisted
  rows are reconciled to expired on the next read; the user is told to reopen
  the page. No submission is ever inferred from this.
* **Duplicates:** `start` is idempotent while live; `SELECT ... FOR UPDATE` plus
  the manager lock prevent two browsers for one application; the per-user limit
  is enforced after reconciling dead rows.
* **Graceful shutdown:** the lifespan cancels the sweeper, closes every browser,
  detaches CDP and disposes the DB engine.
* **No distributed state:** Redis/shared tickets are **not** implemented. A
  multi-worker deployment remains unsafe.
* **Duplicate preparation:** two concurrent `analyze` calls for the same job can
  both read "no application yet". The unique `(user_id, job_id)` constraint
  rejects the loser; the service now rolls back, adopts the winner's row and
  returns it instead of raising a 500. No second application row is created.

### 4.1 Migration design: shared browser state and a multi-worker API

This is a **design only** - nothing below is implemented. It is the smallest
sequence that would let the API run more than one process without breaking
single-owner browser semantics.

**Target shape**

```
Nginx ──> api-1..api-N (stateless HTTP + WebSocket entry)
               │  registry lookups + RPC
               ▼
            Redis (session ownership, tickets, worker heartbeats)
               │  internal, authenticated, loopback/VPC only
               ▼
        browser-worker-1..M (one Chromium per live session)
```

**Redis keys** (all values are ids/digests, never answers or cookies)

| Key | Type | TTL | Purpose |
| --- | --- | --- | --- |
| `ap:bsession:{session_id}` | hash | session TTL + 60s | `user_id`, `application_id`, `worker_id`, `state`, `expires_at` |
| `ap:user:{user_id}:sessions` | set | session TTL | per-user limit; added/removed with a Lua script so the check is atomic |
| `ap:ticket:{sha256}` | string | ticket TTL (45s) | consumed with `GETDEL` so replay is impossible across workers |
| `ap:worker:{worker_id}:heartbeat` | string | 30s, refreshed every 10s | liveness of a browser worker |

**Ownership protocol**

1. API acquires `SET ap:bsession:{id} NX` with the user/application payload. A
   second request either gets the existing owner (idempotent) or is refused.
2. The API runs a Lua check against `ap:user:{id}:sessions` and the configured
   limit before creating the worker session. Dead sessions are removed first.
3. The assigned worker opens Chromium and returns a worker-scoped handle id.
   The API never receives a CDP URL, cookie or storage state.
4. A view ticket is minted in Redis (`SET NX` + 45s TTL). The WebSocket endpoint
   resolves `worker_id` from the registry and proxies frames to the worker over
   an authenticated internal connection, so the browser origin stays the API.
5. Every close path (user stop, cancel, TTL, crash, shutdown) deletes the hash,
   removes the user-set entry and asks the worker to close; all steps are
   idempotent.

**Failure handling**

* Worker heartbeat expires -> API marks sessions failed, revokes tickets, closes
  views and reconciles the persisted row to `expired`/`failed`. The user is told
  to reopen the page; no submission is ever inferred.
* API worker restart -> no browser state is lost because the registry owns it;
  the next request re-attaches to the same worker/session.
* Redis unavailable -> readiness fails (503). Do **not** silently fall back to
  in-process state, or split-brain ownership returns.

**Migration order**

1. Extract `PersistentBrowserDriver` behind an RPC protocol (same handle
   contract) and add the worker process. One host, one worker, no behaviour
   change.
2. Add the Redis registry behind `BROWSER_REGISTRY=memory|redis`; keep `memory`
   as the default for local development and tests.
3. Run two API processes against one worker and prove ticket single-use,
   per-user limits, crash cleanup and WebSocket routing.
4. Add a second worker host; add health-based routing and drain-on-deploy.
5. Load/soak test before raising `--workers` above 1.

**Trade-offs:** Redis becomes a hard dependency and a single point of failure
for browser features (persist it, or run Redis Sentinel/managed Redis). A remote
browser worker adds network hops to every frame and needs its own authentication
and firewall. Neither cost is justified for the current single-host pilot, which
is why this stage keeps `--workers 1` and documents the limitation instead of
claiming horizontal scalability.

## 5. Monitoring and operations

* `/api/v1/health` - liveness, no dependencies.
* `/api/v1/ready` - DB `SELECT 1`, streaming capability, active browser count;
  503 when the database is unavailable.
* Logs: JSON on stdout with `timestamp`, `level`, `request_id`, `stage`,
  `duration_ms`, `status_code` and safe ids. No tokens, cookies, answers or
  form contents.
* Useful greps: `stage=http_request`, `stage=browser_session_start`,
  `stage=browser_session_close`, `stage=browser_sweep`,
  `stage=application_submission_confirmed`, `error_class`.
* Suggested alerts: readiness 503 for 2 minutes; `error_class` rate spike;
  `active_browser_sessions` at the per-user/process ceiling; Chromium RSS;
  disk usage of `var/resumes` and screenshots.

## 6. Deployment guide (Ubuntu + systemd + Nginx + Neon)

The commands below assume Ubuntu 22.04/24.04, a dedicated `applypilot` user,
`/opt/applypilot` and a Neon PostgreSQL database. They install a **fresh**
virtualenv on the server; the developer Conda environment is not used in
production.

### 6.1 Packages and layout

```bash
sudo apt update
sudo apt install -y python3.10 python3.10-venv nginx certbot python3-certbot-nginx git
sudo useradd --system --create-home --shell /usr/sbin/nologin applypilot
sudo mkdir -p /opt/applypilot /etc/applypilot /var/lib/applypilot/resumes \
  /var/lib/applypilot/screenshots /var/lib/applypilot/ms-playwright
sudo chown -R applypilot:applypilot /opt/applypilot /var/lib/applypilot
sudo -u applypilot git clone <REPO_URL> /opt/applypilot
sudo -u applypilot python3.10 -m venv /opt/applypilot/.venv
sudo -u applypilot /opt/applypilot/.venv/bin/pip install --upgrade pip
sudo -u applypilot /opt/applypilot/.venv/bin/pip install -r /opt/applypilot/backend/requirements.txt
# Install browsers into the SAME path the systemd unit uses. Playwright's
# default cache is $HOME/.cache/ms-playwright, and the unit sets HOME to
# /var/lib/applypilot; installing with a different HOME silently makes the
# service unable to launch Chromium.
sudo -u applypilot env HOME=/var/lib/applypilot \
  PLAYWRIGHT_BROWSERS_PATH=/var/lib/applypilot/ms-playwright \
  /opt/applypilot/.venv/bin/python -m playwright install --with-deps chromium
```

### 6.2 Secrets and environment

Create `/etc/applypilot/backend.env` (mode `0600`, owner `applypilot`) from
`backend/.env.example`. Never commit it or paste real values into a ticket.

```bash
sudo install -m 600 -o applypilot -g applypilot /dev/null /etc/applypilot/backend.env
# Fill it with: DATABASE_URL (Neon, sslmode=require), JWT_SECRET_KEY,
# OAUTH_SESSION_SECRET, GOOGLE_CLIENT_ID/SECRET, GOOGLE_REDIRECT_URI,
# GMAIL_TOKEN_ENCRYPTION_KEY, GMAIL_REDIRECT_URI,
# APPLICATION_BROWSER_* limits, BACKEND_CORS_ORIGINS=["https://app.example.com"],
# AUTH_FRONTEND_REDIRECT_URI=https://app.example.com/auth/callback,
# RESUME_STORAGE_DIR=/var/lib/applypilot/resumes,
# APPLICATION_SCREENSHOT_DIR=/var/lib/applypilot/screenshots.
```

Generate secrets on the server:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # JWT
python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # OAuth session
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Register the exact HTTPS callbacks in Google Cloud Console. A mismatch is the
most common cause of `state` failures.

### 6.3 Migrations

```bash
sudo -u applypilot bash -c 'cd /opt/applypilot/backend && \
  set -a && . /etc/applypilot/backend.env && set +a && \
  /opt/applypilot/.venv/bin/alembic upgrade head'
```

Take a Neon branch/snapshot before every migration. Roll back a migration only
with a tested `alembic downgrade <rev>` after restoring the snapshot.

### 6.4 systemd units

`/etc/systemd/system/applypilot-backend.service`:

```ini
[Unit]
Description=ApplyPilot AI backend
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=applypilot
Group=applypilot
WorkingDirectory=/opt/applypilot/backend
EnvironmentFile=/etc/applypilot/backend.env
Environment=HOME=/var/lib/applypilot
Environment=PLAYWRIGHT_BROWSERS_PATH=/var/lib/applypilot/ms-playwright
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/applypilot/.venv/bin/uvicorn app.main:app \
  --host 127.0.0.1 --port 8000 --workers 1 \
  --proxy-headers --forwarded-allow-ips=127.0.0.1
Restart=on-failure
RestartSec=5
KillSignal=SIGTERM
TimeoutStopSec=30
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/applypilot

[Install]
WantedBy=multi-user.target
```

`--workers 1` is mandatory today: browser sessions, view tickets and active
views are process-local. Do not raise it without a shared registry.

`/etc/systemd/system/applypilot-frontend.service`:

```ini
[Unit]
Description=ApplyPilot AI frontend
After=network-online.target applypilot-backend.service

[Service]
Type=simple
User=applypilot
WorkingDirectory=/opt/applypilot/frontend
EnvironmentFile=/etc/applypilot/frontend.env
Environment=NODE_ENV=production
ExecStart=/usr/bin/npm start -- -p 3000 -H 127.0.0.1
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Create `/etc/applypilot/frontend.env` (mode `0600`, owner `applypilot`). The
`NEXT_PUBLIC_*` values are baked in at build time, so they must be present when
`npm run build` runs:

```bash
sudo install -m 600 -o applypilot -g applypilot /dev/null /etc/applypilot/frontend.env
sudo -u applypilot tee /etc/applypilot/frontend.env >/dev/null <<'EOF'
NEXT_PUBLIC_API_BASE_URL=https://api.example.com/api/v1
SESSION_COOKIE_SECURE=true
EOF
sudo -u applypilot bash -c 'cd /opt/applypilot/frontend && set -a && \
  . /etc/applypilot/frontend.env && set +a && npm ci && npm run build'
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now applypilot-backend applypilot-frontend
sudo systemctl status applypilot-backend --no-pager
sudo journalctl -u applypilot-backend -f
```

### 6.5 Nginx + TLS

```bash
sudo certbot --nginx -d app.example.com -d api.example.com
```

`/etc/nginx/conf.d/applypilot.conf` (server block for `api.example.com`):

```nginx
limit_req_zone $binary_remote_addr zone=applypilot_auth:10m rate=10r/m;

server {
    listen 443 ssl http2;
    server_name api.example.com;

    ssl_certificate     /etc/letsencrypt/live/api.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/api.example.com/privkey.pem;

    client_max_body_size 12m;
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Content-Type-Options nosniff always;
    add_header X-Frame-Options DENY always;
    add_header Referrer-Policy no-referrer always;

    location = /api/v1/auth/login/exchange {
        limit_req zone=applypilot_auth burst=5 nodelay;
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Live browser WebSocket: upgrade + no buffering, no ticket in access logs.
    location /api/v1/browser-sessions/view/ws {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;
        proxy_read_timeout 3600s;
        access_log off;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }
}
```

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

Allow 22/80/443 only; keep 3000/8000/Chromium on loopback. Verify with
`sudo nginx -t && sudo systemctl reload nginx`.

### 6.6 Logs, health and backups

* `journalctl -u applypilot-backend` (JSON lines). Configure
  `/etc/systemd/journald.conf` (`SystemMaxUse=1G`) or a logrotate rule if you
  also write files.
* Uptime checks: `GET https://api.example.com/api/v1/health` (liveness) and
  `/api/v1/ready` (DB + streaming), every 30s from outside the host.
* Database: enable Neon PITR/branching; take a `pg_dump` before migrations.
  Back up `GMAIL_TOKEN_ENCRYPTION_KEY` **with** the database or every stored
  Gmail token becomes undecryptable.
* Back up `/var/lib/applypilot/resumes` (encrypted, access-controlled) and the
  environment files. Test a restore quarterly.

### 6.7 Deploy, rollback, restart

```bash
# deploy
sudo -u applypilot git -C /opt/applypilot fetch --all --tags
sudo -u applypilot git -C /opt/applypilot checkout <TAG>
sudo -u applypilot /opt/applypilot/.venv/bin/pip install -r /opt/applypilot/backend/requirements.txt
sudo -u applypilot bash -c 'cd /opt/applypilot/backend && set -a && . /etc/applypilot/backend.env && set +a && /opt/applypilot/.venv/bin/alembic upgrade head'
sudo systemctl restart applypilot-backend applypilot-frontend
curl -fsS https://api.example.com/api/v1/ready

# rollback (code + schema)
sudo systemctl stop applypilot-backend applypilot-frontend
# restore the Neon snapshot/branch taken before the deploy
sudo -u applypilot git -C /opt/applypilot checkout <PREVIOUS_TAG>
sudo -u applypilot /opt/applypilot/.venv/bin/pip install -r /opt/applypilot/backend/requirements.txt
sudo systemctl start applypilot-backend applypilot-frontend
```

Restarting the backend intentionally drops every live browser and view ticket.
Users see `browser_session_expired` and must reopen the employer page; no
application is marked submitted.

Frontend rollback: keep the previous `npm run build` artifact (or redeploy the
previous tag) and restart the frontend unit.

## 7. Environment configuration summary

| Variable | Production requirement |
| --- | --- |
| `ENVIRONMENT` | `production` |
| `DEBUG` | `false` |
| `JWT_SECRET_KEY` | unique, 32+ bytes |
| `OAUTH_SESSION_SECRET` | unique, 32+ chars |
| `DATABASE_URL` | Neon `postgresql+asyncpg://...` |
| `BACKEND_CORS_ORIGINS` | exact HTTPS frontend origins, no wildcard/loopback |
| `AUTH_FRONTEND_REDIRECT_URI` | HTTPS frontend callback on an allowed origin |
| `GOOGLE_REDIRECT_URI` / `GMAIL_REDIRECT_URI` | exact registered HTTPS callbacks |
| `GMAIL_TOKEN_ENCRYPTION_KEY` | valid Fernet key, backed up |
| `RESUME_STORAGE_DIR` / `APPLICATION_SCREENSHOT_DIR` | writable, outside the web root |
| `APPLICATION_AUTOMATION_ENABLED` | `true` only if Playwright/Chromium are installed |
| `APPLICATION_BROWSER_MAX_ACTIVE_PER_USER` | `1` unless capacity is proven |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | keep short (default 60) |

## 8. Verification results (actual, this environment)

All commands were run in the existing `venv_rating` Conda environment
(Python 3.10.18) on Windows, and exit codes were captured directly.

| Check | Command | Result |
| --- | --- | --- |
| Backend suite, no external LLM | `python -m pytest -m "not live" -o addopts=""` | **458 passed, 4 skipped, 1 deselected**, exit 0, 120.93s |
| Live tests | excluded above | 1 `live` test deselected; it needs network/credentials and is skipped when absent |
| Real Chromium | five browser suites with `APPLYPILOT_RUN_BROWSER_TESTS=1` | **21 passed**, exit 0, 21.80s |
| Ruff lint | `ruff check .` | All checks passed, exit 0 |
| Ruff format check | `ruff format --check app tests` | 80 files would be reformatted, 88 formatted, exit 1 - **pre-existing** baseline: the same files fail at `HEAD` (verified with `git show HEAD:... \| ruff format --check -`). Stage G adds no new format violations; its new modules and test file pass. Repo-wide formatting is deliberately not applied because it would rewrite unrelated uncommitted work. |
| Alembic current | `alembic current` | `d4a7b2e9f613 (head)`, exit 0 |
| Alembic heads | `alembic heads` | one head, `d4a7b2e9f613`, exit 0 |
| Alembic check | `alembic check` | No new upgrade operations detected, exit 0 |
| Frontend typecheck | `npx tsc --noEmit` | exit 0 |
| Frontend lint | `npm run lint` | exit 0 |
| Frontend unit tests | `npx vitest run` | **16 passed** (3 files), exit 0 |
| Frontend build | `npm run build` | exit 0 (all routes built) |

Stage G tests added: `tests/test_phase2e_stage_g_hardening.py` (security
headers, readiness counts, invalid/expired JWT, cookie is not an API credential,
auth rate limiting, production CORS, browser sweep on crash/TTL, concurrent
start collision, sliding-window limiter) and the submission-policy tests plus
`test_concurrent_preparation_reuses_the_existing_application` in
`tests/test_applications.py`.

## 9. Known limitations and remaining production risks

**Implemented and verified:** submission removal, manual confirmation,
production CORS checks, security headers, request metrics, idle/crash sweeper,
readiness active-browser reporting, browser launch stability flag, concurrent
duplicate-preparation recovery (unique-constraint loser adopts the winner's row),
all checks above.

**Implemented but not fully verified:** the sweeper's effect on a real crashed
Chromium (tested with a fake handle); HSTS/header behaviour behind the production
proxy (headers are emitted by the app, but Nginx was not deployed here);
`--disable-dev-shm-usage` on the target host.

**Documented but not implemented:** Redis/shared ticket store, a dedicated
browser worker, multi-worker routing, application-level rate limiting (Nginx
`limit_req` is the recommended control), per-user quotas beyond one live
session, Chromium memory cgroups.

**Not addressed:** horizontal scalability; full DNS-rebinding elimination;
visual QA of the live viewport on real employer sites; load/soak testing;
WAF/DDoS protection.

**Cannot claim production-ready yet:** the API must run as exactly one process;
a restart drops live browsers and tickets; there is no shared session registry;
`ruff format` is not clean repo-wide. The deployment above is a single-host,
single-worker production shape, suitable for a small pilot - not a horizontally
scaled service.
