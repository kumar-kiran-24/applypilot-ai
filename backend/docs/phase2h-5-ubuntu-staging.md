# Phase 2H.5 - Ubuntu Staging Deployment and Infrastructure Validation

Status: **BLOCKED - no Ubuntu environment available on this machine.**
No deployment, migration, certificate issuance or service start was performed.
This document records the environment discovery, the checks that *were* really
executed (static, on Windows), and the exact manual steps needed to complete
Phase 2H.5 on a real Ubuntu 24.04 LTS host.

Predecessors: `phase2h.1` live redirect stabilization, `phase2h.2` end-to-end
integration, `phase2h.3` production readiness review, `phase2h.4` deployment
hardening (assets in [`deploy/`](../../deploy)).

---

## 1. Environment details

| Item | Value |
| --- | --- |
| OS | Windows 11 Home Single Language, build 26300 (10.0.26300) |
| Shell | Git Bash (MINGW64_NT-10.0-26300), not a Linux kernel |
| CPU / RAM | 24,284 MB total, 4,063 MB available |
| Free disk on `C:` | 46 GB (88% used) |
| WSL | 2.6.1.0, kernel 6.6.87.2-1; WSLg 1.0.66 |
| WSL distributions | `docker-desktop` only (Stopped) - **no Ubuntu distro** |
| `wsl -l -o` | failed: connection to the distro list endpoint reset (`Wsl/0x80072eff`) |
| Docker | CLI 29.7.2 present; **Docker Desktop not installed**; daemon unreachable (`npipe:////./pipe/dockerDesktopLinuxEngine` missing) |
| SSH client | present (`ssh`) |
| SSH config | none (`~/.ssh/config` absent); keys only for unrelated hosts |
| known_hosts | several public IPv4s and `github.com`; **none identified as ApplyPilot staging** -> not contacted |
| Host toolchain (not used for validation) | Python 3.13.5, Node v24.19.0, npm 11.13.0, psql 17.4 |

Conclusion: there is no Ubuntu kernel, no root-owned Linux filesystem, no
systemd, no Docker daemon and no identified staging server. Any "validation"
produced here would have been fabricated, so none was attempted.

## 2. Ubuntu version

**NOT RUN.** No Ubuntu release is installed. The target is Ubuntu 24.04 LTS
(glibc/Python 3.12), which satisfies `requires-python = ">=3.10"` in
`backend/pyproject.toml`. Ubuntu 22.04 LTS (Python 3.10) is also supported by
the runbook.

## 3. Deployment target

**BLOCKED.** No target has been explicitly identified. The following must be
supplied by you before §5 of the phase (clean installation) can start:

| Required | Why |
| --- | --- |
| Staging hostname (`STAGING_DOMAIN`) | Nginx `server_name`, Certbot `--cert-name`, `NEXT_PUBLIC_API_BASE_URL`, CORS origin |
| Public IPv4 + DNS `A` record | ACME HTTP-01 cannot succeed until the name resolves to the host |
| Root/sudo SSH access | installer requires `EUID 0` |
| Staging `DATABASE_URL` (Neon) | must not be invented |
| Google OAuth client id/secret + redirect URIs for the staging host | must not be invented |
| `GROQ_API_KEY` if AI features are to be exercised | must not be invented |

Do **not** paste secret values into chat, tickets or this document. Enter them
directly into `/etc/applypilot/backend.env` on the host.

## 4. Files changed

None of the existing application code was modified.

* Added: this report (`backend/docs/phase2h-5-ubuntu-staging.md`).
* All 25 modified tracked files and every untracked Phase 2H file were left
  untouched. No `git reset`, `clean`, `stash` or checkout was run; nothing was
  committed.

## 5. Packages installed

**None.** No system package, WSL distribution, container or VM was installed or
modified. Installing WSL/Ubuntu or VirtualBox is a system-level change that
requires your explicit authorization (see §17, Option B).

## 6. Linux validation results

The five commands the phase requires are **NOT RUN**, because they need a Linux
kernel (`nginx`, `systemd-analyze`, `systemctl` do not exist on Windows):

| Command | Status |
| --- | --- |
| `bash -n deploy/scripts/install.sh` (on Ubuntu) | NOT RUN |
| `bash -n deploy/scripts/backup.sh` (on Ubuntu) | NOT RUN |
| `bash -n deploy/scripts/restore.sh` (on Ubuntu) | NOT RUN |
| `bash -n deploy/scripts/smoke-test.sh` (on Ubuntu) | NOT RUN |
| `nginx -t` | NOT RUN - Nginx not installed |
| `systemd-analyze verify deploy/systemd/*.service` | NOT RUN - no systemd |
| `systemctl list-timers certbot.timer --all` | NOT RUN - no certbot/systemd |

### What was actually executed (static, Windows Git Bash)

