"""Per-request correlation context.

The request id is used only for log correlation and the ``X-Request-ID``
response header. It is not authentication, is never trusted for authorization
and contains no user data.
"""

from __future__ import annotations

from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar("applypilot_request_id", default="")


def current_request_id() -> str:
    """Return the current request id, or an empty string outside a request."""

    return request_id_var.get()


__all__ = ["current_request_id", "request_id_var"]
