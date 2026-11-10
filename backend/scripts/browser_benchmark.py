"""Measure real Chromium launch cost and memory for capacity planning.

Runs the production ``PlaywrightPersistentBrowserDriver`` against a local page
(N launches, sequentially) and reports launch latency plus the RSS of the
Playwright/Chromium process tree. This is the number to divide host RAM by when
setting ``APPLICATION_MAX_ACTIVE_BROWSER_SESSIONS`` and systemd ``MemoryMax``.

Usage::

    python scripts/browser_benchmark.py --launches 3 --output var/browser-bench.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

# Allow `python scripts/browser_benchmark.py` from the backend directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PAGE = b"""<!doctype html><html><body><h1>Benchmark</h1>
<form><input name="full_name"><input name="email"><textarea name="motivation"></textarea></form>
</body></html>"""


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(PAGE)))
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, format: str, *args: object) -> None:
        return


def _tree_rss_mb() -> float | None:
    try:
        import psutil

        process = psutil.Process()
        total = process.memory_info().rss
        for child in process.children(recursive=True):
            try:
                total += child.memory_info().rss
            except Exception:
                continue
        return round(total / (1024 * 1024), 1)
    except Exception:
        return None


async def _run(launches: int) -> dict[str, Any]:
    import os

    os.environ.setdefault("APPLICATION_AUTOMATION_ENABLED", "true")
    os.environ.setdefault("APPLICATION_BROWSER_HEADLESS", "true")

    from app.core.config import settings
    from app.integrations.browser.persistent import PlaywrightPersistentBrowserDriver
    from app.services import application_url_security as url_security
    from app.services.application_url_security import (
        ApprovedOrigin,
        NavigationPolicy,
    )

    # Benchmark-only: the page is a local loopback fixture, which production
    # URL validation deliberately rejects. The navigation policy still enforces
    # the pinned loopback origin below.
    url_security.normalize_application_url = lambda value: value

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}/apply"
    # The local benchmark page needs a policy that pins the loopback origin on
    # its ephemeral port; production builds this from the employer URL.
    policy = NavigationPolicy(
        start_origin=ApprovedOrigin(scheme="http", host="127.0.0.1", port=port)
    )

    driver = PlaywrightPersistentBrowserDriver(settings)
    baseline = _tree_rss_mb()
    launch_times: list[float] = []
    samples: list[dict[str, Any]] = []
    try:
        for _ in range(launches):
            started = time.perf_counter()
            handle = await driver.open(
                url, policy=policy, user_id="benchmark", pinned_address=None
            )
            elapsed = (time.perf_counter() - started) * 1000
            launch_times.append(elapsed)
            samples.append({"launch_ms": round(elapsed, 1), "tree_rss_mb": _tree_rss_mb()})
            await handle.close()
    finally:
        server.shutdown()
        server.server_close()

    return {
        "launches": launches,
        "launch_ms": {
            "mean": round(statistics.fmean(launch_times), 1),
            "min": round(min(launch_times), 1),
            "max": round(max(launch_times), 1),
        },
        "baseline_tree_rss_mb": baseline,
        "samples": samples,
        "note": "Headless Chromium on this host; page and locale fixed.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--launches", type=int, default=3)
    parser.add_argument("--output", default="var/browser-benchmark.json")
    args = parser.parse_args()

    report = asyncio.run(_run(args.launches))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
