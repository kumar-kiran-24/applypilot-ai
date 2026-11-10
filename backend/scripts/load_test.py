"""In-process API load and reliability probe.

This drives the real FastAPI app through an ASGI transport with a temporary
SQLite database, so it measures application/database work without needing a
production server or touching real data. It does **not** measure network, Nginx
or TLS overhead; use it to compare builds and to find concurrency regressions,
not to claim internet-scale capacity.

Usage::

    python scripts/load_test.py --users 20 --cycles 5 --output var/load.json

Results include request percentiles, error rate, throughput, process RSS/CPU
and the readiness payload's active browser count.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

# Allow `python scripts/load_test.py` from the backend directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _prepare_environment(storage: str) -> None:
    """Set the env before importing settings (which are cached at import)."""
    os.environ.setdefault("ENVIRONMENT", "development")
    os.environ.setdefault("OAUTH_SESSION_SECRET", "load-test-" + "x" * 40)
    os.environ.setdefault("RESUME_STORAGE_DIR", storage)
    os.environ.setdefault("APPLICATION_SCREENSHOT_DIR", storage)
    # The probe is measuring the app, not the abuse limiter.
    os.environ.setdefault("API_RATE_LIMIT_PER_MINUTE", "10000")
    os.environ.setdefault("API_RATE_LIMIT_ENABLED", "false")


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return ordered[index]


async def _run(args: argparse.Namespace) -> int:
    storage = tempfile.mkdtemp(prefix="applypilot-load-")
    _prepare_environment(storage)

    import app.models
    from app.core.security import create_access_token
    from app.db.base import Base
    from app.db.session import get_db
    from app.main import app
    from app.models.user import User
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    db_path = Path(tempfile.mkdtemp(prefix="applypilot-load-db-")) / "load.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    users: list[User] = []
    async with session_factory() as session:
        for index in range(args.users):
            user = User(
                google_sub=f"load-{uuid.uuid4().hex}",
                email=f"load-{index}-{uuid.uuid4().hex[:6]}@example.com",
                email_verified=True,
                full_name=f"Load User {index}",
                is_active=True,
            )
            session.add(user)
            users.append(user)
        await session.commit()
        tokens = [create_access_token(user.id) for user in users]

    async def override_get_db():  # type: ignore[no-untyped-def]
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app, raise_app_exceptions=False)

    endpoints = (
        ("/api/v1/jobs?limit=20", "jobs"),
        ("/api/v1/applications", "applications"),
        ("/api/v1/applications/platforms", "platforms"),
        ("/api/v1/auth/me", "me"),
    )

    latencies: list[float] = []
    per_endpoint: dict[str, list[float]] = {name: [] for _, name in endpoints}
    statuses: dict[int, int] = {}
    failures = 0

    async with AsyncClient(transport=transport, base_url="http://load.local") as client:

        async def user_journey(token: str) -> None:
            nonlocal failures
            headers = {"Authorization": f"Bearer {token}"}
            for _ in range(args.cycles):
                for path, name in endpoints:
                    started = time.perf_counter()
                    response = await client.get(path, headers=headers)
                    elapsed = (time.perf_counter() - started) * 1000
                    latencies.append(elapsed)
                    per_endpoint[name].append(elapsed)
                    statuses[response.status_code] = (
                        statuses.get(response.status_code, 0) + 1
                    )
                    if response.status_code != 200:
                        failures += 1
                    if args.delay:
                        await asyncio.sleep(args.delay)

        started_at = time.perf_counter()
        await asyncio.gather(*(user_journey(token) for token in tokens))
        elapsed = time.perf_counter() - started_at

        ready = await client.get("/api/v1/ready")

    process_metrics: dict[str, Any] = {}
    try:
        import psutil

        process = psutil.Process()
        process_metrics = {
            "rss_mb": round(process.memory_info().rss / (1024 * 1024), 1),
            "cpu_percent": process.cpu_percent(interval=None),
            "threads": process.num_threads(),
        }
    except Exception:
        process_metrics = {"note": "psutil not available"}

    total = len(latencies)
    report = {
        "users": args.users,
        "cycles": args.cycles,
        "total_requests": total,
        "duration_seconds": round(elapsed, 3),
        "throughput_rps": round(total / elapsed, 1) if elapsed else 0,
        "failures": failures,
        "error_rate": round(failures / total, 4) if total else 0,
        "latency_ms": {
            "mean": round(statistics.fmean(latencies), 2) if latencies else 0,
            "p50": round(_percentile(latencies, 0.50), 2),
            "p95": round(_percentile(latencies, 0.95), 2),
            "p99": round(_percentile(latencies, 0.99), 2),
            "max": round(max(latencies), 2) if latencies else 0,
        },
        "per_endpoint_p95_ms": {
            name: round(_percentile(values, 0.95), 2)
            for name, values in per_endpoint.items()
        },
        "statuses": {str(code): count for code, count in sorted(statuses.items())},
        "readiness_status": ready.status_code,
        "ready_active_browser_sessions": (
            ready.json().get("active_browser_sessions") if ready.status_code == 200 else None
        ),
        "process": process_metrics,
        "note": (
            "In-process ASGI measurement with SQLite: no network, Nginx or TLS "
            "overhead. Not a horizontal-scaling claim."
        ),
    }

    app.dependency_overrides.clear()
    await engine.dispose()

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(
        output.write_text, json.dumps(report, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, indent=2))
    return 0 if failures == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--users", type=int, default=10, help="Concurrent users")
    parser.add_argument("--cycles", type=int, default=3, help="Journeys per user")
    parser.add_argument(
        "--delay",
        type=float,
        default=0.0,
        help="Seconds between requests inside one journey",
    )
    parser.add_argument(
        "--output", default="var/load-test-report.json", help="JSON report path"
    )
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
