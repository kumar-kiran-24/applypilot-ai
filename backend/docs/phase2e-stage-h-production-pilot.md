# Phase 2E Stage H - Production deployment and pilot readiness

Stage H turns the Stage A-G build into a deployable single-host pilot. It closes
the security gaps Stage G documented, adds a real Ubuntu deployment
configuration, adds operational metrics, and measures the application under
concurrent load and real Chromium launches.

It does **not** add horizontal scalability. The API still runs exactly one
worker because live Chromium handles and view tickets are process-local.

Related documents:

* [`phase2e-stage-g-production-hardening.md`](./phase2e-stage-g-production-hardening.md)
  - submission policy, browser isolation, earlier verification.
* [`phase2e-stage-f-end-to-end.md`](./phase2e-stage-f-end-to-end.md) - the
  complete real-browser application workflow.
* [`deploy/README.md`](../../deploy/README.md) - short operator index.

## 1. Architecture

```
                    Internet (HTTPS only)
                            |
                       [ Nginx ]
        TLS, HSTS, rate limits, WS proxy, /metrics allow-list
                 /                          \
     /api/*  ->  FastAPI 127.0.0.1:8000      \  everything else -> Next.js 127.0.0.1:3000
                    |                         \
        SQLAlchemy/asyncpg                     BFF route handlers
                    |                          (HttpOnly session cookie)
             Neon PostgreSQL
                    |
        Playwright Chromium (one per live session,
        same process, launched with pinned DNS and a V8 heap cap)
```

* **One API worker.** `uvicorn --workers 1` is enforced by the systemd unit. A
  second worker would break browser ownership and single-use view tickets.
* **No Redis.** Revocation uses PostgreSQL; browser state stays in process.
  This is a deliberate single-host design, not an oversight.
* **Neon** is the only required external service. Gmail/Groq/Qdrant credentials
  are optional feature credentials.

## 2. Security changes in Stage H

| Area | Change |
| --- | --- |
| Logout / JWT revocation | Tokens now carry a required `jti`. `POST /api/v1/auth/logout` records it in `revoked_tokens`; the auth dependency rejects a revoked `jti` immediately, and a scheduled purge removes rows after the token's own expiry. The frontend logout route calls this before clearing the cookie. |
| API docs | `/docs`, `/redoc` and `/openapi.json` are disabled in production by configuration **and** returned as 404 by Nginx. Production startup rejects `API_DOCS_ENABLED=true`. |
| Security headers | Added `Content-Security-Policy` (`default-src 'none'`), `Permissions-Policy`, `Cross-Origin-Opener-Policy`, `Cross-Origin-Resource-Policy`, `X-Permitted-Cross-Domain-Policies`, `Cache-Control: no-store` for API responses, and removal of the `Server` header. The Next.js app adds its own CSP (see limitations). |
| API rate limiting | A per-principal sliding window (240/min default) applies to every API route except `/health`, `/ready`, `/metrics` and preflight. Authenticated calls are keyed by JWT `sub`; anonymous calls by peer address. |
| DNS rebinding | The validated address is passed to Chromium as `--host-resolver-rules=MAP <host> <ip>`, so the browser cannot re-resolve the starting host to a private address. The deploy installer also denies outbound RFC1918/link-local traffic. |
| Chromium resources | Per-session V8 heap cap (`APPLICATION_BROWSER_JS_HEAP_MB`, 512 default), a process-wide live-session ceiling (`APPLICATION_MAX_ACTIVE_BROWSER_SESSIONS`, 8 default) returning `503 browser_capacity_reached`, and systemd `MemoryMax=6G` / `MemoryHigh`. No renderer-collapsing flags: those would weaken site isolation. |
| Secrets | `scripts/generate_secrets.py` creates distinct secrets at 0600; `scripts/verify_secrets.py` validates production settings, distinctness, Fernet round-trip and file mode. Startup logs only 12-hex fingerprints of each secret. |
| Metrics | `GET /api/v1/metrics` exposes Prometheus text: request counts by route/status, latency histogram, 5xx counts, browser session gauges/opens/closes/launch failures, rate-limit counts and build info. Bearer `METRICS_TOKEN` required in production. |
| Error tracking | Optional `ERROR_WEBHOOK_URL` receives structured JSON for unhandled errors (class, method, route, request id, status). Exception messages and user data are excluded. No vendor SDK. |

