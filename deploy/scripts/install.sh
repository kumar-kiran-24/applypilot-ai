#!/usr/bin/env bash
# ApplyPilot AI - Ubuntu 22.04/24.04 installer (idempotent, root required).
#
# Usage:
#   sudo DOMAIN=app.example.com REPO_URL=git@github.com:you/applypilot.git \
#        MONITORING_CIDR=10.0.0.0/8 bash deploy/scripts/install.sh
#
#   # later, to move an existing checkout to the latest remote commit:
#   sudo DOMAIN=... REPO_URL=... UPDATE=1 bash deploy/scripts/install.sh
#
# It installs packages, creates the service user and directories, builds the
# backend venv and the frontend, obtains a Let's Encrypt certificate over the
# HTTP-01 webroot challenge, enables the systemd units and opens only
# SSH/HTTP/HTTPS in UFW.
#
# It DOES NOT create the Neon database or Google OAuth credentials. It also
# verifies that the environment files exist before starting, and refuses to
# continue if production settings are invalid.
#
# Update policy: without UPDATE=1 the checkout is never modified. With UPDATE=1
# the tree must be clean (no modified tracked files) and is only ever
# fast-forwarded; nothing is reset, cleaned or stashed.

set -euo pipefail

DOMAIN="${DOMAIN:?Set DOMAIN, e.g. app.example.com}"
REPO_URL="${REPO_URL:?Set REPO_URL to the git remote}"
REPO_BRANCH="${REPO_BRANCH:-}"
MONITORING_CIDR="${MONITORING_CIDR:-10.0.0.0/8}"
APPLYPILOT_USER="${APPLYPILOT_USER:-applypilot}"
BASE_DIR="${BASE_DIR:-/opt/applypilot}"
CONFIG_DIR="${CONFIG_DIR:-/etc/applypilot}"
STATE_DIR="${STATE_DIR:-/var/lib/applypilot}"
EMAIL="${LETSENCRYPT_EMAIL:-}"
UPDATE="${UPDATE:-0}"
PLAYWRIGHT_BROWSERS_PATH_DIR="$STATE_DIR/ms-playwright"

if [[ $EUID -ne 0 ]]; then
    echo "Run as root (sudo)." >&2
    exit 1
fi

log() { printf '\n==> %s\n' "$*"; }

as_app() {
    runuser -u "$APPLYPILOT_USER" -- env \
        HOME="$STATE_DIR" \
        GIT_TERMINAL_PROMPT=0 \
        GIT_SSH_COMMAND="ssh -o StrictHostKeyChecking=accept-new" \
        PLAYWRIGHT_BROWSERS_PATH="$PLAYWRIGHT_BROWSERS_PATH_DIR" \
        "$@"
}

# --- System packages ---------------------------------------------------------
log "Installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y \
    python3 python3-venv python3-pip \
    nginx certbot git ufw curl ca-certificates \
    postgresql-client

if ! command -v pg_dump >/dev/null 2>&1 || ! command -v pg_restore >/dev/null 2>&1; then
    echo "postgresql-client did not provide pg_dump/pg_restore; backups cannot run." >&2
    exit 1
fi

# Next.js 15 requires Node >= 18.20; Ubuntu's archive Node is too old on 22.04.
if ! command -v node >/dev/null 2>&1 || [[ "$(node -v | sed 's/^v//; s/\..*//')" -lt 20 ]]; then
    log "Installing Node.js 22"
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
    apt-get install -y nodejs
fi

# --- Service user ------------------------------------------------------------
# The account is created before any directory it must own. Its home is the
# state directory (not /home), so HOME stays valid under ProtectHome=true and
# Playwright/NPM never need a writable home outside managed paths.
if ! id "$APPLYPILOT_USER" >/dev/null 2>&1; then
    log "Creating system user $APPLYPILOT_USER"
    useradd --system --home-dir "$STATE_DIR" --no-create-home \
        --shell /usr/sbin/nologin "$APPLYPILOT_USER"
fi

# --- Directories -------------------------------------------------------------
# Ownership is set at creation time. This is what makes the next step work: the
# clone runs as the service user, so /opt/applypilot must already belong to it.
log "Preparing directories"
if [[ ! -d "$BASE_DIR" ]]; then
    install -d -m 0755 -o "$APPLYPILOT_USER" -g "$APPLYPILOT_USER" "$BASE_DIR"
