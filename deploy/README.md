# ApplyPilot AI - deployment assets

Everything here targets a single Ubuntu 22.04/24.04 host running Nginx, one
FastAPI worker and one Next.js server, with Neon PostgreSQL and Playwright
Chromium. Architecture and pilot limitations:
[`backend/docs/phase2e-stage-h-production-pilot.md`](../backend/docs/phase2e-stage-h-production-pilot.md).

| Path | Purpose |
| --- | --- |
| `systemd/applypilot-backend.service` | One uvicorn worker, sandboxed, 6 GB memory ceiling |
| `systemd/applypilot-frontend.service` | `next start` on 127.0.0.1:3000 |
| `nginx/applypilot.conf` | TLS, HSTS, rate limits, WebSocket proxy, metrics allow-list, query-free access log |
| `scripts/install.sh` | Packages, user, directories, venv, Chromium, secrets check, migration, build, TLS, Nginx, UFW |
| `scripts/smoke-test.sh` | Post-deploy HTTPS/health/headers/CORS/docs/metrics/TLS/WS/log checks |
| `scripts/backup.sh` | Encrypted `pg_dump` + environment files |
| `scripts/restore.sh` | Destructive restore (`CONFIRM=YES`) |

Filesystem layout created by the installer:

| Path | Owner | Mode | Contents |
| --- | --- | --- | --- |
| `/opt/applypilot` | `applypilot` | 0755 | git checkout, `.venv`, `frontend/` build |
| `/etc/applypilot` | `applypilot` | 0700 | `backend.env`, `frontend.env` (0600) |
| `/var/lib/applypilot` | `applypilot` | 0750 | `resumes/`, `screenshots/`, `ms-playwright/`, service `HOME` |
| `/var/www/certbot` | `root` | 0755 | ACME HTTP-01 challenge files |
| `/var/log/nginx/applypilot.access.log` | `www-data` | 0640 | Query-string-free access log |

---

## 1. Ubuntu server prerequisites

* Ubuntu 22.04 LTS or 24.04 LTS, x86_64, 2 vCPU / 4 GB RAM minimum. Chromium is
  the dominant memory consumer: size the host at ~1 GB per
  `APPLICATION_MAX_ACTIVE_BROWSER_SESSIONS` beyond the base footprint (the
  backend unit caps the service at 6 GB).
* Root or sudo access, plus a DNS `A`/`AAAA` record for the staging hostname
  pointing at the server. **Certificate issuance fails until DNS resolves to
  this host.**
* Inbound TCP 22/80/443 reachable from the internet (80 is required for the
  HTTP-01 challenge and for the HTTP->HTTPS redirect).
* An SSH deploy key or a public repository URL for `REPO_URL`.
* A PostgreSQL database (Neon or self-hosted) reachable from the host, and
  Google OAuth credentials whose redirect URIs match the staging hostname.

The installer is idempotent: it creates the user, directories and files only
when missing, and never overwrites existing environment files.

## 2. Required system packages

Installed by `install.sh`:

```bash
sudo apt-get update
sudo apt-get install -y \
  python3 python3-venv python3-pip \
  nginx certbot git ufw curl ca-certificates \
  postgresql-client
```

Notes:

* `postgresql-client` provides `pg_dump`/`pg_restore`, which `backup.sh` and
  `restore.sh` require. Both scripts abort with an actionable message if the
  binaries are missing, and the installer fails if the package did not supply
  them.
  * If the database server is newer than the distro client (Neon runs 16/17,
    Ubuntu 22.04 ships 14), install the matching major instead:
    `sudo apt-get install -y postgresql-client-16`.
* Node.js 22 is installed from NodeSource because the Ubuntu archive Node is
  too old for Next.js 15. The installer only touches Node when the current
  major is below 20.
* Chromium's shared libraries are installed with
  `python -m playwright install-deps chromium` (root) — never install Playwright
  globally; it lives in `/opt/applypilot/.venv`.

## 3. Application user and directory setup

Created by the installer, in this order (order matters — the clone runs as the
service user and therefore needs a writable target):