The submission policy is unchanged and remains authoritative: ApplyPilot never
clicks Submit/Apply/Finish, and `SUBMITTED` is recorded only after an explicit
user confirmation (`confirmed: true`).

## 3. Deployment instructions (Ubuntu 22.04/24.04)

All files live in `deploy/`. The installer is idempotent and refuses to start
the services with invalid production settings.

```bash
# 1. DNS: point app.example.com at the host before requesting a certificate.
# 2. Install (root):
sudo DOMAIN=app.example.com \
     REPO_URL=git@github.com:you/applypilot.git \
     MONITORING_CIDR=10.0.0.0/8 \
     LETSENCRYPT_EMAIL=ops@example.com \
     bash deploy/scripts/install.sh
```

The script:

1. Installs Python, Nginx, Certbot, git, UFW and build tools.
2. Creates the `applypilot` system user and `/var/lib/applypilot`
   (`resumes/`, `screenshots/`, `ms-playwright/`).
3. Clones the repo to `/opt/applypilot`, builds the venv, installs
   `requirements.txt`, and installs Chromium **with the same `HOME` and
   `PLAYWRIGHT_BROWSERS_PATH`** the unit uses.
4. Creates `/etc/applypilot/{backend,frontend}.env` at mode `0600`.
5. Runs `verify_secrets.py --environment production` and aborts on failure.
6. Runs `alembic upgrade head`.
7. Builds the Next.js app with `npm ci && npm run build`.
8. Installs the systemd units and obtains a Let's Encrypt certificate
   (`certbot certonly --standalone`).
9. Installs the templated Nginx site (domain + monitoring CIDR substituted),
   runs `nginx -t`, reloads Nginx, configures UFW.
10. Starts both services and checks `/health` and `/ready` over HTTPS.

### 3.1 Environment configuration

`backend/.env.example` is the source of truth. Stage H adds:

| Variable | Production value | Purpose |
| --- | --- | --- |
| `API_DOCS_ENABLED` | `false` (or omit) | Public docs are rejected in production |
| `API_RATE_LIMIT_ENABLED` | `true` | Global per-principal limiter |
| `API_RATE_LIMIT_PER_MINUTE` | `240` | Per-user/IP budget; Nginx adds an edge limit |
| `METRICS_ENABLED` | `true` | Prometheus endpoint |
| `METRICS_TOKEN` | 32+ random chars | Bearer token for `/api/v1/metrics` |
| `ERROR_WEBHOOK_URL` | optional HTTPS URL | Structured unhandled-error reporting |
| `APPLICATION_MAX_ACTIVE_BROWSER_SESSIONS` | `8` | Process-wide Chromium ceiling |
| `APPLICATION_BROWSER_JS_HEAP_MB` | `512` | Per-session V8 heap cap |
| `APPLICATION_BROWSER_PIN_DNS` | `true` | Must stay true in production |

Production validation additionally rejects: the dev JWT secret, missing/short
OAuth secret, identical JWT/OAuth secrets, missing Gmail Fernet key, HTTP or
loopback CORS origins, HTTP redirect URIs, HTTP error webhooks, disabled DNS
pinning, disabled rate limiting, and metrics without a token.

### 3.2 Nginx and TLS

`deploy/nginx/applypilot.conf` provides:

