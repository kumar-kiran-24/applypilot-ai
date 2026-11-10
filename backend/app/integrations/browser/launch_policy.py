"""Chromium launch arguments and DNS pinning.

The pre-launch validation in :mod:`app.services.application_url_security`
resolves the employer hostname and rejects private answers. Chromium then
resolves the same name again, which leaves a DNS-rebinding window: an attacker
who controls the name can answer with a public address during validation and a
private one milliseconds later.

Pinning closes that window for the session's starting host: the validated
address is injected into Chromium with ``--host-resolver-rules``, so the
browser never performs a second lookup for that host. Redirect hosts allowed by
the navigation policy are still resolved normally, which is why the deployment
also applies an egress firewall that only permits web ports to the public
internet (see ``deploy/``).

This module is pure (no Playwright import) so the argument list is unit-testable
without a browser.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from app.core.config import Settings

#: Fixed hardening flags. None of these relax web security.
_BASE_ARGS: tuple[str, ...] = (
    "--disable-dev-shm-usage",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-default-apps",
    "--disable-sync",
    "--disable-features=Translate,MediaRouter,OptimizationHints",
    "--dns-prefetch-disable",
    "--mute-audio",
    "--no-default-browser-check",
    "--no-first-run",
    "--metrics-recording-only",
)


def preferred_address(addresses: tuple[str, ...] | list[str]) -> str | None:
    """Pick one validated address, preferring IPv4 for broad compatibility."""
    parsed: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for raw in addresses:
        try:
            parsed.append(ipaddress.ip_address(str(raw).split("%", 1)[0]))
        except ValueError:
            continue
    for address in parsed:
        if isinstance(address, ipaddress.IPv4Address):
            return str(address)
    return str(parsed[0]) if parsed else None


def host_resolver_rule(url: str, address: str | None) -> str | None:
    """Return ``MAP host address`` for Chromium, or None when not applicable.

    IP-literal URLs need no mapping (Chromium connects to the literal directly),
    which also keeps the local-browser test fixtures working unchanged.
    """
    if not address:
        return None
    try:
        parsed_address = ipaddress.ip_address(str(address).split("%", 1)[0])
    except ValueError:
        return None
    try:
        host = (urlsplit(url).hostname or "").strip().lower()
    except ValueError:
        return None
    if not host:
        return None
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        return None
    rendered = f"[{parsed_address}]" if parsed_address.version == 6 else str(parsed_address)
    return f"MAP {host} {rendered}"


def chromium_launch_args(
    settings: Settings,
    *,
    url: str | None = None,
    pinned_address: str | None = None,
) -> list[str]:
    """Build the full launch argument list for one browser session."""
    args = list(_BASE_ARGS)
    args.append(
        f"--js-flags=--max-old-space-size={int(settings.APPLICATION_BROWSER_JS_HEAP_MB)}"
    )
    # No --renderer-process-limit/--process-per-site: collapsing renderers would
    # weaken Chromium's site isolation more than it saves memory. Memory is
    # bounded by the heap flag above and systemd's MemoryMax on the host.
    rule = (
        host_resolver_rule(url, pinned_address)
        if url and settings.APPLICATION_BROWSER_PIN_DNS
        else None
    )
    if rule:
        args.append(f"--host-resolver-rules={rule}")
    return args


__all__ = [
    "chromium_launch_args",
    "host_resolver_rule",
    "preferred_address",
]