These are honest but *weaker* checks: they prove the files parse and are
internally consistent, not that they run on Linux.

| Check | Command | Result |
| --- | --- | --- |
| install.sh parse | `bash -n deploy/scripts/install.sh` | PASSED (exit 0) |
| backup.sh parse | `bash -n deploy/scripts/backup.sh` | PASSED (exit 0) |
| restore.sh parse | `bash -n deploy/scripts/restore.sh` | PASSED (exit 0) |
| smoke-test.sh parse | `bash -n deploy/scripts/smoke-test.sh` | PASSED (exit 0) |
| Line endings | `file` on all 8 files under `deploy/` | PASSED - all LF, no CRLF (CRLF would break `#!/usr/bin/env bash` on Linux) |
| Exec bits (local) | `ls -l deploy/scripts` | PASSED - all four `0755` (docs invoke them via `bash`, so this is non-fatal either way) |
| Secret helper flags | `install.sh` calls `verify_secrets.py --environment production --env-file <f>`; parser declares both | PASSED - no mismatch |
| Secret generator flags | `install.sh`/README call `generate_secrets.py --env-file <f>`; parser declares it | PASSED |
| Referenced helpers exist | `backend/scripts/{verify_secrets,generate_secrets}.py` | PASSED |
| Pins | `playwright==1.64.1`, `next 15.5.29`, `uvicorn 0.38.0`, `alembic 1.20.1`, `fastapi 0.121.0` | PASSED - matches README |
| Frontend bind | `package.json` `start` = `next start -H 127.0.0.1 -p 3000` | PASSED - matches Nginx upstream |
| Domain placeholders | `__DOMAIN__` / `__MONITORING_CIDR__` used consistently and substituted by the installer | PASSED |

### Pre-flight observation to verify on the host

`deploy/nginx/applypilot.conf` sets `ssl_stapling on` and
`ssl_stapling_verify on`. Let's Encrypt has retired its OCSP responder, so on a
2026 host Nginx can log stapling errors and staple nothing. This does **not**
break TLS or `nginx -t`, but treat it as a known-open item: after the VM exists,
confirm with `openssl s_client -status -connect $STAGING_DOMAIN:443 | head -20`
and drop the two directives if the responder is gone. Not changed now, because
it is your staging config to decide on and it was not part of this phase's
scope to alter.

## 7. Installation results

**NOT RUN.** No `install.sh` execution, no user/directory creation, no venv, no
`pip install`, no Playwright/Chromium install, no `npm ci`, no Next.js build.

## 8. Backend service status

**NOT RUN.** `applypilot-backend` was never installed or started; no
`systemctl status`, no `/health` or `/ready` request.

## 9. Frontend service status

**NOT RUN.** `applypilot-frontend` was never installed or started.

## 10. Database migration status

* Against a real database: **NOT RUN** (no staging `DATABASE_URL`, correctly not
  invented).
* Static single-head check (reading the migration files, **not** `alembic heads`
  execution): **PASSED** - 20 revisions, 19 distinct `down_revision` values plus
  one `None` root; the only revision that is nobody's parent is
  `c3f9a72d1e05` (the Phase 2H Easy Apply redirects migration). So the head is
  unique and consistent with a single-head history.
* `alembic current`, `alembic check`: NOT RUN. No database was dropped,
  recreated or truncated.

## 11. HTTPS and Nginx results

**NOT RUN.** No HTTP->HTTPS redirect test, no TLS validation, no HSTS check, no
`certbot renew --dry-run`, no `certbot.timer` inspection. No certificate was
requested (production or staging).

Static read of `deploy/nginx/applypilot.conf` confirms the intended properties
to be *proven* on the host: `server_tokens off`; HSTS and the other security
headers `add_header ... always` with matching `proxy_hide_header` (single
value); `Content-Security-Policy` passed through by design; `^/(docs|redoc|openapi\.json)$`
-> 404; `/api/v1/metrics` limited to `127.0.0.1`, `::1` and `MONITORING_CIDR`
with `deny all`; WebSocket location with `Upgrade`/`Connection`,
`proxy_buffering off`, `proxy_read_timeout 3700s`; access log format uses
`$uri` (never `$request`/`$request_uri`), so `?ticket=` and OAuth codes cannot
be written to disk.

## 12. Browser / WebSocket results

**NOT RUN.** No browser session was created, no viewer ticket was issued, no
WebSocket connection was made, no Chromium was launched. The single-worker rule
is preserved: `applypilot-backend.service` still runs `--workers 1` and nothing
in this phase proposed changing it.

## 13. Security verification

No security control was weakened, disabled or edited. Verified statically that
the assets still encode the required safeguards:

