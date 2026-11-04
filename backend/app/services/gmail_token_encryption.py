"""Symmetric encryption for Gmail refresh tokens.

A Gmail refresh token is a long-lived credential that grants read access to a
user's mailbox, so it is encrypted with Fernet (AES-128-CBC + HMAC-SHA256)
before it is ever written to the database. Three rules are enforced here:

* refresh tokens are never returned in plaintext;
* the key is never generated automatically at startup (that would silently
  invalidate every stored token on restart);
* a missing or malformed key raises a clear, non-leaking error.

Generating the key (run once, store it in the secret manager / ``.env``):

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

Keep it *separate* from ``JWT_SECRET_KEY`` and ``OAUTH_SESSION_SECRET``. Back it
up with the database: losing the key makes every stored refresh token
undecryptable and every user has to reconnect Gmail. Rotating the key means
decrypting with the old key and re-encrypting with the new one (or asking users
to reconnect) - there is deliberately no automatic rotation.
"""

from __future__ import annotations

import base64
import binascii
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings
from app.core.exceptions import AppError

# Fernet keys are urlsafe-base64 encodings of exactly 32 raw bytes.
FERNET_KEY_BYTES = 32


class GmailTokenEncryptionError(AppError):
    """Base class for Gmail token encryption failures."""

    status_code = 500
    code = "gmail_token_encryption_error"
    message = "Gmail credentials could not be secured."


class GmailTokenEncryptionNotConfiguredError(GmailTokenEncryptionError):
    """``GMAIL_TOKEN_ENCRYPTION_KEY`` is missing."""

    code = "gmail_encryption_not_configured"
    message = (
        "Gmail token encryption is not configured. Set GMAIL_TOKEN_ENCRYPTION_KEY "
        "to a Fernet key before connecting Gmail."
    )


class GmailTokenEncryptionKeyError(GmailTokenEncryptionError):
    """``GMAIL_TOKEN_ENCRYPTION_KEY`` is not a valid Fernet key."""

    code = "gmail_encryption_key_invalid"
    message = (
        "GMAIL_TOKEN_ENCRYPTION_KEY is not a valid Fernet key. It must be the "
        "urlsafe-base64 encoding of 32 bytes, e.g. the output of "
        "Fernet.generate_key()."
    )


class GmailTokenDecryptionError(GmailTokenEncryptionError):
    """A stored token could not be decrypted with the configured key."""

    code = "gmail_token_decryption_failed"
    message = (
        "Stored Gmail credentials could not be decrypted. The encryption key may "
        "have changed; reconnect Gmail to store a new refresh token."
    )


def validate_encryption_key(raw_key: str) -> str:
    """Return ``raw_key`` stripped, after checking it is a valid Fernet key.

    Raises:
        GmailTokenEncryptionKeyError: the value is not urlsafe-base64 or does
            not decode to 32 bytes.
    """
    candidate = raw_key.strip()
    try:
        decoded = base64.urlsafe_b64decode(candidate.encode("ascii"))
    except (binascii.Error, UnicodeEncodeError, ValueError):
        raise GmailTokenEncryptionKeyError() from None
    if len(decoded) != FERNET_KEY_BYTES:
        raise GmailTokenEncryptionKeyError()
    return candidate


def _load_key(key: str | None = None) -> str:
    """Resolve and validate the encryption key, without ever logging it."""
    raw_key = key if key is not None else get_settings().GMAIL_TOKEN_ENCRYPTION_KEY
    if not raw_key or not raw_key.strip():
        raise GmailTokenEncryptionNotConfiguredError()
    return validate_encryption_key(raw_key)


@lru_cache(maxsize=8)
def _fernet(key: str) -> Fernet:
    """Return a cached Fernet instance for an already-validated key."""
    return Fernet(key.encode("ascii"))


def encrypt_refresh_token(refresh_token: str) -> str:
    """Encrypt a Gmail refresh token for storage.

    The returned ciphertext is a Fernet token (base64 text). It is safe to store
    in the ``encrypted_refresh_token`` column but must never be logged.
    """
    if not isinstance(refresh_token, str) or not refresh_token.strip():
        raise GmailTokenEncryptionError(
            "A non-empty Gmail refresh token is required for encryption."
        )
    return _fernet(_load_key()).encrypt(refresh_token.strip().encode("utf-8")).decode("ascii")


def decrypt_refresh_token(encrypted_token: str) -> str:
    """Decrypt a stored Gmail refresh token.

    Raises:
        GmailTokenEncryptionError: configuration is missing or invalid.
        GmailTokenDecryptionError: the ciphertext is corrupt or was produced
            with a different key.
    """
    if not isinstance(encrypted_token, str) or not encrypted_token.strip():
        raise GmailTokenDecryptionError()
    try:
        plaintext = _fernet(_load_key()).decrypt(encrypted_token.strip().encode("ascii"))
    except InvalidToken:
        raise GmailTokenDecryptionError() from None
    except UnicodeEncodeError:
        raise GmailTokenDecryptionError() from None
    return plaintext.decode("utf-8", errors="replace")


def generate_encryption_key() -> str:
    """Return a fresh Fernet key for provisioning (never called automatically)."""
    return Fernet.generate_key().decode("ascii")


__all__ = [
    "GmailTokenDecryptionError",
    "GmailTokenEncryptionError",
    "GmailTokenEncryptionKeyError",
    "GmailTokenEncryptionNotConfiguredError",
    "decrypt_refresh_token",
    "encrypt_refresh_token",
    "generate_encryption_key",
    "validate_encryption_key",
]
