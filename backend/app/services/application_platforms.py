"""Application-platform detection.

Every supported platform is described declaratively (host patterns, whether it
supports automation, whether it normally requires an account). Detection never
fetches the URL: it only classifies the link the user supplied.

URL validation is deliberately strict because the optional browser automation
will visit the URL: only public http(s) hosts are accepted, credentials in the
URL are rejected and loopback/private/internal names are refused.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from enum import Enum
from urllib.parse import urlsplit, urlunsplit

from app.core.exceptions import InvalidOperationError
from app.services.indeed_smartapply import is_smartapply_url

MAX_APPLICATION_URL_LENGTH = 1000


class ApplicationPlatform(str, Enum):
    """Platforms with a dedicated adapter."""

    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    WORKDAY = "workday"
    ASHBY = "ashby"
    SMARTRECRUITERS = "smartrecruiters"
    INDEED_SMARTAPPLY = "indeed_smartapply"
    ICIMS = "icims"
    COMPANY_HOSTED = "company_hosted"


class DetectionConfidence(str, Enum):
    """How sure detection is about the platform."""

    HIGH = "high"
    MEDIUM = "medium"
    NONE = "none"


class ApplicationUrlError(InvalidOperationError):
    """The application URL is unsafe or malformed."""

    status_code = 400
    code = "invalid_application_url"
    message = "The application URL is not valid."


@dataclass(frozen=True)
class PlatformSpec:
    """Static description of one application platform."""

    platform: ApplicationPlatform
    display_name: str
    host_patterns: tuple[str, ...]
    path_pattern: str | None
    supports_automation: bool
    requires_account: bool | None
    documentation_url: str
    notes: tuple[str, ...] = field(default_factory=tuple)


PLATFORM_SPECS: tuple[PlatformSpec, ...] = (
    PlatformSpec(
        platform=ApplicationPlatform.GREENHOUSE,
        display_name="Greenhouse",
        host_patterns=(r"(^|\.)greenhouse\.io$",),
        path_pattern=None,
        supports_automation=True,
        requires_account=False,
        documentation_url="https://developers.greenhouse.io/job-board.html",
        notes=(
            "Greenhouse application forms are usually a single page and do not "
            "require an account unless the employer enables one.",
        ),
    ),
    PlatformSpec(
        platform=ApplicationPlatform.LEVER,
        display_name="Lever",
        host_patterns=(r"(^|\.)lever\.co$",),
        path_pattern=None,
        supports_automation=True,
        requires_account=False,
        documentation_url="https://help.lever.co/hc/en-us",
        notes=("Lever postings normally accept a resume and a short application form.",),
    ),
    PlatformSpec(
        platform=ApplicationPlatform.ASHBY,
        display_name="Ashby",
        host_patterns=(r"(^|\.)ashbyhq\.com$",),
        path_pattern=None,
        supports_automation=True,
        requires_account=False,
        documentation_url="https://www.ashbyhq.com/",
        notes=("Ashby forms may include custom questions added by the employer.",),
    ),
    PlatformSpec(
        platform=ApplicationPlatform.SMARTRECRUITERS,
        display_name="SmartRecruiters",
        host_patterns=(r"(^|\.)smartrecruiters\.com$",),
        path_pattern=None,
        supports_automation=True,
        requires_account=True,
        documentation_url="https://developers.smartrecruiters.com/",
        notes=("SmartRecruiters often asks candidates to create an account first.",),
    ),
    PlatformSpec(
        platform=ApplicationPlatform.WORKDAY,
        display_name="Workday",
        host_patterns=(r"(^|\.)myworkdayjobs\.com$", r"(^|\.)workday\.com$"),
        path_pattern=None,
        supports_automation=False,
        requires_account=True,
        documentation_url="https://www.workday.com/",
        notes=(
            "Workday applications are multi-step and require a company account, "
            "so ApplyPilot guides this workflow manually instead of driving it.",
        ),
    ),
    PlatformSpec(
        platform=ApplicationPlatform.INDEED_SMARTAPPLY,
        display_name="Indeed SmartApply",
        # Exact host only: a URL that merely contains "indeed" is not trusted.
        host_patterns=(r"^smartapply\.indeed\.com$",),
        path_pattern=None,
        supports_automation=True,
        requires_account=None,
        documentation_url="https://smartapply.indeed.com/",
        notes=(
            "Indeed SmartApply is often reached from a Glassdoor Easy Apply "
            "button. ApplyPilot detects the redirect, validates this exact host "
            "and analyses the visible form without submitting it.",
        ),
    ),
    PlatformSpec(
        platform=ApplicationPlatform.ICIMS,
        display_name="iCIMS",
        host_patterns=(r"(^|\.)icims\.com$",),
        path_pattern=None,
        supports_automation=False,
        requires_account=True,
        documentation_url="https://www.icims.com/",
        notes=(
            "iCIMS portals vary by employer and commonly require registration, so "
            "they are handled by the guided manual workflow.",
        ),
    ),
    PlatformSpec(
        platform=ApplicationPlatform.COMPANY_HOSTED,
        display_name="Company careers portal",
        host_patterns=(),
        path_pattern=None,
        supports_automation=False,
        requires_account=None,
        documentation_url="",
        notes=(
            "This is an employer-hosted form with an unknown layout. ApplyPilot "
            "prepares the answers and resume but does not pretend to fill it.",
        ),
    ),
)

_SPECS_BY_PLATFORM = {spec.platform: spec for spec in PLATFORM_SPECS}

# Job-alert links are never used as application forms. Glassdoor blocks
# automated access and ApplyPilot does not scrape it or bypass anti-bot checks.
_JOB_BOARD_HOSTS = (
    r"(^|\.)glassdoor\.[a-z.]+$",
    r"(^|\.)indeed\.[a-z.]+$",
    r"(^|\.)linkedin\.com$",
)

#: Source board for a listing link. Used for messaging only; the listing URL is
#: never scraped and is always preserved as ``job_listing_url``.
_LISTING_SOURCES: tuple[tuple[str, str], ...] = (
    ("glassdoor", r"(^|\.)glassdoor\.[a-z.]+$"),
    ("indeed", r"(^|\.)indeed\.[a-z.]+$"),
    ("linkedin", r"(^|\.)linkedin\.com$"),
)

_BLOCKED_HOST_SUFFIXES = (".local", ".internal", ".localhost", ".lan", ".home.arpa")
_BLOCKED_HOSTS = {"localhost", "metadata.google.internal"}


@dataclass(frozen=True)
class PlatformDetection:
    """Result of classifying an application URL."""

    platform: ApplicationPlatform
    display_name: str
    confidence: DetectionConfidence
    supports_automation: bool
    requires_account: bool | None
    application_url: str
    host: str
    reason: str
    notes: tuple[str, ...]
    is_job_board_link: bool = False
    #: Which job board a listing link came from ("glassdoor", "indeed", ...).
    listing_source: str | None = None
    #: True only for a recognised application form on a supported platform.
    is_application_page: bool = False

    @property
    def is_automation_capable(self) -> bool:
        return self.supports_automation and not self.is_job_board_link

    @property
    def supports_easy_apply_redirect(self) -> bool:
        """A listing ApplyPilot can open while the user starts Easy Apply."""
        return bool(self.is_job_board_link)


def _hostname_is_safe(hostname: str) -> bool:
    """Reject loopback, private, link-local and internal hostnames."""
    lowered = hostname.lower().strip(".")
    if lowered in _BLOCKED_HOSTS or any(
        lowered.endswith(suffix) for suffix in _BLOCKED_HOST_SUFFIXES
    ):
        return False
    try:
        address = ipaddress.ip_address(lowered)
    except ValueError:
        return True
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
    )


def normalize_application_url(raw: str) -> str:
    """Validate and normalise an application URL.

    Raises:
        ApplicationUrlError: when the URL is not a public http(s) URL.
    """
    candidate = (raw or "").strip()
    if not candidate or len(candidate) > MAX_APPLICATION_URL_LENGTH:
        raise ApplicationUrlError("Enter a valid application URL.")
    parts = urlsplit(candidate)
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        raise ApplicationUrlError("The application URL must be an absolute http(s) URL.")
    if parts.username is not None or parts.password is not None:
        raise ApplicationUrlError("The application URL must not contain credentials.")
    if not _hostname_is_safe(parts.hostname):
        raise ApplicationUrlError(
            "The application URL must point to a public website.",
            code="unsafe_application_url",
        )
    if parts.port not in (None, 80, 443):
        raise ApplicationUrlError(
            "The application URL must use the standard HTTP or HTTPS port.",
            code="unsafe_application_url",
        )
    # Fragments never identify a posting; drop them so duplicates collapse.
    return urlunsplit((parts.scheme.lower(), parts.netloc, parts.path, parts.query, ""))


def _matches_any(patterns: tuple[str, ...], value: str) -> bool:
    return any(re.search(pattern, value, re.IGNORECASE) for pattern in patterns)


def listing_source_for_url(raw_url: str) -> str | None:
    """Name the job board a listing link belongs to, or ``None``."""
    try:
        host = (urlsplit(normalize_application_url(raw_url)).hostname or "").lower()
    except ApplicationUrlError:
        return None
    if not host:
        return None
    for source, pattern in _LISTING_SOURCES:
        if re.search(pattern, host, re.IGNORECASE):
            return source
    return None


def is_job_board_url(raw_url: str) -> bool:
    """Return True when ``raw_url`` points at a supported job board.

    The check is static and offline. An invalid or unsafe URL is not a job
    board; its own validation reports the precise problem.
    """
    try:
        host = (urlsplit(normalize_application_url(raw_url)).hostname or "").lower()
    except ApplicationUrlError:
        return False
    return bool(host) and _matches_any(_JOB_BOARD_HOSTS, host)


_URL_IN_TEXT = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)
_APPLY_HINTS = re.compile(
    r"(apply|application|careers?|jobs?|positions?|openings?)", re.IGNORECASE
)
_SKIPPED_PATH_SUFFIXES = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".webp",
    ".css",
    ".js",
    ".pdf",
    ".ico",
)


def suggest_employer_application_url(
    text: str | None, *, listing_url: str | None = None
) -> str | None:
    """Suggest a plausible employer application URL from trusted listing text.

    This is deliberately conservative: it only extracts absolute public http(s)
    URLs from the stored job description, ignores job-board links and obvious
    assets, and prefers links whose path looks like an application or careers
    page. The suggestion is advisory - the update endpoint validates it again
    before it is stored or visited.
    """
    if not text:
        return None
    normalized_listing = ""
    if listing_url:
        try:
            normalized_listing = normalize_application_url(listing_url)
        except ApplicationUrlError:
            normalized_listing = ""

    scored: list[tuple[int, str]] = []
    seen: set[str] = set()
    for match in _URL_IN_TEXT.findall(text):
        candidate = match.rstrip(".,;:!?")
        try:
            normalized = normalize_application_url(candidate)
        except ApplicationUrlError:
            continue
        if normalized in seen or normalized == normalized_listing:
            continue
        seen.add(normalized)
        parts = urlsplit(normalized)
        if parts.path.lower().endswith(_SKIPPED_PATH_SUFFIXES):
            continue
        if is_job_board_url(normalized):
            continue
        score = 1
        if _APPLY_HINTS.search(f"{parts.path} {parts.query}"):
            score += 2
        scored.append((score, normalized))
    if not scored:
        return None
    scored.sort(key=lambda item: (-item[0], len(item[1])))
    return scored[0][1]



def detect_platform(raw_url: str) -> PlatformDetection:
    """Classify an application URL into a supported platform.

    Detection is pure and offline: no request is made to the URL. A job-board
    link (Glassdoor/Indeed/LinkedIn) is reported explicitly instead of being
    treated as an application form.
    """
    url = normalize_application_url(raw_url)
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()

    # Greenhouse embeds on company domains are identified by the gh_jid query.
    query = parts.query.lower()
    if "gh_jid=" in query or "gh_src=" in query:
        spec = _SPECS_BY_PLATFORM[ApplicationPlatform.GREENHOUSE]
        return PlatformDetection(
            platform=spec.platform,
            display_name=spec.display_name,
            confidence=DetectionConfidence.MEDIUM,
            supports_automation=spec.supports_automation,
            requires_account=spec.requires_account,
            application_url=url,
            host=host,
            reason="The URL carries a Greenhouse job id, so it uses an embedded Greenhouse form.",
            notes=spec.notes,
            is_application_page=True,
        )

    for spec in PLATFORM_SPECS:
        if not spec.host_patterns:
            continue
        if not _matches_any(spec.host_patterns, host):
            continue
        if spec.path_pattern and not re.search(spec.path_pattern, parts.path, re.IGNORECASE):
            continue
        if spec.platform is ApplicationPlatform.INDEED_SMARTAPPLY and not is_smartapply_url(
            url
        ):
            # A look-alike host (extra labels, a non-https scheme or a custom
            # port) must never be treated as the approved SmartApply origin.
            continue
        return PlatformDetection(
            platform=spec.platform,
            display_name=spec.display_name,
            confidence=DetectionConfidence.HIGH,
            supports_automation=spec.supports_automation,
            requires_account=spec.requires_account,
            application_url=url,
            host=host,
            reason=f"Recognised a {spec.display_name} application URL.",
            notes=spec.notes,
            is_application_page=True,
        )

    if _matches_any(_JOB_BOARD_HOSTS, host):
        spec = _SPECS_BY_PLATFORM[ApplicationPlatform.COMPANY_HOSTED]
        source = listing_source_for_url(url)
        return PlatformDetection(
            platform=spec.platform,
            display_name=spec.display_name,
            confidence=DetectionConfidence.NONE,
            supports_automation=False,
            requires_account=None,
            application_url=url,
            host=host,
            reason=(
                "This link is a job board listing, not the employer's application "
                "form. ApplyPilot can open it for an Easy Apply hand-off; the "
                "employer's form is detected and validated after you start the "
                "application."
            ),
            notes=(
                "ApplyPilot does not scrape job boards or bypass their anti-bot "
                "protections.",
            ),
            is_job_board_link=True,
            listing_source=source,
        )

    spec = _SPECS_BY_PLATFORM[ApplicationPlatform.COMPANY_HOSTED]
    return PlatformDetection(
        platform=spec.platform,
        display_name=spec.display_name,
        confidence=DetectionConfidence.NONE,
        supports_automation=False,
        requires_account=None,
        application_url=url,
        host=host,
        reason=(
            "This is an employer-hosted form that ApplyPilot does not recognise, "
            "so it follows the guided manual workflow."
        ),
        notes=spec.notes,
    )


def platform_catalog() -> list[dict[str, object]]:
    """Describe every supported platform for the UI."""
    return [
        {
            "platform": spec.platform.value,
            "display_name": spec.display_name,
            "supports_automation": spec.supports_automation,
            "requires_account": spec.requires_account,
            "documentation_url": spec.documentation_url,
            "notes": list(spec.notes),
        }
        for spec in PLATFORM_SPECS
    ]


__all__ = [
    "PLATFORM_SPECS",
    "ApplicationPlatform",
    "ApplicationUrlError",
    "DetectionConfidence",
    "PlatformDetection",
    "PlatformSpec",
    "detect_platform",
    "is_job_board_url",
    "listing_source_for_url",
    "normalize_application_url",
    "platform_catalog",
    "suggest_employer_application_url",
]
