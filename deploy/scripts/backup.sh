#!/usr/bin/env bash
# Encrypted ApplyPilot backup: Neon database + environment files.
#
# Usage:
#   sudo BACKUP_DIR=/var/backups/applypilot BACKUP_PASSPHRASE_FILE=/root/.ap_backup_key \
#        bash deploy/scripts/backup.sh
#
# The passphrase file must already exist (0600) and be stored separately from
# the backups. Losing it makes the database dump AND the Gmail encryption key
# unrecoverable. Test restore quarterly with deploy/scripts/restore.sh.

set -euo pipefail

# Fail with an actionable message instead of "command not found" in a cron mail.
require_cmd() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "ERROR: '$1' is not installed." >&2
        echo "Install the PostgreSQL client utilities and retry:" >&2
        echo "  sudo apt-get install -y postgresql-client" >&2
        echo "If pg_dump is older than the server, install the matching major:" >&2
        echo "  sudo apt-get install -y postgresql-client-16" >&2
        exit 1
    fi
}

require_cmd pg_dump
require_cmd openssl
require_cmd tar

CONFIG_DIR="${CONFIG_DIR:-/etc/applypilot}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/applypilot}"
PASSPHRASE_FILE="${BACKUP_PASSPHRASE_FILE:?Set BACKUP_PASSPHRASE_FILE}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

if [[ ! -f "$PASSPHRASE_FILE" ]]; then
    echo "ERROR: BACKUP_PASSPHRASE_FILE not found: $PASSPHRASE_FILE" >&2
    exit 1
fi
if [[ ! -f "$CONFIG_DIR/backend.env" ]]; then
    echo "ERROR: $CONFIG_DIR/backend.env not found; DATABASE_URL cannot be read." >&2
    exit 1
fi

install -d -m 0700 "$BACKUP_DIR"

# shellcheck disable=SC1091
set -a
# shellcheck source=/dev/null
. "$CONFIG_DIR/backend.env"
set +a
: "${DATABASE_URL:?DATABASE_URL missing from backend.env}"

# asyncpg is a SQLAlchemy driver name; pg_dump needs the plain scheme.
PG_URL="${DATABASE_URL/postgresql+asyncpg/postgresql}"

echo "Dumping database..."
if ! pg_dump --format=custom --no-owner --no-privileges \
        --file "$WORK/database.dump" "$PG_URL"; then
    echo "ERROR: pg_dump failed. Confirm DATABASE_URL in $CONFIG_DIR/backend.env" >&2
    echo "is reachable and that pg_dump is not older than the server major version." >&2
    exit 1
fi

echo "Collecting environment files..."
tar -C "$CONFIG_DIR" -czf "$WORK/env.tar.gz" backend.env frontend.env

echo "Encrypting..."
tar -C "$WORK" -czf "$WORK/backup.tar.gz" database.dump env.tar.gz
openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt \
    -pass "file:$PASSPHRASE_FILE" \
    -in "$WORK/backup.tar.gz" \
    -out "$BACKUP_DIR/applypilot-$STAMP.tar.gz.enc"

sha256sum "$BACKUP_DIR/applypilot-$STAMP.tar.gz.enc" \
    > "$BACKUP_DIR/applypilot-$STAMP.tar.gz.enc.sha256"

find "$BACKUP_DIR" -name 'applypilot-*.enc*' -type f -mtime "+$RETENTION_DAYS" -delete

echo "Backup written: $BACKUP_DIR/applypilot-$STAMP.tar.gz.enc"
echo "Store it off-host and keep the passphrase separate."
