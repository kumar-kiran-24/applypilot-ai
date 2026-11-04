"""Log redaction tests: OAuth values must never reach the log stream."""

from __future__ import annotations

import logging

from app.core.logging import RedactingFilter


def _record(message: str, *args: object) -> logging.LogRecord:
    return logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=args,
        exc_info=None,
    )


def test_filter_redacts_oauth_query_values() -> None:
    record = _record(
        'GET /api/v1/gmail/callback?code=4/0AbCdEf&state=secret-state HTTP/1.1" 200'
    )

    assert RedactingFilter().filter(record) is True

    rendered = record.getMessage()
    assert "4/0AbCdEf" not in rendered
    assert "secret-state" not in rendered
    assert "code=[REDACTED]" in rendered
    assert "state=[REDACTED]" in rendered


def test_filter_redacts_tokens_in_lower_and_upper_case() -> None:
    record = _record(
        "callback?ACCESS_TOKEN=ya29.value&refresh_token=1//value&id_token=eyJ.value"
    )

    RedactingFilter().filter(record)
    rendered = record.getMessage()

    for value in ("ya29.value", "1//value", "eyJ.value"):
        assert value not in rendered
    assert rendered.count("[REDACTED]") == 3


def test_filter_redacts_percent_formatted_messages() -> None:
    record = _record(
        '%s - "GET /api/v1/auth/google/callback?code=%s HTTP/1.1" 200',
        "127.0.0.1:5000",
        "one-time-code",
    )

    RedactingFilter().filter(record)
    rendered = record.getMessage()

    assert "one-time-code" not in rendered
    assert "127.0.0.1:5000" in rendered
    assert "code=[REDACTED]" in rendered


def test_filter_leaves_ordinary_messages_unchanged() -> None:
    record = _record('GET /api/v1/gmail/status HTTP/1.1" 200')
    original = record.getMessage()

    RedactingFilter().filter(record)

    assert record.getMessage() == original
