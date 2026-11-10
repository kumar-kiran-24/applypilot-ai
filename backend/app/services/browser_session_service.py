"""Phase 2E Stage A: owned, validated browser sessions with a state machine.

Responsibilities:

* resolve the persisted employer URL from an application the user owns;
* reject job-board links and unsafe destinations (SSRF/DNS policy);
* prevent duplicate execution and open at most one browser per application;
* persist execution state and honest failure/expiry information;
* pause/resume/cancel without pretending a dead browser was preserved.

This stage opens the employer page and holds it. It does **not** analyze or fill
the form, and it does not claim a user-visible embedded browser: the view
capability is reported separately by :mod:`app.integrations.browser.view`.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.exceptions import AppError, InvalidOperationError, ResourceNotFoundError
from app.core.logging import get_logger
from app.integrations.browser.launch_policy import preferred_address
from app.integrations.browser.persistent import PersistentBrowserHandle
from app.integrations.browser.session import BrowserUnavailableError
from app.integrations.browser.view import browser_view_capability
from app.models.application import Application, ApplicationStatus
from app.models.browser_session import (
    BrowserSession,
)
from app.models.browser_session import (
    BrowserSessionState as State,
)
from app.models.browser_session import (
    BrowserSessionStatus as Status,
)
from app.services.application_adapters import adapter_for_url
from app.services.application_service import ApplicationService
from app.services.application_url_security import (
    HostResolver,
    UnsafeNavigationError,
    policy_for_detection,
    sanitize_url_for_display,
    validate_application_destination,
)
from app.services.browser_session_manager import (
    BrowserCapacityError,
    BrowserSessionConflictError,
    BrowserSessionManager,
    get_browser_session_manager,
)
from app.services.browser_session_state import (
    ACTIVE_STATES,
    BrowserSessionStateError,
    can_transition,
    transition,
)

if TYPE_CHECKING:
    from app.models.user import User

logger = get_logger(__name__)

#: An application in these states must not receive a new browser run.
FINALIZED_APPLICATION_STATES: frozenset[ApplicationStatus] = frozenset(
    {ApplicationStatus.SUBMITTED, ApplicationStatus.SUBMITTING, ApplicationStatus.CANCELLED}
)

EXPIRED_MESSAGE = (
    "The browser session expired or was closed. ApplyPilot did not preserve the "
    "employer's page; start the session again to reopen it."
)


class BrowserSessionNotFoundError(ResourceNotFoundError):
    """No browser session exists for this application and user."""

    code = "browser_session_not_found"
    message = "This application has no browser session yet."


class BrowserSessionUnavailableError(AppError):
    """Browser automation is disabled, uninstalled or could not start."""

    status_code = 503
    code = "browser_automation_unavailable"
    message = "Live browser automation is not available for this application."


class BrowserSessionCapacityError(AppError):
    """The host is running as many browsers as it is configured to allow."""

    status_code = 503
    code = "browser_capacity_reached"
    message = (
        "The server has reached its live browser capacity. Try again when an "
        "open session has finished."
    )


class BrowserSessionConflict(AppError):
    """A live browser session already exists or the user has too many."""

    status_code = 409
    code = "browser_session_conflict"
    message = "A browser session is already active."


class BrowserSessionExpiredError(InvalidOperationError):
    """The browser is gone; the user must reopen the employer page."""

    code = "browser_session_expired"
    message = EXPIRED_MESSAGE


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class BrowserSessionService:
    """Owns the browser-session lifecycle for one authenticated user."""

    def __init__(
        self,
        session: Any,
        *,
        manager: BrowserSessionManager | None = None,
        resolver: HostResolver | None = None,
    ) -> None:
        self.session = session
        self.applications = ApplicationService(session)
        self.manager = manager or get_browser_session_manager()
        self.resolver = resolver

    # --- Reads ---------------------------------------------------------------
    async def get_session(self, user: User, application_id: uuid.UUID) -> BrowserSession:
        """Return the application's session, reconciling a dead browser."""
        application = await self.applications.get_application(user, application_id)
        row = await self._row_for_application(application.id, user.id)
        if row is None:
            raise BrowserSessionNotFoundError()
        if await self._reconcile(user, row):
            await self.session.commit()
        return row

    async def get_session_by_id(self, user: User, session_id: uuid.UUID) -> BrowserSession:
        row = await self._row_by_id(session_id, user.id)
        if row is None:
            raise BrowserSessionNotFoundError()
        if await self._reconcile(user, row):
            await self.session.commit()
        return row

    async def require_live_handle(
        self, user: User, application_id: uuid.UUID
    ) -> tuple[BrowserSession, PersistentBrowserHandle]:
        """Return the owned, live browser handle for read-only form analysis.

        Ownership is checked twice: the application belongs to the user and the
        session row belongs to both the user and that application. A browser
        that expired, crashed or left its approved origin is reported honestly
        instead of being used.
        """
        row = await self.get_session(user, application_id)
        if row.browser_status is not Status.OPEN:
            raise BrowserSessionExpiredError()
        live = await self.manager.get(row.id, user_id=user.id)
        if live is None:
            await self._mark_expired(row)
            await self.session.commit()
            raise BrowserSessionExpiredError()
        if live.handle.unsafe_navigation:
            await self.manager.release(row.id)
            await self._mark_failed(
                row,
                "navigation_blocked",
                "The employer's page attempted to leave the approved site.",
            )
            raise UnsafeNavigationError(
                "The employer's page attempted to navigate outside the approved site.",
                code="navigation_blocked",
            )
        return row, live.handle

    async def list_active(self, user: User) -> list[BrowserSession]:
        """Every session whose browser is genuinely open right now."""
        rows = list(
            (
                await self.session.scalars(
                    select(BrowserSession)
                    .where(
                        BrowserSession.user_id == user.id,
                        BrowserSession.browser_status == Status.OPEN,
                    )
                    .options(
                        selectinload(BrowserSession.application).selectinload(Application.job)
                    )
                    .order_by(BrowserSession.last_activity_at.desc())
                )
            ).all()
        )
        active: list[BrowserSession] = []
        changed = False
        for row in rows:
            changed = await self._reconcile(user, row) or changed
            if row.browser_status is Status.OPEN:
                active.append(row)
        if changed:
            await self.session.commit()
        return active

    # --- Start ---------------------------------------------------------------
    async def start(
        self,
        user: User,
        application_id: uuid.UUID,
        *,
        restart: bool = False,
    ) -> BrowserSession:
        """Open the employer page, idempotently for an already-live session."""
        application = await self.applications.get_application(user, application_id)
        if application.status in FINALIZED_APPLICATION_STATES:
            raise InvalidOperationError(
                "A browser session cannot be started for a finished application.",
                code="application_finalized",
            )

        adapter, detection = adapter_for_url(application.application_url)
        if detection.is_job_board_link:
            raise InvalidOperationError(
                "This link points to a job board. Add the employer's own application "
                "URL before starting a browser session.",
                code="job_board_url_not_supported",
            )

        row = await self._row_for_application(application.id, user.id, for_update=True)
        if row is not None:
            live = await self.manager.get(row.id, user_id=user.id)
            if live is not None:
                if restart:
                    raise BrowserSessionConflict(
                        "A live browser session is already open for this application. "
                        "Pause or cancel it before starting a new run.",
                        code="browser_session_already_open",
                    )
                live.expires_at_monotonic = (
                    self.manager.clock() + self.manager.ttl_seconds
                )
                row.last_activity_at = _utcnow()
                row.expires_at = row.last_activity_at + timedelta(
                    seconds=self.manager.ttl_seconds
                )
                await self.session.commit()
                return await self._reload(row.id, user.id)
            # The row claims an open browser but the process no longer has one.
            if row.browser_status is Status.OPEN:
                await self._mark_expired(row, release=False)

        await self._enforce_user_session_limit(user, exclude=row.id if row else None)
        # Resolves and validates every address, then pins one into Chromium's
        # resolver so the name cannot be rebound after validation.
        _, addresses = await validate_application_destination(
            application.application_url, resolver=self.resolver
        )
        pinned_address = (
            preferred_address(addresses)
            if settings.APPLICATION_BROWSER_PIN_DNS
            else None
        )
        policy = policy_for_detection(
            detection,
            extra_origins=tuple(settings.APPLICATION_BROWSER_ALLOWED_REDIRECT_ORIGINS),
        )

        now = _utcnow()
        if row is None:
            row = BrowserSession(
                id=uuid.uuid4(),
                user_id=user.id,
                application_id=application.id,
                run_number=1,
                state=State.CREATED,
                browser_status=Status.OPENING,
                allowed_origins=list(policy.allowed_origins),
                checkpoint={},
            )
            self.session.add(row)
        else:
            # Explicit new run: previous in-memory state is gone, so run_number
            # advances and stale checkpoints are cleared rather than reused.
            row.run_number += 1
            row.state = State.CREATED
            row.browser_status = Status.OPENING
            row.allowed_origins = list(policy.allowed_origins)
            row.checkpoint = {}
            row.paused_from_state = None
            row.pause_reason = None
            row.failure_code = None
            row.failure_reason = None
            row.stopped_at = None
        row.current_url = sanitize_url_for_display(application.application_url)
        row.started_at = now
        row.last_activity_at = now
        row.expires_at = now + timedelta(seconds=self.manager.ttl_seconds)
        transition(row, State.BROWSER_OPENING)
        await self.session.commit()

        try:
            live = await self.manager.acquire(
                session_id=row.id,
                user_id=user.id,
                application_id=application.id,
                url=application.application_url,
                policy=policy,
                pinned_address=pinned_address,
            )
        except BrowserCapacityError as exc:
            await self._mark_unavailable(row, str(exc))
            raise BrowserSessionCapacityError(str(exc)) from exc
        except BrowserSessionConflictError as exc:
            await self.session.refresh(row)
            raise BrowserSessionConflict(str(exc), code="browser_session_conflict") from exc
        except BrowserUnavailableError as exc:
            await self._mark_unavailable(row, str(exc))
            raise BrowserSessionUnavailableError(str(exc)) from exc
        except UnsafeNavigationError as exc:
            await self._mark_failed(
                row, exc.code, "The employer's page could not be opened safely."
            )
            raise
        except Exception as exc:  # pragma: no cover - live browser failures
            logger.warning(
                "Browser session failed to start",
                extra={
                    "stage": "browser_session_start",
                    "application_id": str(application.id),
                    "error_class": type(exc).__name__,
                },
            )
            await self._mark_unavailable(
                row, "The browser session could not be opened."
            )
            raise BrowserSessionUnavailableError(
                "The browser session could not be opened."
            ) from exc

        current_url = sanitize_url_for_display(await live.handle.current_url())
        row.current_url = current_url or row.current_url
        if live.handle.unsafe_navigation:
            await self.manager.release(row.id)
            await self._mark_failed(
                row,
                "navigation_blocked",
                "The employer's page attempted to leave the approved site.",
            )
            raise UnsafeNavigationError(
                "The employer's page attempted to navigate outside the approved site.",
                code="navigation_blocked",
            )

        transition(row, State.BROWSER_READY)
        row.browser_status = Status.OPEN
        row.last_activity_at = _utcnow()
        row.expires_at = row.last_activity_at + timedelta(seconds=self.manager.ttl_seconds)
        await self.session.commit()
        logger.info(
            "Browser session started",
            extra={
                "stage": "browser_session_start",
                "user_id": str(user.id),
                "application_id": str(application.id),
                "platform": adapter.platform.value,
                "run_number": row.run_number,
            },
        )
        return await self._reload(row.id, user.id)

    # --- Controls ------------------------------------------------------------
    async def pause(
        self, user: User, application_id: uuid.UUID, *, reason: str | None = None
    ) -> BrowserSession:
        row = await self.get_session(user, application_id)
        if row.state is State.PAUSED:
            return row
        if row.state not in ACTIVE_STATES:
            raise BrowserSessionStateError("This browser session cannot be paused.")
        live = await self.manager.get(row.id, user_id=user.id)
        if live is None:
            await self._mark_expired(row)
            await self.session.commit()
            raise BrowserSessionExpiredError()
        row.paused_from_state = row.state.value
        transition(row, State.PAUSED)
        row.pause_reason = (reason or "Paused by you.").strip()[:500]
        row.last_activity_at = _utcnow()
        await self.session.commit()
        return await self._reload(row.id, user.id)

    async def resume(self, user: User, application_id: uuid.UUID) -> BrowserSession:
        row = await self.get_session(user, application_id)
        if row.state is not State.PAUSED:
            raise BrowserSessionStateError(
                "Only a paused browser session can be resumed."
            )
        live = await self.manager.get(row.id, user_id=user.id)
        if live is None:
            await self._mark_expired(row)
            await self.session.commit()
            raise BrowserSessionExpiredError()
        if live.handle.unsafe_navigation:
            await self.manager.release(row.id)
            await self._mark_failed(
                row,
                "navigation_blocked",
                "The employer's page attempted to leave the approved site.",
            )
            raise BrowserSessionStateError(
                "The session was stopped because the page left the approved site."
            )

        target = State.BROWSER_READY
        paused_from = row.paused_from_state
        if paused_from:
            try:
                candidate = State(paused_from)
            except ValueError:
                candidate = State.BROWSER_READY
            if can_transition(State.PAUSED, candidate):
                target = candidate
        transition(row, target)
        row.paused_from_state = None
        row.pause_reason = None
        row.last_activity_at = _utcnow()
        row.expires_at = row.last_activity_at + timedelta(seconds=self.manager.ttl_seconds)
        await self.manager.touch(row.id, user_id=user.id)
        await self.session.commit()
        return await self._reload(row.id, user.id)

    async def cancel(self, user: User, application_id: uuid.UUID) -> BrowserSession:
        row = await self.get_session(user, application_id)
        if row.state is State.CANCELLED:
            return row
        if row.state is State.COMPLETED_BY_USER:
            raise BrowserSessionStateError(
                "A completed application cannot be cancelled here."
            )
        transition(row, State.CANCELLED)
        await self.manager.release(row.id)
        row.browser_status = Status.CLOSED
        row.stopped_at = _utcnow()
        row.pause_reason = None
        row.paused_from_state = None
        await self.session.commit()
        return await self._reload(row.id, user.id)

    # --- Reconciliation ------------------------------------------------------
    async def _reconcile(self, user: User, row: BrowserSession) -> bool:
        """Align persisted status with the actual browser. Returns True on change."""
        if row.browser_status is not Status.OPEN:
            return False
        live = await self.manager.get(row.id, user_id=user.id)
        if live is None:
            await self._mark_expired(row, release=False)
            return True
        if live.handle.unsafe_navigation:
            await self.manager.release(row.id)
            row.browser_status = Status.CLOSED
            row.stopped_at = _utcnow()
            if row.state not in {State.CANCELLED, State.COMPLETED_BY_USER}:
                row.state = State.FAILED
            row.failure_code = "navigation_blocked"
            row.failure_reason = "The employer's page attempted to leave the approved site."
            return True
        current = sanitize_url_for_display(await live.handle.current_url())
        if current and current != row.current_url:
            row.current_url = current
        row.last_activity_at = _utcnow()
        return True

    async def _mark_expired(self, row: BrowserSession, *, release: bool = True) -> None:
        if release:
            await self.manager.release(row.id)
        row.browser_status = Status.EXPIRED
        row.stopped_at = _utcnow()
        if row.state not in {State.CANCELLED, State.COMPLETED_BY_USER}:
            if row.state is not State.PAUSED:
                row.paused_from_state = row.state.value
                row.state = State.PAUSED
            row.pause_reason = EXPIRED_MESSAGE
        row.failure_code = "browser_session_expired"

    async def _mark_unavailable(self, row: BrowserSession, reason: str) -> None:
        await self.manager.release(row.id)
        row.state = State.FAILED
        row.browser_status = Status.UNAVAILABLE
        row.failure_code = "browser_automation_unavailable"
        row.failure_reason = reason[:2000]
        row.stopped_at = _utcnow()
        await self.session.commit()

    async def _mark_failed(self, row: BrowserSession, code: str, reason: str) -> None:
        row.state = State.FAILED
        row.browser_status = Status.CLOSED
        row.failure_code = code
        row.failure_reason = reason[:2000]
        row.stopped_at = _utcnow()
        await self.session.commit()

    async def _enforce_user_session_limit(self, user: User, *, exclude: uuid.UUID | None) -> None:
        # Reconcile first: a dead browser left behind by a crash or expiry must
        # not block the user from ever starting another session.
        candidates = list(
            (
                await self.session.scalars(
                    select(BrowserSession).where(
                        BrowserSession.user_id == user.id,
                        BrowserSession.browser_status.in_([Status.OPEN, Status.OPENING]),
                    )
                )
            ).all()
        )
        live_count = 0
        for candidate in candidates:
            if exclude is not None and candidate.id == exclude:
                continue
            if await self.manager.get(candidate.id, user_id=user.id) is None:
                await self._mark_expired(candidate, release=False)
                continue
            live_count += 1
        if live_count >= settings.APPLICATION_BROWSER_MAX_ACTIVE_PER_USER:
            raise BrowserSessionConflict(
                "Only one live browser session is allowed at a time. Pause or "
                "cancel the open session first.",
                code="browser_session_limit_reached",
            )

    # --- Serialisation -------------------------------------------------------
    def snapshot(self, row: BrowserSession) -> dict[str, Any]:
        application = row.application
        active = row.state in ACTIVE_STATES and row.browser_status is Status.OPEN
        return {
            "session_id": row.id,
            "application_id": row.application_id,
            "job_id": application.job_id if application else None,
            "job_title": application.job.job_title if application and application.job else "",
            "company": application.job.company if application and application.job else None,
            "application_url": application.application_url if application else "",
            "run_number": row.run_number,
            "state": row.state.value,
            "browser_status": row.browser_status.value,
            "current_url": row.current_url,
            "allowed_origins": list(row.allowed_origins or []),
            "pause_reason": row.pause_reason,
            "failure_code": row.failure_code,
            "failure_reason": row.failure_reason,
            "checkpoint": dict(row.checkpoint or {}),
            "active": active,
            "can_pause": active and row.state is not State.PAUSED,
            "can_resume": row.state is State.PAUSED and row.browser_status is Status.OPEN,
            "can_cancel": row.state not in {State.CANCELLED, State.COMPLETED_BY_USER},
            "started_at": row.started_at,
            "last_activity_at": row.last_activity_at,
            "expires_at": row.expires_at,
            "stopped_at": row.stopped_at,
            "view": browser_view_capability().to_payload(),
        }

    # --- Queries -------------------------------------------------------------
    async def _row_for_application(
        self, application_id: uuid.UUID, user_id: uuid.UUID, *, for_update: bool = False
    ) -> BrowserSession | None:
        statement = (
            select(BrowserSession)
            .where(
                BrowserSession.application_id == application_id,
                BrowserSession.user_id == user_id,
            )
            .options(
                selectinload(BrowserSession.application).selectinload(Application.job)
            )
        )
        if for_update:
            # Serialises two concurrent starts for the same application.
            statement = statement.with_for_update()
        return await self.session.scalar(statement)

    async def _row_by_id(
        self, session_id: uuid.UUID, user_id: uuid.UUID
    ) -> BrowserSession | None:
        return await self.session.scalar(
            select(BrowserSession)
            .join(Application, Application.id == BrowserSession.application_id)
            .where(
                BrowserSession.id == session_id,
                BrowserSession.user_id == user_id,
                Application.user_id == user_id,
            )
            .options(
                selectinload(BrowserSession.application).selectinload(Application.job)
            )
        )

    async def _reload(self, session_id: uuid.UUID, user_id: uuid.UUID) -> BrowserSession:
        await self.session.flush()
        row = await self._row_by_id(session_id, user_id)
        if row is None:  # pragma: no cover - row cannot vanish mid-request
            raise BrowserSessionNotFoundError()
        return row


__all__ = [
    "EXPIRED_MESSAGE",
    "FINALIZED_APPLICATION_STATES",
    "BrowserSessionCapacityError",
    "BrowserSessionConflict",
    "BrowserSessionExpiredError",
    "BrowserSessionNotFoundError",
    "BrowserSessionService",
    "BrowserSessionUnavailableError",
]
