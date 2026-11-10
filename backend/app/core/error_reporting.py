"""Optional vendored error tracking.

When ``ERROR_WEBHOOK_URL`` is configured, unhandled exceptions are reported to
an HTTP endpoint (for example a small relay in front of Sentry, or an internal
alerting service) as structured JSON. No SDK and no third-party account are
required, which keeps the pilot deployable without another vendor.

The payload is deliberately narrow: error class, HTTP method, route template,
status and request id. Exception messages are excluded because they can contain
SQL fragments or employer page content; tokens, cookies, headers and form
values are never included.
"""

from __future__ import annotations

import asyncio
import json
import urllib.request

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def build_error_payload(
    *,
    error: BaseException,
    method: str,
    route: str,
    request_id: str,
    status_code: int = 500,
) -> dict[str, object]:
    """Return the exact, non-sensitive payload sent to the webhook."""
    return {
        "service": "applypilot-backend",
        "environment": settings.ENVIRONMENT,
        "event": "unhandled_exception",
        "error_class": type(error).__name__,
        "method": method,
        "route": route,
        "request_id": request_id,
        "status_code": status_code,
    }


async def report_unhandled_error(
    *,
    error: BaseException,
    method: str,
    route: str,
    request_id: str,
) -> None:
    """Fire-and-forget webhook POST; never raises and never blocks a response."""
    url = settings.ERROR_WEBHOOK_URL
    if not url:
        return
    payload = json.dumps(
        build_error_payload(
            error=error,
            method=method,
            route=route,
            request_id=request_id,
        )
    ).encode("utf-8")

    def send() -> None:
        request = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(
            request, timeout=settings.ERROR_WEBHOOK_TIMEOUT_SECONDS
        ):
            return

    try:
        await asyncio.to_thread(send)
    except Exception as exc:  # pragma: no cover - monitoring must never break requests
        logger.warning(
            "Error webhook delivery failed",
            extra={"stage": "error_webhook", "error_class": type(exc).__name__},
        )


__all__ = ["build_error_payload", "report_unhandled_error"]