else
    owner="$(stat -c '%U:%G' "$BASE_DIR")"
    if [[ "$owner" != "$APPLYPILOT_USER:$APPLYPILOT_USER" ]]; then
        echo "Fixing ownership of $BASE_DIR ($owner -> $APPLYPILOT_USER)"
        chown -R "$APPLYPILOT_USER:$APPLYPILOT_USER" "$BASE_DIR"
    fi
    chmod 0755 "$BASE_DIR"
fi

install -d -m 0700 -o "$APPLYPILOT_USER" -g "$APPLYPILOT_USER" "$CONFIG_DIR"
install -d -m 0750 -o "$APPLYPILOT_USER" -g "$APPLYPILOT_USER" \
    "$STATE_DIR" "$STATE_DIR/resumes" "$STATE_DIR/screenshots" \
    "$PLAYWRIGHT_BROWSERS_PATH_DIR"
# ACME challenges are written by certbot (root) and read by Nginx workers.
install -d -m 0755 -o root -g root /var/www/certbot

# --- Repository --------------------------------------------------------------
log "Cloning or updating the repository"
if [[ ! -d "$BASE_DIR/.git" ]]; then
    if [[ -n "$(ls -A "$BASE_DIR" 2>/dev/null)" ]]; then
        echo "$BASE_DIR exists, is not empty and is not a git checkout." >&2
        echo "Move it aside, or set BASE_DIR to a different path." >&2
        exit 1
    fi
    if [[ -n "$REPO_BRANCH" ]]; then
        as_app git clone --branch "$REPO_BRANCH" "$REPO_URL" "$BASE_DIR"
    else
        as_app git clone "$REPO_URL" "$BASE_DIR"
    fi
else
    # Only tracked modifications matter: build output (frontend/.next,
    # node_modules, .venv) is ignored by git and must not block a rerun.
    if ! dirty="$(as_app git -C "$BASE_DIR" status --porcelain --untracked-files=no)" \
        || [[ -n "$dirty" ]]; then
        if [[ "$UPDATE" == "1" ]]; then
            {
                echo "Refusing to update: $BASE_DIR has local modifications."
                echo "Nothing was reset, cleaned or stashed. Resolve these files"
                echo "manually (or commit them) and re-run:"
                echo "---"
                printf '%s\n' "${dirty:-(git status failed)}"
                echo "---"
            } >&2
            exit 1
        fi
        echo "Note: $BASE_DIR has local modifications; leaving the tree as is."
        echo "      Re-run with UPDATE=1 after resolving them to deploy new code."
    elif [[ "$UPDATE" == "1" ]]; then
        branch="${REPO_BRANCH:-$(as_app git -C "$BASE_DIR" rev-parse --abbrev-ref HEAD)}"
        log "Fast-forwarding $branch"
        as_app git -C "$BASE_DIR" fetch --prune --tags origin
        as_app git -C "$BASE_DIR" merge --ff-only "origin/$branch"
    else
        echo "Checkout kept at its current commit (set UPDATE=1 to fast-forward)."
    fi
fi

# --- Backend virtualenv ------------------------------------------------------
log "Installing backend dependencies"
if [[ ! -x "$BASE_DIR/.venv/bin/python" ]]; then
    as_app python3 -m venv "$BASE_DIR/.venv"
fi
as_app "$BASE_DIR/.venv/bin/pip" install --upgrade pip
as_app "$BASE_DIR/.venv/bin/pip" install -r "$BASE_DIR/backend/requirements.txt"
as_app "$BASE_DIR/.venv/bin/python" - <<'PY'
import sys

from playwright._repo_version import version

print(f"playwright driver {version} on Python {sys.version.split()[0]}")
PY

# --- Playwright / Chromium ---------------------------------------------------
# System libraries need root; the browser binary itself must be installed with
# the SAME HOME and browsers path the unit uses, otherwise the service silently
# cannot launch a browser. Both are pinned by backend/requirements.txt.
log "Installing Chromium for Playwright"
"$BASE_DIR/.venv/bin/python" -m playwright install-deps chromium
as_app env PLAYWRIGHT_BROWSERS_PATH="$PLAYWRIGHT_BROWSERS_PATH_DIR" \
    "$BASE_DIR/.venv/bin/python" -m playwright install chromium
as_app env PLAYWRIGHT_BROWSERS_PATH="$PLAYWRIGHT_BROWSERS_PATH_DIR" \
    "$BASE_DIR/.venv/bin/python" - <<'PY'
from playwright.sync_api import sync_playwright

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(args=["--no-sandbox"])
    print(f"Chromium launch OK: {browser.version}")
    browser.close()
PY

