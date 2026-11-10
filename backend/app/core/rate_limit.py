"""Small in-process rate limiter for abuse-prone endpoints.

The API intentionally runs as one process today because live browser sessions
and view tickets are process-local (see
``backend/docs/phase2e-stage-g-production-hardening.md``). This limiter is
therefore a per-process safety net for endpoints that accept guessable values
(login codes, the OAuth start/callback) - not a distributed quota and not a
replacement for the reverse proxy's ``limit_req``.

Only a fixed scope plus the client address are used as the key. No user data,
token, ticket, code or request body is stored, and nothing here is logged.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from fastapi import Request

from app.core.config import settings
from app.core.exceptions import AppError
from app.core.metrics import record_counter
from app.core.security import TokenError, decode_access_token

#: Bound memory growth from many distinct client addresses on one process.
MAX_TRACKED_KEYS = 10_000


class RateLimitError(AppError):
    """Too many attempts for one scope and client address."""

    status_code = 429
    code = "rate_limited"
    message = "Too many requests. Wait a moment and try again."


@dataclass
class SlidingWindowRateLimiter:
    """Count attempts per key in a rolling window."""

    clock: Callable[[], float] = time.monotonic
    max_keys: int = MAX_TRACKED_KEYS
    _events: dict[str, deque[float]] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def hit(self, key: str, *, limit: int, window_seconds: int) -> int | None:
        """Record one attempt.

        Returns ``None`` when the attempt is allowed, otherwise the number of
        seconds until the oldest attempt leaves the window.
        """
        now = self.clock()
        async with self._lock:
            events = self._events.get(key)
            if events is None:
                if len(self._events) >= self.max_keys:
                    self._prune(now, window_seconds)
                events = self._events[key] = deque()
            while events and now - events[0] >= window_seconds:
                events.popleft()
            if len(events) >= limit:
                return max(1, math.ceil(window_seconds - (now - events[0])))
            events.append(now)
            return None

    def reset(self) -> None:
        """Forget every key (tests and process restarts)."""
        self._events.clear()

    def _prune(self, now: float, window_seconds: int) -> None:
        stale = [
            key
            for key, events in self._events.items()
            if not events or now - events[-1] >= window_seconds
        ]
        for key in stale:
            self._events.pop(key, None)


_limiter = SlidingWindowRateLimiter()

# A separate budget for the bulk of the API, so exhausting the auth limiter
# never blocks normal read traffic (and vice versa).
_api_limiter = SlidingWindowRateLimiter()

#: Probe/observability routes must never consume a caller's budget.
_EXEMPT_SUFFIXES = ("/health", "/ready", "/metrics", "/openapi.json")


async def enforce_rate_limit(
    request: Request,
    *,
    scope: str,
    limit: int,
    window_seconds: int = 60,
) -> None:
    """Raise :class:`RateLimitError` when this client exceeded ``scope``.

    ``request.client.host`` is the peer address uvicorn sees. Behind Nginx the
    documented deployment starts uvicorn with ``--proxy-headers`` and a trusted
    loopback proxy, so this is the real client address, not the proxy's.
    """
    client = request.client.host if request.client else "unknown"
    retry_after = await _limiter.hit(
        f"{scope}:{client}", limit=limit, window_seconds=window_seconds
    )
    if retry_after is not None:
        raise RateLimitError(headers={"Retry-After": str(retry_after)})


def api_client_identity(request: Request) -> str:
    """Key by authenticated user when possible, otherwise by peer address.

    The JWT is only decoded (signature/expiry checked); no database lookup is
    made in the hot path of every request. An invalid token is treated as an
    anonymous caller and will be rejected by the auth dependency afterwards.
    """
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() == "bearer" and token.strip():
        try:
            claims = decode_access_token(token.strip())
        except TokenError:
            pass
        else:
            return f"user:{claims.get('sub')}"
    client = request.client.host if request.client else "unknown"
    return f"ip:{client}"


async def check_api_rate_limit(request: Request) -> int | None:
    """Return ``Retry-After`` seconds when the caller is over budget.

    Middleware cannot rely on FastAPI's exception handlers, so this returns a
    value instead of raising :class:`RateLimitError`.
    """
    if not settings.API_RATE_LIMIT_ENABLED or request.method == "OPTIONS":
        return None
    path = request.url.path
    if path.endswith(_EXEMPT_SUFFIXES):
        return None
    retry_after = await _api_limiter.hit(
        f"api:{api_client_identity(request)}",
        limit=settings.API_RATE_LIMIT_PER_MINUTE,
        window_seconds=60,
    )
    if retry_after is not None:
        record_counter("applypilot_rate_limited_total", scope="api")
    return retry_after


def reset_rate_limits() -> None:
    """Clear all in-process counters (test isolation)."""
    _limiter.reset()
    _api_limiter.reset()


__all__ = [
    "MAX_TRACKED_KEYS",
    "RateLimitError",
    "SlidingWindowRateLimiter",
    "api_client_identity",
    "check_api_rate_limit",
    "enforce_rate_limit",
    "reset_rate_limits",
]
