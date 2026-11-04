"""Centralised logging with JSON output and secret redaction.

Secrets must never reach the log stream: credential-like keys passed via
``extra`` are replaced with a placeholder, and settings objects are never
logged wholesale.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timezone
from typing import Any

from app.core.config import Settings, get_settings

_RESERVED_RECORD_KEYS = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__)

SENSITIVE_KEY_MARKERS = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "private_key",
    "client_secret",
)

REDACTED = "[REDACTED]"

# OAuth codes, state and tokens can appear inside a rendered message (uvicorn's
# access log includes the full query string). They are redacted in-place so a
# callback URL never writes a usable authorization code to the log stream.
_SENSITIVE_QUERY_PARAM = re.compile(
    r"(?i)\b(code|state|access_token|refresh_token|id_token|client_secret)=([^&\s\"]+)"
)


def is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in SENSITIVE_KEY_MARKERS)


def redact(value: Any) -> Any:
    """Recursively redact sensitive keys from mappings/sequences."""
    if isinstance(value, dict):
        return {
            key: (REDACTED if is_sensitive_key(str(key)) else redact(item))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    return value


class RedactingFilter(logging.Filter):
    """Redact sensitive query parameters from the rendered log message.

    Attached to the handler (not a logger) so records propagated from uvicorn's
    loggers are filtered too. Structured ``extra`` fields are handled by
    :class:`JsonFormatter`; this filter covers string messages such as access
    log lines.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - malformed record
            return True
        redacted = _SENSITIVE_QUERY_PARAM.sub(r"\1=[REDACTED]", message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


class JsonFormatter(logging.Formatter):
    """Render log records as single-line JSON."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        for key, value in record.__dict__.items():
            if key in _RESERVED_RECORD_KEYS or key.startswith("_"):
                continue
            if key in {"message", "asctime"}:
                continue
            payload[key] = REDACTED if is_sensitive_key(key) else redact(value)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str, ensure_ascii=False)


def setup_logging(settings: Settings | None = None, level: int | None = None) -> None:
    """Configure the root logger once, at process start."""
    settings = settings or get_settings()
    resolved_level = level or (logging.DEBUG if settings.DEBUG else logging.INFO)

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactingFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(resolved_level)
    root.addHandler(handler)

    # Uvicorn loggers propagate to root so all output shares one format.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True


def get_logger(name: str) -> logging.Logger:
    """Return a module-scoped logger."""
    return logging.getLogger(name)