* HTTP→HTTPS redirect with an ACME challenge exception.
* TLS 1.2/1.3, OCSP stapling, session tickets off, HSTS (`includeSubDomains`).
* `server_tokens off`, 12 MB body limit.
* `limit_req` zones for `/api/v1/auth/` (30 r/min) and `/api/` (10 r/s), plus a
  per-IP connection limit.
* WebSocket proxying only for `/api/v1/browser-sessions/view/ws`, with
  buffering off and a 3700 s read timeout (longer than the 3600 s view cap).
* `/api/v1/metrics` restricted to loopback plus `MONITORING_CIDR`.
* `/docs`, `/redoc`, `/openapi.json` return 404 even if the backend were
  misconfigured.
* Access logs use `$uri` (not `$request`) and add `X-Request-ID`, so query
  strings carrying one-time codes are never written to disk.

Renewal is Certbot's default systemd timer; `deploy/scripts/smoke-test.sh`
fails if the certificate has less than 14 days left.

### 3.3 systemd and resource limits

`deploy/systemd/applypilot-backend.service` runs one uvicorn worker with
`--proxy-headers --forwarded-allow-ips=127.0.0.1 --no-server-header`, restarts
on failure, and uses `KillMode=mixed` so uvicorn can close Chromium before the
cgroup is killed. Hardening: `NoNewPrivileges`, `PrivateTmp`,
`ProtectSystem=strict`, `ProtectHome`, `ReadWritePaths=/var/lib/applypilot`,
`RestrictSUIDSGID`, `LockPersonality`, `RestrictAddressFamilies`, `UMask=0077`.
`MemoryMax=6G` matches the 8-session ceiling measured below.

The frontend unit runs `next start`, writes only to `.next/cache`, and is capped
at 1 GB.

### 3.4 Firewall / egress

Inbound: SSH, 80, 443 only. Outbound allows the public internet. Optional
egress containment (`RESTRICT_EGRESS=1`) denies `10/8`, `172.16/12`,
`192.168/16` and `169.254/16` so a rebound hostname cannot reach internal
services; loopback stays open for Nginx→uvicorn and Next→uvicorn.

It defaults to **off** because cloud DNS resolvers and Neon private endpoints
are commonly inside `10/8`; enabling it blind can break name resolution. Check
`/etc/resolv.conf` and the database endpoint, allow those addresses, then enable
it as the second layer behind DNS pinning.

## 4. Monitoring and operations

### 4.1 Endpoints

* `GET /api/v1/health` - liveness; no dependencies.
* `GET /api/v1/ready` - `SELECT 1`, streaming capability, live browser/view
  counts, single-process note; 503 when the database is unreachable.
* `GET /api/v1/metrics` - Prometheus text (token-protected in production).

### 4.2 Prometheus scrape

```yaml
scrape_configs:
  - job_name: applypilot
    scheme: https
    metrics_path: /api/v1/metrics
    authorization:
      credentials_file: /etc/prometheus/applypilot_metrics_token
    static_configs:
      - targets: ["app.example.com"]
```

The token file must contain the same value as `METRICS_TOKEN`. Nginx only
permits the scrape from `MONITORING_CIDR`.

### 4.3 Suggested alerts

| Alert | Expression | For |
| --- | --- | --- |
| API 5xx rate | `sum(rate(applypilot_http_errors_total[5m])) > 0.05` | 5m |
| Readiness down | `probe_success{job="applypilot-ready"} == 0` | 2m |
| Browser capacity | `applypilot_browser_sessions_active >= 8` | 10m |
| Browser launch failures | `increase(applypilot_browser_launch_failures_total[10m]) > 2` | 0m |
| Rate-limit storm | `sum(rate(applypilot_rate_limited_total[5m])) > 5` | 10m |
| Latency | `histogram_quantile(0.95, sum(rate(applypilot_http_request_duration_seconds_bucket[5m])) by (le)) > 1` | 10m |
| Host memory | `node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes < 0.15` | 5m |
| Disk | `node_filesystem_avail_bytes{mountpoint="/"} < 10%` | 10m |
| Certificate | `probe_ssl_earliest_cert_expiry - time() < 14*86400` | 1h |
| Backup age | newest backup file older than 26h | 1h |

