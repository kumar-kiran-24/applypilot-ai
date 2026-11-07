"""Rules for values that must never enter the reusable answer bank.

Passwords, one-time codes, authentication cookies, session tokens and payment
card details are never stored, reused or logged, whatever a form calls them.
This module is importable from both the answer matcher and the candidate
service without creating a service-level import cycle.
"""

from __future__ import annotations

import re

#: Never eligible for the reusable answer bank, whatever a form calls them.
FORBIDDEN_ANSWER_TOKENS = frozenset(
    {
        "password",
        "passcode",
        "passphrase",
        "otp",
        "mfa",
        "2fa",
        "captcha",
        "cvv",
        "cvc",
        "iban",
    }
)

FORBIDDEN_ANSWER_PHRASES: tuple[str, ...] = (
    "one time code",
    "one time password",
    "credit card",
    "debit card",
    "card number",
    "cardholder name",
    "security code",
    "authentication cookie",
    "auth cookie",
    "session cookie",
    "session token",
    "access token",
    "bank account",
    "routing number",
    "sort code",
    "payment information",
    "payment details",
)


def normalise_for_safety(question_key: str, question_text: str = "") -> str:
    """Normalise a question into the token stream checked for secrets."""
    return re.sub(
        r"[^a-z0-9]+", " ", f"{question_key} {question_text}".casefold()
    ).strip()


def is_forbidden_answer_storage(question_key: str, question_text: str = "") -> bool:
    """Return True for credentials, one-time codes and payment data.

    The reusable answer bank is user-scoped, but these values must never be
    stored at all, not even after an explicit confirmation.
    """
    normalised = normalise_for_safety(question_key, question_text)
    if set(normalised.split()) & FORBIDDEN_ANSWER_TOKENS:
        return True
    return any(phrase in normalised for phrase in FORBIDDEN_ANSWER_PHRASES)


__all__ = [
    "FORBIDDEN_ANSWER_PHRASES",
    "FORBIDDEN_ANSWER_TOKENS",
    "is_forbidden_answer_storage",
    "normalise_for_safety",
]
