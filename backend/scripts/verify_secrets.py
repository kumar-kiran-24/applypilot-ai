"""Verify a deployment's secret configuration without printing secrets.

Checks:
* the loaded settings pass the production validator;
* JWT, OAuth, Gmail and metrics secrets are distinct;
* the Gmail key can encrypt/decrypt a round-trip;
* optionally, the environment file is not group/world readable.

Usage::

    python scripts/verify_secrets.py --environment production
    python scripts/verify_secrets.py --env-file /etc/applypilot/backend.env
"""

from __future__ import annotations

import argparse
import os
import stat
import sys
from pathlib import Path

# Allow `python scripts/verify_secrets.py` from the backend directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings
from app.core.secrets_audit import settings_fingerprints
from pydantic import ValidationError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--environment",
        default=None,
        help="Override ENVIRONMENT for the validation pass (e.g. production).",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="Also check the permission bits of this environment file.",
    )
    args = parser.parse_args()

    overrides = {}
    if args.environment:
        overrides["ENVIRONMENT"] = args.environment

    failures: list[str] = []
    try:
        settings = Settings(**overrides)
    except ValidationError as exc:
        print("FAIL: settings validation failed")
        for error in exc.errors():
            location = ".".join(str(part) for part in error.get("loc", ()))
            print(f"  - {location}: {error.get('msg', 'invalid')}")
        failures.append("settings")

    if not failures:
        values = {
            "JWT_SECRET_KEY": settings.JWT_SECRET_KEY,
            "OAUTH_SESSION_SECRET": settings.OAUTH_SESSION_SECRET,
            "GMAIL_TOKEN_ENCRYPTION_KEY": settings.GMAIL_TOKEN_ENCRYPTION_KEY,
            "METRICS_TOKEN": settings.METRICS_TOKEN,
        }
        configured = {name: value for name, value in values.items() if value}
        if len(set(configured.values())) != len(configured):
            print("FAIL: two secrets share the same value")
            failures.append("duplicate-secrets")

        fernet_key = settings.GMAIL_TOKEN_ENCRYPTION_KEY
        if fernet_key:
            try:
                from cryptography.fernet import Fernet

                fernet = Fernet(fernet_key.encode("ascii"))
                probe = b"applypilot-secret-check"
                if fernet.decrypt(fernet.encrypt(probe)) != probe:
                    raise ValueError("round-trip mismatch")
            except Exception:
                print("FAIL: GMAIL_TOKEN_ENCRYPTION_KEY cannot encrypt/decrypt")
                failures.append("fernet")

        print(f"Environment: {settings.ENVIRONMENT}")
        print("Secret fingerprints (safe to record in a runbook):")
        for name, fingerprint in settings_fingerprints(settings).items():
            print(f"  {name}: {fingerprint}")

    if args.env_file is not None:
        path = args.env_file
        if not path.exists():
            print(f"FAIL: {path} does not exist")
            failures.append("env-file")
        elif os.name == "posix":
            mode = stat.S_IMODE(path.stat().st_mode)
            if mode & (stat.S_IRGRP | stat.S_IROTH | stat.S_IWGRP | stat.S_IWOTH):
                print(f"FAIL: {path} mode is {oct(mode)}; expected 0600")
                failures.append("env-file-permissions")
            else:
                print(f"Permissions OK: {path} is 0600")

    if failures:
        print(f"FAILED: {', '.join(failures)}")
        return 1
    print("OK: secrets are configured and distinct")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
