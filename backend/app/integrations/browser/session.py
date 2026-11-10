"""Contracts for an isolated, ephemeral browser session."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from app.models.application import QuestionType


class BrowserUnavailableError(RuntimeError):
    """Automation is disabled or Playwright is not installed."""


class ManualInterventionRequired(RuntimeError):
    """A human must complete a step (password, OTP, CAPTCHA, MFA).

    The driver raises this instead of trying to solve or bypass the protection.
    """

    def __init__(self, reason: str, *, step: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.step = step


@dataclass(frozen=True)
class RawFormField:
    """One input/section detected on a live form."""

    identifier: str
    label: str
    field_type: QuestionType
    required: bool = False
    options: tuple[str, ...] = field(default_factory=tuple)


@runtime_checkable
class AutomationSession(Protocol):
    """Operations an adapter may perform inside one browser session.

    Implementations must never accept or return passwords, OTPs, MFA codes or
    CAPTCHA solutions, must clean up the browser when the session ends, and
    expose no submit/click primitive: the final application action belongs to
    the user on the employer's page.
    """

    async def inspect(self, selectors: object) -> tuple[list[RawFormField], bool]:
        """Return detected fields and whether sign-in/registration is required."""
        ...

    async def fill(self, values: dict[str, str], selectors: object) -> list[str]:
        """Fill mapped fields and return the identifiers that were filled."""
        ...

    async def upload_file(self, path: str, selector: str) -> str | None:
        """Attach a file to the first matching input; return its identifier."""
        ...

    async def validation_errors(self, selectors: object) -> list[str]:
        """Return text of client-side validation errors currently shown."""
        ...


__all__ = [
    "AutomationSession",
    "BrowserUnavailableError",
    "ManualInterventionRequired",
    "RawFormField",
]