```bash
sudo useradd --system --home-dir /var/lib/applypilot --no-create-home \
     --shell /usr/sbin/nologin applypilot

sudo install -d -m 0755 -o applypilot -g applypilot /opt/applypilot
sudo install -d -m 0700 -o applypilot -g applypilot /etc/applypilot
sudo install -d -m 0750 -o applypilot -g applypilot \
     /var/lib/applypilot /var/lib/applypilot/resumes \
     /var/lib/applypilot/screenshots /var/lib/applypilot/ms-playwright
sudo install -d -m 0755 -o root -g root /var/www/certbot
```

* The account is a *system* user with a nologin shell; the service runs as
  `applypilot` with `HOME=/var/lib/applypilot` (see §9).
* Secrets live in `/etc/applypilot` (0700/0600) and are never world-readable.
* Ownership is only changed for `/opt/applypilot` itself, and only when the
  top-level owner is wrong (e.g. after an earlier root-owned clone). No system
  directory is ever recursively re-owned.

## 4. Environment variable configuration

The installer creates empty `/etc/applypilot/backend.env` and
`frontend.env` (0600, owned by `applypilot`) and then pauses so you can fill
them. Template: [`backend/.env.example`](../backend/.env.example).

### Backend - `/etc/applypilot/backend.env`

```bash
sudo -u applypilot /opt/applypilot/.venv/bin/python \
  /opt/applypilot/backend/scripts/generate_secrets.py \
  --env-file /etc/applypilot/backend.env
```

That appends `JWT_SECRET_KEY`, `OAUTH_SESSION_SECRET`,
`GMAIL_TOKEN_ENCRYPTION_KEY` and `METRICS_TOKEN` without overwriting existing
values. Then set the deployment-specific keys:

```bash
ENVIRONMENT=production
DEBUG=false
DATABASE_URL="postgresql+asyncpg://USER:PASSWORD@HOST:5432/DBNAME?ssl=require"
APP_VERSION="0.1.0"

GOOGLE_CLIENT_ID="..."
GOOGLE_CLIENT_SECRET="..."
GOOGLE_REDIRECT_URI="https://staging.example.com/api/v1/auth/google/callback"
GMAIL_REDIRECT_URI="https://staging.example.com/api/v1/gmail/callback"
AUTH_FRONTEND_REDIRECT_URI="https://staging.example.com/auth/callback"
GMAIL_FRONTEND_REDIRECT_URI="https://staging.example.com/gmail/callback"
BACKEND_CORS_ORIGINS=["https://staging.example.com"]

# Writable paths: /opt/applypilot is read-only at runtime (ProtectSystem=strict).
RESUME_STORAGE_DIR="/var/lib/applypilot/resumes"
APPLICATION_SCREENSHOT_DIR="/var/lib/applypilot/screenshots"

# Browser tier: keep these safeguards.
APPLICATION_AUTOMATION_ENABLED=true
APPLICATION_BROWSER_PIN_DNS=true
APPLICATION_MAX_ACTIVE_BROWSER_SESSIONS=4
GROQ_API_KEY="..."
METRICS_ENABLED=true
# METRICS_TOKEN is generated above; do not paste it into chat, tickets or git.
```

`ENVIRONMENT=production` makes the production validator mandatory: the installer
runs `scripts/verify_secrets.py --environment production` and refuses to
continue on placeholder secrets, loopback CORS origins, non-HTTPS redirect URIs
or a missing `METRICS_TOKEN`.

### Frontend - `/etc/applypilot/frontend.env`

```bash
NEXT_PUBLIC_API_BASE_URL="https://staging.example.com/api/v1"
SESSION_MAX_AGE_SECONDS="3600"
SESSION_COOKIE_SECURE="true"
```

`NEXT_PUBLIC_API_BASE_URL` **must be an absolute URL**: the Next.js server side
resolves it with `new URL()` for the BFF proxy, and the browser uses the same
origin for the live-view WebSocket. It is inlined at build time, so
`install.sh` sources this file before `npm run build`; changing it requires a
rebuild (`UPDATE=1` re-run).

## 5. Database setup and migrations

Create the database/role with your provider (Neon console, or
`CREATE ROLE`/`CREATE DATABASE` on a self-hosted server) and put the asyncpg URL
in `backend.env`. Then:

