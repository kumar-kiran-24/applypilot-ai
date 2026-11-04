"""Tests for Gmail refresh-token encryption.

These tests never print or compare secrets: they assert that ciphertext differs
from plaintext and that failures carry no credential material.
"""

from __future__ import annotations

import pytest
from app.core.config import settings
from app.services.gmail_token_encryption import (
    GmailTokenDecryptionError,
    GmailTokenEncryptionError,
    GmailTokenEncryptionKeyError,
    GmailTokenEncryptionNotConfiguredError,
    decrypt_refresh_token,
    encrypt_refresh_token,
    generate_encryption_key,
    validate_encryption_key,
)
from cryptography.fernet import Fernet

REFRESH_TOKEN = "1//0g-test-refresh-token-value"


@pytest.fixture(autouse=True)
def configured_key(monkeypatch: pytest.MonkeyPatch) -> str:
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "GMAIL_TOKEN_ENCRYPTION_KEY", key)
    return key


def test_encrypt_decrypt_round_trip() -> None:
    encrypted = encrypt_refresh_token(REFRESH_TOKEN)

    assert encrypted != REFRESH_TOKEN
    assert REFRESH_TOKEN not in encrypted
    assert decrypt_refresh_token(encrypted) == REFRESH_TOKEN


def test_ciphertext_is_randomised_per_call() -> None:
    first = encrypt_refresh_token(REFRESH_TOKEN)
    second = encrypt_refresh_token(REFRESH_TOKEN)

    assert first != second
    assert decrypt_refresh_token(first) == decrypt_refresh_token(second) == REFRESH_TOKEN


def test_stored_value_does_not_reveal_the_token() -> None:
    encrypted = encrypt_refresh_token(REFRESH_TOKEN)

    assert "refresh" not in encrypted.lower()
    assert encrypted.startswith("gAAAAA")


def test_missing_configuration_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "GMAIL_TOKEN_ENCRYPTION_KEY", None)

    with pytest.raises(GmailTokenEncryptionNotConfiguredError) as excinfo:
        encrypt_refresh_token(REFRESH_TOKEN)

    assert excinfo.value.code == "gmail_encryption_not_configured"
    assert "GMAIL_TOKEN_ENCRYPTION_KEY" in str(excinfo.value)


def test_blank_configuration_is_treated_as_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "GMAIL_TOKEN_ENCRYPTION_KEY", "   ")

    with pytest.raises(GmailTokenEncryptionNotConfiguredError):
        encrypt_refresh_token(REFRESH_TOKEN)


@pytest.mark.parametrize(
    "invalid_key",
    [
        "not-a-fernet-key",
        "c2hvcnQta2V5",  # valid base64, wrong length
        "!!!!not-base64!!!!",
    ],
)
def test_invalid_key_format_is_rejected(monkeypatch: pytest.MonkeyPatch, invalid_key: str) -> None:
    monkeypatch.setattr(settings, "GMAIL_TOKEN_ENCRYPTION_KEY", invalid_key)

    with pytest.raises(GmailTokenEncryptionKeyError) as excinfo:
        encrypt_refresh_token(REFRESH_TOKEN)

    assert excinfo.value.code == "gmail_encryption_key_invalid"
    assert invalid_key not in str(excinfo.value)


def test_validate_encryption_key_returns_stripped_key() -> None:
    key = Fernet.generate_key().decode()

    assert validate_encryption_key(f"  {key}\n") == key


def test_decryption_with_a_different_key_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    encrypted = encrypt_refresh_token(REFRESH_TOKEN)
    monkeypatch.setattr(settings, "GMAIL_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())

    with pytest.raises(GmailTokenDecryptionError) as excinfo:
        decrypt_refresh_token(encrypted)

    assert "reconnect gmail" in str(excinfo.value).lower()
    assert encrypted not in str(excinfo.value)


def test_corrupt_ciphertext_fails_cleanly() -> None:
    with pytest.raises(GmailTokenDecryptionError):
        decrypt_refresh_token("gAAAAA-not-really-a-token")


def test_empty_ciphertext_is_rejected() -> None:
    with pytest.raises(GmailTokenDecryptionError):
        decrypt_refresh_token("   ")


@pytest.mark.parametrize("value", ["", "   ", None])
def test_encrypt_rejects_empty_tokens(value: str | None) -> None:
    with pytest.raises(GmailTokenEncryptionError):
        encrypt_refresh_token(value)  # type: ignore[arg-type]


def test_generated_key_is_valid_and_not_auto_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "GMAIL_TOKEN_ENCRYPTION_KEY", None)
    generated = generate_encryption_key()

    assert validate_encryption_key(generated) == generated
    # Generating a key must not silently configure or use it.
    with pytest.raises(GmailTokenEncryptionNotConfiguredError):
        encrypt_refresh_token(REFRESH_TOKEN)


def test_encryption_key_is_separate_from_other_secrets(configured_key: str) -> None:
    assert configured_key != settings.JWT_SECRET_KEY
    assert configured_key != settings.OAUTH_SESSION_SECRET