# --- Environment files -------------------------------------------------------
if [[ ! -f "$CONFIG_DIR/backend.env" ]]; then
    install -m 0600 -o "$APPLYPILOT_USER" -g "$APPLYPILOT_USER" \
        /dev/null "$CONFIG_DIR/backend.env"
    echo "Created empty $CONFIG_DIR/backend.env"
fi
if [[ ! -f "$CONFIG_DIR/frontend.env" ]]; then
    install -m 0600 -o "$APPLYPILOT_USER" -g "$APPLYPILOT_USER" \
        /dev/null "$CONFIG_DIR/frontend.env"
    echo "Created empty $CONFIG_DIR/frontend.env"
fi
# Secrets stay 0600 and are never group/world readable, even on a rerun.
chmod 0600 "$CONFIG_DIR/backend.env" "$CONFIG_DIR/frontend.env"
chown "$APPLYPILOT_USER:$APPLYPILOT_USER" \
    "$CONFIG_DIR/backend.env" "$CONFIG_DIR/frontend.env"

echo
echo "Fill $CONFIG_DIR/backend.env from backend/.env.example, then re-run this"
echo "script (or continue) - it will not overwrite secrets you already set."
echo "Generate missing secrets with:"
echo "  sudo -u $APPLYPILOT_USER $BASE_DIR/.venv/bin/python $BASE_DIR/backend/scripts/generate_secrets.py --env-file $CONFIG_DIR/backend.env"
echo
if [[ "${ASSUME_READY:-0}" != "1" ]]; then
    read -r -p "Press Enter when backend.env is complete (Ctrl-C to abort)... " _
fi

# Refuse to deploy with invalid production settings.
log "Verifying secrets"
as_app bash -c "cd $BASE_DIR/backend && set -a && . $CONFIG_DIR/backend.env && set +a && $BASE_DIR/.venv/bin/python scripts/verify_secrets.py --environment production --env-file $CONFIG_DIR/backend.env"

# --- Database migrations -----------------------------------------------------
log "Applying database migrations"
as_app bash -c "cd $BASE_DIR/backend && set -a && . $CONFIG_DIR/backend.env && set +a && $BASE_DIR/.venv/bin/alembic upgrade head"

# --- Frontend build ----------------------------------------------------------
# NEXT_PUBLIC_* is inlined at build time, so the build must see the same values
# the service gets at runtime. Only public values are loaded here.
log "Building the frontend"
as_app bash -c "cd $BASE_DIR/frontend && set -a && . $CONFIG_DIR/frontend.env && set +a && npm ci && NEXT_TELEMETRY_DISABLED=1 npm run build && mkdir -p .next/cache"
install -d -m 0750 -o "$APPLYPILOT_USER" -g "$APPLYPILOT_USER" \
    "$BASE_DIR/frontend/.next/cache"

# --- systemd -----------------------------------------------------------------
log "Installing systemd units"
install -m 0644 "$BASE_DIR/deploy/systemd/applypilot-backend.service" /etc/systemd/system/
install -m 0644 "$BASE_DIR/deploy/systemd/applypilot-frontend.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable applypilot-backend applypilot-frontend

# --- Firewall ----------------------------------------------------------------
# Configured before certificate issuance so the ACME HTTP-01 challenge is
# reachable from the internet. Rules are added before the default policy flips.
log "Configuring UFW"
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw default deny incoming
ufw default allow outgoing
# Egress containment for the browser tier: even if a hostname is rebound after
# validation, Chromium cannot connect to RFC1918, link-local or metadata
# addresses. Loopback stays open for frontend -> backend.
#
# DEFAULT OFF: many cloud providers serve DNS (and Neon private endpoints) from
# 10.x, so enabling this blindly can break name resolution. Check
# /etc/resolv.conf and the Neon endpoint first, allow those addresses, then set
# RESTRICT_EGRESS=1.
if [[ "${RESTRICT_EGRESS:-0}" == "1" ]]; then
    ufw allow out on lo
    ufw deny out to 10.0.0.0/8
    ufw deny out to 172.16.0.0/12
    ufw deny out to 192.168.0.0/16
    ufw deny out to 169.254.0.0/16
fi
ufw --force enable

# --- TLS (HTTP-01 webroot, no downtime, renews without stopping Nginx) -------
# Nginx cannot start with the production vhost before the certificate exists, so
# a minimal HTTP vhost is enabled first to serve the ACME challenge. This avoids
# --standalone (which would need port 80, i.e. stopping Nginx) both now and at
# renewal time, because webroot is written into the renewal configuration.
log "Obtaining the TLS certificate"
if [[ ! -d "/etc/letsencrypt/live/$DOMAIN" ]]; then
    cat > /etc/nginx/sites-available/applypilot-bootstrap.conf <<EOF