Logs are JSON on stdout/journald with `request_id`, `stage`, `status_code`,
`duration_ms`. Grep stages: `http_request`, `browser_session_open`,
`browser_session_close`, `browser_sweep`, `jwt_revoke`,
`jwt_revocation_purge`, `error_webhook`, `application_submission_confirmed`.
Never log credentials, cookies, refresh tokens, OTP/CAPTCHA values or answers;
the redaction filter covers key names and URL query parameters, and request
logging excludes bodies entirely.

## 5. Backup, recovery and rollback

### 5.1 Backup

```bash
sudo BACKUP_DIR=/var/backups/applypilot \
     BACKUP_PASSPHRASE_FILE=/root/.ap_backup_key \
     bash deploy/scripts/backup.sh
```

Produces `applypilot-<UTC>.tar.gz.enc` containing a custom-format `pg_dump` and
both environment files, encrypted with AES-256 (PBKDF2, 200k iterations), plus
a SHA-256 checksum. Retention defaults to 14 days. **Keep the passphrase
off-host.** Losing it loses both the database dump and the Gmail Fernet key, so
every stored refresh token becomes undecryptable.

Run it from a systemd timer or cron with `Persistent=true`, and copy the output
off-host (object storage with versioning is sufficient).

### 5.2 Restore

```bash
sudo systemctl stop applypilot-frontend applypilot-backend
sudo CONFIRM=YES BACKUP_FILE=/var/backups/applypilot/applypilot-...enc \
     BACKUP_PASSPHRASE_FILE=/root/.ap_backup_key \
     bash deploy/scripts/restore.sh
# Restore /etc/applypilot/backend.env from the same archive if the host was lost.
sudo systemctl start applypilot-backend applypilot-frontend
DOMAIN=app.example.com METRICS_TOKEN=... bash deploy/scripts/smoke-test.sh
```

Restoring the database without the matching Fernet key silently breaks Gmail;
the archive contains both, which is why they are encrypted together.

### 5.3 Rollback

```bash
sudo systemctl stop applypilot-backend applypilot-frontend
sudo -u applypilot git -C /opt/applypilot checkout <PREVIOUS_TAG>
sudo -u applypilot /opt/applypilot/.venv/bin/pip install -r /opt/applypilot/backend/requirements.txt
sudo -u applypilot bash -c 'cd /opt/applypilot/backend && set -a && . /etc/applypilot/backend.env && set +a && /opt/applypilot/.venv/bin/alembic downgrade <PREVIOUS_REVISION>'
sudo -u applypilot bash -c 'cd /opt/applypilot/frontend && npm ci && npm run build'
sudo systemctl start applypilot-backend applypilot-frontend
```

A backend restart intentionally drops every live browser and view ticket; users
see `browser_session_expired` and must reopen the employer page. No application
is marked submitted by a restart.

## 6. Verification results (this environment)

All commands ran in the existing `venv_rating` Conda environment (Python 3.10.18)
on Windows, with exit codes captured directly. **No Ubuntu server was available,
so Nginx, TLS, systemd, UFW and the real HTTPS/WebSocket path are not
server-verified.**