```bash
# What the installer runs, and what to run after any pull:
sudo -u applypilot bash -c '
  set -a; . /etc/applypilot/backend.env; set +a
  cd /opt/applypilot/backend && /opt/applypilot/.venv/bin/alembic upgrade head'

# Verify:
sudo -u applypilot bash -c '
  set -a; . /etc/applypilot/backend.env; set +a
  cd /opt/applypilot/backend
  /opt/applypilot/.venv/bin/alembic current
  /opt/applypilot/.venv/bin/alembic heads
  /opt/applypilot/.venv/bin/alembic check'
```

`alembic current` must show the same revision as `alembic heads`, and
`alembic check` must report "No new upgrade operations detected". Migrations
run before the frontend build and before the services restart, so a failed
migration stops the deploy.

## 6. Playwright and Chromium installation

`backend/requirements.txt` pins `playwright==1.64.1`, and the installer does the
rest in the service's own environment (never globally):

```bash
sudo /opt/applypilot/.venv/bin/python -m playwright install-deps chromium
sudo -u applypilot env HOME=/var/lib/applypilot \
  PLAYWRIGHT_BROWSERS_PATH=/var/lib/applypilot/ms-playwright \
  /opt/applypilot/.venv/bin/python -m playwright install chromium
```

The browser revision is tied to the pinned driver, so do not upgrade
`playwright` without re-running `playwright install chromium` (an `UPDATE=1`
installer run does exactly that).

Verify launch with the same environment the unit uses:

```bash
sudo -u applypilot env HOME=/var/lib/applypilot \
  PLAYWRIGHT_BROWSERS_PATH=/var/lib/applypilot/ms-playwright \
  /opt/applypilot/.venv/bin/python - <<'PY'
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(args=["--no-sandbox"])
    print("Chromium OK:", b.version)
    b.close()
PY

# And through the deployed service:
curl -fsS https://staging.example.com/api/v1/ready | grep -o '"browser_streaming_available":[a-z]*'
```

`--no-sandbox` is only for this one-off check under `ProtectHome`; the service
itself launches Chromium through the application's launcher, which uses
unprivileged user namespaces (`NoNewPrivileges=true`).

## 7. Nginx setup

`/etc/nginx/sites-available/applypilot.conf` is generated from
`deploy/nginx/applypilot.conf` with `__DOMAIN__` and `__MONITORING_CIDR__`
substituted:

```bash
sudo sed -e "s|__DOMAIN__|staging.example.com|g" \
         -e "s|__MONITORING_CIDR__|10.0.0.0/8|g" \
    /opt/applypilot/deploy/nginx/applypilot.conf \
    | sudo tee /etc/nginx/sites-available/applypilot.conf >/dev/null
sudo ln -sf /etc/nginx/sites-available/applypilot.conf /etc/nginx/sites-enabled/applypilot.conf
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
```

Key behaviours:

* **Access log without query strings.** A named `applypilot` format logs
  `"$request_method $uri $server_protocol"`, status, `$request_time`,
  `$upstream_response_time` and `$request_id`. `$uri` never contains arguments,
  so one-time browser-view tickets (`?ticket=...`) and OAuth codes are never
  written to disk. `$request`/`$request_uri` must never be added to the format.
* **Security headers are emitted exactly once.** Nginx sets HSTS,
  `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`,
  `Permissions-Policy`, `Cross-Origin-Opener-Policy`,
  `Cross-Origin-Resource-Policy` and `X-Permitted-Cross-Domain-Policies`, and
  strips the upstream copies with `proxy_hide_header`. `Content-Security-Policy`
  is deliberately passed through: the API and the Next.js pages need different
  policies.
* `/docs`, `/redoc` and `/openapi.json` return 404 at the edge.
* `/api/v1/metrics` is allow-listed to loopback and `MONITORING_CIDR`; everyone
  else gets 403 before the request reaches FastAPI.
* The single backend worker must stay: browser handles and view tickets are
  process-local.

## 8. HTTPS and certificate renewal

The installer issues the certificate with the **webroot** authenticator, which
never binds port 80 itself:

