"""Legal transitions for the Phase 2E browser execution state machine.

Keeping the transitions in one place means a stage cannot silently move a
session from ``FAILED`` back to ``FILLING_FIELDS``, or mark an application
submitted because every field happened to be filled. Terminal states stay
terminal; reopening a session always starts a new, explicitly recorded run.
"""

from __future__ import annotations

from app.core.exceptions import InvalidOperationError
from app.models.browser_session import BrowserSession
from app.models.browser_session import BrowserSessionState as State

#: States in which the browser may still be running or resumable.
ACTIVE_STATES: frozenset[State] = frozenset(
    {
        State.CREATED,
        State.BROWSER_OPENING,
        State.BROWSER_READY,
        State.ANALYZING_FORM,
        State.FILLING_FIELDS,
        State.NEEDS_USER_INPUT,
        State.NEEDS_CAPTCHA,
        State.NEEDS_VERIFICATION,
        State.NEEDS_LEGAL_CONFIRMATION,
        State.READY_FOR_REVIEW,
        State.USER_REVIEWED,
        State.PAUSED,
    }
)

#: States from which a fresh run is allowed to replace the previous one.
RESUMABLE_STATES: frozenset[State] = frozenset(
    {State.PAUSED, State.FAILED, State.CANCELLED, State.COMPLETED_BY_USER}
)

#: Nothing may happen after these without an explicit new run.
TERMINAL_STATES: frozenset[State] = frozenset(
    {State.CANCELLED, State.COMPLETED_BY_USER}
)

#: Automation may not be started twice in a row for the same application.
IN_PROGRESS_STATES: frozenset[State] = ACTIVE_STATES - {State.PAUSED}

_TRANSITIONS: dict[State, frozenset[State]] = {
    State.CREATED: frozenset(
        {State.BROWSER_OPENING, State.PAUSED, State.FAILED, State.CANCELLED}
    ),
    State.BROWSER_OPENING: frozenset(
        {State.BROWSER_READY, State.ANALYZING_FORM, State.PAUSED, State.FAILED, State.CANCELLED}
    ),
    State.BROWSER_READY: frozenset(
        {
            State.ANALYZING_FORM,
            State.FILLING_FIELDS,
            State.NEEDS_USER_INPUT,
            State.NEEDS_CAPTCHA,
            State.NEEDS_VERIFICATION,
            State.NEEDS_LEGAL_CONFIRMATION,
            State.READY_FOR_REVIEW,
            State.PAUSED,
            State.FAILED,
            State.CANCELLED,
        }
    ),
    State.ANALYZING_FORM: frozenset(
        {
            State.FILLING_FIELDS,
            State.NEEDS_USER_INPUT,
            State.NEEDS_CAPTCHA,
            State.NEEDS_VERIFICATION,
            State.NEEDS_LEGAL_CONFIRMATION,
            State.READY_FOR_REVIEW,
            State.PAUSED,
            State.FAILED,
            State.CANCELLED,
        }
    ),
    State.FILLING_FIELDS: frozenset(
        {
            State.NEEDS_USER_INPUT,
            State.NEEDS_CAPTCHA,
            State.NEEDS_VERIFICATION,
            State.NEEDS_LEGAL_CONFIRMATION,
            State.READY_FOR_REVIEW,
            State.PAUSED,
            State.FAILED,
            State.CANCELLED,
        }
    ),
    State.NEEDS_USER_INPUT: frozenset(
        {State.FILLING_FIELDS, State.PAUSED, State.FAILED, State.CANCELLED}
    ),
    State.NEEDS_CAPTCHA: frozenset(
        {State.FILLING_FIELDS, State.PAUSED, State.FAILED, State.CANCELLED}
    ),
    State.NEEDS_VERIFICATION: frozenset(
        {State.FILLING_FIELDS, State.PAUSED, State.FAILED, State.CANCELLED}
    ),
    State.NEEDS_LEGAL_CONFIRMATION: frozenset(
        {State.FILLING_FIELDS, State.READY_FOR_REVIEW, State.PAUSED, State.FAILED, State.CANCELLED}
    ),
    State.READY_FOR_REVIEW: frozenset(
        {State.USER_REVIEWED, State.COMPLETED_BY_USER, State.PAUSED, State.FAILED, State.CANCELLED}
    ),
    State.USER_REVIEWED: frozenset(
        {State.COMPLETED_BY_USER, State.READY_FOR_REVIEW, State.PAUSED, State.CANCELLED}
    ),
    # PAUSED may resume into any non-terminal state it was paused from.
    State.PAUSED: frozenset((ACTIVE_STATES | {State.FAILED, State.CANCELLED}) - {State.PAUSED}),
    State.FAILED: frozenset({State.BROWSER_OPENING, State.CANCELLED}),
    State.COMPLETED_BY_USER: frozenset(),
    State.CANCELLED: frozenset(),
}


class BrowserSessionStateError(InvalidOperationError):
    """The execution state does not permit the requested operation."""

    code = "invalid_browser_session_state"
    message = "The browser session is not in a state that allows this action."


def can_transition(current: State, target: State) -> bool:
    """Return True when ``current -> target`` is a legal move."""
    if current is target:
        return False
    return target in _TRANSITIONS.get(current, frozenset())


def transition(session: BrowserSession, target: State) -> BrowserSession:
    """Apply a legal transition or raise :class:`BrowserSessionStateError`."""
    current = session.state
    if current is target:
        return session
    if not can_transition(current, target):
        raise BrowserSessionStateError(
            f"A browser session cannot move from {current.value} to {target.value}."
        )
    session.state = target
    return session


def is_terminal(state: State) -> bool:
    return state in TERMINAL_STATES


__all__ = [
    "ACTIVE_STATES",
    "IN_PROGRESS_STATES",
    "RESUMABLE_STATES",
    "TERMINAL_STATES",
    "BrowserSessionStateError",
    "can_transition",
    "is_terminal",
    "transition",
]
