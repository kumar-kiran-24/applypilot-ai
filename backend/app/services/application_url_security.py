"""Navigation policy for the Phase 2E browser session.

The browser agent visits URLs supplied by a discovered job posting. That makes
every navigation an SSRF boundary, so a URL is validated twice:

1. **Statically** - only absolute http(s) URLs on standard ports, without
   credentials, loopback/private/link-local/reserved IP literals or internal
   hostnames are accepted (``normalize_application_url``).
2. **Dynamically** - the hostname is resolved before the browser opens, and any
   address that is not globally routable rejects the URL. This closes the
   "public name, private address" DNS trick.

Redirects and popups are not trusted either: a :class:`NavigationPolicy` pins
the session to the employer's origin plus the platform's own domains (and any
explicit operator allow-list). Every navigation request is checked against it
again, so a redirect cannot escape the approved destinations.

No function here makes an application request; resolution is the only network
lookup and it carries no cookies or headers.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from app.services.application_platforms import (
    ApplicationPlatform,
    ApplicationUrlError,
    normalize_application_url,
)
from app.services.indeed_smartapply import AUTHENTICATION_HOSTS

if TYPE_CHECKING:
    from app.services.application_platforms import PlatformDetection

#: Hosts a platform owns. Redirects inside these domains stay allowed for the
#: session that started on the same platform.
PLATFORM_NAVIGATION_DOMAINS: dict[ApplicationPlatform, tuple[str, ...]] = {
    ApplicationPlatform.GREENHOUSE: ("greenhouse.io",),
    ApplicationPlatform.LEVER: ("lever.co",),
    ApplicationPlatform.ASHBY: ("ashbyhq.com",),
    ApplicationPlatform.SMARTRECRUITERS: ("smartrecruiters.com",),
    ApplicationPlatform.INDEED_SMARTAPPLY: ("smartapply.indeed.com",),
    ApplicationPlatform.WORKDAY: ("myworkdayjobs.com", "workday.com"),
    ApplicationPlatform.ICIMS: ("icims.com",),
}

#: Application destinations a job-listing session may reach after the user
#: starts Easy Apply. Only domains owned by recognised application platforms
#: are listed: an unknown employer host is still refused, so a redirect cannot
#: escape to an arbitrary site. Company-hosted links stay out deliberately.
TRUSTED_APPLICATION_DOMAINS: tuple[str, ...] = tuple(
    dict.fromkeys(
        domain
        for platform, domains in PLATFORM_NAVIGATION_DOMAINS.items()
        if platform not in {ApplicationPlatform.WORKDAY, ApplicationPlatform.ICIMS}
        for domain in domains
    )
)

#: Exact Indeed auth hosts. They may be reached so the user can sign in, but the
#: redirect service classifies them as an authentication step, never as the
#: validated application form. No credentials are read or typed.
SMARTAPPLY_AUTH_DOMAINS: tuple[str, ...] = tuple(sorted(AUTHENTICATION_HOSTS))

#: A listing hand-off may reach recognised forms and Indeed's own auth hosts.
LISTING_REDIRECT_DOMAINS: tuple[str, ...] = tuple(
    dict.fromkeys((*TRUSTED_APPLICATION_DOMAINS, *SMARTAPPLY_AUTH_DOMAINS))
)

DEFAULT_HTTPS_PORT = 443
DEFAULT_HTTP_PORT = 80
SUPPORTED_PORTS = {DEFAULT_HTTP_PORT, DEFAULT_HTTPS_PORT}


class UnsafeNavigationError(ApplicationUrlError):
    """The destination is outside the approved, public navigation boundary."""

    status_code = 400
    code = "unsafe_navigation"
    message = "The application URL points somewhere the agent may not navigate."


HostResolver = Callable[[str, int], Awaitable[list[str]]]


@dataclass(frozen=True)
class ApprovedOrigin:
    """One exact scheme/host/port triple the browser may navigate to."""

    scheme: str
    host: str
    port: int

    @property
    def origin(self) -> str:
        return f"{self.scheme}://{self.host}:{self.port}"

    def matches(self, url: str) -> bool:
        parts = urlsplit(url)
        if parts.scheme.lower() != self.scheme:
            return False
        if (parts.hostname or "").lower() != self.host:
            return False
        return _effective_port(parts) == self.port


@dataclass(frozen=True)
class NavigationPolicy:
    """The only destinations a single browser session may reach.

    ``allowed_domains`` are platform-owned suffixes. ``allowed_origins`` are
    exact origins from the starting URL or operator configuration.
    """

    start_origin: ApprovedOrigin
    allowed_domains: tuple[str, ...] = ()
    extra_origins: tuple[ApprovedOrigin, ...] = field(default_factory=tuple)
    #: A listing hand-off may legitimately leave the listing host. Blocked
    #: destinations are then reported as an unsupported application platform
    #: instead of being treated as a compromised browser session.
    redirect_flow: bool = False

    @property
    def allowed_origins(self) -> tuple[str, ...]:
        origins = {self.start_origin.origin}
        origins.update(origin.origin for origin in self.extra_origins)
        return tuple(sorted(origins))

    @property
    def allowed_hosts(self) -> tuple[str, ...]:
        hosts = {self.start_origin.host}
        hosts.update(origin.host for origin in self.extra_origins)
        return tuple(sorted(hosts))

    def allows(self, url: str) -> bool:
        """Return True when ``url`` may be navigated to in this session."""
        try:
            normalized = normalize_application_url(url)
        except ApplicationUrlError:
            return False
        parts = urlsplit(normalized)
        host = (parts.hostname or "").lower()
        if not host:
            return False
        if parts.scheme.lower() != self.start_origin.scheme:
            return False
        if _effective_port(parts) != self.start_origin.port:
            return False
        if any(origin.matches(normalized) for origin in self.extra_origins):
            return True
        # The starting host and its subdomains.
        if host == self.start_origin.host or host.endswith(f".{self.start_origin.host}"):
            return True
        # Domains owned by the detected platform.
        return any(
            host == domain or host.endswith(f".{domain}")
            for domain in self.allowed_domains
        )

    def check(self, url: str) -> str:
        """Normalise ``url`` or raise :class:`UnsafeNavigationError`."""
        try:
            normalized = normalize_application_url(url)
        except ApplicationUrlError as exc:
            raise UnsafeNavigationError(
                "The agent may only open a public http(s) application page.",
                code="unsafe_navigation",
            ) from exc
        if not self.allows(normalized):
            raise UnsafeNavigationError(
                "The page tried to navigate outside the approved employer site.",
                code="navigation_blocked",
            )
        return normalized


def _effective_port(parts: object) -> int:
    """Port explicit in the URL, or the scheme default."""
    port = getattr(parts, "port", None)
    if port is not None:
        return int(port)
    scheme = getattr(parts, "scheme", "").lower()
    return DEFAULT_HTTPS_PORT if scheme == "https" else DEFAULT_HTTP_PORT


def _origin(value: str) -> ApprovedOrigin:
    try:
        normalized = normalize_application_url(value)
    except ApplicationUrlError as exc:
        raise UnsafeNavigationError(
            "An approved redirect origin is not a public http(s) origin.",
            code="invalid_redirect_origin",
        ) from exc
    parts = urlsplit(normalized)
    return ApprovedOrigin(
        scheme=parts.scheme.lower(),
        host=(parts.hostname or "").lower(),
        port=_effective_port(parts),
    )


def build_navigation_policy(
    application_url: str,
    *,
    platform: ApplicationPlatform | None = None,
    extra_origins: tuple[str, ...] = (),
    redirect_flow: bool = False,
) -> NavigationPolicy:
    """Pin one session to the employer origin and its platform's domains."""
    start = _origin(application_url)
    domains = PLATFORM_NAVIGATION_DOMAINS.get(platform, ()) if platform else ()
    if platform is ApplicationPlatform.INDEED_SMARTAPPLY:
        domains = (*domains, *SMARTAPPLY_AUTH_DOMAINS)
    extras = tuple(_origin(value) for value in extra_origins)
    return NavigationPolicy(
        start_origin=start,
        allowed_domains=domains,
        extra_origins=extras,
        redirect_flow=redirect_flow,
    )


