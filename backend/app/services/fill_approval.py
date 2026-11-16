"""Phase 2J.3: bind a fill run to the exact plan the user approved.

The plan preview and the fill execution used to be two independent reads of the
same analysis. Between them the user can edit an answer, re-analyse a different
page or move to the next step of a multi-step form, and the engine would still
type whatever the new plan said because "start" never referenced the preview.

The approval fingerprint is the missing link. It is computed deterministically
from the page/step, the form's structure and every planned
``(field, action, status, value)`` row. The preview returns it; the start and
resume calls must echo it; a mismatch is refused instead of filling.

The digest deliberately excludes the analysis id/version: re-analyzing an
unchanged page produces a new immutable analysis row but the same material
content, and that must not force the user to approve identical fields twice.
Any page, step, structure, status or value change still invalidates it.

Nothing is persisted on a schema level, so the existing execution row's
``summary`` JSON records the approved fingerprint and no migration is needed.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence

APPROVAL_FINGERPRINT_VERSION = 2

APPROVAL_STALE_MESSAGE = (
    "The form, page or proposed values changed since you reviewed this plan. "
    "Review the current form again before anything is filled."
)


def approval_fingerprint(
    *,
    page_url: str | None,
    application_step: str | None,
    structure: str,
    fields: Iterable[tuple[str, str, str, str | None]],
    resume: Sequence[str] | None = None,
) -> str:
    """Return the digest of exactly what the user would approve.

    ``fields`` rows are ``(field_id, action, status, proposed_value)``. The
    status is part of the digest because a field moving from "ready" to "needs
    confirmation" is a material change even when its value is unchanged.
    """
    payload = {
        "version": APPROVAL_FINGERPRINT_VERSION,
        "page_url": page_url or "",
        "application_step": application_step or "",
        "structure": structure,
        "fields": [
            [str(field_id), str(action), str(status), value or ""]
            for field_id, action, status, value in fields
        ],
        "resume": [str(part) for part in (resume or ())],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "APPROVAL_FINGERPRINT_VERSION",
    "APPROVAL_STALE_MESSAGE",
    "approval_fingerprint",
]