| Control | Evidence |
| --- | --- |
| SSRF / DNS pinning | `APPLICATION_BROWSER_PIN_DNS=true` required in the documented production env; README "Rules that must not be broken" forbids disabling it |
| Single-use view tickets | single uvicorn worker; Nginx logs `$uri` only, so tickets never hit disk |
| WS Origin checks | smoke test check 9 expects 403 for a foreign `Origin` before handshake |
| Edge exposure | `/docs`, `/redoc`, `/openapi.json` -> 404; metrics allow-listed |
| Manual submission | README forbids any deploy change clicking Submit/Apply/Finish or recording `SUBMITTED` without `confirmed: true` |
| Manual human steps | CAPTCHA/OTP/MFA, passwords, legal declarations, work-authorization answers, resume choice and final submit stay manual per README |
| Secrets | `/etc/applypilot` 0700, env files 0600, never overwritten by re-runs; `verify_secrets.py --environment production` gates the deploy |

Behavioral verification of these is **NOT RUN** (no host). No job application
was submitted.

## 14. Backup / update verification

**NOT RUN** (needs `pg_dump`/`pg_restore`, a passphrase file and a real
database). Static review only:

* `backup.sh`/`restore.sh` `require_cmd` for `pg_dump`/`pg_restore`, `openssl`,
  `tar` and exit with an install hint if missing.
* `restore.sh` refuses without `CONFIRM=YES`; no restore was attempted.
* `install.sh` update path: `git status --porcelain --untracked-files=no` must
  be empty, then `fetch --prune --tags` + `merge --ff-only`; no reset/clean/
  stash anywhere; env files only created when missing (`install -m 0600`), so a
  re-run cannot overwrite secrets; services restart only after venv/Chromium/
  migration/build succeed.

## 15. Test counts and exit codes (this phase)

No application test suite was run in Phase 2H.5, because nothing in the
application changed and the phase's subject is the Linux deployment, not the
code. The only commands executed and their exit codes:

| # | Command | Exit |
| --- | --- | --- |
| 1 | `git status --short` | 0 |
| 2 | `bash -n deploy/scripts/install.sh` | 0 |
| 3 | `bash -n deploy/scripts/backup.sh` | 0 |
| 4 | `bash -n deploy/scripts/restore.sh` | 0 |
| 5 | `bash -n deploy/scripts/smoke-test.sh` | 0 |
| 6 | `file` over `deploy/**` (LF audit) | 0 |
| 7 | `wsl.exe -l -v` / `--status` | 0 (only `docker-desktop`) |
| 8 | `wsl.exe -l -o` | 1 (endpoint reset) |
| 9 | `docker info` | 1 (daemon unreachable) |

The previously reported Phase 2H.4 totals (backend 680 passed/6 skipped,
redirect 28 passed/1 skipped, Chromium 10 passed, Vitest 79, Playwright 17,
Ruff/ESLint/TS/Next build and the Alembic check) are **carried over as prior
results, not re-executed here**. They say nothing about Ubuntu runtime behavior.

## 16. Remaining blockers

1. **No Ubuntu host** - the primary blocker (this machine has no Ubuntu WSL
   distro, no Docker daemon, no VM).
2. **No staging domain + DNS** - required for Nginx, CORS, OAuth redirects and
   ACME HTTP-01.
3. **No staging secrets** - `DATABASE_URL`, Google OAuth client, `GROQ_API_KEY`
   must be provided by you on the host.
4. **No identified staging server** - SSH access and target must be named.
5. **Everything in §5-§14** is NOT RUN until 1-4 are resolved.

## 17. Exact staging deployment commands

### Option A - cloud VM (only option that validates real HTTPS)

Recommended for a *genuine* staging deployment, because ACME, the HTTP->HTTPS
redirect, HSTS and WebSocket-over-TLS all need a public IP and public DNS.
2 vCPU / 4 GB RAM Ubuntu 24.04 LTS with root SSH; hourly billing, destroy after
validation. Create the instance in the provider console, point an `A` record at
it, then:

```bash
# 0. From your machine - confirm DNS first. Do not proceed until this prints
#    the server IP, or certbot will fail.
dig +short "$STAGING_DOMAIN"

ssh root@"$STAGING_SERVER_IP"

# 1. Host prerequisites (Ubuntu 24.04 LTS)
apt-get update
apt-get install -y git
git clone https://github.com/kumar-kiran-24/applypilot-ai.git /srv/applypilot-src
cd /srv/applypilot-src
git log --oneline -1

# 2. Installer - packages, applypilot user, dirs, venv, Chromium, secrets gate,
#    migrations, frontend build, UFW, TLS, Nginx, systemd.
#    It pauses (unless ASSUME_READY=1) so you can fill backend.env.
DOMAIN="$STAGING_DOMAIN" \
REPO_URL=https://github.com/kumar-kiran-24/applypilot-ai.git \
LETSENCRYPT_EMAIL="$STAGING_EMAIL" \
MONITORING_CIDR="$MONITORING_CIDR" \
bash deploy/scripts/install.sh

# 3. Only if you intend to deploy the *committed* code: the working tree here
#    contains uncommitted Phase 2H work, and the installer clones from the
#    remote. Either commit/push first (NOT done in this phase) or copy the tree
#    and run the installer against that checkout:
#      rsync -a --exclude node_modules --exclude .venv --exclude .next \
#        ./ root@"$STAGING_SERVER_IP":/srv/applypilot-src/
#    No commit was made in Phase 2H.5 by instruction.
```

