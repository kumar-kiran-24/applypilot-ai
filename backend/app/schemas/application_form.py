"""Phase 2E Stage B schemas: detected form fields and their classification.

Nothing here is a credential. Password values, OTP/MFA codes, CAPTCHA answers,
cookies and hidden security tokens are never extracted, so they cannot appear
in a response. ``current_value`` is only populated for safe, non-sensitive
visible controls.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field


class FormFieldType(str, Enum):
    TEXT = "text"
    EMAIL = "email"
    PHONE = "phone"
    NUMBER = "number"
    DATE = "date"
    URL = "url"
    TEXTAREA = "textarea"
    SELECT = "select"
    RADIO_GROUP = "radio_group"
    CHECKBOX_GROUP = "checkbox_group"
    CHECKBOX = "checkbox"
    FILE = "file"
    PASSWORD = "password"
    HIDDEN = "hidden"
    CAPTCHA = "captcha"
    OTHER = "other"


class FieldCategory(str, Enum):
    PERSONAL_NAME = "personal_name"
    EMAIL = "email"
    PHONE = "phone"
    LOCATION = "location"
    ADDRESS = "address"
    EDUCATION = "education"
    DEGREE = "degree"
    COLLEGE = "college"
    GRADUATION_YEAR = "graduation_year"
    SKILLS = "skills"
    EXPERIENCE = "experience"
    EMPLOYMENT_HISTORY = "employment_history"
    PROJECTS = "projects"
    LINKEDIN = "linkedin"
    GITHUB = "github"
    PORTFOLIO = "portfolio"
    CURRENT_SALARY = "current_salary"
    EXPECTED_SALARY = "expected_salary"
    NOTICE_PERIOD = "notice_period"
    RELOCATION = "relocation"
    WORK_AUTHORIZATION = "work_authorization"
    RESUME_UPLOAD = "resume_upload"
    COVER_LETTER = "cover_letter"
    LEGAL_DECLARATION = "legal_declaration"
    DEMOGRAPHIC_INFORMATION = "demographic_information"
    CAPTCHA = "captcha"
    OTP_VERIFICATION = "otp_verification"
    MFA_VERIFICATION = "mfa_verification"
    OTHER = "other"
    UNKNOWN = "unknown"


class FieldSource(str, Enum):
    """Where a suggested value would come from (Stage B never fills it)."""

    VERIFIED_PROFILE = "verified_profile"
    CONFIRMED_REUSABLE_ANSWER = "confirmed_reusable_answer"
    USER_PROVIDED = "user_provided"
    RESUME_SUGGESTION = "resume_suggestion"
    NONE = "none"


class FieldSourceMatch(BaseModel):
    """Non-secret provenance for a potential future value."""

    source: FieldSource = FieldSource.NONE
    suggested_value: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source_record_id: str | None = None
    last_confirmed_at: datetime | None = None
    stale: bool = False
    explanation: str = ""


class DetectedFormField(BaseModel):
    field_id: str
    form_index: int
    label: str
    field_type: FormFieldType
    classification: FieldCategory
    required: bool
    current_value: str | None = None
    options: list[str] = Field(default_factory=list)
    placeholder: str | None = None
    help_text: str | None = None
    name: str | None = None
    html_id: str | None = None
    visible: bool = True
    confidence: float = Field(ge=0.0, le=1.0)
    requires_human_input: bool
    requires_confirmation: bool
    is_sensitive: bool
    is_legal: bool
    automation_eligible: bool
    classification_reason: str
    source: FieldSourceMatch = Field(default_factory=FieldSourceMatch)


class ApplicationFormAnalysisResponse(BaseModel):
    analysis_id: UUID
    application_id: UUID
    browser_session_id: UUID
    version: int
    page_url: str | None
    analyzed_at: datetime
    form_count: int
    field_count: int
    visible_field_count: int
    requires_human_input_count: int
    automation_eligible_count: int
    captcha_detected: bool
    otp_detected: bool
    mfa_detected: bool
    legal_declaration_count: int
    sensitive_field_count: int
    warnings: list[str] = Field(default_factory=list)
    fields: list[DetectedFormField] = Field(default_factory=list)


class ApplicationFormFieldsResponse(BaseModel):
    analysis_id: UUID
    version: int
    page_url: str | None
    analyzed_at: datetime
    total: int
    fields: list[DetectedFormField]


class ReanalyzeFormRequest(BaseModel):
    """No options: re-analysis always inspects the current page again."""


__all__ = [
    "ApplicationFormAnalysisResponse",
    "ApplicationFormFieldsResponse",
    "DetectedFormField",
    "FieldCategory",
    "FieldSource",
    "FieldSourceMatch",
    "FormFieldType",
    "ReanalyzeFormRequest",
]