```bash
# What install.sh does on a fresh host:
#  1. enable a temporary HTTP-only ACME vhost for the hostname
#  2. certbot certonly --webroot --webroot-path /var/www/certbot \
#       --cert-name staging.example.com -d staging.example.com
#  3. install the real TLS vhost and reload Nginx
```

Renewal is fully automatic:

* `/etc/letsencrypt/renewal/<domain>.conf` records `authenticator = webroot`
  and `webroot_path = /var/www/certbot`, so `certbot renew` serves the challenge
  through the running Nginx (port 80) instead of stopping it.
* `/etc/letsencrypt/renewal-hooks/deploy/10-reload-nginx.sh` runs only after a
  successful renewal: `nginx -t && systemctl reload nginx`.
* `certbot.timer` performs the twice-daily check (Ubuntu's `certbot` package
  ships and enables it; `install.sh` enables and starts it explicitly).

Verify the whole chain:

```bash
systemctl list-timers certbot.timer --all
systemctl status certbot.timer --no-pager
cat /etc/letsencrypt/renewal/staging.example.com.conf | grep -E 'authenticator|webroot_path'
sudo certbot renew --dry-run
sudo sh -c 'echo "renew-hook smoke" && /etc/letsencrypt/renewal-hooks/deploy/10-reload-nginx.sh'
sudo openssl x509 -in /etc/letsencrypt/live/staging.example.com/cert.pem -noout -dates
sudo journalctl -u certbot -n 50 --no-pager
```

`certbot renew --dry-run` uses the Let's Encrypt *staging* server, so it costs
no rate limit and is the correct way to prove renewal works. Never request a
production certificate from a laptop; do it on the staging host only.

If `install.sh` warns that the renewal configuration still says
`authenticator = standalone` (a host installed by an older script), switch it
once:

```bash
sudo certbot certonly --webroot -w /var/www/certbot \
     --cert-name staging.example.com -d staging.example.com --force-renewal
```

## 9. Backend and frontend systemd services

```bash
sudo install -m 0644 /opt/applypilot/deploy/systemd/applypilot-backend.service /etc/systemd/system/
sudo install -m 0644 /opt/applypilot/deploy/systemd/applypilot-frontend.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now applypilot-backend applypilot-frontend
```

Backend (details in the unit file):

* `--workers 1` is mandatory: browser handles and view tickets live in process
  memory, so a second worker breaks ownership and single-use tickets.
* `EnvironmentFile=/etc/applypilot/backend.env`, `HOME=/var/lib/applypilot`,
  `PLAYWRIGHT_BROWSERS_PATH=/var/lib/applypilot/ms-playwright`.
* `ProtectSystem=strict` makes `/opt/applypilot` read-only; only
  `/var/lib/applypilot` is writable (that is why `RESUME_STORAGE_DIR` and
  `APPLICATION_SCREENSHOT_DIR` must point there).
* `MemoryMax=6G`; size it against `APPLICATION_MAX_ACTIVE_BROWSER_SESSIONS`.

Frontend:

* `ProtectHome=true` hides `/home` and `/root`, so `HOME=/var/lib/applypilot`
  (a managed, writable path) is set explicitly.
* `ExecStartPre=+/usr/bin/install -d ... /opt/applypilot/frontend/.next/cache`
  creates the cache directory before startup — `next start` writes there for
  image optimisation and fails if it is missing or read-only.
* `ReadWritePaths=/var/lib/applypilot /opt/applypilot/frontend/.next`: nothing
  else in the checkout is writable at runtime.

Operations:

```bash
sudo systemctl status applypilot-backend applypilot-frontend --no-pager
sudo journalctl -u applypilot-backend -n 100 --no-pager
sudo journalctl -u applypilot-frontend -n 100 --no-pager
sudo systemctl restart applypilot-backend   # frontend needs no restart for API changes
```

## 10. Backup and restore

```bash
# One-time: create a passphrase stored OUTSIDE the backups.
sudo install -m 0600 -o root -g root /dev/null /root/.ap_backup_key
sudo sh -c 'openssl rand -base64 48 > /root/.ap_backup_key'

# Nightly backup (cron example: 02:15 UTC).
sudo BACKUP_DIR=/var/backups/applypilot \
     BACKUP_PASSPHRASE_FILE=/root/.ap_backup_key \
     bash /opt/applypilot/deploy/scripts/backup.sh
```