### Option B - local VM (no public DNS; validates all but real TLS/ACME)

Windows 11 Home has no Hyper-V manager, so use VirtualBox (or VMware Workstation
Pro) and install **Ubuntu Server 24.04 LTS**, 2 vCPU / 4 GB RAM / 25 GB disk
(46 GB free is enough). Then run the same installer with a local hostname.

Expected limitations, stated up front so results are not over-claimed:

* `certbot certonly --webroot` will **fail** without public DNS, so either skip
  TLS for the VM or point `DOMAIN` at a real name with port 80 forwarded from
  your router. A failed issuance is a DNS/port-forwarding result, not an
  application defect.
* §6 (installed packages, user, venv, Chromium, build, systemd units) and §7
  (service status, `/health`, `/ready`) *are* fully validatable this way.
* §8 (WS over TLS) can be validated over plain HTTP on the VM only if Nginx is
  temporarily configured without TLS - do not ship that config.

### Option C - WSL2 Ubuntu (fastest, weakest fidelity)

```powershell
# In an elevated PowerShell/CMD - a system change, so only with your approval:
wsl --install -d Ubuntu-24.04
```

Then enable systemd (required for the systemd units under test):

```bash
sudo tee /etc/wsl.conf >/dev/null <<'EOF'
[boot]
systemd=true
EOF
# then from Windows: wsl --shutdown
```

WSL validates §6/§7 and the unit files under real systemd, but it is **not** a
staging deployment: no public inbound, so Certbot/ACME, the public
HTTP->HTTPS redirect, HSTS-over-the-internet behaviour and external
reachability cannot be proven. Chromium under WSL also needs
`playwright install-deps` plus, if GUI-less, the same headless launch the
service uses.

### Commands to run once a host exists (verbatim from the runbook)

```bash
# static checks now actually meaningful
bash -n deploy/scripts/install.sh
bash -n deploy/scripts/backup.sh
bash -n deploy/scripts/restore.sh
bash -n deploy/scripts/smoke-test.sh
nginx -t
systemd-analyze verify deploy/systemd/applypilot-backend.service
systemd-analyze verify deploy/systemd/applypilot-frontend.service
systemctl list-timers certbot.timer --all

# database (read-only)
sudo -u applypilot bash -c '
  set -a; . /etc/applypilot/backend.env; set +a
  cd /opt/applypilot/backend
  /opt/applypilot/.venv/bin/alembic current
  /opt/applypilot/.venv/bin/alembic heads
  /opt/applypilot/.venv/bin/alembic check'

# services and endpoints
systemctl status applypilot-backend --no-pager
systemctl status applypilot-frontend --no-pager
curl -i "https://$STAGING_DOMAIN/api/v1/health"
curl -i "https://$STAGING_DOMAIN/api/v1/ready"

# TLS / renewal
sudo certbot renew --dry-run
sudo openssl x509 -in "/etc/letsencrypt/live/$STAGING_DOMAIN/cert.pem" -noout -dates
sudo sh -c 'cat /etc/letsencrypt/renewal/$0.conf' "$STAGING_DOMAIN" \
  | grep -E 'authenticator|webroot_path'

# full gate
sudo DOMAIN="$STAGING_DOMAIN" METRICS_TOKEN=<token> \
  bash /opt/applypilot/deploy/scripts/smoke-test.sh

# Chromium through the deployed service
curl -fsS "https://$STAGING_DOMAIN/api/v1/ready" \
  | grep -o '"browser_streaming_available":[a-z]*'

# backup dependency check (writes an encrypted archive only; no restore)
pg_dump --version && pg_restore --version
sudo BACKUP_PASSPHRASE_FILE=/root/.ap_backup_key \
  bash /opt/applypilot/deploy/scripts/backup.sh
```

Do **not** run `restore.sh` against staging; a restore is destructive and was
explicitly out of scope.

---

## Verdict

Phase 2H.5 is **not complete**: the deployment assets are syntactically valid
and internally consistent (verified as far as a Windows host allows), but no
package was installed, no service ran, no migration was applied and no
certificate was requested. Supplying an Ubuntu 24.04 host plus the four items in
§3 unblocks the remaining checks; §17 lists the exact commands.
