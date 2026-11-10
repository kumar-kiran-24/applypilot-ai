"""Minimal in-process Prometheus metrics.

The deployment runs exactly one API worker, so a library such as
``prometheus_client`` (with its multiprocess mode) would add a dependency
without adding value. This registry exposes the handful of signals the Stage H
runbook alerts on:

* HTTP request count and latency by route/status;
* 5xx errors by class;
* live browser sessions, opens/closes and launch failures;
* rate-limit rejections and token revocations;
* build/environment info.

Cardinality is bounded on purpose: routes come from the matched route template
(not the raw path), unmatched requests collapse to ``unmatched``, and no query
string, user id, token or form value is ever recorded.

The endpoint is excluded from request logging and from the API rate limiter.
"""

from __future__ import annotations

import math
import threading
from collections import Counter
from dataclasses import dataclass, field

#: Prometheus histogram buckets for API latency, in seconds.
LATENCY_BUCKETS: tuple[float, ...] = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
)

_MAX_ROUTES = 200


@dataclass
class MetricsRegistry:
    """Thread-safe counters, gauges and one latency histogram."""

    _lock: threading.Lock = field(default_factory=threading.Lock)
    _requests: Counter[tuple[str, str, str]] = field(default_factory=Counter)
    _errors: Counter[str] = field(default_factory=Counter)
    _counters: Counter[tuple[str, tuple[tuple[str, str], ...]]] = field(
        default_factory=Counter
    )
    _latency_buckets: Counter[tuple[tuple[str, str], int]] = field(
        default_factory=Counter
    )
    _latency_sum: Counter[tuple[str, str]] = field(default_factory=Counter)
    _latency_count: Counter[tuple[str, str]] = field(default_factory=Counter)
    _gauges: dict[str, float] = field(default_factory=dict)

    # --- Recording ---------------------------------------------------------
    def record_request(
        self, *, method: str, route: str, status_code: int, duration_seconds: float
    ) -> None:
        route = route if len(route) <= 200 else "unmatched"
        counter_key = (method.upper(), route, str(status_code))
        latency_key = (method.upper(), route)
        duration = max(0.0, float(duration_seconds))
        with self._lock:
            self._requests[counter_key] += 1
            self._latency_sum[latency_key] += duration
            self._latency_count[latency_key] += 1
            if status_code >= 500:
                self._errors[str(status_code)] += 1
            # Prometheus histograms are cumulative; store the observation in
            # the first bucket it fits so rendering can accumulate once.
            for index, boundary in enumerate(LATENCY_BUCKETS):
                if duration <= boundary:
                    self._latency_buckets[(latency_key, index)] += 1
                    break

    def record_counter(self, name: str, *, labels: dict[str, str] | None = None) -> None:
        key = (name, tuple(sorted((labels or {}).items())))
        with self._lock:
            self._counters[key] += 1

    def set_gauge(self, name: str, value: float) -> None:
        with self._lock:
            self._gauges[name] = float(value)

    # --- Read --------------------------------------------------------------
    def request_count(self, *, route: str | None = None) -> int:
        with self._lock:
            return sum(
                count
                for (_, current_route, _), count in self._requests.items()
                if route is None or current_route == route
            )

    def error_count(self) -> int:
        with self._lock:
            return sum(self._errors.values())

    def reset(self) -> None:
        with self._lock:
            self._requests.clear()
            self._errors.clear()
            self._counters.clear()
            self._latency_buckets.clear()
            self._latency_sum.clear()
            self._latency_count.clear()
            self._gauges.clear()

    def render(self, *, version: str, environment: str) -> str:
        """Render the Prometheus text exposition format."""
        lines: list[str] = []

        lines.append("# HELP applypilot_build_info Build and environment labels.")
        lines.append("# TYPE applypilot_build_info gauge")
        lines.append(
            f'applypilot_build_info{{version="{_escape(version)}",'
            f'environment="{_escape(environment)}"}} 1'
        )

        with self._lock:
            requests = sorted(self._requests.items())
            errors = sorted(self._errors.items())
            counters = sorted(self._counters.items())
            gauges = sorted(self._gauges.items())
            buckets = dict(self._latency_buckets)
            latency_sum = dict(self._latency_sum)
            latency_count = dict(self._latency_count)

        lines.append("# HELP applypilot_http_requests_total HTTP requests by route and status.")
        lines.append("# TYPE applypilot_http_requests_total counter")
        for (method, route, status_code), count in requests:
            lines.append(
                f'applypilot_http_requests_total{{method="{_escape(method)}",'
                f'route="{_escape(route)}",status="{_escape(status_code)}"}} {count}'
            )

        lines.append("# HELP applypilot_http_errors_total 5xx responses by status code.")
        lines.append("# TYPE applypilot_http_errors_total counter")
        for status_code, count in errors:
            lines.append(
                f'applypilot_http_errors_total{{status="{_escape(status_code)}"}} {count}'
            )

        lines.append(
            "# HELP applypilot_http_request_duration_seconds API request latency."
        )
        lines.append("# TYPE applypilot_http_request_duration_seconds histogram")
        for (method, route) in sorted(latency_count):
            cumulative = 0
            for index, boundary in enumerate(LATENCY_BUCKETS):
                cumulative += buckets.get(((method, route), index), 0)
                lines.append(
                    "applypilot_http_request_duration_seconds_bucket"
                    f'{{method="{_escape(method)}",route="{_escape(route)}",'
                    f'le="{boundary}"}} {cumulative}'
                )
            lines.append(
                "applypilot_http_request_duration_seconds_bucket"
                f'{{method="{_escape(method)}",route="{_escape(route)}",le="+Inf"}} '
                f"{latency_count[(method, route)]}"
            )
            lines.append(
                "applypilot_http_request_duration_seconds_sum"
                f'{{method="{_escape(method)}",route="{_escape(route)}"}} '
                f"{latency_sum[(method, route)]:.6f}"
            )
            lines.append(
                "applypilot_http_request_duration_seconds_count"
                f'{{method="{_escape(method)}",route="{_escape(route)}"}} '
                f"{latency_count[(method, route)]}"
            )

        names = sorted({key[0] for key, _ in counters})
        for name in names:
            lines.append(f"# TYPE {name} counter")
            for (counter_name, labels), count in counters:
                if counter_name != name:
                    continue
                rendered = ",".join(
                    f'{key}="{_escape(value)}"' for key, value in labels
                )
                suffix = f"{{{rendered}}}" if rendered else ""
                lines.append(f"{name}{suffix} {count}")

        for name, value in gauges:
            rendered = _format_float(value)
            lines.append(f"# TYPE {name} gauge")
            lines.append(f"{name} {rendered}")

        return "\n".join(lines) + "\n"


def _escape(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "")


def _format_float(value: float) -> str:
    if math.isinf(value) or math.isnan(value):
        return "0"
    return str(int(value)) if float(value).is_integer() else f"{value:.6f}"


_registry = MetricsRegistry()


def get_metrics() -> MetricsRegistry:
    """Return the process-wide registry."""
    return _registry


def reset_metrics() -> None:
    """Clear every metric (test isolation)."""
    _registry.reset()


def record_http_request(
    *, method: str, route: str, status_code: int, duration_seconds: float
) -> None:
    _registry.record_request(
        method=method,
        route=route,
        status_code=status_code,
        duration_seconds=duration_seconds,
    )


def record_counter(name: str, **labels: str) -> None:
    _registry.record_counter(name, labels=labels)


def set_gauge(name: str, value: float) -> None:
    _registry.set_gauge(name, value)


def render_metrics(*, version: str, environment: str) -> str:
    return _registry.render(version=version, environment=environment)


__all__ = [
    "LATENCY_BUCKETS",
    "MetricsRegistry",
    "get_metrics",
    "record_counter",
    "record_http_request",
    "render_metrics",
    "reset_metrics",
    "set_gauge",
]