# Temporary HTTP-01 ACME vhost written by deploy/scripts/install.sh.
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }
    location / {
        return 404;
    }
}
EOF
    # A half-installed production vhost would reference a certificate that does
    # not exist yet and make `nginx -t` fail, so it is disabled first.
    rm -f /etc/nginx/sites-enabled/applypilot.conf
    ln -sf /etc/nginx/sites-available/applypilot-bootstrap.conf \
        /etc/nginx/sites-enabled/applypilot-bootstrap.conf
    rm -f /etc/nginx/sites-enabled/default
    nginx -t
    systemctl enable nginx
    systemctl reload nginx 2>/dev/null || systemctl restart nginx

    EMAIL_ARGS=(--register-unsafely-without-email)
    if [[ -n "$EMAIL" ]]; then
        EMAIL_ARGS=(--email "$EMAIL")
    fi
    certbot certonly --webroot --webroot-path /var/www/certbot \
        --cert-name "$DOMAIN" --non-interactive --agree-tos --no-eff-email \
        "${EMAIL_ARGS[@]}" -d "$DOMAIN"

    rm -f /etc/nginx/sites-enabled/applypilot-bootstrap.conf
    rm -f /etc/nginx/sites-available/applypilot-bootstrap.conf
else
    # A certificate issued by an older --standalone install keeps using
    # standalone at renewal, which needs port 80 and therefore an Nginx stop.
    renewal_conf="/etc/letsencrypt/renewal/$DOMAIN.conf"
    if [[ -f "$renewal_conf" ]] && grep -q '^authenticator = standalone' "$renewal_conf"; then
        echo "WARNING: $DOMAIN renews with the standalone authenticator, which" >&2
        echo "         needs port 80 and conflicts with Nginx. Switch it to webroot" >&2
        echo "         once (this re-issues the certificate):" >&2
        echo "           certbot certonly --webroot -w /var/www/certbot --cert-name $DOMAIN -d $DOMAIN --force-renewal" >&2
    fi
fi

# Renewal hook: validate the configuration, then reload (not restart). Runs only
# after a successful renewal.
install -d -m 0755 -o root -g root /etc/letsencrypt/renewal-hooks/deploy
cat > /etc/letsencrypt/renewal-hooks/deploy/10-reload-nginx.sh <<'EOF'
#!/usr/bin/env bash
# Written by applypilot deploy/scripts/install.sh
set -euo pipefail
nginx -t
systemctl reload nginx
EOF
chmod 0755 /etc/letsencrypt/renewal-hooks/deploy/10-reload-nginx.sh
chown root:root /etc/letsencrypt/renewal-hooks/deploy/10-reload-nginx.sh

# The distro certbot package ships certbot.timer; enabling it is idempotent and
# makes renewal independent of cron. Renewal uses the webroot authenticator
# recorded in /etc/letsencrypt/renewal/<domain>.conf, so Nginx keeps port 80.
if systemctl list-unit-files --no-legend certbot.timer 2>/dev/null | grep -q '^certbot\.timer'; then
    systemctl enable --now certbot.timer
else
    echo "WARNING: certbot.timer not found; configure a cron entry for" >&2
    echo "         'certbot renew --quiet' (see deploy/README.md)." >&2
fi

if [[ "${RENEWAL_DRY_RUN:-0}" == "1" ]]; then
    log "Testing certificate renewal (staging, no rate-limit impact)"
    certbot renew --dry-run --non-interactive
fi

# --- Nginx -------------------------------------------------------------------
log "Installing the Nginx site"
sed -e "s|__DOMAIN__|$DOMAIN|g" \
    -e "s|__MONITORING_CIDR__|$MONITORING_CIDR|g" \
    "$BASE_DIR/deploy/nginx/applypilot.conf" > /etc/nginx/sites-available/applypilot.conf
ln -sf /etc/nginx/sites-available/applypilot.conf /etc/nginx/sites-enabled/applypilot.conf
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl enable nginx
systemctl reload nginx 2>/dev/null || systemctl restart nginx

# --- Services ----------------------------------------------------------------
log "Starting services"
systemctl restart applypilot-backend applypilot-frontend
sleep 3
curl -fsS "https://$DOMAIN/api/v1/health"
echo
curl -fsS "https://$DOMAIN/api/v1/ready"
echo

log "Deployment complete"
echo "Run: DOMAIN=$DOMAIN METRICS_TOKEN=<token> bash $BASE_DIR/deploy/scripts/smoke-test.sh"
echo "Renewal: systemctl list-timers certbot.timer ; certbot renew --dry-run"
