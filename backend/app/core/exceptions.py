"""Application exception hierarchy and safe error payload construction."""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class for all expected, domain-level application errors.

    Subclasses carry the HTTP status and a stable machine-readable ``code`` so
    that API handlers can translate errors without leaking internals.
    """

    status_code: int = 500
    code: str = "internal_error"
    message: str = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        details: list[dict[str, Any]] | None = None,
    ) -> None:
        self.message = message or type(self).message
        self.code = code or type(self).code
        self.details = details or []
        super().__init__(self.message)

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"detail": self.message, "code": self.code}
        if self.details:
            payload["details"] = self.details
        return payload


class ResourceNotFoundError(AppError):
    """Requested resource does not exist."""

    status_code = 404
    code = "not_found"
    message = "The requested resource was not found."


class DuplicateResourceError(AppError):
    """Unique constraint would be violated."""

    status_code = 409
    code = "conflict"
    message = "The resource already exists."


class InvalidOperationError(AppError):
    """Request conflicts with the current state of a resource."""

    status_code = 400
    code = "invalid_operation"
    message = "The requested operation is not allowed."


class ConfigurationError(AppError):
    """Required configuration is missing or invalid."""

    status_code = 500
    code = "configuration_error"
    message = "The application is not configured correctly."
