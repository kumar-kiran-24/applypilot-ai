"""Schemas for auto-apply settings and eligibility reports.

No schema exposes a resume storage path, raw resume text, a credential or a
browser session. A report always carries the version a user approves.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.auto_apply import AutoApplyApproval
from app.models.experience import EmploymentType
from app.models.preference import RelocationPreference, WorkMode
from app.schemas.common import ORMModel

SettingsList = Annotated[list[str], Field(default_factory=list, max_length=50)]


def _clean_settings_list(values: list[str], *, label: str) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = " ".join((value or "").split())
        key = item.casefold()
        if not item or key in seen:
            continue
        if len(item) > 120:
            msg = f"each {label} entry must be at most 120 characters"
            raise ValueError(msg)
        seen.add(key)
        cleaned.append(item)
    return cleaned


class AutoApplySettingsUpdate(BaseModel):
    """Partial update of a user's automation settings.

    Every field is optional; omitted fields keep their current value. Sending
    ``null`` for a salary clears it rather than inventing a number.
    """

    automation_enabled: bool | None = None
    require_user_approval: bool | None = None
    require_user_review: bool | None = None
    max_applications_per_day: int | None = Field(default=None, ge=1, le=50)
    minimum_resume_match_percentage: int | None = Field(default=None, ge=0, le=100)
    preferred_job_titles: list[str] | None = Field(default=None, max_length=50)
    excluded_job_titles: list[str] | None = Field(default=None, max_length=50)
    preferred_companies: list[str] | None = Field(default=None, max_length=50)
    excluded_companies: list[str] | None = Field(default=None, max_length=50)
    preferred_locations: list[str] | None = Field(default=None, max_length=50)
    work_modes: list[WorkMode] | None = Field(default=None, max_length=10)
    employment_types: list[EmploymentType] | None = Field(default=None, max_length=20)
    minimum_salary: Decimal | None = Field(
        default=None, ge=0, max_digits=12, decimal_places=2
    )
    maximum_salary: Decimal | None = Field(
        default=None, ge=0, max_digits=12, decimal_places=2
    )
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    relocation_willingness: RelocationPreference | None = None

    @field_validator(
        "preferred_job_titles",
        "excluded_job_titles",
        "preferred_companies",
        "excluded_companies",
        "preferred_locations",
    )
    @classmethod
    def _clean_list(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return _clean_settings_list(value, label="settings")

    @field_validator("currency")
    @classmethod
    def _normalise_currency(cls, value: str | None) -> str | None:
        return value.strip().upper() if value is not None else None

    @model_validator(mode="after")
    def _validate_salary_range(self) -> AutoApplySettingsUpdate:
        if (
            self.minimum_salary is not None
            and self.maximum_salary is not None
            and self.maximum_salary < self.minimum_salary
        ):
            msg = "maximum_salary must be greater than or equal to minimum_salary"
            raise ValueError(msg)
        return self


class AutoApplySettingsResponse(ORMModel):
    """The authenticated user's automation settings."""

    id: UUID
    automation_enabled: bool
    require_user_approval: bool
    require_user_review: bool
    max_applications_per_day: int
    minimum_resume_match_percentage: int
    preferred_job_titles: list[str] = Field(default_factory=list)
    excluded_job_titles: list[str] = Field(default_factory=list)
    preferred_companies: list[str] = Field(default_factory=list)
    excluded_companies: list[str] = Field(default_factory=list)
    preferred_locations: list[str] = Field(default_factory=list)
    work_modes: list[str] = Field(default_factory=list)
    employment_types: list[str] = Field(default_factory=list)
    minimum_salary: Decimal | None = None
    maximum_salary: Decimal | None = None
    currency: str
    relocation_willingness: RelocationPreference
    created_at: datetime
    updated_at: datetime