def build_listing_redirect_policy(
    listing_url: str, *, extra_origins: tuple[str, ...] = ()
) -> NavigationPolicy:
    """Policy for a job listing whose Easy Apply button opens a form elsewhere.

    The listing host stays allowed, and only recognised application-platform
    domains are added. Every other destination is refused by the navigation
    guard, so the agent never silently follows an unrelated redirect.
    """
    start = _origin(listing_url)
    extras = tuple(_origin(value) for value in extra_origins)
    return NavigationPolicy(
        start_origin=start,
        allowed_domains=LISTING_REDIRECT_DOMAINS,
        extra_origins=extras,
        redirect_flow=True,
    )


def policy_for_detection(
    detection: PlatformDetection, *, extra_origins: tuple[str, ...] = ()
) -> NavigationPolicy:
    """Build the policy for a detected application link."""
    if detection.is_job_board_link:
        return build_listing_redirect_policy(
            detection.application_url, extra_origins=extra_origins
        )
    return build_navigation_policy(
        detection.application_url,
        platform=detection.platform,
        extra_origins=extra_origins,
    )


def sanitize_url_for_display(url: str) -> str:
    """Persist only scheme://host/path.

    Query strings and fragments can carry single-use tokens, so they are never
    written to the database or returned in a session payload.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        return ""
    host = parts.hostname.lower()
    try:
        port = parts.port
    except ValueError:
        return ""
    netloc = host if port in (None, DEFAULT_HTTPS_PORT, DEFAULT_HTTP_PORT) else f"{host}:{port}"
    return f"{parts.scheme.lower()}://{netloc}{parts.path or '/'}"


def _is_public_address(raw: str) -> bool:
    """Reject anything that is not a globally routable unicast address."""
    address_text = str(raw).split("%", 1)[0]
    try:
        address = ipaddress.ip_address(address_text)
    except ValueError:
        return False
    return bool(address.is_global) and not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


async def _resolve_host(host: str, port: int) -> list[str]:
    """Resolve ``host`` without sending any application data."""
    loop = asyncio.get_running_loop()

    def resolve() -> list[str]:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        return sorted({info[4][0] for info in infos})

    return await loop.run_in_executor(None, resolve)


async def validate_application_destination(
    url: str, *, resolver: HostResolver | None = None
) -> tuple[str, tuple[str, ...]]:
    """Validate a URL statically and confirm it resolves to public addresses.

    Returns the normalised URL and every validated address. Callers that launch
    Chromium pin one of these addresses into the browser's resolver, so the
    name cannot be re-resolved to a private address after validation.

    Raises:
        UnsafeNavigationError: when the name cannot be resolved or any resolved
            address is loopback, private, link-local or otherwise non-public.
    """
    try:
        normalized = normalize_application_url(url)
    except ApplicationUrlError as exc:
        raise UnsafeNavigationError(
            "The agent may only open a public http(s) application page.",
            code="unsafe_navigation",
        ) from exc

    parts = urlsplit(normalized)
    host = (parts.hostname or "").lower()
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        # An IP literal was already validated by normalize_application_url.
        return normalized, (host,)

    resolver = resolver or _resolve_host
    try:
        addresses = await resolver(host, _effective_port(parts))
    except OSError as exc:
        raise UnsafeNavigationError(
            "The employer's application page could not be resolved.",
            code="application_host_unresolvable",
        ) from exc
    if not addresses:
        raise UnsafeNavigationError(
            "The employer's application page could not be resolved.",
            code="application_host_unresolvable",
        )
    if any(not _is_public_address(address) for address in addresses):
        raise UnsafeNavigationError(
            "The application host resolved to a private or internal address.",
            code="unsafe_application_host",
        )
    return normalized, tuple(dict.fromkeys(addresses))


async def ensure_public_destination(
    url: str, *, resolver: HostResolver | None = None
) -> str:
    """Validate that a URL is a public destination; return it normalised.

    Kept for callers that only need the gate; new code should use
    :func:`validate_application_destination` when it also needs the address.
    """
    normalized, _ = await validate_application_destination(url, resolver=resolver)
    return normalized


__all__ = [
    "LISTING_REDIRECT_DOMAINS",
    "PLATFORM_NAVIGATION_DOMAINS",
    "SMARTAPPLY_AUTH_DOMAINS",
    "TRUSTED_APPLICATION_DOMAINS",
    "ApprovedOrigin",
    "HostResolver",
    "NavigationPolicy",
    "UnsafeNavigationError",
    "build_listing_redirect_policy",
    "build_navigation_policy",
    "ensure_public_destination",
    "policy_for_detection",
    "sanitize_url_for_display",
    "validate_application_destination",
]