| Check | Command | Result |
| --- | --- | --- |
| Backend suite | `python -m pytest -m "not live" -o addopts=""` | **478 passed, 4 skipped, 1 deselected**, exit 0, 107.06s |
| Real Chromium suites | five browser suites with `APPLYPILOT_RUN_BROWSER_TESTS=1` | **21 passed**, exit 0, 23.46s |
| Ruff lint | `ruff check .` | All checks passed, exit 0 |
| Ruff format | `ruff format --check app tests` | Pre-existing baseline failure (unrelated files); Stage H files are format-clean |
| Alembic current/heads | `alembic current` / `heads` | `b8c1f4a2d907 (head)` |
| Alembic schema check | `alembic check` | No new upgrade operations detected, exit 0 |
| Frontend typecheck | `npx tsc --noEmit` | exit 0 |
| Frontend lint | `npm run lint` | exit 0 |
| Frontend unit tests | `npx vitest run` | **20 passed** (4 files), exit 0 |
| Frontend build | `npm run build` | exit 0, all routes built |
| Deployment scripts | `bash -n deploy/scripts/*.sh` | all four parse, exit 0 |
| Local server boot | uvicorn on 127.0.0.1:8099 with the dev database | `/health` 200, `/ready` 200 with `database.ok=true`, `/auth/me` 401, `/metrics` 200, security headers present, startup log contains fingerprints only (no secret values) |

### 6.1 Load test (in-process ASGI, SQLite)

`python scripts/load_test.py --users 20 --cycles 4`:

| Metric | Value |
| --- | --- |
| Requests | 320 (4 endpoints × 4 cycles × 20 users) |
| Failures | 0 |
| Throughput | 124.5 req/s |
| p50 / p95 / p99 | 146.0 / 262.3 / 344.4 ms |
| Max | 399.2 ms |
| Process RSS / threads | 162.1 MB / 10 |
| Readiness during run | 200, 0 active browsers |

Caveat: SQLite and ASGI transport, no network/Nginx/TLS; it measures request
handling and DB access, including the new per-request revocation lookup. It is
not evidence of internet-scale capacity.

### 6.2 Real Chromium launch benchmark

`python scripts/browser_benchmark.py --launches 3`:

| Metric | Value |
| --- | --- |
| Launch latency mean / min / max | 846.4 / 679.8 / 1031.2 ms |
| Process-tree RSS while open | ~350 MB (67.6 MB baseline) |
| Incremental per browser | ~283 MB |

At 8 concurrent sessions the browser tier is roughly 2.3-2.8 GB, which fits the
6 GB systemd ceiling with headroom for the API and page cache. Measure again on
the actual Linux host before raising the cap.

### 6.3 Reliability checks

Verified by automated tests:

* Idle/expired and crashed browser cleanup (sweeper, no request required).
* Per-user and per-process session limits, including the 503 capacity envelope.
* Idempotent start, explicit-restart conflict, crash/expiry reconciliation,
  backend-restart reconciliation to `expired` without inferring submission.
* Duplicate concurrent application preparation reuses one row.
* View ticket single use, TTL, origin and cross-user rejection; WebSocket
  disconnect leaves no active view and does not cancel the browser.
* Large resume rejection (413), interrupted database → `/ready` 503 while
  `/health` stays 200.
* Token revocation idempotency, per-session logout isolation, tokens without
  `jti` rejected.

## 7. End-to-end production simulation

The required 15-step workflow is covered by existing automated suites rather
than one new combined script:

| Steps | Coverage |
| --- | --- |
| 1. Authenticate | `test_auth_google.py` (OAuth state, one-time code, JWT, logout) |
| 2-4. Discover job, upload/match resumes | `test_jobs.py`, `test_resumes.py`, `test_job_matching.py` |
| 5-6. Evaluate eligibility and approve | `test_phase2d_auto_apply_eligibility.py` (21 tests) |
| 7-13. Launch browser, analyze, fill, intervene, re-analyze, review | `test_phase2e_stage_f_e2e_browser.py` with real Chromium and a local employer form |
| 14. User manually submits | The test acts as the user and clicks the employer button itself |
| 15. Explicit confirmation | `mark-submitted` requires `confirmed: true`; workflow shows `submission_confirmed` |

The Stage F browser test asserts the page says `not-submitted` after filling,
after human intervention, and after the final review, and that no product code
clicked Submit. **These steps were not executed as one single Stage H script**;
that combination is an acknowledged gap (see limitations).

## 8. Remaining limitations and risks