`backup.sh` checks for `pg_dump`, `openssl` and `tar` up front and exits with an
install hint if one is missing; it also validates the passphrase file and
`backend.env` before touching the database. Output is a single encrypted
`applypilot-<stamp>.tar.gz.enc` (+ `.sha256`) containing the custom-format dump
and both environment files, so the Gmail Fernet key always matches the restored
refresh tokens. Copy backups off-host; retention defaults to 14 days.

```bash
# Dependency/behaviour checks without writing anything real:
pg_dump --version && pg_restore --version
BACKUP_PASSPHRASE_FILE=/root/.ap_backup_key bash /opt/applypilot/deploy/scripts/backup.sh

# Restore (DESTRUCTIVE - replaces matching database objects):
sudo systemctl stop applypilot-backend applypilot-frontend
sudo CONFIRM=YES BACKUP_FILE=/var/backups/applypilot/applypilot-<stamp>.tar.gz.enc \
     BACKUP_PASSPHRASE_FILE=/root/.ap_backup_key \
     bash /opt/applypilot/deploy/scripts/restore.sh
sudo systemctl start applypilot-backend applypilot-frontend
```

Restore the environment files from the same archive if the host lost them
(`tar -C /etc/applypilot -xzf <archive>/env.tar.gz`), then run the smoke test.
Test a restore into a scratch database quarterly.

## 11. Smoke tests

```bash
sudo DOMAIN=staging.example.com METRICS_TOKEN="$(sudo grep -m1 '^METRICS_TOKEN=' /etc/applypilot/backend.env | cut -d= -f2- | tr -d '\"')" \
     bash /opt/applypilot/deploy/scripts/smoke-test.sh
```

Ten checks, each printing `ok`/`FAIL`/`skip`; the script exits non-zero if any
check failed, so it can gate a deploy:

1. HTTP->HTTPS redirect.
2. `/health` 200 with a healthy payload; `/ready` 200 with `"status":"ready"`,
   `"name":"database","ok":true`, `"browser_streaming_available":true` and the
   browser-session counters.
3. `/docs`, `/redoc`, `/openapi.json` are 404.
4. Security headers present exactly once; the `Server` header carries no version
   (`server_tokens off` still emits a bare `Server: nginx`, which is expected —
   only `nginx/<version>`, `uvicorn` or `python` banners fail the check).
5. `/auth/me` 401 and the Google login redirect.
6. Same-origin CORS preflight succeeds; a foreign origin is not reflected.
7. `/api/v1/metrics` is not publicly readable: 403 (blocked by the Nginx
   allow-list) or 401 (FastAPI token required) both pass; 200 fails. When run on
   the host with `METRICS_TOKEN`, the token path is exercised against the
   loopback backend.
8. Certificate has more than 14 days left.
9. A foreign `Origin` WebSocket upgrade is refused with 403; an allowed origin
   with an invalid ticket does not stream and is not held open.
10. The access log contains no query strings.

Run it with `sudo` on the host so check 10 can read
`/var/log/nginx/applypilot.access.log`.

## 12. Safe application updates

The installer never modifies the checkout unless an update is requested, and it
never resets, cleans or stashes:

```bash
# Deploy the latest commit on the current branch (fast-forward only).
sudo DOMAIN=staging.example.com REPO_URL=git@github.com:you/applypilot.git \
     UPDATE=1 bash /opt/applypilot/deploy/scripts/install.sh
```

* Without `UPDATE=1` the working tree is left exactly as it is.
* With `UPDATE=1`, `git status --porcelain --untracked-files=no` must be empty.
  Any modified tracked file aborts the update with the file list and a clear
  message; nothing is discarded. Commit or revert those files first.
* The update is `git fetch --prune --tags` + `git merge --ff-only
  origin/<branch>`, so a diverged branch fails instead of creating a merge
  commit.
* Build output (`frontend/.next`, `node_modules`, `.venv`) is git-ignored and
  never blocks an update.
