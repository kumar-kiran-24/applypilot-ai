"""Event-loop selection that keeps Playwright working on Windows.

Playwright launches Chromium over an asyncio subprocess pipe, which
``asyncio.create_subprocess_exec`` only implements for the Proactor loop on
Windows. A ``SelectorEventLoop`` raises ``NotImplementedError`` from
``asyncio.base_events._make_subprocess_transport`` instead, so every live
browser session fails with a 503.

Uvicorn 0.38 chooses ``SelectorEventLoop`` on Windows whenever ``reload`` or
``workers`` is set (``Config.use_subprocess``), because its reload supervisor is
subprocess-based. That is exactly what ``uvicorn app.main:app --reload`` does,
so the documented development command silently disables browser automation.

The fix is to select the loop *before* the application loop starts, by running
Uvicorn through :mod:`scripts.run_server` with this factory. Linux is left alone:
there ``SelectorEventLoop`` is the normal loop and supports subprocesses.
"""

from __future__ import annotations

import asyncio
import sys

#: Import path accepted by ``uvicorn --loop`` / ``uvicorn.run(loop=...)``.
WINDOWS_LOOP_FACTORY_PATH = "app.core.event_loop:windows_proactor_loop_factory"


def windows_proactor_loop_factory() -> asyncio.AbstractEventLoop:
    """Create a loop Playwright can spawn a browser on.

    Uvicorn treats a custom ``loop`` import path as a zero-argument **loop
    factory** (``uvicorn.config.Config.get_loop_factory`` returns it unchanged
    and the runner calls it), so this returns a loop instance, not a callable.

    A Proactor loop is required on Windows both with and without ``--reload``;
    outside Windows the ordinary selector loop already supports subprocesses.
    """
    if sys.platform == "win32":
        return asyncio.ProactorEventLoop()
    return asyncio.SelectorEventLoop()


def loop_supports_subprocess(loop: asyncio.AbstractEventLoop) -> bool:
    """True when this loop can create the subprocess Playwright needs."""
    if sys.platform != "win32":
        # uvloop and the standard selector loops all support subprocesses
        # outside Windows.
        return True
    return not isinstance(loop, asyncio.SelectorEventLoop)


def configure_event_loop_policy() -> bool:
    """Install the Proactor policy on Windows. Returns True when applied.

    This must run before the application's event loop is created (and before
    ``app.main`` is imported by the server), otherwise Playwright still binds to
    the loop that is already running. It is a no-op outside Windows, so the
    Linux deployment keeps its existing policy.
    """
    if sys.platform != "win32":
        return False
    policy_factory = getattr(asyncio, "WindowsProactorEventLoopPolicy", None)
    if policy_factory is None:  # pragma: no cover - future Python versions
        # Python 3.14 deprecates loop policies; the loop factory passed to
        # Uvicorn is still what matters there.
        return False
    if not isinstance(asyncio.get_event_loop_policy(), policy_factory):
        asyncio.set_event_loop_policy(policy_factory())
    return True


__all__ = [
    "WINDOWS_LOOP_FACTORY_PATH",
    "configure_event_loop_policy",
    "loop_supports_subprocess",
    "windows_proactor_loop_factory",
]
