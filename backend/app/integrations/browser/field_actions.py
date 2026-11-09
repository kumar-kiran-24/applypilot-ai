"""Narrow, non-submitting actions the fill engine may perform on a live page.

Stage C needs to type into a page, which Stage B deliberately could not do.
Rather than expose arbitrary JavaScript or a generic element-click primitive,
this module defines the *only* four interactions the engine can request:

* fill a text-like control;
* choose one option in a select or radio group;
* check one or more options in a checkbox group;
* attach the already-owned resume PDF to a file input.

There is no click, submit, keypress or navigation action. The Playwright
implementation additionally refuses credential/verification controls and never
touches a field whose stored query is a password, OTP, MFA or CAPTCHA input.

Values live only in process memory for the duration of an action; they are
never logged or persisted by this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class FieldActionKind(str, Enum):
    """The only operations the fill engine may request."""

    FILL = "fill"
    SELECT = "select"
    CHECK = "check"
    UPLOAD = "upload"


@dataclass(frozen=True)
class FieldAction:
    """One requested interaction with a single analysed field.

    ``field_id`` is the non-secret Stage B field identifier. ``html_id`` and
    ``name`` are optional stable hints used to find the same control again;
    ``label`` is the last-resort accessible-name match.
    """

    kind: FieldActionKind
    field_id: str
    label: str
    html_id: str | None = None
    name: str | None = None
    text: str | None = None
    option_labels: tuple[str, ...] = field(default_factory=tuple)
    upload_path: str | None = None


@dataclass(frozen=True)
class FieldActionResult:
    """What the browser observed after an action.

    ``observed_value`` is used only for immediate verification. The service
    does not persist it: execution records keep status and reasons, not the
    employer-form contents or the candidate's personal data.
    """

    ok: bool
    field_id: str
    observed_value: str | None = None
    reason: str = ""


class FieldActionError(RuntimeError):
    """The requested interaction could not be performed safely."""


__all__ = [
    "FieldAction",
    "FieldActionError",
    "FieldActionKind",
    "FieldActionResult",
]
