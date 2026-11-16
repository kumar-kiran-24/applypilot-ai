#!/usr/bin/env bash
# Restore an ApplyPilot backup. DESTRUCTIVE: replaces database objects.
#
# Usage:
#   sudo CONFIRM=YES BACKUP_FILE=/var/backups/applypilot/applypilot-....enc \
#        BACKUP_PASSPHRASE_FILE=/root/.ap_backup_key bash deploy/scripts/restore.sh
#
# Recommended order for a full recovery:
#   1. Stop applypilot-backend and applypilot-frontend.
#   2. Restore the database (this script).
#   3. Restore /etc/applypilot/backend.env from the same archive (so the Gmail
#      Fernet key matches the encrypted refresh tokens).
#   4. Start the backend, then the frontend.
#   5. Run deploy/scripts/smoke-test.sh.

set -euo pipefail

# Fail with an actionable message instead of "command not found".
require_cmd() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "ERROR: '$1' is not installed." >&2
        echo "Install the PostgreSQL client utilities and retry:" >&2
        echo "  sudo apt-get install -y postgresql-client" >&2
        echo "If pg_restore is older than the server, install the matching major:" >&2
        echo "  sudo apt-get install -y postgresql-client-16" >&2
        exit 1
    fi
}

require_cmd pg_restore
require_cmd openssl
require_cmd tar

CONFIG_DIR="${CONFIG_DIR:-/etc/applypilot}"
BACKUP_FILE="${BACKUP_FILE:?Set BACKUP_FILE}"
PASSPHRASE_FILE="${BACKUP_PASSPHRASE_FILE:?Set BACKUP_PASSPHRASE_FILE}"

if [[ "${CONFIRM:-}" != "YES" ]]; then
    echo "Refusing to restore without CONFIRM=YES." >&2
    exit 1
fi
if [[ ! -f "$BACKUP_FILE" ]]; then
    echo "ERROR: BACKUP_FILE not found: $BACKUP_FILE" >&2
    exit 1
fi
if [[ ! -f "$PASSPHRASE_FILE" ]]; then
    echo "ERROR: BACKUP_PASSPHRASE_FILE not found: $PASSPHRASE_FILE" >&2
    exit 1
fi
if [[ ! -f "$CONFIG_DIR/backend.env" ]]; then
    echo "ERROR: $CONFIG_DIR/backend.env not found; DATABASE_URL cannot be read." >&2
    exit 1
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 \
    -pass "file:$PASSPHRASE_FILE" \
    -in "$BACKUP_FILE" -out "$WORK/backup.tar.gz"
tar -C "$WORK" -xzf "$WORK/backup.tar.gz"

# shellcheck disable=SC1091
set -a
# shellcheck source=/dev/null
. "$CONFIG_DIR/backend.env"
set +a
PG_URL="${DATABASE_URL/postgresql+asyncpg/postgresql}"

echo "Restoring database (this replaces matching objects)..."
if ! pg_restore --clean --if-exists --no-owner --no-privileges \
        --dbname "$PG_URL" "$WORK/database.dump"; then
    echo "ERROR: pg_restore failed. Confirm DATABASE_URL is reachable and that" >&2
    echo "pg_restore is not older than the server major version." >&2
    exit 1
fi

echo "Database restored."
echo
echo "Restore $CONFIG_DIR/backend.env from $WORK/env.tar.gz if this host lost it:"
echo "  tar -C $CONFIG_DIR -xzf $WORK/env.tar.gz"
echo
echo "Then: systemctl start applypilot-backend applypilot-frontend"
echo "      deploy/scripts/smoke-test.sh"
