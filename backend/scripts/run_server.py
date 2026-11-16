"""Backend launcher that picks a Playwright-compatible event loop first.

Why this exists: on Windows, Uvicorn 0.38 selects ``SelectorEventLoop`` whenever
it runs with ``--reload`` or ``--workers`` (its reload supervisor is
subprocess-based). A selector loop cannot create the subprocess Playwright uses
to launch Chromium, so ``POST /api/v1/browser-sessions`` fails with
``NotImplementedError`` and a 503. Running ``uvicorn app.main:app`` directly
cannot avoid that, because the loop is chosen inside Uvicorn after this process
has started.

Run from ``backend/``::

    python -m scripts.run_server                 # 127.0.0.1:8000, no reload
    python -m scripts.run_server --reload        # reload with a Proactor loop
    python -m scripts.run_server --port 8080     # any other Uvicorn-ish option

The Proactor policy (Windows) or the untouched default (Linux) is installed
before ``app.main`` is imported and before any event loop is created. Linux keeps
Uvicorn's ``auto`` loop selection, which is already subprocess-capable.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from app.core.event_loop import WINDOWS_LOOP_FACTORY_PATH, configure_event_loop_policy


def build_parser() -> argparse.ArgumentParser:
    """Options that match the previously documented Uvicorn command."""
    parser = argparse.ArgumentParser(
        description="Start the ApplyPilot AI backend with a browser-safe event loop."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--reload",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Reload on code changes (supported with the Proactor loop on Windows).",
    )
    parser.add_argument("--log-level", default="info")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # Must happen before uvicorn creates the application loop.
    configure_event_loop_policy()
    loop = WINDOWS_LOOP_FACTORY_PATH if sys.platform == "win32" else "auto"

    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        loop=loop,
        log_level=args.log_level,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
