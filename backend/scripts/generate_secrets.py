"""Generate the ApplyPilot production secrets.

Usage::

    python scripts/generate_secrets.py                 # print env lines
    python scripts/generate_secrets.py --env-file /etc/applypilot/backend.env

The file mode is set to 0600 where the platform supports it. Existing keys in
the target file are never overwritten: the script only appends missing values,
so it is safe to re-run during an incident. The Gmail key is a Fernet key and
must be backed up with the database; losing it makes stored refresh tokens
undecryptable.
"""

from __future__ import annotations

import argparse
import os
import secrets
import stat
import sys
from pathlib import Path

from cryptography.fernet import Fernet

SECRETS = {
    "JWT_SECRET_KEY": lambda: secrets.token_urlsafe(48),
    "OAUTH_SESSION_SECRET": lambda: secrets.token_urlsafe(48),
    "GMAIL_TOKEN_ENCRYPTION_KEY": lambda: Fernet.generate_key().decode("ascii"),
    "METRICS_TOKEN": lambda: secrets.token_urlsafe(32),
}


def _existing_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    if not path.exists():
        return keys
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        keys.add(stripped.split("=", 1)[0].strip())
    return keys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Append missing keys to this file instead of printing them.",
    )
    args = parser.parse_args()

    generated = {name: factory() for name, factory in SECRETS.items()}

    if args.env_file is None:
        print("# Add these to the backend environment file (mode 0600).")
        for name, value in generated.items():
            print(f'{name}="{value}"')
        print(
            "# Store an encrypted copy of the Gmail key with your database "
            "backups."
        )
        return 0

    target: Path = args.env_file
    target.parent.mkdir(parents=True, exist_ok=True)
    existing = _existing_keys(target)
    lines = []
    for name, value in generated.items():
        if name in existing:
            print(f"{name}: already present, left unchanged", file=sys.stderr)
            continue
        lines.append(f'{name}="{value}"')

    if lines:
        prefix = ""
        if target.exists() and target.stat().st_size > 0:
            text = target.read_text(encoding="utf-8")
            if not text.endswith("\n"):
                prefix = "\n"
        with target.open("a", encoding="utf-8") as handle:
            handle.write(prefix + "\n".join(lines) + "\n")
        if os.name == "posix":
            os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)
        print(f"Wrote {len(lines)} secret(s) to {target} (mode 0600).")
    else:
        print(f"No changes needed in {target}.")
    print("Back up the Gmail encryption key with your database.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
