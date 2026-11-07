"""Deterministic auto-apply eligibility evaluation.

Nothing here opens a browser, submits an application or changes a verified
profile value. The engine reads the application, its questionnaire, the
missing-information queue, resume matches, the candidate profile and the user's
automation settings, then returns an explainable report.

Every check is deterministic and carries:

* a stable check name and a human label;
* a status (:class:`EligibilityStatus`);
* an explanation and the evidence source it was derived from;
* whether it blocks preparation;
* a suggested resolution.

Only an explicitly approved, current report can be ``READY_TO_PREPARE``. A high
resume match score never makes an application eligible on its own, and an AI
suggestion is never treated as a verified answer.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import func, select

from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.models.application import Application, ApplicationStatus
from app.models.auto_apply import AutoApplyApproval, UserAutoApplySettings
from app.models.candidate import Candidate
from app.models.job import Job
from app.models.job_match import JobResumeMatch
from app.models.missing_information import MissingInformationItemStatus
from app.models.preference import JobPreference, RelocationPreference
from app.models.user import User
from app.services.application_platforms import (
    ApplicationUrlError,
    PlatformDetection,
    detect_platform,
    normalize_application_url,
)
from app.services.application_service import ApplicationService
from app.services.auto_apply_settings import AutoApplySettingsService
from app.services.matching_service import MatchingService
from app.services.missing_information_service import (
    MissingInformationService,
    QueueSnapshot,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

#: Check names are stable API identifiers; the UI groups on them.
CHECK_PROFILE = "profile_completeness"
CHECK_RESUME = "resume_suitability"
CHECK_ANSWERS = "answer_readiness"
CHECK_JOB_PREFERENCES = "job_preferences"
CHECK_SALARY = "salary_expectations"
CHECK_AUTHORIZATION = "work_authorization"
CHECK_URL = "application_url"
CHECK_PLATFORM = "platform_support"
CHECK_STATUS = "application_status"
CHECK_DUPLICATE = "duplicate_application"
CHECK_AUTOMATION = "automation_settings"
CHECK_USAGE = "usage_limit"
CHECK_APPROVAL = "user_approval"

#: Checks that depend on a user action (approval count, usage today) are
#: excluded from the report fingerprint so approving cannot invalidate itself.
_VOLATILE_CHECKS = frozenset({CHECK_APPROVAL, CHECK_USAGE})

_NO_SPONSORSHIP_PHRASES: tuple[str, ...] = (
    "no sponsorship",
    "no visa sponsorship",
    "without sponsorship",
    "unable to sponsor",
    "cannot sponsor",
    "can not sponsor",
    "do not sponsor",
    "does not sponsor",
    "not able to sponsor",
    "sponsorship is not available",
    "sponsorship not available",
)

_REMOTE_TOKENS = ("remote", "work from home", "telecommute")
_HYBRID_TOKENS = ("hybrid",)
_ONSITE_TOKENS = ("on-site", "onsite", "in office", "in-person", "in person")

_CURRENCY_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP"}

_SALARY_SYMBOL_PATTERN = re.compile(
    r"(?P<symbol>[$€£])\s*(?P<amount>\d[\d,]*(?:\.\d+)?)\s*(?P<scale>[kK])?"
)
_SALARY_CODE_PATTERN = re.compile(
    r"\b(?P<code>USD|EUR|GBP)\s*(?P<amount>\d[\d,]*(?:\.\d+)?)\s*(?P<scale>[kK])?\b",
    re.IGNORECASE,
)
_SALARY_TRAILING_CODE_PATTERN = re.compile(
    r"\b(?P<amount>\d[\d,]*(?:\.\d+)?)\s*(?P<scale>[kK])?\s*(?P<code>USD|EUR|GBP)\b",
    re.IGNORECASE,
)


class EligibilityStatus(str, Enum):
    """The outcome of one eligibility check."""

    ELIGIBLE = "eligible"
    NEEDS_INFORMATION = "needs_information"
    NEEDS_CONFIRMATION = "needs_confirmation"
    NEEDS_USER_APPROVAL = "needs_user_approval"
    INELIGIBLE = "ineligible"
    DUPLICATE = "duplicate"
    ALREADY_APPLIED = "already_applied"
    UNSUPPORTED_PLATFORM = "unsupported_platform"
    FAILED = "failed"


class EligibilityDecision(str, Enum):
    """The single next step for an application."""

    READY_TO_PREPARE = "ready_to_prepare"
    READY_FOR_USER_REVIEW = "ready_for_user_review"
    BLOCKED = "blocked"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _text(value: str | None) -> str:
    return (value or "").strip()


def _normalise(value: str | None) -> str:
    return " ".join(_text(value).casefold().split())


def _contains_match(value: str, options: list[str]) -> bool:
    """Case-insensitive containment either way, never fuzzy inference."""
    if not value:
        return False
    for option in options:
        candidate = _normalise(option)
        if candidate and (candidate in value or value in candidate):
            return True
    return False


def _job_text(job: Job) -> str:
    return " ".join(
        part
        for part in (job.job_title, job.company, job.location, job.job_description)
        if part
    ).casefold()


def _detected_work_modes(job: Job) -> set[str]:
    text = _job_text(job)
    modes: set[str] = set()
    if any(token in text for token in _REMOTE_TOKENS):
        modes.add("remote")
    if any(token in text for token in _HYBRID_TOKENS):
        modes.add("hybrid")
    if any(token in text for token in _ONSITE_TOKENS):
        modes.add("onsite")
    return modes


def _detected_employment_types(job: Job) -> set[str]:
    title = _normalise(job.job_title)
    text = _job_text(job)
    found: set[str] = set()
    if "intern" in title or "internship" in title:
        found.add("internship")
    if "part-time" in title or "part time" in title:
        found.add("part_time")
    if "temporary" in title or "temp " in title:
        found.add("temporary")
    if "contract" in title or "contractor" in title:
        found.add("contract")
    if "freelance" in title:
        found.add("freelance")
    if not found and ("full-time" in text or "full time" in text):
        found.add("full_time")
    return found


def _salary_amount(raw: str, scale: str | None) -> Decimal | None:
    try:
        value = Decimal(raw.replace(",", ""))
    except (InvalidOperation, ValueError):
        return None
    if scale:
        value *= Decimal(1000)
    return value


def extract_salary_mentions(text: str | None) -> list[tuple[Decimal, str]]:
    """Extract explicit salary amounts and their currency from a posting.

    Only values carrying a currency symbol or ISO code are considered, so a
    year, a team size or a job id is never mistaken for pay.
    """
    if not text:
        return []
    mentions: list[tuple[Decimal, str]] = []
    for pattern, is_symbol in (
        (_SALARY_SYMBOL_PATTERN, True),
        (_SALARY_CODE_PATTERN, False),
        (_SALARY_TRAILING_CODE_PATTERN, False),
    ):
        for match in pattern.finditer(text):
            groups = match.groupdict()
            amount = _salary_amount(groups["amount"], groups.get("scale"))
            if amount is None or amount < Decimal(100):
                continue
            currency = (
                _CURRENCY_SYMBOLS[groups["symbol"]]
                if is_symbol
                else groups["code"].upper()
            )
            mentions.append((amount, currency))
    # De-duplicate ranges that match two patterns for the same amount.
    unique: list[tuple[Decimal, str]] = []
    for amount, currency in mentions:
        if (amount, currency) not in unique:
            unique.append((amount, currency))
    return unique


@dataclass(frozen=True)
class EligibilityCheck:
    """One explainable result in an eligibility report."""

    name: str
    label: str
    status: EligibilityStatus
    explanation: str
    blocking: bool
    source: str
    suggested_resolution: str
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.status is EligibilityStatus.ELIGIBLE


@dataclass(frozen=True)
class EligibilityReport:
    """The complete, versioned report for one application."""

    application_id: UUID
    job_id: UUID
    job_title: str
    company: str | None
    platform: str
    platform_name: str
    application_url: str
    application_status: ApplicationStatus
    resume_id: UUID | None
    resume_filename: str | None
    resume_match_score: float | None
    overall_decision: EligibilityDecision
    readiness_percentage: int
    checks: tuple[EligibilityCheck, ...]
    evaluation_timestamp: datetime
    report_version: str
    automation_enabled: bool
    require_user_approval: bool
    require_user_review: bool
    approval: AutoApplyApproval | None
    approval_current: bool

    @property
    def blocking_issues(self) -> tuple[EligibilityCheck, ...]:
        return tuple(check for check in self.checks if check.blocking)

    @property
    def failed_checks(self) -> tuple[EligibilityCheck, ...]:
        return tuple(
            check for check in self.checks if check.status is not EligibilityStatus.ELIGIBLE
        )

    @property
    def passed_checks(self) -> tuple[EligibilityCheck, ...]:
        return tuple(check for check in self.checks if check.passed)

    @property
    def warnings(self) -> tuple[EligibilityCheck, ...]:
        return tuple(
            check
            for check in self.checks
            if not check.blocking and check.status is not EligibilityStatus.ELIGIBLE
        )

    @property
    def required_user_actions(self) -> tuple[str, ...]:
        actions: list[str] = []
        for check in self.failed_checks:
            action = _text(check.suggested_resolution)
            if action and action not in actions:
                actions.append(action)
        return tuple(actions)

    @property
    def approved(self) -> bool:
        return self.approval is not None and self.approval_current

    @property
    def message(self) -> str:
        if self.overall_decision is EligibilityDecision.READY_TO_PREPARE:
            return (
                "All eligibility checks passed and the current report is approved. "
                "ApplyPilot may prepare this application."
            )
        if self.overall_decision is EligibilityDecision.READY_FOR_USER_REVIEW:
            return (
                "No blocking issue remains. Review the report and approve it before "
                "ApplyPilot prepares the employer's form."
            )
        return "This application is blocked. Resolve the blocking issues and evaluate it again."


class AutoApplyEligibilityService:
    """Evaluates whether one owned application is ready for preparation."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.applications = ApplicationService(session)
        self.candidates = self.applications.candidates
        self.matching = MatchingService(session)
        self.missing_information = MissingInformationService(session)
        self.settings = AutoApplySettingsService(session)

    # --- Public API ----------------------------------------------------------
    async def evaluate_application(self, user: User, application_id: UUID) -> EligibilityReport:
        """Evaluate one owned application and persist nothing but settings/queue."""
        application = await self.applications.get_application(user, application_id)
        candidate = await self.candidates.ensure_profile_for_user(user)
        settings = await self.settings.get_or_create(user)
        completeness = await self.candidates.completeness_for_user(user)
        snapshot = await self.missing_information.get_queue(user, application_id)
        match = await self._resume_match(user, application)
        approval = await self._approval(user, application_id)
        detection = self._detect(application)

        checks = [
            self.evaluate_profile_completeness(completeness),
            self.evaluate_resume_suitability(application, match, settings),
            self.evaluate_answer_readiness(snapshot),
            self.evaluate_job_preferences(application, settings, candidate.preference),
            self.evaluate_salary_expectations(application, settings, candidate.preference),
            self.evaluate_work_authorization(application, candidate),
            self.evaluate_application_url(application),
            self.evaluate_platform_support(application, detection),
            self.evaluate_application_status(application),
            await self.evaluate_duplicate_application(user, application),
            self.evaluate_user_automation_settings(settings),
            await self.evaluate_user_usage_limit(user, settings),
        ]
        report_version = self.build_report_version(
            application,
            checks,
            settings,
            completeness=completeness,
            snapshot=snapshot,
            match=match,
        )
        approval_check = self.evaluate_user_approval(settings, approval, report_version)
        checks.append(approval_check)

        decision = self.build_eligibility_report(
            application=application,
            checks=checks,
            settings=settings,
        )
        approval_current = bool(
            approval is not None and approval.report_version == report_version
        )
        report = EligibilityReport(
            application_id=application.id,
            job_id=application.job_id,
            job_title=application.job.job_title if application.job else "",
            company=application.job.company if application.job else None,
            platform=application.platform,
            platform_name=application.platform_name,
            application_url=application.application_url,
            application_status=application.status,
            resume_id=application.resume_id,
            resume_filename=application.resume.original_filename
            if application.resume
            else application.resume_filename,
            resume_match_score=match.overall_score
            if match is not None
            else application.resume_score,
            overall_decision=decision,
            readiness_percentage=self._readiness_percentage(checks),
            checks=tuple(checks),
            evaluation_timestamp=_utcnow(),
            report_version=report_version,
            automation_enabled=settings.automation_enabled,
            require_user_approval=settings.require_user_approval,
            require_user_review=settings.require_user_review,
            approval=approval,
            approval_current=approval_current,
        )
        await self.session.commit()
        logger.info(
            "Auto-apply eligibility evaluated",
            extra={
                "stage": "auto_apply_eligibility",
                "user_id": str(user.id),
                "application_id": str(application.id),
                "decision": decision.value,
                "blocking": len(report.blocking_issues),
                "report_version": report_version,
            },
        )
        return report

    async def list_reports(self, user: User) -> list[EligibilityReport]:
        """Evaluate every application owned by the user."""
        applications = await self.applications.list_applications(user)
        reports = [await self.evaluate_application(user, item.id) for item in applications]
        reports.sort(
            key=lambda report: (report.readiness_percentage, report.job_title),
            reverse=True,
        )
        return reports

    async def approve_application(
        self,
        user: User,
        application_id: UUID,
        *,
        report_version: str,
        approved: bool,
    ) -> EligibilityReport:
        """Record (or withdraw) explicit user approval of one report version."""
        report = await self.evaluate_application(user, application_id)
        existing = await self._approval(user, application_id)

        if not approved:
            if existing is not None:
                await self.session.delete(existing)
                await self.session.commit()
                logger.info(
                    "Auto-apply approval withdrawn",
                    extra={
                        "stage": "auto_apply_approval",
                        "user_id": str(user.id),
                        "application_id": str(application_id),
                        "approved": False,
                    },
                )
            return await self.evaluate_application(user, application_id)

        if report.report_version != report_version:
            raise AutoApplyApprovalVersionError(
                "The eligibility report changed after you reviewed it. "
                "Review the current report and approve that version."
            )
        if report.overall_decision is EligibilityDecision.BLOCKED:
            raise AutoApplyBlockedError(
                "This application still has blocking eligibility issues. "
                "Resolve them before approving preparation."
            )

        now = _utcnow()
        if existing is None:
            existing = AutoApplyApproval(
                id=uuid.uuid4(),
                user_id=user.id,
                application_id=application_id,
                report_version=report.report_version,
                report_decision=report.overall_decision.value,
                approved_at=now,
            )
            self.session.add(existing)
        else:
            existing.report_version = report.report_version
            existing.report_decision = report.overall_decision.value
            existing.approved_at = now
        await self.session.commit()
        logger.info(
            "Auto-apply approval recorded",
            extra={
                "stage": "auto_apply_approval",
                "user_id": str(user.id),
                "application_id": str(application_id),
                "approved": True,
                "report_version": report.report_version,
            },
        )
        return await self.evaluate_application(user, application_id)

    # --- Individual checks ---------------------------------------------------
    def evaluate_profile_completeness(self, completeness: dict[str, Any]) -> EligibilityCheck:
        missing = list(completeness.get("missing_required") or [])
        if missing:
            return EligibilityCheck(
                name=CHECK_PROFILE,
                label="Candidate profile completeness",
                status=EligibilityStatus.NEEDS_INFORMATION,
                explanation=(
                    f"{len(missing)} required profile field"
                    f"{'s are' if len(missing) != 1 else ' is'} missing: "
                    + ", ".join(missing)
                    + "."
                ),
                blocking=True,
                source="candidate profile (verified values only)",
                suggested_resolution=(
                    "Complete the missing profile fields. ApplyPilot never infers "
                    "candidate information."
                ),
                details={"missing_fields": missing},
            )
        return EligibilityCheck(
            name=CHECK_PROFILE,
            label="Candidate profile completeness",
            status=EligibilityStatus.ELIGIBLE,
            explanation="Every required candidate profile field is present.",
            blocking=False,
            source="candidate profile (verified values only)",
            suggested_resolution="",
            details={"missing_fields": []},
        )

    def evaluate_resume_suitability(
        self,
        application: Application,
        match: JobResumeMatch | None,
        settings: UserAutoApplySettings,
    ) -> EligibilityCheck:
        if application.resume_id is None or application.resume is None:
            return EligibilityCheck(
                name=CHECK_RESUME,
                label="Resume availability",
                status=EligibilityStatus.NEEDS_INFORMATION,
                explanation="No resume is selected for this application.",
                blocking=True,
                source="application.resume_id",
                suggested_resolution="Upload a PDF resume and select it for this application.",
                details={"resume_id": None, "match_score": None},
            )

        resume = application.resume
        if resume.user_id != application.user_id:
            return EligibilityCheck(
                name=CHECK_RESUME,
                label="Resume availability",
                status=EligibilityStatus.FAILED,
                explanation="The selected resume is not owned by this user.",
                blocking=True,
                source="resume ownership check",
                suggested_resolution="Select one of your own resumes.",
                details={"resume_id": str(resume.id)},
            )

        score = match.overall_score if match is not None else application.resume_score
        minimum = int(settings.minimum_resume_match_percentage)
        if score is None:
            return EligibilityCheck(
                name=CHECK_RESUME,
                label="Resume suitability",
                status=EligibilityStatus.NEEDS_CONFIRMATION,
                explanation=(
                    "The resume match score could not be calculated for this posting."
                ),
                blocking=True,
                source="resume matching engine",
                suggested_resolution=(
                    "Run job matching again, or select a resume whose match can be "
                    "evaluated."
                ),
                details={
                    "resume_id": str(resume.id),
                    "match_score": None,
                    "minimum_score": minimum,
                },
            )
        if score < minimum:
            return EligibilityCheck(
                name=CHECK_RESUME,
                label="Resume suitability",
                status=EligibilityStatus.INELIGIBLE,
                explanation=(
                    f"The selected resume scores {score}% against this job, below "
                    f"your {minimum}% minimum."
                ),
                blocking=True,
                source="resume matching engine",
                suggested_resolution=(
                    "Select a better-matching resume or lower your minimum match "
                    "percentage in auto-apply settings."
                ),
                details={
                    "resume_id": str(resume.id),
                    "resume_filename": resume.original_filename,
                    "match_score": score,
                    "minimum_score": minimum,
                },
            )
        return EligibilityCheck(
            name=CHECK_RESUME,
            label="Resume suitability",
            status=EligibilityStatus.ELIGIBLE,
            explanation=(
                f"The selected resume scores {score}% against this job, meeting your "
                f"{minimum}% minimum."
            ),
            blocking=False,
            source="resume matching engine",
            suggested_resolution="",
            details={
                "resume_id": str(resume.id),
                "resume_filename": resume.original_filename,
                "match_score": score,
                "minimum_score": minimum,
            },
        )

    def evaluate_answer_readiness(self, snapshot: QueueSnapshot) -> EligibilityCheck:
        required_remaining = snapshot.queue.required_remaining
        # Any relevant conflict means two stored values disagree. Preparation
        # waits until the user resolves it, even when the question is optional.
        blocking_conflicts = list(snapshot.conflicts)
        required_items = list(snapshot.required_questions)
        needs_confirmation = any(
            item.status
            in {
                MissingInformationItemStatus.STALE,
                MissingInformationItemStatus.SENSITIVE_CONFIRMATION,
                MissingInformationItemStatus.LEGAL_CONFIRMATION,
            }
            for item in required_items
        ) or bool(blocking_conflicts)

        details: dict[str, Any] = {
            "required_remaining": required_remaining,
            "stale_answers": len(snapshot.stale_answers),
            "stale_optional": sum(1 for item in snapshot.stale_answers if not item.required),
            "conflicts": len(snapshot.conflicts),
            "blocking_conflicts": len(blocking_conflicts),
            "sensitive_confirmations": len(snapshot.sensitive_confirmations),
            "legal_confirmations": sum(
                1
                for item in snapshot.sensitive_confirmations
                if item.status is MissingInformationItemStatus.LEGAL_CONFIRMATION
            ),
            "unresolved_question_keys": [
                item.question_key for item in required_items
            ],
        }

        if required_remaining or blocking_conflicts:
            reasons: list[str] = []
            if required_remaining:
                reasons.append(
                    f"{required_remaining} required question"
                    f"{'s are' if required_remaining != 1 else ' is'} unresolved."
                )
            if blocking_conflicts:
                reasons.append(
                    f"{len(blocking_conflicts)} conflicting answer"
                    f"{'s' if len(blocking_conflicts) != 1 else ''} block preparation."
                )
            return EligibilityCheck(
                name=CHECK_ANSWERS,
                label="Application answer readiness",
                status=(
                    EligibilityStatus.NEEDS_CONFIRMATION
                    if needs_confirmation
                    else EligibilityStatus.NEEDS_INFORMATION
                ),
                explanation=" ".join(reasons),
                blocking=True,
                source="missing-information queue and answer provenance",
                suggested_resolution=(
                    "Answer or confirm every required question and resolve conflicting "
                    "answers in the Missing Information queue."
                    if blocking_conflicts
                    else "Answer every required question in the Missing Information queue."
                ),
                details=details,
            )

        soft_issues = [
            item for item in snapshot.stale_answers if not item.required
        ] + [
            item for item in snapshot.sensitive_confirmations if not item.required
        ]
        if soft_issues:
            return EligibilityCheck(
                name=CHECK_ANSWERS,
                label="Application answer readiness",
                status=EligibilityStatus.NEEDS_CONFIRMATION,
                explanation=(
                    "Every required question is resolved, but optional or reusable "
                    "answers still need a fresh confirmation."
                ),
                blocking=False,
                source="missing-information queue and answer provenance",
                suggested_resolution=(
                    "Review the stale, sensitive or conflicting optional answers before "
                    "preparing the form."
                ),
                details=details,
            )

        return EligibilityCheck(
            name=CHECK_ANSWERS,
            label="Application answer readiness",
            status=EligibilityStatus.ELIGIBLE,
            explanation="Every required question is answered and confirmed.",
            blocking=False,
            source="missing-information queue and answer provenance",
            suggested_resolution="",
            details=details,
        )

    def evaluate_job_preferences(
        self,
        application: Application,
        settings: UserAutoApplySettings,
        preference: JobPreference | None,
    ) -> EligibilityCheck:
        job = application.job
        if job is None:
            return EligibilityCheck(
                name=CHECK_JOB_PREFERENCES,
                label="Job preferences",
                status=EligibilityStatus.FAILED,
                explanation="The job posting for this application is missing.",
                blocking=True,
                source="stored job posting",
                suggested_resolution="Prepare the application again from a stored job.",
                details={},
            )

        company = _normalise(job.company)
        title = _normalise(job.job_title)
        location = _normalise(job.location)

        preferred_titles = list(settings.preferred_job_titles or []) or list(
            preference.preferred_roles if preference else []
        )
        preferred_companies = list(settings.preferred_companies or [])
        preferred_locations = list(settings.preferred_locations or []) or list(
            preference.preferred_locations if preference else []
        )
        work_modes = list(settings.work_modes or []) or list(
            preference.preferred_work_modes if preference else []
        )
        employment_types = list(settings.employment_types or []) or [
            item.value if hasattr(item, "value") else str(item)
            for item in (preference.preferred_job_types if preference else [])
        ]
        relocation = settings.relocation_willingness
        if relocation is RelocationPreference.UNKNOWN and preference is not None:
            relocation = preference.relocation_preference

        hard: list[str] = []
        soft: list[str] = []

        for excluded in settings.excluded_companies or []:
            if _contains_match(company, [excluded]):
                hard.append(f"The company is on your excluded list ({excluded}).")
                break
        for excluded in settings.excluded_job_titles or []:
            if _contains_match(title, [excluded]):
                hard.append(f"The job title is on your excluded list ({excluded}).")
                break
        if preferred_companies and not _contains_match(company, preferred_companies):
            hard.append("The company is not one of your preferred companies.")
        if preferred_titles and not _contains_match(title, preferred_titles):
            hard.append("The job title does not match your preferred job titles.")

        if preferred_locations and not _contains_match(location, preferred_locations):
            if relocation is RelocationPreference.YES:
                soft.append(
                    "The job is outside your preferred locations, but your settings "
                    "explicitly allow relocation."
                )
            elif relocation is RelocationPreference.NEGOTIABLE:
                soft.append(
                    "The job is outside your preferred locations; you marked relocation "
                    "as negotiable."
                )
            else:
                hard.append(
                    "The job location is outside your preferred locations and you have "
                    "not indicated that you are willing to relocate."
                )

        detected_modes = _detected_work_modes(job)
        if work_modes:
            allowed_modes = {_normalise(item) for item in work_modes}
            if detected_modes and not (detected_modes & allowed_modes):
                hard.append(
                    "The posting's work mode ("
                    + ", ".join(sorted(detected_modes))
                    + ") is not one you selected."
                )
            elif not detected_modes:
                soft.append("The posting does not state a work mode.")

        detected_types = _detected_employment_types(job)
        if employment_types:
            allowed_types = {_normalise(item) for item in employment_types}
            if detected_types and not (detected_types & allowed_types):
                hard.append(
                    "The posting's employment type ("
                    + ", ".join(sorted(detected_types))
                    + ") is not one you selected."
                )
            elif not detected_types:
                soft.append("The posting does not state an employment type.")

        details = {
            "excluded_companies": list(settings.excluded_companies or []),
            "excluded_job_titles": list(settings.excluded_job_titles or []),
            "preferred_companies": preferred_companies,
            "preferred_job_titles": preferred_titles,
            "preferred_locations": preferred_locations,
            "work_modes": work_modes,
            "employment_types": employment_types,
            "relocation_willingness": relocation.value,
            "detected_work_modes": sorted(detected_modes),
            "detected_employment_types": sorted(detected_types),
        }
        if hard:
            return EligibilityCheck(
                name=CHECK_JOB_PREFERENCES,
                label="Job preferences",
                status=EligibilityStatus.INELIGIBLE,
                explanation=" ".join(hard),
                blocking=True,
                source="auto-apply settings and stored job posting",
                suggested_resolution=(
                    "This posting does not match your declared automation preferences. "
                    "Prepare it manually or update your settings."
                ),
                details=details,
            )
        if soft:
            return EligibilityCheck(
                name=CHECK_JOB_PREFERENCES,
                label="Job preferences",
                status=EligibilityStatus.NEEDS_CONFIRMATION,
                explanation=" ".join(soft),
                blocking=False,
                source="auto-apply settings and stored job posting",
                suggested_resolution=(
                    "Confirm this posting's location, work mode or employment type is "
                    "acceptable before preparing it."
                ),
                details=details,
            )
        return EligibilityCheck(
            name=CHECK_JOB_PREFERENCES,
            label="Job preferences",
            status=EligibilityStatus.ELIGIBLE,
            explanation="The posting matches your declared job preferences.",
            blocking=False,
            source="auto-apply settings and stored job posting",
            suggested_resolution="",
            details=details,
        )

    def evaluate_salary_expectations(
        self,
        application: Application,
        settings: UserAutoApplySettings,
        preference: JobPreference | None,
    ) -> EligibilityCheck:
        minimum = settings.minimum_salary
        maximum = settings.maximum_salary
        if minimum is None and preference is not None:
            minimum = preference.minimum_salary
        currency = (settings.currency or (preference.currency if preference else "USD")).upper()
        details = {
            "minimum_salary": str(minimum) if minimum is not None else None,
            "maximum_salary": str(maximum) if maximum is not None else None,
            "currency": currency,
            "salary_mentions": [],
        }
        if minimum is None and maximum is None:
            return EligibilityCheck(
                name=CHECK_SALARY,
                label="Salary expectations",
                status=EligibilityStatus.ELIGIBLE,
                explanation=(
                    "No salary limits are configured, so there is nothing to compare."
                ),
                blocking=False,
                source="auto-apply settings",
                suggested_resolution="",
                details=details,
            )

        job = application.job
        mentions = extract_salary_mentions(
            " ".join(
                part for part in (job.job_description, job.job_title) if job and part
            )
        )
        details["salary_mentions"] = [
            {"amount": str(amount), "currency": code} for amount, code in mentions
        ]
        if not mentions:
            return EligibilityCheck(
                name=CHECK_SALARY,
                label="Salary expectations",
                status=EligibilityStatus.NEEDS_CONFIRMATION,
                explanation=(
                    "The posting does not state a salary, so your limits could not be "
                    "checked."
                ),
                blocking=False,
                source="stored job posting",
                suggested_resolution=(
                    "Check the posting's compensation yourself before approving "
                    "preparation."
                ),
                details=details,
            )

        matching_currency = [
            amount for amount, code in mentions if code.upper() == currency
        ]
        if not matching_currency:
            return EligibilityCheck(
                name=CHECK_SALARY,
                label="Salary expectations",
                status=EligibilityStatus.NEEDS_CONFIRMATION,
                explanation=(
                    f"The posting states pay in a different currency than your "
                    f"{currency} limits."
                ),
                blocking=False,
                source="stored job posting",
                suggested_resolution=(
                    "Compare the posting's currency or update your salary limits."
                ),
                details=details,
            )

        top = max(matching_currency)
        bottom = min(matching_currency)
        if minimum is not None and top < Decimal(minimum):
            return EligibilityCheck(
                name=CHECK_SALARY,
                label="Salary expectations",
                status=EligibilityStatus.INELIGIBLE,
                explanation=(
                    f"The posting's top stated pay ({top} {currency}) is below your "
                    f"minimum of {minimum} {currency}."
                ),
                blocking=True,
                source="stored job posting and auto-apply settings",
                suggested_resolution=(
                    "Skip this posting or lower your minimum salary in auto-apply "
                    "settings."
                ),
                details=details,
            )
        if maximum is not None and bottom > Decimal(maximum):
            return EligibilityCheck(
                name=CHECK_SALARY,
                label="Salary expectations",
                status=EligibilityStatus.INELIGIBLE,
                explanation=(
                    f"The posting's lowest stated pay ({bottom} {currency}) is above "
                    f"your maximum of {maximum} {currency}."
                ),
                blocking=True,
                source="stored job posting and auto-apply settings",
                suggested_resolution=(
                    "Skip this posting or raise your maximum salary in auto-apply "
                    "settings."
                ),
                details=details,
            )
        return EligibilityCheck(
            name=CHECK_SALARY,
            label="Salary expectations",
            status=EligibilityStatus.ELIGIBLE,
            explanation="The posting's stated pay falls within your configured limits.",
            blocking=False,
            source="stored job posting and auto-apply settings",
            suggested_resolution="",
            details=details,
        )

    def evaluate_work_authorization(
        self, application: Application, candidate: Candidate
    ) -> EligibilityCheck:
        authorization = _text(candidate.work_authorization)
        sponsorship = candidate.requires_sponsorship
        details = {
            "work_authorization": authorization or None,
            "requires_sponsorship": sponsorship,
        }
        if not authorization:
            return EligibilityCheck(
                name=CHECK_AUTHORIZATION,
                label="Work authorization",
                status=EligibilityStatus.NEEDS_INFORMATION,
                explanation="Your work authorization is not recorded on your profile.",
                blocking=True,
                source="candidate profile",
                suggested_resolution=(
                    "Add your work authorization and sponsorship requirement to your "
                    "profile."
                ),
                details=details,
            )
        if sponsorship is None:
            return EligibilityCheck(
                name=CHECK_AUTHORIZATION,
                label="Work authorization",
                status=EligibilityStatus.NEEDS_CONFIRMATION,
                explanation=(
                    "It is unclear whether you require visa sponsorship. ApplyPilot "
                    "will not guess."
                ),
                blocking=True,
                source="candidate profile",
                suggested_resolution=(
                    "Confirm whether you require visa sponsorship on your profile."
                ),
                details=details,
            )

        job = application.job
        text = _job_text(job) if job is not None else ""
        if sponsorship and any(phrase in text for phrase in _NO_SPONSORSHIP_PHRASES):
            return EligibilityCheck(
                name=CHECK_AUTHORIZATION,
                label="Work authorization",
                status=EligibilityStatus.INELIGIBLE,
                explanation=(
                    "You require visa sponsorship, but the posting states that "
                    "sponsorship is not available."
                ),
                blocking=True,
                source="candidate profile and stored job posting",
                suggested_resolution=(
                    "Do not auto-prepare this posting, or review the employer's "
                    "sponsorship policy yourself."
                ),
                details=details,
            )
        return EligibilityCheck(
            name=CHECK_AUTHORIZATION,
            label="Work authorization",
            status=EligibilityStatus.ELIGIBLE,
            explanation=(
                "Your recorded work authorization and sponsorship requirement are "
                "compatible with this posting."
            ),
            blocking=False,
            source="candidate profile and stored job posting",
            suggested_resolution="",
            details=details,
        )

    def evaluate_application_url(self, application: Application) -> EligibilityCheck:
        raw = _text(application.application_url)
        if not raw:
            return EligibilityCheck(
                name=CHECK_URL,
                label="Employer application URL",
                status=EligibilityStatus.INELIGIBLE,
                explanation="No employer application URL is stored.",
                blocking=True,
                source="application.application_url",
                suggested_resolution="Paste the employer's own application URL.",
                details={"application_url": None},
            )
        try:
            normalized = normalize_application_url(raw)
        except ApplicationUrlError as exc:
            return EligibilityCheck(
                name=CHECK_URL,
                label="Employer application URL",
                status=EligibilityStatus.INELIGIBLE,
                explanation=exc.message,
                blocking=True,
                source="application.application_url",
                suggested_resolution=(
                    "Paste a public https URL for the employer's application form. "
                    "Credentials, local addresses and non-HTTP schemes are refused."
                ),
                details={"application_url": raw},
            )
        return EligibilityCheck(
            name=CHECK_URL,
            label="Employer application URL",
            status=EligibilityStatus.ELIGIBLE,
            explanation="The employer application URL is a safe public http(s) URL.",
            blocking=False,
            source="application.application_url",
            suggested_resolution="",
            details={"application_url": normalized},
        )

    def evaluate_platform_support(
        self, application: Application, detection: PlatformDetection | None
    ) -> EligibilityCheck:
        if detection is None:
            return EligibilityCheck(
                name=CHECK_PLATFORM,
                label="Application platform support",
                status=EligibilityStatus.FAILED,
                explanation="The application platform could not be detected.",
                blocking=True,
                source="application URL platform detection",
                suggested_resolution="Correct the employer application URL and re-analyse it.",
                details={},
            )
        details = {
            "platform": detection.platform.value,
            "display_name": detection.display_name,
            "supports_automation": detection.supports_automation,
            "requires_account": detection.requires_account,
            "is_job_board_link": detection.is_job_board_link,
        }
        if detection.is_job_board_link:
            return EligibilityCheck(
                name=CHECK_PLATFORM,
                label="Application platform support",
                status=EligibilityStatus.UNSUPPORTED_PLATFORM,
                explanation=(
                    "The URL points to a job board, not the employer's application form."
                ),
                blocking=True,
                source="application URL platform detection",
                suggested_resolution=(
                    "Paste the employer's own application URL. ApplyPilot does not "
                    "scrape job boards."
                ),
                details=details,
            )
        if not detection.supports_automation:
            return EligibilityCheck(
                name=CHECK_PLATFORM,
                label="Application platform support",
                status=EligibilityStatus.UNSUPPORTED_PLATFORM,
                explanation=(
                    f"{detection.display_name} is handled by the guided manual workflow, "
                    "not automated preparation."
                ),
                blocking=True,
                source="application URL platform detection",
                suggested_resolution=(
                    "Prepare this application manually in the employer's portal."
                ),
                details=details,
            )
        if detection.requires_account:
            return EligibilityCheck(
                name=CHECK_PLATFORM,
                label="Application platform support",
                status=EligibilityStatus.NEEDS_USER_APPROVAL,
                explanation=(
                    f"{detection.display_name} normally requires an employer account, "
                    "which you must create and sign in to yourself."
                ),
                blocking=False,
                source="application URL platform detection",
                suggested_resolution=(
                    "Create or sign in to the employer account yourself before the "
                    "browser prepares the form."
                ),
                details=details,
            )
        return EligibilityCheck(
            name=CHECK_PLATFORM,
            label="Application platform support",
            status=EligibilityStatus.ELIGIBLE,
            explanation=f"{detection.display_name} supports prepared applications.",
            blocking=False,
            source="application URL platform detection",
            suggested_resolution="",
            details=details,
        )

    def evaluate_application_status(self, application: Application) -> EligibilityCheck:
        status = application.status
        details = {"application_status": status.value}
        if status in {
            ApplicationStatus.SUBMITTED,
            ApplicationStatus.SUBMITTING,
            ApplicationStatus.NEEDS_USER_ACTION,
        }:
            return EligibilityCheck(
                name=CHECK_STATUS,
                label="Existing application status",
                status=EligibilityStatus.ALREADY_APPLIED,
                explanation=(
                    "This application was already submitted or handed over for manual "
                    "submission."
                ),
                blocking=True,
                source="application state machine",
                suggested_resolution="Open the existing application instead of preparing it again.",
                details=details,
            )
        if status is ApplicationStatus.CANCELLED:
            return EligibilityCheck(
                name=CHECK_STATUS,
                label="Existing application status",
                status=EligibilityStatus.INELIGIBLE,
                explanation="This application was cancelled.",
                blocking=True,
                source="application state machine",
                suggested_resolution=(
                    "Restart the application from the Applications page if you want to "
                    "prepare it again."
                ),
                details=details,
            )
        if status is ApplicationStatus.FAILED:
            return EligibilityCheck(
                name=CHECK_STATUS,
                label="Existing application status",
                status=EligibilityStatus.FAILED,
                explanation="The previous application attempt failed.",
                blocking=True,
                source="application state machine",
                suggested_resolution=(
                    "Review the failure reason and prepare the application again."
                ),
                details=details,
            )
        return EligibilityCheck(
            name=CHECK_STATUS,
            label="Existing application status",
            status=EligibilityStatus.ELIGIBLE,
            explanation=f"The application is in the editable {status.value!r} state.",
            blocking=False,
            source="application state machine",
            suggested_resolution="",
            details=details,
        )

    async def evaluate_duplicate_application(
        self, user: User, application: Application
    ) -> EligibilityCheck:
        others = [
            item
            for item in await self.applications.list_applications(user)
            if item.id != application.id
        ]
        url = _normalise(application.application_url)
        company = _normalise(application.job.company if application.job else None)
        title = _normalise(application.job.job_title if application.job else None)

        duplicates: list[Application] = []
        for other in others:
            if other.status is ApplicationStatus.CANCELLED:
                continue
            same = other.job_id == application.job_id
            if not same and url and _normalise(other.application_url) == url:
                same = True
            if (
                not same
                and company
                and title
                and _normalise(other.job.company if other.job else None) == company
                and _normalise(other.job.job_title if other.job else None) == title
            ):
                same = True
            if same:
                duplicates.append(other)

        details = {
            "duplicate_application_ids": [str(item.id) for item in duplicates],
            "duplicate_statuses": [item.status.value for item in duplicates],
        }
        if not duplicates:
            return EligibilityCheck(
                name=CHECK_DUPLICATE,
                label="Duplicate application risk",
                status=EligibilityStatus.ELIGIBLE,
                explanation="No other application for this job or employer posting was found.",
                blocking=False,
                source="user application history",
                suggested_resolution="",
                details=details,
            )

        if any(
            item.status
            in {
                ApplicationStatus.SUBMITTED,
                ApplicationStatus.SUBMITTING,
                ApplicationStatus.NEEDS_USER_ACTION,
            }
            for item in duplicates
        ):
            return EligibilityCheck(
                name=CHECK_DUPLICATE,
                label="Duplicate application risk",
                status=EligibilityStatus.ALREADY_APPLIED,
                explanation=(
                    "Another application for the same posting was already submitted or "
                    "handed over."
                ),
                blocking=True,
                source="user application history",
                suggested_resolution="Open the existing application instead of applying twice.",
                details=details,
            )
        return EligibilityCheck(
            name=CHECK_DUPLICATE,
            label="Duplicate application risk",
            status=EligibilityStatus.DUPLICATE,
            explanation=(
                "Another application already exists for the same job, URL or employer "
                "posting."
            ),
            blocking=True,
            source="user application history",
            suggested_resolution="Reuse the existing application instead of creating a duplicate.",
            details=details,
        )

    def evaluate_user_automation_settings(
        self, settings: UserAutoApplySettings
    ) -> EligibilityCheck:
        if not settings.automation_enabled:
            return EligibilityCheck(
                name=CHECK_AUTOMATION,
                label="Auto-apply master switch",
                status=EligibilityStatus.NEEDS_USER_APPROVAL,
                explanation=(
                    "Automation is disabled. ApplyPilot will not open an employer "
                    "application until you enable it."
                ),
                blocking=False,
                source="auto-apply settings",
                suggested_resolution=(
                    "Enable auto-apply in the Auto-Apply dashboard when you are ready."
                ),
                details={"automation_enabled": False},
            )
        return EligibilityCheck(
            name=CHECK_AUTOMATION,
            label="Auto-apply master switch",
            status=EligibilityStatus.ELIGIBLE,
            explanation="Auto-apply automation is enabled in your settings.",
            blocking=False,
            source="auto-apply settings",
            suggested_resolution="",
            details={"automation_enabled": True},
        )

    async def evaluate_user_usage_limit(
        self, user: User, settings: UserAutoApplySettings
    ) -> EligibilityCheck:
        now = _utcnow()
        day_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
        submitted_today = await self.session.scalar(
            select(func.count())
            .select_from(Application)
            .where(
                Application.user_id == user.id,
                Application.submitted_at.is_not(None),
                Application.submitted_at >= day_start,
            )
        )
        count = int(submitted_today or 0)
        limit = int(settings.max_applications_per_day)
        details = {"applications_today": count, "max_applications_per_day": limit}
        if count >= limit:
            return EligibilityCheck(
                name=CHECK_USAGE,
                label="Daily application limit",
                status=EligibilityStatus.NEEDS_USER_APPROVAL,
                explanation=(
                    f"You already submitted {count} application"
                    f"{'s' if count != 1 else ''} today, reaching your limit of {limit}."
                ),
                blocking=True,
                source="application history and auto-apply settings",
                suggested_resolution=(
                    "Wait until tomorrow or raise your daily application limit."
                ),
                details=details,
            )
        return EligibilityCheck(
            name=CHECK_USAGE,
            label="Daily application limit",
            status=EligibilityStatus.ELIGIBLE,
            explanation=f"{count} of {limit} daily applications used.",
            blocking=False,
            source="application history and auto-apply settings",
            suggested_resolution="",
            details=details,
        )

    def evaluate_user_approval(
        self,
        settings: UserAutoApplySettings,
        approval: AutoApplyApproval | None,
        report_version: str,
    ) -> EligibilityCheck:
        details = {
            "require_user_approval": settings.require_user_approval,
            "require_user_review": settings.require_user_review,
            "approved_at": approval.approved_at.isoformat() if approval else None,
            "approved_report_version": approval.report_version if approval else None,
            "current_report_version": report_version,
        }
        if approval is not None and approval.report_version != report_version:
            return EligibilityCheck(
                name=CHECK_APPROVAL,
                label="Explicit user approval",
                status=EligibilityStatus.NEEDS_USER_APPROVAL,
                explanation=(
                    "The application changed after you approved it, so the previous "
                    "approval no longer applies."
                ),
                blocking=False,
                source="recorded auto-apply approval",
                suggested_resolution=(
                    "Review the current eligibility report and approve this version."
                ),
                details=details,
            )
        if approval is not None:
            return EligibilityCheck(
                name=CHECK_APPROVAL,
                label="Explicit user approval",
                status=EligibilityStatus.ELIGIBLE,
                explanation=(
                    f"You approved this exact report version on "
                    f"{approval.approved_at.isoformat()}."
                ),
                blocking=False,
                source="recorded auto-apply approval",
                suggested_resolution="",
                details=details,
            )
        if settings.require_user_approval:
            return EligibilityCheck(
                name=CHECK_APPROVAL,
                label="Explicit user approval",
                status=EligibilityStatus.NEEDS_USER_APPROVAL,
                explanation=(
                    "Your settings require an explicit approval before ApplyPilot may "
                    "open the employer's application."
                ),
                blocking=False,
                source="auto-apply settings",
                suggested_resolution=(
                    "Review this report and approve the current version."
                ),
                details=details,
            )
        return EligibilityCheck(
            name=CHECK_APPROVAL,
            label="Explicit user approval",
            status=EligibilityStatus.ELIGIBLE,
            explanation="Your settings do not require a separate preparation approval.",
            blocking=False,
            source="auto-apply settings",
            suggested_resolution="",
            details=details,
        )

    # --- Report construction ------------------------------------------------
    def build_eligibility_report(
        self,
        *,
        application: Application,
        checks: list[EligibilityCheck],
        settings: UserAutoApplySettings,
    ) -> EligibilityDecision:
        """Combine checks into the single next step. Blocking always wins."""
        if any(check.blocking for check in checks):
            return EligibilityDecision.BLOCKED
        if not settings.automation_enabled:
            return EligibilityDecision.READY_FOR_USER_REVIEW
        approval_check = next(
            (check for check in checks if check.name == CHECK_APPROVAL), None
        )
        if (
            settings.require_user_approval
            and approval_check is not None
            and approval_check.status is not EligibilityStatus.ELIGIBLE
        ):
            return EligibilityDecision.READY_FOR_USER_REVIEW
        return EligibilityDecision.READY_TO_PREPARE

    def build_report_version(
        self,
        application: Application,
        checks: list[EligibilityCheck],
        settings: UserAutoApplySettings,
        *,
        completeness: dict[str, Any],
        snapshot: QueueSnapshot,
        match: JobResumeMatch | None,
    ) -> str:
        """Fingerprint every input a user's approval is bound to."""
        payload = {
            "schema": 1,
            "application_id": str(application.id),
            "job_id": str(application.job_id),
            "status": application.status.value,
            "application_url": application.application_url,
            "platform": application.platform,
            "resume_id": str(application.resume_id or ""),
            "resume_score": (
                match.overall_score
                if match is not None
                else application.resume_score
            ),
            "missing_required": sorted(completeness.get("missing_required") or []),
            "queue_status": snapshot.queue.status.value,
            "required_remaining": snapshot.queue.required_remaining,
            "checks": [
                [check.name, check.status.value, check.blocking]
                for check in checks
                if check.name not in _VOLATILE_CHECKS
            ],
            "settings": {
                "automation_enabled": settings.automation_enabled,
                "require_user_approval": settings.require_user_approval,
                "require_user_review": settings.require_user_review,
                "minimum_resume_match_percentage": settings.minimum_resume_match_percentage,
                "preferred_job_titles": list(settings.preferred_job_titles or []),
                "excluded_job_titles": list(settings.excluded_job_titles or []),
                "preferred_companies": list(settings.preferred_companies or []),
                "excluded_companies": list(settings.excluded_companies or []),
                "preferred_locations": list(settings.preferred_locations or []),
                "work_modes": list(settings.work_modes or []),
                "employment_types": list(settings.employment_types or []),
                "minimum_salary": str(settings.minimum_salary or ""),
                "maximum_salary": str(settings.maximum_salary or ""),
                "currency": settings.currency,
                "relocation_willingness": settings.relocation_willingness.value,
                "max_applications_per_day": settings.max_applications_per_day,
            },
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return f"elig-1-{hashlib.sha256(encoded).hexdigest()[:16]}"

    # --- Helpers -------------------------------------------------------------
    async def _resume_match(
        self, user: User, application: Application
    ) -> JobResumeMatch | None:
        if application.resume_id is None or application.job is None:
            return None
        rows = await self.matching.ensure_job_matches(user, application.job)
        return next(
            (row for row in rows if row.resume_id == application.resume_id), None
        )

    async def _approval(
        self, user: User, application_id: UUID
    ) -> AutoApplyApproval | None:
        return await self.session.scalar(
            select(AutoApplyApproval).where(
                AutoApplyApproval.user_id == user.id,
                AutoApplyApproval.application_id == application_id,
            )
        )

    @staticmethod
    def _detect(application: Application) -> PlatformDetection | None:
        try:
            return detect_platform(application.application_url)
        except ApplicationUrlError:
            return None

    @staticmethod
    def _readiness_percentage(checks: list[EligibilityCheck]) -> int:
        if not checks:
            return 0
        weights = {
            EligibilityStatus.ELIGIBLE: 1.0,
            EligibilityStatus.NEEDS_USER_APPROVAL: 0.8,
            EligibilityStatus.NEEDS_CONFIRMATION: 0.5,
            EligibilityStatus.NEEDS_INFORMATION: 0.3,
            EligibilityStatus.INELIGIBLE: 0.0,
            EligibilityStatus.DUPLICATE: 0.0,
            EligibilityStatus.ALREADY_APPLIED: 0.0,
            EligibilityStatus.UNSUPPORTED_PLATFORM: 0.0,
            EligibilityStatus.FAILED: 0.0,
        }
        total = sum(weights[check.status] for check in checks)
        return round((total / len(checks)) * 100)


class AutoApplyBlockedError(AppError):
    """The report still contains blocking issues."""

    status_code = 409
    code = "application_not_eligible"
    message = "This application still has blocking eligibility issues."


class AutoApplyApprovalVersionError(AppError):
    """The approved version is no longer the current report version."""

    status_code = 409
    code = "approval_version_mismatch"
    message = "The eligibility report changed after it was approved."


__all__ = [
    "AutoApplyApprovalVersionError",
    "AutoApplyBlockedError",
    "AutoApplyEligibilityService",
    "EligibilityCheck",
    "EligibilityDecision",
    "EligibilityReport",
    "EligibilityStatus",
    "extract_salary_mentions",
]
