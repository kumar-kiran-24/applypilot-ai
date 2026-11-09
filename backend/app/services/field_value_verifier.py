"""Phase 2E Stage C: verify that an interaction actually landed.

Verification compares only the value the engine intended with the value the
browser reports immediately afterwards. It never stores or logs those values.
"""

from __future__ import annotations

import re
from datetime import date

from app.integrations.browser.field_actions import FieldActionResult
from app.schemas.application_fill import FillAction
from app.services.application_field_planner import PlannedField


def _normalise(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip()).casefold()


def _number(value: str) -> float | None:
    try:
        return float((value or "").replace(",", "").strip())
    except ValueError:
        return None


class FieldValueVerifier:
    """Determines whether a completed action can be trusted."""

    def verify(self, item: PlannedField, result: FieldActionResult) -> tuple[bool, str]:
        if not result.ok:
            return False, result.reason or "The interaction did not complete."

        expected = item.proposed_value or ""
        observed = result.observed_value
        if observed is None:
            return False, "The page did not report the value it received."

        if item.action is FillAction.UPLOAD:
            if not observed.strip():
                return False, "The page did not attach a file."
            expected_name = expected.strip().casefold()
            observed_name = observed.strip().casefold()
            if expected_name and expected_name != observed_name:
                # Browsers may rewrite the basename; a mismatch is reported
                # rather than assumed correct.
                return (
                    False,
                    "The attached file name does not match the selected resume.",
                )
            return True, "The selected resume is attached."

        if item.action is FillAction.FILL:
            if item.field_type.value == "date":
                try:
                    same = date.fromisoformat(expected[:10]) == date.fromisoformat(
                        observed[:10]
                    )
                except ValueError:
                    same = _normalise(expected) == _normalise(observed)
                return (True, "The value was verified.") if same else (
                    False,
                    "The page did not keep the expected date.",
                )
            if item.field_type.value == "number":
                left, right = _number(expected), _number(observed)
                if left is not None and right is not None:
                    return (True, "The value was verified.") if left == right else (
                        False,
                        "The page did not keep the expected number.",
                    )
            same = _normalise(expected) == _normalise(observed)
            return (True, "The value was verified.") if same else (
                False,
                "The page did not keep the expected value.",
            )

        if item.action is FillAction.SELECT:
            same = _normalise(expected) == _normalise(observed)
            return (True, "The option was verified.") if same else (
                False,
                "The page did not keep the chosen option.",
            )

        if item.action is FillAction.CHECK:
            expected_values = {
                _normalise(part)
                for part in expected.split(",")
                if _normalise(part)
            }
            observed_values = {
                _normalise(part)
                for part in (observed or "").split(",")
                if _normalise(part)
            }
            if expected_values <= observed_values:
                return True, "The selected option was verified."
            return False, "The page did not keep the selected option."

        return False, "The action could not be verified."


__all__ = ["FieldValueVerifier"]