1. **Single process only.** One API worker is a hard requirement until shared
   browser state exists. Horizontal scaling is not implemented.
2. **No server-verified deployment.** The Nginx/systemd/TLS/UFW configuration is
   committed and syntax-checked, but no Ubuntu host was available in this
   environment. Treat the first server install as a change window with the
   smoke test as the gate.
3. **DNS pinning covers the starting host.** Redirect origins allowed by the
   navigation policy are still resolved by Chromium; the egress firewall is the
   second layer. A fully controlled resolver/proxy is future work.
4. **Rate limiting is per worker.** With the mandated single worker this is
   correct, but it is not a cluster quota; Nginx `limit_req` is the edge tier.
5. **Frontend CSP allows inline scripts/styles.** Next.js injects inline
   bootstrap code; a nonce-based policy needs middleware and is not implemented.
6. **No WAF/DDoS protection** beyond Nginx limits and UFW.
7. **JWT revocation adds one indexed DB lookup per authenticated request.**
   Acceptable at pilot scale; measure before raising traffic.
8. **No key rotation.** Rotating `JWT_SECRET_KEY` invalidates all sessions at
   once (acceptable, but plan it). Rotating the Gmail key requires re-encrypting
   refresh tokens.
9. **Error webhook is fire-and-forget.** There is no retry queue; pair it with
   log-based alerting.
10. **Browser memory numbers come from the Windows development host.** Re-measure
    on the Linux server (the benchmark script is included).
11. **No load testing through Nginx/TLS** and no multi-host soak test.
12. **No automated deployment test in CI.** The installer is designed to be
    re-runnable but has only been parsed, not executed here.

## 9. Classification

**Implemented and verified**

* JWT revocation and logout invalidation (migration + dependency + endpoint +
  frontend call + tests).
* Production docs lockdown (config validator + app config + Nginx 404).
* Security headers (CSP, Permissions-Policy, COOP/CORP, no-store, server header
  removal).
* Global per-principal API rate limiting with metric/counter coverage.
* DNS pinning helper integrated into the Playwright driver, with unit tests.
* Chromium heap cap and process-wide session ceiling with 503 envelope.
* Metrics endpoint, registry, tests and readiness gauges.
* Secret generation/verification scripts and fingerprint-only startup logging.
* systemd units, Nginx config, firewall, backup/restore/rollback scripts
  (syntax-checked).
* Stage H backend/frontend tests; full suite, Chromium suite, lint, typecheck,
  build, migration check.
* In-process load test and real Chromium launch/memory benchmark.

**Implemented but not verified**

* Nginx TLS/WebSocket proxying, systemd sandboxing, UFW egress rules, Certbot
  issuance/renewal (no server available).
* Capacity refusal through the full HTTP stack with a real manager (unit-level
  only).
* Frontend CSP behavior in a real production browser.

**Documented only**

* Redis/browser-worker migration design (Stage G §4.1).
* Prometheus/alertmanager deployment (scrape and alert definitions provided,
  not installed).
* Nonce-based frontend CSP.

**Not implemented**

* Horizontal scaling / multi-worker API.
* Controlled egress proxy or internal resolver.
* WAF/DDoS protection.
* Automated CI deployment or a single combined 15-step Stage H E2E script.
* Error-tracking vendor integration (the webhook is vendor-neutral).

## 10. Pilot readiness assessment

The application is ready for a **single-host, single-worker pilot** with a small
number of concurrent users, provided the first install follows the runbook and
the smoke test passes. The security posture is materially stronger than Stage G:
logout is immediate, docs are not public, DNS rebinding is pinned for the
employer host, browser memory is bounded, and operational metrics exist.

It is **not** production-ready for horizontal scale, and no claim of server
verification is made: Nginx, TLS, systemd and the real HTTPS/WebSocket path must
be exercised on the target host before opening the pilot. The first deployment
is the remaining verification step, not a formality.
