"""Redirect-aware application navigation for listing Easy Apply flows.

A Glassdoor (or other board) listing is opened exactly as discovered. When the
user clicks Easy Apply themselves, the persistent Playwright session follows
the navigation *inside its approved boundary* and this service inspects where it
landed. It never clicks the button, never navigates programmatically, never
scrapes a protected listing and never overwrites the original listing URL.

Security model:

* the browser's :class:`NavigationPolicy` already refused unapproved hosts, so
  an unknown destination is never loaded;
* a detected destination is checked again against strict platform hostnames and
  resolved to public addresses before anything is stored;
* private, loopback and internal destinations are rejected and not recorded;
* only non-secret metadata is persisted (origin + path, platform, status).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import InvalidOperationError
from app.core.logging import get_logger
from app.models.application import Application, RedirectStatus
from app.models.browser_session import BrowserSessionStatus
from app.models.user import User
from app.services.application_platforms import (
    ApplicationPlatform,
    ApplicationUrlError,
    DetectionConfidence,
    PlatformDetection,
    detect_platform,
    listing_source_for_url,
)
from app.services.application_service import ApplicationService
from app.services.application_url_security import (
    HostResolver,
    UnsafeNavigationError,
    sanitize_url_for_display,
    validate_application_destination,
)
from app.services.browser_session_service import (
    FINALIZED_APPLICATION_STATES,
    BrowserSessionExpiredError,
    BrowserSessionService,
)
from app.services.indeed_smartapply import (
    SmartApplyStep,
    application_step_for_url,
    inspect_page,
    is_authentication_url,
    is_smartapply_url,
)

logger = get_logger(__name__)

#: Domains that may hand a listing application off to a form. Everything else is
#: blocked by the navigation guard before this service ever sees it.
REDIRECT_PLATFORMS: frozenset[ApplicationPlatform] = frozenset(
    {
        ApplicationPlatform.GREENHOUSE,
        ApplicationPlatform.LEVER,
        ApplicationPlatform.ASHBY,
        ApplicationPlatform.SMARTRECRUITERS,
        ApplicationPlatform.INDEED_SMARTAPPLY,
    }
)

AWAITING_MESSAGE = (
    "The job listing is open. Click Easy Apply yourself in the live browser; "
    "ApplyPilot watches where it goes but never clicks the button for you."
)
UNSUPPORTED_MESSAGE = (
    "That destination is not a supported application platform. ApplyPilot did "
    "not follow or store it. If the employer uses another form, paste its URL "
    "on the application page and continue manually."
)
BLOCKED_MESSAGE = (
    "The page tried to open a destination ApplyPilot does not allow. The "
    "navigation was blocked and the listing is still open. Continue Easy Apply "
    "yourself or add the employer's application URL manually."
)
LOGIN_MESSAGE = (
    "This application platform requires you to sign in. Complete the sign-in "
    "yourself in the live browser; ApplyPilot never enters passwords, one-time "
    "codes or MFA details."
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class RedirectOutcome:
    """Result of inspecting the live page for an application hand-off."""

    application_id: uuid.UUID
    browser_session_id: uuid.UUID
    status: RedirectStatus
    message: str
    listing_url: str | None
    destination_url: str | None
    application_platform: str | None
    platform_name: str | None
    application_step: str | None
    step_supported: bool
    destination_validated: bool
    awaiting_user_action: bool
    can_analyze: bool
    authentication_required: bool = False
    warnings: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "application_id": self.application_id,
            "browser_session_id": self.browser_session_id,
            "status": self.status.value,
            "message": self.message,
            "listing_url": self.listing_url,
            "destination_url": self.destination_url,
            "application_platform": self.application_platform,
            "platform_name": self.platform_name,
            "application_step": self.application_step,
            "step_supported": self.step_supported,
            "destination_validated": self.destination_validated,
            "awaiting_user_action": self.awaiting_user_action,
            "can_analyze": self.can_analyze,
            "authentication_required": self.authentication_required,
            "warnings": list(self.warnings),
        }


class ApplicationRedirectService:
    """Detect, validate and record where an Easy Apply hand-off landed."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        browser_sessions: BrowserSessionService | None = None,
        resolver: HostResolver | None = None,
    ) -> None:
        self.session = session
        self.applications = ApplicationService(session)
        self.browser_sessions = browser_sessions or BrowserSessionService(
            session, resolver=resolver
        )
        self.resolver = resolver

    async def check(self, user: User, application_id: uuid.UUID) -> RedirectOutcome:
        """Inspect the current live page and record a validated destination.

        Returns a precise status instead of raising for an unsupported or unsafe
        redirect, because that is a normal user-facing outcome - not a broken
        browser session. A missing/expired browser still raises.
        """
        application = await self.applications.get_application(user, application_id)
        if application.status in FINALIZED_APPLICATION_STATES:
            raise InvalidOperationError(
                "A redirect cannot be detected for a finished application.",
                code="application_finalized",
            )

        session_row = await self.browser_sessions.get_session(user, application_id)
        if session_row.browser_status is not BrowserSessionStatus.OPEN:
            raise BrowserSessionExpiredError()

        live = await self.browser_sessions.manager.get(
            session_row.id, user_id=user.id
        )
        if live is None:
            raise BrowserSessionExpiredError()
        handle = live.handle

        listing_url = application.job_listing_url or application.application_url
        start_detection = self._detect(application.application_url)
        list_mode = bool(start_detection and start_detection.is_job_board_link)
        current_url = sanitize_url_for_display(await handle.current_url())
        current_detection = self._detect(current_url) if current_url else None
        page_html = ""
        if (
            current_detection is not None
            and current_detection.platform is ApplicationPlatform.INDEED_SMARTAPPLY
        ):
            # Read-only inspection of the already-open page. No click, submit or
            # extra request leaves the browser.
            try:
                page_html = await handle.content()
            except Exception:
                page_html = ""

        outcome: RedirectOutcome
        if current_url and is_authentication_url(current_url):
            # A supported Indeed sign-in step. The session stays alive so the
            # user can authenticate manually; nothing is typed for them.
            outcome = self._authentication_required(
                application=application,
                session_row_id=session_row.id,
                listing_url=listing_url,
            )
        elif current_detection is not None and self._is_supported_destination(
            current_detection, current_url
        ):
            outcome = await self._validate_destination(
                application=application,
                session_row_id=session_row.id,
                listing_url=listing_url,
                current_url=current_url,
                detection=current_detection,
                page_html=page_html,
            )
        elif getattr(handle, "blocked_navigation", False):
            outcome = self._unsupported(
                application=application,
                session_row_id=session_row.id,
                listing_url=listing_url,
                message=BLOCKED_MESSAGE,
            )
        elif list_mode and (
            current_detection is None or current_detection.is_job_board_link
        ):
            outcome = self._awaiting(
                application=application,
                session_row_id=session_row.id,
                listing_url=listing_url,
            )
        elif current_detection is not None and current_detection.is_application_page:
            # A recognised form on a platform that is deliberately manual-only
            # (for example Workday) is still a real destination.
            outcome = self._unsupported(
                application=application,
                session_row_id=session_row.id,
                listing_url=listing_url,
                message=(
                    "This application platform is handled manually. Open it in "
                    "the live browser yourself; ApplyPilot will not drive it."
                ),
            )
        else:
            outcome = self._unsupported(
                application=application,
                session_row_id=session_row.id,
                listing_url=listing_url,
                message=UNSUPPORTED_MESSAGE,
            )

        await self.session.commit()
        logger.info(
            "Application destination checked",
            extra={
                "stage": "application_redirect",
                "user_id": str(user.id),
                "application_id": str(application.id),
                "status": outcome.status.value,
                "platform": outcome.application_platform,
                "step": outcome.application_step,
            },
        )
        return outcome

    # --- Classification -----------------------------------------------------
    @staticmethod
    def _detect(url: str) -> PlatformDetection | None:
        if not url:
            return None
        try:
            return detect_platform(url)
        except ApplicationUrlError:
            return None

    def _is_supported_destination(
        self, detection: PlatformDetection, url: str
    ) -> bool:
        if detection.is_job_board_link or not detection.is_application_page:
            return False
        if detection.platform not in REDIRECT_PLATFORMS:
            return False
        if detection.confidence is DetectionConfidence.NONE:
            return False
        if detection.platform is ApplicationPlatform.INDEED_SMARTAPPLY:
            # Strict hostname/transport re-check; never trust "indeed" in a URL.
            return is_smartapply_url(url)
        return True

    async def _validate_destination(
        self,
        *,
        application: Application,
        session_row_id: uuid.UUID,
        listing_url: str | None,
        current_url: str,
        detection: PlatformDetection,
        page_html: str = "",
    ) -> RedirectOutcome:
        try:
            normalized, _addresses = await validate_application_destination(
                current_url, resolver=self.resolver
            )
        except UnsafeNavigationError as exc:
            return self._record(
                application=application,
                session_row_id=session_row_id,
                status=RedirectStatus.REJECTED,
                message=(
                    f"{getattr(exc, 'message', str(exc))} ApplyPilot did not store "
                    "or open it."
                ),
                listing_url=listing_url,
                destination_url=None,
                platform=None,
                platform_name=None,
                step=None,
                step_supported=False,
                validated=False,
            )

        display_url = sanitize_url_for_display(normalized)
        step = (
            application_step_for_url(display_url)
            if detection.platform is ApplicationPlatform.INDEED_SMARTAPPLY
            else None
        )
        supported_step = step is not None and step is not SmartApplyStep.UNKNOWN
        # `step_supported` describes SmartApply steps only. Every other
        # recognised platform exposes an ordinary form, so a validated
        # destination there can always be analysed.
        can_analyze = (
            supported_step
            if detection.platform is ApplicationPlatform.INDEED_SMARTAPPLY
            else True
        )
        warnings: list[str] = []
        message = (
            f"{detection.display_name} application form detected and validated."
        )
        auth_detected = False
        if detection.platform is ApplicationPlatform.INDEED_SMARTAPPLY:
            page = inspect_page(display_url, page_html)
            if page is not None:
                warnings.extend(page.notes)
                auth_detected = page.authentication_detected
            if step is SmartApplyStep.RESUME_SELECTION:
                message = (
                    "Indeed SmartApply detected at the resume-selection step. "
                    "Analyse the form to review the visible controls; choosing "
                    "or uploading the resume remains your decision."
                )
            elif step is SmartApplyStep.RESUME_UPLOAD:
                message = (
                    "Indeed SmartApply detected at the resume-upload step. "
                    "Attaching the approved resume still requires your approval."
                )
            elif step is SmartApplyStep.QUESTIONS:
                message = (
                    "Indeed SmartApply detected at the questions step. Analyse "
                    "the form to review the visible controls."
                )
            elif step is SmartApplyStep.REVIEW:
                message = (
                    "Indeed SmartApply detected at the review step. ApplyPilot "
                    "never submits the application; review and submit it yourself."
                )
            else:
                message = (
                    "Indeed SmartApply detected, but this step is not recognised "
                    "yet. Use the live browser to review it manually."
                )
                warnings.append(
                    "This SmartApply step is not supported yet; ApplyPilot will "
                    "not guess at its controls."
                )
        if auth_detected:
            message = f"{LOGIN_MESSAGE} {message}"

        return self._record(
            application=application,
            session_row_id=session_row_id,
            status=RedirectStatus.VALIDATED,
            message=message,
            listing_url=listing_url,
            destination_url=display_url,
            platform=detection.platform,
            platform_name=detection.display_name,
            step=step.value if step else None,
            step_supported=supported_step,
            validated=True,
            warnings=warnings,
            can_analyze=can_analyze,
        )

    # --- Persistence --------------------------------------------------------
    def _awaiting(
        self,
        *,
        application: Application,
        session_row_id: uuid.UUID,
        listing_url: str | None,
    ) -> RedirectOutcome:
        source = listing_source_for_url(application.application_url) or "job board"
        return self._record(
            application=application,
            session_row_id=session_row_id,
            status=RedirectStatus.AWAITING_USER_ACTION,
            message=AWAITING_MESSAGE,
            listing_url=listing_url,
            destination_url=None,
            platform=None,
            platform_name=None,
            step=None,
            step_supported=False,
            validated=False,
            warnings=(f"The open page is the {source} job listing.",),
            awaiting=True,
        )

    def _authentication_required(
        self,
        *,
        application: Application,
        session_row_id: uuid.UUID,
        listing_url: str | None,
    ) -> RedirectOutcome:
        return self._record(
            application=application,
            session_row_id=session_row_id,
            status=RedirectStatus.AWAITING_USER_ACTION,
            message=LOGIN_MESSAGE,
            listing_url=listing_url,
            destination_url=None,
            platform=None,
            platform_name=None,
            step=None,
            step_supported=False,
            validated=False,
            warnings=(
                "ApplyPilot detected Indeed's sign-in step. Complete it yourself "
                "in the live browser; the session stays yours.",
            ),
            awaiting=True,
            authentication_required=True,
        )

    def _unsupported(
        self,
        *,
        application: Application,
        session_row_id: uuid.UUID,
        listing_url: str | None,
        message: str,
    ) -> RedirectOutcome:
        return self._record(
            application=application,
            session_row_id=session_row_id,
            status=RedirectStatus.UNSUPPORTED,
            message=message,
            listing_url=listing_url,
            destination_url=None,
            platform=None,
            platform_name=None,
            step=None,
            step_supported=False,
            validated=False,
        )

    def _record(
        self,
        *,
        application: Application,
        session_row_id: uuid.UUID,
        status: RedirectStatus,
        message: str,
        listing_url: str | None,
        destination_url: str | None,
        platform: ApplicationPlatform | None,
        platform_name: str | None,
        step: str | None,
        step_supported: bool,
        validated: bool,
        warnings: list[str] | tuple[str, ...] = (),
        awaiting: bool = False,
        can_analyze: bool = False,
        authentication_required: bool = False,
    ) -> RedirectOutcome:
        application.redirect_status = status
        if validated and platform is not None and destination_url:
            # The original listing and the page the browser opens are untouched.
            application.application_destination_url = destination_url
            application.destination_platform = platform.value
            application.destination_validated = True
            application.redirect_detected_at = _utcnow()
            application.platform = platform.value
            application.platform_name = platform_name or platform.value
        # A refused destination never becomes the stored form. A previously
        # validated destination, if any, stays exactly as it was.

        self.session.add(application)
        return RedirectOutcome(
            application_id=application.id,
            browser_session_id=session_row_id,
            status=status,
            message=message,
            listing_url=listing_url,
            destination_url=(
                application.application_destination_url if validated else None
            ),
            application_platform=platform.value if platform else None,
            platform_name=platform_name,
            application_step=step,
            step_supported=step_supported,
            destination_validated=bool(
                validated or application.destination_validated
            ),
            awaiting_user_action=awaiting,
            can_analyze=can_analyze,
            authentication_required=authentication_required,
            warnings=tuple(warnings),
        )


__all__ = [
    "AWAITING_MESSAGE",
    "LOGIN_MESSAGE",
    "REDIRECT_PLATFORMS",
    "ApplicationRedirectService",
    "RedirectOutcome",
]
