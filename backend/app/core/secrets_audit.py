"""Non-reversible secret fingerprints for deployment verification.

Operators need to prove that the secrets loaded by the running service match the
ones in the encrypted backup, without ever printing a secret. A truncated
SHA-256 fingerprint does that: it is stable for the same value, useless for
recovering it, and cannot be compared against a leaked dump.
"""

from __future__ import annotations

import hashlib

from app.core.config import Settings

#: Log field -> settings attribute. Field names deliberately avoid redaction
#: markers ("secret", "token", "key") so the fingerprint is actually emitted;
#: they describe a hash, not a credential.
_FINGERPRINTED = {
    "fingerprint_jwt": "JWT_SECRET_KEY",
    "fingerprint_oauth": "OAUTH_SESSION_SECRET",
    "fingerprint_gmail": "GMAIL_TOKEN_ENCRYPTION_KEY",
    "fingerprint_metrics": "METRICS_TOKEN",
}


def secret_fingerprint(value: str | None) -> str:
    """Return a 12-hex-character fingerprint, or ``unset``."""
    if not value:
        return "unset"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def settings_fingerprints(settings: Settings) -> dict[str, str]:
    """Fingerprint each configured secret for the startup log."""
    return {
        field: secret_fingerprint(getattr(settings, attribute, None))
        for field, attribute in _FINGERPRINTED.items()
    }


__all__ = ["secret_fingerprint", "settings_fingerprints"]
