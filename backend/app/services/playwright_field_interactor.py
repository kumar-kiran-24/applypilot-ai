"""Phase 2E Stage C: perform approved actions on the live page.

The interactor is a thin adapter between a planned field from
:mod:`app.services.application_field_planner` and the narrow
:mod:`app.integrations.browser.field_actions` contract. It never invents a
locator from user input, never clicks a button and never submits.
"""

from __future__ import annotations

from app.integrations.browser.field_actions import (
    FieldAction,
    FieldActionError,
    FieldActionKind,
    FieldActionResult,
)
from app.integrations.browser.persistent import PersistentBrowserHandle
from app.schemas.application_fill import FillAction
from app.services.application_field_planner import PlannedField

_KIND: dict[FillAction, FieldActionKind] = {
    FillAction.FILL: FieldActionKind.FILL,
    FillAction.SELECT: FieldActionKind.SELECT,
    FillAction.CHECK: FieldActionKind.CHECK,
    FillAction.UPLOAD: FieldActionKind.UPLOAD,
}


class PlaywrightFieldInteractor:
    """Executes only the actions the planner marked as ready."""

    async def execute(
        self,
        handle: PersistentBrowserHandle,
        item: PlannedField,
        *,
        upload_path: str | None = None,
    ) -> FieldActionResult:
        if item.action is FillAction.NONE:
            return FieldActionResult(
                False, item.field_id, reason="No action was approved for this field."
            )
        kind = _KIND.get(item.action)
        if kind is None:  # pragma: no cover - enum is exhaustive
            raise FieldActionError("Unsupported field action.")
        value = item.proposed_value or ""
        option_labels: tuple[str, ...] = ()
        if item.action is FillAction.SELECT:
            option_labels = (value,)
        elif item.action is FillAction.CHECK:
            option_labels = tuple(
                part.strip() for part in value.split(",") if part.strip()
            )
        action = FieldAction(
            kind=kind,
            field_id=item.field_id,
            label=item.label,
            html_id=item.html_id,
            name=item.name,
            text=value if kind is FieldActionKind.FILL else None,
            option_labels=option_labels,
            upload_path=upload_path,
        )
        return await handle.apply_field_action(action)


__all__ = ["PlaywrightFieldInteractor"]