class EligibilityCheckResponse(ORMModel):
    """One explainable eligibility check."""

    name: str
    label: str
    status: str
    explanation: str
    blocking: bool
    source: str
    suggested_resolution: str
    details: dict[str, object] = Field(default_factory=dict)

    @field_validator("status", mode="before")
    @classmethod
    def _status_value(cls, value: object) -> object:
        return getattr(value, "value", value)


class EligibilityApprovalResponse(BaseModel):
    """The recorded user approval and whether it still applies."""

    approved: bool
    current: bool
    approved_at: datetime | None = None
    approved_by_user_id: UUID | None = None
    report_version: str | None = None
    report_decision: str | None = None

    @classmethod
    def from_record(
        cls, approval: AutoApplyApproval | None, *, current: bool
    ) -> EligibilityApprovalResponse:
        if approval is None:
            return cls(approved=False, current=False)
        return cls(
            approved=True,
            current=current,
            approved_at=approval.approved_at,
            approved_by_user_id=approval.user_id,
            report_version=approval.report_version,
            report_decision=approval.report_decision,
        )


class EligibilityReportResponse(BaseModel):
    """The full, explainable eligibility report for one application."""

    application_id: UUID
    job_id: UUID
    job_title: str
    company: str | None = None
    platform: str
    platform_name: str
    application_url: str
    application_status: str
    resume_selected: bool
    resume_id: UUID | None = None
    resume_filename: str | None = None
    resume_match_score: float | None = None
    overall_decision: str
    readiness_percentage: int
    message: str
    blocking_issues: list[EligibilityCheckResponse] = Field(default_factory=list)
    warnings: list[EligibilityCheckResponse] = Field(default_factory=list)
    required_user_actions: list[str] = Field(default_factory=list)
    passed_checks: list[EligibilityCheckResponse] = Field(default_factory=list)
    failed_checks: list[EligibilityCheckResponse] = Field(default_factory=list)
    checks: list[EligibilityCheckResponse] = Field(default_factory=list)
    automation_enabled: bool
    require_user_approval: bool
    require_user_review: bool
    approval: EligibilityApprovalResponse
    evaluation_timestamp: datetime
    report_version: str


class EligibilitySummaryResponse(BaseModel):
    """Compact report row used by the dashboard."""

    application_id: UUID
    job_id: UUID
    job_title: str
    company: str | None = None
    application_status: str
    platform_name: str
    resume_filename: str | None = None
    resume_match_score: float | None = None
    overall_decision: str
    readiness_percentage: int
    blocking_count: int
    warning_count: int
    blocking_issue_names: list[str] = Field(default_factory=list)
    next_action: str | None = None
    automation_enabled: bool
    approved: bool
    evaluation_timestamp: datetime
    report_version: str


class EligibilityOverviewResponse(BaseModel):
    """Counts that let the dashboard distinguish every readiness bucket."""

    total: int = 0
    ready_to_prepare: int = 0
    ready_for_user_review: int = 0
    blocked: int = 0
    needs_information: int = 0
    needs_confirmation: int = 0
    needs_user_approval: int = 0
    ineligible: int = 0
    duplicate: int = 0
    already_applied: int = 0
    unsupported_platform: int = 0
    failed: int = 0
    automation_enabled: bool = False


class EligibilityListResponse(BaseModel):
    """Every owned application's eligibility summary."""

    overview: EligibilityOverviewResponse
    applications: list[EligibilitySummaryResponse] = Field(default_factory=list)


class ApproveApplicationEligibilityRequest(BaseModel):
    """Explicit approval bound to one evaluated report version."""

    report_version: str = Field(min_length=1, max_length=80)
    approved: bool = True


__all__ = [
    "ApproveApplicationEligibilityRequest",
    "AutoApplySettingsResponse",
    "AutoApplySettingsUpdate",
    "EligibilityApprovalResponse",
    "EligibilityCheckResponse",
    "EligibilityListResponse",
    "EligibilityOverviewResponse",
    "EligibilityReportResponse",
    "EligibilitySummaryResponse",
]