* Rollback is a checkout of the previous commit followed by a re-run:

```bash
sudo -u applypilot git -C /opt/applypilot log --oneline -5
sudo -u applypilot git -C /opt/applypilot checkout <previous-commit>
sudo DOMAIN=staging.example.com REPO_URL=git@github.com:you/applypilot.git \
     ASSUME_READY=1 bash /opt/applypilot/deploy/scripts/install.sh
```

(Use `git -C /opt/applypilot checkout <branch>` to return.) A rollback does not
revert database migrations; check `alembic history` before downgrading.

## 13. Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| `install.sh` stops at `git clone` with permission denied | `/opt/applypilot` pre-existed and is not owned by `applypilot`. Re-run the installer (it fixes the top-level owner) or `sudo chown -R applypilot:applypilot /opt/applypilot`. |
| `Refusing to update: ... has local modifications` | Intended safety stop. Commit/revert the listed files; nothing was changed by the installer. |
| Frontend unit fails with `Failed to set up mount namespacing` | `frontend/.next` is missing. Run `sudo -u applypilot bash -c 'cd /opt/applypilot/frontend && npm ci && npm run build'` and restart. |
| `next start` exits with `EACCES` on `.next/cache` | Cache dir/permissions: `sudo install -d -o applypilot -g applypilot -m 0750 /opt/applypilot/frontend/.next/cache`. |
| `/api/v1/ready` shows `browser_streaming_available: false` | Chromium not installed for the service user. Re-run the two `playwright install` commands in §6 with `HOME`/`PLAYWRIGHT_BROWSERS_PATH` exactly as in the unit. |
| Health works but pages 502 | `applypilot-frontend` is down or the build is missing: `journalctl -u applypilot-frontend -n 100`. |
| `nginx: [emerg] cannot load certificate` | The certificate directory does not exist yet; the installer issues it with the temporary ACME vhost (§8). Run `install.sh` rather than applying the config by hand. |
| `certbot renew` fails on port 80 | Renewal configuration still uses `standalone`. Switch it to webroot (§8). |
| `pg_dump: error: server version: 16.x; pg_dump version: 14.x` | Client older than server: `sudo apt-get install -y postgresql-client-16`. |
| `backup.sh`: `'pg_dump' is not installed` | `sudo apt-get install -y postgresql-client`. |
| Smoke test: `server header has no version` FAIL | An upstream banner leaked: confirm uvicorn runs with `--no-server-header` and that `server_tokens off` is in the TLS server block. |
| Smoke test: `metrics is publicly readable` | The `/api/v1/metrics` location or the allow-list is missing; re-apply `deploy/nginx/applypilot.conf`. |
| Smoke test: `access-log line(s) contain a query string` | A log format uses `$request`/`$request_uri`. Restore the `access_log ... applypilot` lines. |
| Browser view connects, then closes 4401 | Ticket invalid, expired (45 s) or already used. Re-issue from the application page. |
| Browser view closes 4403 | `Origin` not in `BACKEND_CORS_ORIGINS`; it must contain the exact HTTPS origin. |
| `502` on WebSocket only | The `Upgrade`/`Connection` headers or `proxy_read_timeout` are missing from the `/api/v1/browser-sessions/view/ws` location. |

Logs: `journalctl -u applypilot-backend`, `journalctl -u applypilot-frontend`,
`tail -f /var/log/nginx/applypilot.access.log` (paths only, never query strings),
`journalctl -u certbot`.

## Rules that must not be broken

* **Never add `--workers`.** Browser sessions and view tickets are in-process.
* **Never disable `APPLICATION_BROWSER_PIN_DNS`** in production.
* **Never publish `/api/v1/metrics`.** Keep the Nginx allow-list and the token.
* **Never bypass the manual-submission policy.** No deploy change may click
  Submit/Apply/Finish or record `SUBMITTED` without `confirmed: true`.
* **Never log query strings.** Keep `$uri` in the access-log format.
* **Never let the browser automation touch CAPTCHA, OTP/MFA, password fields,
  legal declarations, work-authorization questions or existing form values.**
  Resume selection stays user-approved and final submission stays manual.
