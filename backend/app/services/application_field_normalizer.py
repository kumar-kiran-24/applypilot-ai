"""Phase 2J.3: normalise detected controls into the canonical field shape.

The Stage B analysis stores ``DetectedFormField`` records. The approval screen
must show the same controls the engine will fill, with their provenance,
sensitivity and status, so every read path goes through this module instead of
re-deriving its own view.

Two deterministic fingerprints are produced here:

* ``structure_fingerprint`` - what the form *is* (identifiers, types, required
  flags, options, visibility). It deliberately excludes current values, so a
  user typing into the employer's page does not look like a changed form.
* ``approval_fingerprint`` (in :mod:`app.services.fill_approval`) - what the
  user approved, including the exact proposed values.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import datetime

from app.schemas.application_field import (
    ApplicationField,
    FieldProvenance,
    FieldSensitivity,
    FieldVerification,
)
from app.schemas.application_form import (
    DetectedFormField,
    FieldCategory,
    FieldSource,
    FormFieldType,
)

_INPUT_TYPES: dict[FormFieldType, str] = {
    FormFieldType.TEXT: "text",
    FormFieldType.EMAIL: "email",
    FormFieldType.PHONE: "tel",
    FormFieldType.NUMBER: "number",
    FormFieldType.DATE: "date",
    FormFieldType.URL: "url",
    FormFieldType.TEXTAREA: "textarea",
    FormFieldType.SELECT: "select",
    FormFieldType.RADIO_GROUP: "radio",
    FormFieldType.CHECKBOX_GROUP: "checkbox",
    FormFieldType.CHECKBOX: "checkbox",
    FormFieldType.FILE: "file",
    FormFieldType.PASSWORD: "password",
    FormFieldType.HIDDEN: "hidden",
    FormFieldType.CAPTCHA: "text",
    FormFieldType.OTHER: "text",
}

_SENSITIVE_CATEGORIES: frozenset[FieldCategory] = frozenset(
    {
        FieldCategory.CURRENT_SALARY,
        FieldCategory.EXPECTED_SALARY,
        FieldCategory.NOTICE_PERIOD,
        FieldCategory.RELOCATION,
        FieldCategory.WORK_AUTHORIZATION,
    }
)

_VERIFICATION_BY_SOURCE: dict[FieldSource, FieldVerification] = {
    FieldSource.VERIFIED_PROFILE: FieldVerification.VERIFIED,
    FieldSource.CONFIRMED_REUSABLE_ANSWER: FieldVerification.VERIFIED,
    FieldSource.USER_PROVIDED: FieldVerification.VERIFIED,
    FieldSource.RESUME_SUGGESTION: FieldVerification.DERIVED,
    FieldSource.NONE: FieldVerification.UNVERIFIED,
}


def input_type_for(field_type: FormFieldType) -> str:
    return _INPUT_TYPES.get(field_type, "text")


def sensitivity_for(field: DetectedFormField) -> FieldSensitivity:
    """Classify how carefully the control's value must be handled."""
    if field.field_type is FormFieldType.PASSWORD:
        return FieldSensitivity.CREDENTIAL
    if field.classification is FieldCategory.CAPTCHA or (
        field.classification
        in {FieldCategory.OTP_VERIFICATION, FieldCategory.MFA_VERIFICATION}
    ):
        return FieldSensitivity.VERIFICATION
    if field.is_legal or field.classification is FieldCategory.LEGAL_DECLARATION:
        return FieldSensitivity.LEGAL
    if field.classification is FieldCategory.DEMOGRAPHIC_INFORMATION:
        return FieldSensitivity.DEMOGRAPHIC
    if field.is_sensitive or field.classification in _SENSITIVE_CATEGORIES:
        return FieldSensitivity.SENSITIVE
    return FieldSensitivity.PUBLIC


def verification_for(source: FieldSource) -> FieldVerification:
    return _VERIFICATION_BY_SOURCE.get(source, FieldVerification.UNVERIFIED)


def accessible_name_for(field: DetectedFormField) -> str:
    """A reasonable accessible name, using only detected label metadata."""
    return (field.label or field.placeholder or field.name or "").strip()[:500]


def locator_ref_for(field_id: str) -> str:
    """Opaque, safe reference. Never a selector the client could replay."""
    return f"field:{field_id}"


def normalise_fields(
    fields: Iterable[DetectedFormField],
    *,
    page_url: str | None = None,
    application_step: str | None = None,
    analyzed_at: datetime | None = None,
) -> list[ApplicationField]:
    """Return the canonical view of an analysis, in page order.

    A control that appears twice is kept once per occurrence (the analysis
    already de-duplicates identifiers) and any genuinely repeated identifier is
    marked ``duplicate_of`` and made non-fillable, so a stale or malformed
    analysis can never cause the same control to be filled twice.
    """
    normalised: list[ApplicationField] = []
    seen: dict[str, str] = {}
    for field in fields:
        duplicate_of = seen.get(field.field_id)
        seen.setdefault(field.field_id, field.field_id)
        source = field.source
        normalised.append(
            ApplicationField(
                field_id=field.field_id,
                question_key=field.question_key,
                name=field.name,
                label=(field.label or "").strip(),
                accessible_name=accessible_name_for(field),
                options=list(field.options),
                field_type=field.field_type,
                input_type=input_type_for(field.field_type),
                classification=field.classification,
                required=field.required,
                visible=field.visible,
                current_value=field.current_value,
                proposed_value=None,
                candidate_answer=None,
                source=source.source,
                provenance=FieldProvenance(
                    source=source.source,
                    source_record_id=source.source_record_id,
                    last_confirmed_at=source.last_confirmed_at,
                    confidence=source.confidence,
                    stale=source.stale,
                    explanation=source.explanation,
                ),
                verification_status=verification_for(source.source),
                sensitivity=sensitivity_for(field),
                fillable=bool(field.automation_eligible and duplicate_of is None),
                requires_confirmation=bool(
                    field.requires_confirmation
                    or duplicate_of is not None
                    or sensitivity_for(field) is not FieldSensitivity.PUBLIC
                ),
                already_populated=bool((field.current_value or "").strip()),
                form_index=field.form_index,
                page_url=page_url,
                application_step=application_step,
                locator_ref=locator_ref_for(field.field_id),
                analyzed_at=analyzed_at,
                duplicate_of=duplicate_of,
            )
        )
    return normalised


def structure_fingerprint(fields: Iterable[object]) -> str:
    """Digest of the form's structure, excluding every current value.

    Accepts either ``DetectedFormField`` records or canonical
    ``ApplicationField`` rows so the stored analysis and a fresh live analysis
    can be compared without converting twice.
    """
    payload = [
        {
            "field_id": _value(item, "field_id"),
            "form_index": _value(item, "form_index"),
            "field_type": _enum(_value(item, "field_type")),
            "required": bool(_value(item, "required")),
            "visible": bool(_value(item, "visible", True)),
            "options": [
                str(option if isinstance(option, str) else getattr(option, "label", option))
                for option in (_value(item, "options", []) or [])
            ],
            "duplicate_of": _value(item, "duplicate_of", None),
        }
        for item in fields
    ]
    payload.sort(key=lambda row: str(row["field_id"]))
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _value(item: object, name: str, default: object = None) -> object:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _enum(value: object) -> object:
    return getattr(value, "value", value)


__all__ = [
    "accessible_name_for",
    "input_type_for",
    "locator_ref_for",
    "normalise_fields",
    "sensitivity_for",
    "structure_fingerprint",
    "verification_for",
]
