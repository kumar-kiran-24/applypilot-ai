"""Explicit state machine for job applications.

Every status change goes through :func:`transition`, which rejects moves that
are not declared below. Keeping the rules in one place makes it impossible to
edit a submitted application, re-submit an approved one twice or skip approval:

    DRAFT -> ANALYZING -> AWAITING_USER_INFORMATION <-> READY_FOR_REVIEW
                        -> AWAITING_APPROVAL -> SUBMITTING -> SUBMITTED
                                             \\-> NEEDS_USER_ACTION
    FAILED and NEEDS_USER_ACTION can be recovered by preparing the application
    again; SUBMITTED and SUBMITTING cannot be cancelled because the employer may
    already have received the application.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.exceptions import AppError
from app.models.application import ApplicationStatus

if TYPE_CHECKING:
    from app.models.application import Application


class ApplicationStateError(AppError):
    """The requested action conflicts with the application's current state."""

    status_code = 409
    code = "application_state_conflict"
    message = "This action is not allowed for the application's current state."


#: Legal one-step moves. A missing key means the state is terminal.
ALLOWED_TRANSITIONS: dict[ApplicationStatus, frozenset[ApplicationStatus]] = {
    ApplicationStatus.DRAFT: frozenset(
        {ApplicationStatus.ANALYZING, ApplicationStatus.CANCELLED}
    ),
    ApplicationStatus.ANALYZING: frozenset(
        {
            ApplicationStatus.AWAITING_USER_INFORMATION,
            ApplicationStatus.READY_FOR_REVIEW,
            ApplicationStatus.NEEDS_USER_ACTION,
            ApplicationStatus.FAILED,
            ApplicationStatus.CANCELLED,
        }
    ),
    ApplicationStatus.AWAITING_USER_INFORMATION: frozenset(
        {
            ApplicationStatus.ANALYZING,
            ApplicationStatus.READY_FOR_REVIEW,
            ApplicationStatus.CANCELLED,
        }
    ),
    ApplicationStatus.READY_FOR_REVIEW: frozenset(
        {
            ApplicationStatus.ANALYZING,
            ApplicationStatus.AWAITING_USER_INFORMATION,
            ApplicationStatus.AWAITING_APPROVAL,
            ApplicationStatus.CANCELLED,
        }
    ),
    ApplicationStatus.AWAITING_APPROVAL: frozenset(
        {
            ApplicationStatus.ANALYZING,
            ApplicationStatus.AWAITING_USER_INFORMATION,
            ApplicationStatus.READY_FOR_REVIEW,
            ApplicationStatus.SUBMITTING,
            ApplicationStatus.NEEDS_USER_ACTION,
            ApplicationStatus.CANCELLED,
        }
    ),
    ApplicationStatus.SUBMITTING: frozenset(
        {
            ApplicationStatus.SUBMITTED,
            ApplicationStatus.FAILED,
            ApplicationStatus.NEEDS_USER_ACTION,
        }
    ),
    ApplicationStatus.SUBMITTED: frozenset(),
    ApplicationStatus.FAILED: frozenset(
        {
            ApplicationStatus.ANALYZING,
            ApplicationStatus.AWAITING_USER_INFORMATION,
            ApplicationStatus.READY_FOR_REVIEW,
            ApplicationStatus.SUBMITTED,
            ApplicationStatus.CANCELLED,
        }
    ),
    ApplicationStatus.NEEDS_USER_ACTION: frozenset(
        {
            ApplicationStatus.ANALYZING,
            ApplicationStatus.SUBMITTED,
            ApplicationStatus.CANCELLED,
        }
    ),
    # A cancelled draft can be restarted by preparing it again.
    ApplicationStatus.CANCELLED: frozenset({ApplicationStatus.ANALYZING}),
}

#: States whose answers and resume may still be edited.
EDITABLE_STATUSES: frozenset[ApplicationStatus] = frozenset(
    {
        ApplicationStatus.AWAITING_USER_INFORMATION,
        ApplicationStatus.READY_FOR_REVIEW,
        ApplicationStatus.AWAITING_APPROVAL,
        ApplicationStatus.FAILED,
    }
)

#: States where completing questions may move the application forward.
PROGRESSABLE_STATUSES: frozenset[ApplicationStatus] = frozenset(
    {
        ApplicationStatus.ANALYZING,
        ApplicationStatus.AWAITING_USER_INFORMATION,
        ApplicationStatus.READY_FOR_REVIEW,
        ApplicationStatus.AWAITING_APPROVAL,
        ApplicationStatus.FAILED,
    }
)

#: States that must not be re-analysed: the employer may already have the form.
UNREANALYZABLE_STATUSES: frozenset[ApplicationStatus] = frozenset(
    {
        ApplicationStatus.SUBMITTING,
        ApplicationStatus.SUBMITTED,
        ApplicationStatus.NEEDS_USER_ACTION,
    }
)


def can_transition(current: ApplicationStatus, target: ApplicationStatus) -> bool:
    """Whether ``current -> target`` is a declared move."""
    if current is target:
        return True
    return target in ALLOWED_TRANSITIONS[current]


def transition(
    application: Application,
    target: ApplicationStatus,
    *,
    code: str | None = None,
    message: str | None = None,
) -> None:
    """Move ``application`` to ``target`` or raise :class:`ApplicationStateError`.

    The caller is responsible for committing; the move itself stays in memory so
    it can be committed together with the change that justified it.
    """
    current = application.status
    if current is target:
        return
    if not can_transition(current, target):
        raise ApplicationStateError(
            message
            or (
                f"An application cannot move from {current.value!r} to "
                f"{target.value!r}."
            ),
            code=code,
        )
    application.status = target


__all__ = [
    "ALLOWED_TRANSITIONS",
    "EDITABLE_STATUSES",
    "PROGRESSABLE_STATUSES",
    "UNREANALYZABLE_STATUSES",
    "ApplicationStateError",
    "can_transition",
    "transition",
]
