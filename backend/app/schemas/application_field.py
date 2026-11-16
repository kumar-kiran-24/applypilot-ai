"""Phase 2J.3: one canonical application-field representation.

Before this module the questionnaire used ``ApplicationQuestion`` rows, the live
analysis used ``DetectedFormField`` records and the fill preview used
``FillPlanField`` rows. They described the same employer control three times,
so a user could approve one set of fields and values and have a different set
filled.

``ApplicationField`` is the single shape the plan-preview, approval and
execution read paths produce. It is computed from the persisted Stage B
analysis (and the planner's decisions); it is **not** a new database table,
because the existing immutable analysis and execution records already hold
everything needed. The raw analysis endpoint keeps returning its detection
metadata, but anything the user approves or the engine fills is serialized
through this canonical shape.

Deliberately absent: Playwright selectors, HTML, cookies, storage state and any
credential or verification value. ``locator_ref`` is an opaque token derived
from the stable field id, not something the browser can be driven with.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from app.schemas.application_form import FieldCategory, FieldSource, FormFieldType


class FieldSensitivity(str, Enum):
    """How carefully a field's value must be handled."""

    PUBLIC = "public"
    SENSITIVE = "sensitive"
    LEGAL = "legal"
    CREDENTIAL = "credential"
    VERIFICATION = "verification"
    DEMOGRAPHIC = "demographic"


class FieldVerification(str, Enum):
    """Provenance of the candidate value considered for a field."""

    #: Verified profile data, a confirmed reusable answer or the user's own
    #: confirmation for this application.
    VERIFIED = "verified"
    #: Derived from the resume; a suggestion that still needs confirmation.
    DERIVED = "derived"
    #: Nothing safe was found; the field must be answered by the user.
    UNVERIFIED = "unverified"


class FieldProvenance(BaseModel):
    """Where a candidate value came from, without exposing the value itself."""

    source: FieldSource = FieldSource.NONE
    source_record_id: str | None = None
    last_confirmed_at: datetime | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    stale: bool = False
    explanation: str = ""


class ApplicationField(BaseModel):
    """A canonical, non-secret description of one control on the live form.

    The plan preview, approval and execution paths all serialize this shape, so
    the field a user reviews is the field the engine fills.
    """

    #: Stable identifier derived from the control's own attributes; identical
    #: across analyses of the same page.
    field_id: str
    #: Questionnaire key this control maps to, when one is known. This is the
    #: link between a confirmed ``ApplicationQuestion`` answer and the live
    #: control rather than a second, parallel model.
    question_key: str | None = None
    name: str | None = None
    label: str
    accessible_name: str = ""
    options: list[str] = Field(default_factory=list)
    field_type: FormFieldType = FormFieldType.TEXT
    input_type: str = "text"
    classification: FieldCategory = FieldCategory.UNKNOWN
    required: bool = False
    visible: bool = True
    #: Value already present on the page, only for safe visible controls.
    current_value: str | None = None
    #: Value the engine would type. ``None`` for never-automated fields.
    proposed_value: str | None = None
    #: The matched source value before it was fitted to the control's shape.
    candidate_answer: str | None = None
    source: FieldSource = FieldSource.NONE
    provenance: FieldProvenance = Field(default_factory=FieldProvenance)
    verification_status: FieldVerification = FieldVerification.UNVERIFIED
    sensitivity: FieldSensitivity = FieldSensitivity.PUBLIC
    #: True only when the planner would interact with this control.
    fillable: bool = False
    requires_confirmation: bool = False
    already_populated: bool = False
    #: True when two credible sources disagree and the user must choose.
    conflicting: bool = False
    form_index: int = 0
    page_url: str | None = None
    application_step: str | None = None
    #: Opaque, safe reference (``field:<id>``); never a selector.
    locator_ref: str = ""
    analyzed_at: datetime | None = None
    #: Set when the analysis contained the same control more than once.
    duplicate_of: str | None = None


__all__ = [
    "ApplicationField",
    "FieldProvenance",
    "FieldSensitivity",
    "FieldVerification",
]
