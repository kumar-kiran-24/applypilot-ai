"""Discovery and extraction of Glassdoor job recommendation emails.

Scope: read-only parsing of emails the user already received. Glassdoor itself
is never scraped, and no CAPTCHA, MFA or anti-bot protection is touched - the
extracted ``job_url`` is stored for the user to open in a browser.

Glassdoor alert emails have no schema. Extraction therefore works on structure:

1. sender domains and job URLs are matched against an **exact allow-list** of
   Glassdoor hostnames - suffix checks such as ``glassdoor.com.evil.com`` are
   never accepted;
2. job links are detected by validated host plus URL shape (``/job-listing/``,
   ``/partner/jobListing.htm``, ``/Job/``);
3. each link gets the lines of its own job block: the smallest enclosing element
   with no other job link, or for repeated-row layouts the link's row plus its
   adjacent sibling rows up to the next job link;
4. links are canonicalised (tracking parameters removed) and deduplicated, so
   running extraction twice - or over several emails - does not yield duplicate
   job records.

Extraction is intentionally conservative: when a field cannot be identified it
is ``None`` rather than guessed from an unrelated line.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from email.utils import parseaddr
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.services.gmail_service import GmailMessage

GLASSDOOR_MAX_DESCRIPTION_CHARS = 2000
GLASSDOOR_MAX_JOBS_PER_MESSAGE = 50

#: Gmail search query used to discover job recommendation emails.
GLASSDOOR_SENDER_QUERY = "from:glassdoor.com"


def glassdoor_search_query(days: int) -> str:
    """Return the Gmail ``q`` value for Glassdoor emails from the last ``days``."""
    return f"{GLASSDOOR_SENDER_QUERY} newer_than:{max(1, int(days))}d"


_JOB_URL_RE = re.compile(
    r"https?://(?:[A-Za-z0-9-]+\.)*glassdoor\.[A-Za-z.]{2,}/[^\s\"'<>]+",
    re.IGNORECASE,
)
_JOB_PATH_MARKERS = ("/job-listing/", "/joblisting", "/partner/joblisting", "/job/")

# Exact hostnames only. Country domains are the ones Glassdoor operates for this
# integration; extend deliberately rather than matching any ``glassdoor.*``.
GLASSDOOR_APEX_DOMAINS = frozenset(
    {
        "glassdoor.com",
        "glassdoor.ca",
        "glassdoor.co.uk",
        "glassdoor.com.au",
        "glassdoor.de",
        "glassdoor.fr",
        "glassdoor.nl",
        "glassdoor.ie",
        "glassdoor.in",
        "glassdoor.co.in",
        "glassdoor.sg",
        "glassdoor.com.br",
        "glassdoor.com.mx",
        "glassdoor.co.nz",
        "glassdoor.at",
        "glassdoor.be",
        "glassdoor.ch",
        "glassdoor.es",
        "glassdoor.it",
        "glassdoor.co.za",
        "glassdoor.hk",
    }
)

# Explicitly supported subdomains. A lookalike is a different registrable domain
# and can never appear here, which is why this is a list and not a suffix test.
GLASSDOOR_SUBDOMAIN_HOSTS = frozenset(
    {
        "www.glassdoor.com",
        "mail.glassdoor.com",
        "email.glassdoor.com",
        "jobs.glassdoor.com",
        "alerts.glassdoor.com",
        "noreply.glassdoor.com",
        "t.glassdoor.com",
        "click.glassdoor.com",
        "links.glassdoor.com",
        "notifications.glassdoor.com",
        "updates.glassdoor.com",
    }
)
_ALLOWED_URL_PORTS = {None, 80, 443}

_TRACKING_PARAMS = {
    "src",
    "pos",
    "ao",
    "guid",
    "campaignid",
    "campaign",
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "fromage",
    "ref",
    "refid",
    "loc",
    "sortby",
    "filter",
}

_GENERIC_LINK_TEXT = {
    "view job",
    "view jobs",
    "view job details",
    "see job",
    "see jobs",
    "job details",
    "view details",
    "learn more",
    "apply",
    "apply now",
    "apply on glassdoor",
    "easy apply",
    "see all jobs",
    "view all jobs",
    "unsubscribe",
    "manage alerts",
    "update preferences",
}

_SALARY_RE = re.compile(r"(?:\$\s?\d|per\s+(?:hour|year|annum|month))", re.IGNORECASE)
_DATE_RE = re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b")
# Promotional / boilerplate lines that appear next to job cards.
_PROMO_RE = re.compile(
    r"\b(?:new jobs|jobs? (?:for|matching|recommended)|job alert|recommended jobs|"
    r"see all jobs|view in browser|update your (?:alerts|preferences)|unsubscribe)\b",
    re.IGNORECASE,
)
_COUNTRY_NAMES = {
    "usa",
    "u.s.",
    "u.s.a.",
    "united states",
    "united states of america",
    "uk",
    "united kingdom",
    "canada",
    "india",
    "germany",
    "france",
    "spain",
    "netherlands",
    "ireland",
    "australia",
    "singapore",
    "remote",
}

_VOID_TAGS = {"br", "hr", "img", "input", "meta", "link", "source", "area", "base", "col"}
_BLOCK_TAGS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "br",
    "dd",
    "div",
    "dl",
    "dt",
    "fieldset",
    "figcaption",
    "figure",
    "footer",
    "form",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hr",
    "li",
    "main",
    "nav",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "ul",
}
_SKIP_TAGS = {"script", "style", "title", "head", "noscript"}


@dataclass(slots=True, frozen=True)
class GlassdoorJob:
    """One job recommendation extracted from an email."""

    job_title: str
    company: str | None
    location: str | None
    job_url: str
    email_subject: str
    received_at: datetime | None
    job_description: str | None
    message_id: str

    @property
    def dedupe_key(self) -> str:
        """Stable identity of the posting (Glassdoor id when available)."""
        params = dict(parse_qsl(urlsplit(self.job_url).query, keep_blank_values=False))
        job_id = params.get("jobListingId") or params.get("jl") or params.get("jobId")
        if job_id:
            return f"glassdoor-id:{job_id}"
        return f"glassdoor-url:{self.job_url}"

    @property
    def content_key(self) -> str:
        """Secondary identity used when the same job appears under two URLs."""
        parts = [
            self.job_title,
            self.company or "",
            self.location or "",
        ]
        return "glassdoor-content:" + "|".join(part.strip().lower() for part in parts)


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------
def is_glassdoor_host(hostname: str | None) -> bool:
    """Return True only for an exact, supported Glassdoor hostname.

    ``www.`` is supported on every apex domain. Nothing else is matched, so
    ``glassdoor.com.evil.com`` and ``notglassdoor.com`` are rejected.
    """
    if not isinstance(hostname, str):
        return False
    host = hostname.strip().lower().rstrip(".")
    if not host:
        return False
    if host in GLASSDOOR_APEX_DOMAINS or host in GLASSDOOR_SUBDOMAIN_HOSTS:
        return True
    return host.startswith("www.") and host[4:] in GLASSDOOR_APEX_DOMAINS


def is_glassdoor_job_url(url: str) -> bool:
    """Validate a job URL independently of the sender.

    Accepts only http(s), a supported Glassdoor host, a default port and a
    job-listing path. Credentials (``https://glassdoor.com@evil.com``),
    deceptive hosts and unexpected schemes are rejected.
    """
    if not isinstance(url, str) or not url.strip():
        return False
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return False

    if parts.scheme.lower() not in {"http", "https"}:
        return False
    if parts.username is not None or parts.password is not None:
        return False
    if not is_glassdoor_host(parts.hostname):
        return False
    if port not in _ALLOWED_URL_PORTS:
        return False

    path = parts.path.lower()
    return any(marker in path for marker in _JOB_PATH_MARKERS)


def canonicalize_job_url(url: str) -> str:
    """Drop tracking parameters and fragments, keeping job identifiers."""
    parts = urlsplit(url.strip())
    query_pairs = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=False)
        if key.lower() not in _TRACKING_PARAMS
    ]
    query = urlencode(sorted(query_pairs))
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, query, ""))


def is_glassdoor_sender(sender: str) -> bool:
    """True when the *addressed mailbox* uses a supported Glassdoor hostname.

    The header is parsed as an address instead of scanning for the first
    ``@``: a display name such as ``"noreply@glassdoor.com" <evil@evil.com>``
    would otherwise be mistaken for a Glassdoor sender. Only the domain of the
    actual addr-spec (the part after the last ``@``) is validated, so quoted
    local parts cannot smuggle in a trusted host either.

    Accepts ``noreply@glassdoor.com``, ``jobs@mail.glassdoor.com`` and ccTLDs
    such as ``glassdoor.co.uk``. Rejects lookalikes such as
    ``glassdoor.com.evil.com``.
    """
    if not isinstance(sender, str) or not sender.strip():
        return False
    _display_name, address = parseaddr(sender)
    if not address or "@" not in address:
        return False
    domain = address.rsplit("@", 1)[1].strip()
    return is_glassdoor_host(domain)


def _find_job_links(text: str) -> list[str]:
    links: list[str] = []
    for raw_url in _JOB_URL_RE.findall(text or ""):
        cleaned = raw_url.rstrip(").,;]>")
        if is_glassdoor_job_url(cleaned):
            links.append(cleaned)
    return links


def is_glassdoor_job_email(message: GmailMessage) -> bool:
    """True when the message is a Glassdoor email containing job links."""
    if not is_glassdoor_sender(message.sender):
        return False
    return bool(_find_job_links(f"{message.html}\n{message.plain_text}"))


# ---------------------------------------------------------------------------
# Minimal HTML DOM (stdlib only)
# ---------------------------------------------------------------------------
class _Node:
    __slots__ = ("attrs", "children", "parent", "tag")

    def __init__(
        self,
        tag: str,
        attrs: dict[str, str] | None = None,
        parent: _Node | None = None,
    ) -> None:
        self.tag = tag
        self.attrs = attrs or {}
        self.children: list[_Node | str] = []
        self.parent = parent


class _TreeBuilder(HTMLParser):
    """Build a tiny element tree; no third-party HTML dependency is required."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("root")
        self._stack: list[_Node] = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        node = _Node(
            tag,
            {name.lower(): (value or "") for name, value in attrs},
            self._stack[-1],
        )
        self._stack[-1].children.append(node)
        if tag not in _VOID_TAGS:
            self._stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if not data:
            return
        if self._stack[-1].tag in _SKIP_TAGS:
            return
        self._stack[-1].children.append(data)


def _iter_elements(node: _Node):
    for child in node.children:
        if isinstance(child, _Node):
            yield child
            yield from _iter_elements(child)


def _node_text(node: _Node) -> str:
    chunks: list[str] = []

    def walk(current: _Node) -> None:
        for child in current.children:
            if isinstance(child, str):
                chunks.append(child)
            elif child.tag not in _SKIP_TAGS:
                walk(child)

    walk(node)
    return " ".join("".join(chunks).split())


def _node_lines(node: _Node) -> list[str]:
    """Render an element as normalised, non-empty text lines."""
    chunks: list[str] = []

    def walk(current: _Node) -> None:
        for child in current.children:
            if isinstance(child, str):
                chunks.append(child)
                continue
            if child.tag in _SKIP_TAGS:
                continue
            if child.tag in _BLOCK_TAGS:
                chunks.append("\n")
                walk(child)
                chunks.append("\n")
            else:
                walk(child)

    walk(node)
    lines: list[str] = []
    for raw_line in "".join(chunks).split("\n"):
        line = " ".join(raw_line.split())
        if line:
            lines.append(line)
    return lines


def _count_job_anchors(node: _Node) -> int:
    return sum(
        1
        for element in _iter_elements(node)
        if element.tag == "a" and is_glassdoor_job_url(element.attrs.get("href", ""))
    )


_UNIT_TAGS = ("li", "article", "div", "p", "td")
_MAX_SIBLING_GROUPS = 5


def _nearest_job_unit(anchor: _Node) -> _Node:
    """Return the row/card that owns this anchor, when it owns only one link."""
    parent = anchor.parent
    depth = 0
    while parent is not None and depth < 6:
        if parent.tag == "tr" and _count_job_anchors(parent) <= 1:
            return parent
        parent = parent.parent
        depth += 1

    parent = anchor.parent
    depth = 0
    while parent is not None and depth < 8:
        if parent.tag in _UNIT_TAGS and _count_job_anchors(parent) <= 1:
            return parent
        parent = parent.parent
        depth += 1
    return anchor.parent or anchor


def _single_anchor_block(anchor: _Node) -> list[str] | None:
    """Lines of the nearest enclosing element that contains one job link."""
    parent = anchor.parent
    depth = 0
    while parent is not None and depth < 8:
        if _count_job_anchors(parent) <= 1:
            lines = _node_lines(parent)
            if len(_clean_block_lines(lines)) >= 2:
                return lines
        parent = parent.parent
        depth += 1
    return None


def _sibling_groups(node: _Node, direction: int) -> list[list[str]]:
    """Text of up to N adjacent elements, stopping at the next job link."""
    if node.parent is None:
        return []
    siblings = node.parent.children
    index = next((i for i, sibling in enumerate(siblings) if sibling is node), None)
    if index is None:  # pragma: no cover - node is always a child
        return []

    groups: list[list[str]] = []
    position = index + direction
    while 0 <= position < len(siblings) and len(groups) < _MAX_SIBLING_GROUPS:
        sibling = siblings[position]
        if isinstance(sibling, str):
            text = " ".join(sibling.split())
            if text:
                groups.append([text])
        else:
            if _count_job_anchors(sibling):
                break
            lines = _node_lines(sibling)
            if lines:
                groups.append(lines)
        position += direction
    return groups


def _sibling_block_lines(anchor: _Node) -> list[str]:
    """Lines around one link in a layout that repeats rows for each job.

    Only one side is used: when the link text is a real title the metadata rows
    follow it, otherwise they precede the "View job" row. Mixing both sides
    would attach a neighbouring job's company/location to this posting.
    """
    unit = _nearest_job_unit(anchor)
    preceding = [line for group in reversed(_sibling_groups(unit, -1)) for line in group]
    following = [line for group in _sibling_groups(unit, 1) for line in group]

    if _looks_like_title(_node_text(anchor).strip()):
        primary, secondary = following, preceding
    else:
        primary, secondary = preceding, following

    if not primary:
        primary = secondary
    return [*_node_lines(unit), *primary]


def _anchor_lines(anchor: _Node) -> list[str]:
    """Return the text lines belonging to exactly one job link."""
    block = _single_anchor_block(anchor)
    if block is not None:
        return block
    return _sibling_block_lines(anchor)


# ---------------------------------------------------------------------------
# Line classification
# ---------------------------------------------------------------------------
def _looks_like_location(line: str) -> bool:
    lowered = line.lower().strip()
    if not lowered:
        return False
    if lowered == "remote" or lowered.startswith(
        ("remote ", "remote,", "remote -", "hybrid ", "hybrid,", "on-site", "onsite")
    ):
        return True
    if len(line) > 70:
        return False
    segments = [segment.strip() for segment in line.split(",") if segment.strip()]
    if len(segments) < 2:
        return False
    if re.fullmatch(r"[A-Z]{2}", segments[-1]):
        return True
    return segments[-1].lower() in _COUNTRY_NAMES


def _looks_like_title(line: str) -> bool:
    if not 3 <= len(line) <= 120:
        return False
    lowered = line.lower().strip(" .:>»-|")
    if lowered in _GENERIC_LINK_TEXT:
        return False
    if _SALARY_RE.search(line) or _looks_like_location(line) or _PROMO_RE.search(line):
        return False
    if lowered.startswith(("glassdoor", "unsubscribe", "you are receiving", "jobs at")):
        return False
    return bool(re.search(r"[A-Za-z]", line))


def _looks_like_company(line: str, *, title: str, location: str | None) -> bool:
    if not 2 <= len(line) <= 80:
        return False
    if line.lower() == title.lower():
        return False
    if location is not None and line.lower() == location.lower():
        return False
    if _looks_like_location(line) or _SALARY_RE.search(line) or _DATE_RE.search(line):
        return False
    lowered = line.lower().strip(" .:>»-|")
    if lowered in _GENERIC_LINK_TEXT:
        return False
    if lowered.startswith(("apply ", "view ", "see ", "unsubscribe", "why you", "new jobs")):
        return False
    return bool(re.search(r"[A-Za-z]", line))


@dataclass(slots=True)
class _ExtractedFields:
    job_title: str
    company: str | None
    location: str | None
    job_description: str | None


def _clean_block_lines(lines: list[str]) -> list[str]:
    cleaned: list[str] = []
    for line in lines:
        stripped = " ".join(line.split())
        if not stripped or len(stripped) > 300:
            continue
        if stripped.lower().strip(" .:>»-|") in _GENERIC_LINK_TEXT:
            continue
        if _JOB_URL_RE.search(stripped):
            # A bare link line carries no job metadata.
            continue
        if cleaned and cleaned[-1].lower() == stripped.lower():
            continue
        cleaned.append(stripped)
    return cleaned


def _classify_block(
    lines: list[str],
    *,
    anchor_text: str,
    title_hint: str | None,
) -> _ExtractedFields | None:
    """Extract title/company/location/description from a block's text lines."""
    cleaned = _clean_block_lines(lines)
    hint = (title_hint or "").strip()
    anchor_label = anchor_text.strip()

    if not cleaned:
        # A link can still carry a usable job title (e.g. a compact alert card).
        for candidate in (hint, anchor_label):
            if _looks_like_title(candidate):
                return _ExtractedFields(
                    job_title=candidate,
                    company=None,
                    location=None,
                    job_description=None,
                )
        return None

    location_index: int | None = None
    for index, line in enumerate(cleaned):
        if _looks_like_location(line):
            location_index = index
            break
    location = cleaned[location_index] if location_index is not None else None

    title: str | None = None
    title_index: int | None = None

    # A specific link label ("Senior Backend Engineer" rather than "View job")
    # is the most reliable title signal in an alert email.
    if _looks_like_title(anchor_label):
        title = anchor_label
        for index, line in enumerate(cleaned):
            if line.lower() == anchor_label.lower():
                title_index = index
                break

    if title is None:
        for index, line in enumerate(cleaned):
            if index == location_index:
                continue
            if _looks_like_title(line):
                title = line
                title_index = index
                break

    if title is None and _looks_like_title(hint):
        title = hint

    if title is None:
        return None

    company: str | None = None
    company_index: int | None = None
    forward_start = 0 if title_index is None else title_index + 1
    for index in range(forward_start, len(cleaned)):
        if index == location_index:
            continue
        if _looks_like_company(cleaned[index], title=title, location=location):
            company = cleaned[index]
            company_index = index
            break
    if company is None and title_index is not None:
        for index in range(title_index - 1, -1, -1):
            if index == location_index:
                continue
            if _looks_like_company(cleaned[index], title=title, location=location):
                company = cleaned[index]
                company_index = index
                break

    excluded = {title_index, company_index, location_index}
    description_lines = [
        line
        for index, line in enumerate(cleaned)
        if index not in excluded and line.lower() != title.lower()
    ]
    description = "\n".join(description_lines).strip() or None
    if description is not None and len(description) < 40:
        description = None
    if description is not None:
        description = description[:GLASSDOOR_MAX_DESCRIPTION_CHARS]

    return _ExtractedFields(
        job_title=title,
        company=company,
        location=location,
        job_description=description,
    )


# ---------------------------------------------------------------------------
# Extraction entry points
# ---------------------------------------------------------------------------
def _extract_from_html(message: GmailMessage) -> list[GlassdoorJob]:
    if not message.html:
        return []
    builder = _TreeBuilder()
    try:
        builder.feed(message.html)
        builder.close()
    except Exception:  # pragma: no cover - HTMLParser is lenient; stay safe
        return []

    jobs: list[GlassdoorJob] = []
    for element in _iter_elements(builder.root):
        if element.tag != "a":
            continue
        href = element.attrs.get("href", "").strip()
        if not href or not _JOB_URL_RE.match(href) or not is_glassdoor_job_url(href):
            continue
        fields = _classify_block(
            _anchor_lines(element),
            anchor_text=_node_text(element),
            title_hint=element.attrs.get("title"),
        )
        if fields is None:
            continue
        jobs.append(
            GlassdoorJob(
                job_title=fields.job_title,
                company=fields.company,
                location=fields.location,
                job_url=canonicalize_job_url(href),
                email_subject=message.subject,
                received_at=message.received_at,
                job_description=fields.job_description,
                message_id=message.id,
            )
        )
        if len(jobs) >= GLASSDOOR_MAX_JOBS_PER_MESSAGE:
            break
    return jobs


def _extract_from_text(message: GmailMessage) -> list[GlassdoorJob]:
    raw_text = message.plain_text or ""
    if not raw_text and message.html:
        builder = _TreeBuilder()
        try:
            builder.feed(message.html)
            builder.close()
            raw_text = "\n".join(_node_lines(builder.root))
        except Exception:  # pragma: no cover
            raw_text = ""
    if not raw_text:
        return []

    lines = [" ".join(line.split()) for line in raw_text.splitlines()]
    lines = [line for line in lines if line]

    jobs: list[GlassdoorJob] = []
    for index, line in enumerate(lines):
        for raw_url in _JOB_URL_RE.findall(line):
            url = raw_url.rstrip(").,;]>")
            if not is_glassdoor_job_url(url):
                continue
            label = _JOB_URL_RE.sub("", line).strip(" -|:>»")
            context = lines[max(0, index - 4) : index]
            block = [*context, label] if label else list(context)
            fields = _classify_block(block, anchor_text=label, title_hint=None)
            if fields is None:
                continue
            jobs.append(
                GlassdoorJob(
                    job_title=fields.job_title,
                    company=fields.company,
                    location=fields.location,
                    job_url=canonicalize_job_url(url),
                    email_subject=message.subject,
                    received_at=message.received_at,
                    job_description=fields.job_description,
                    message_id=message.id,
                )
            )
            if len(jobs) >= GLASSDOOR_MAX_JOBS_PER_MESSAGE:
                return jobs
    return jobs


def extract_glassdoor_jobs(message: GmailMessage) -> list[GlassdoorJob]:
    """Extract job recommendations from one Glassdoor email (deduplicated)."""
    if not is_glassdoor_sender(message.sender):
        return []

    jobs = _extract_from_html(message)
    if not jobs:
        jobs = _extract_from_text(message)

    unique: list[GlassdoorJob] = []
    seen: set[str] = set()
    for job in jobs:
        # The same posting can appear under a partner URL and a path URL inside
        # one email, so both identities are checked.
        keys = {job.dedupe_key, job.content_key}
        if keys & seen:
            continue
        seen.update(keys)
        unique.append(job)
    return unique


def extract_glassdoor_jobs_from_messages(
    messages: list[GmailMessage],
    *,
    limit: int = 200,
) -> list[GlassdoorJob]:
    """Extract jobs across several emails, skipping duplicate postings.

    A posting is considered duplicate when either its Glassdoor job id / canonical
    URL or its (title, company, location) triple has already been seen.
    """
    jobs: list[GlassdoorJob] = []
    seen: set[str] = set()
    for message in messages:
        for job in extract_glassdoor_jobs(message):
            keys = {job.dedupe_key, job.content_key}
            if keys & seen:
                continue
            seen.update(keys)
            jobs.append(job)
            if len(jobs) >= limit:
                return jobs
    return jobs


__all__ = [
    "GLASSDOOR_APEX_DOMAINS",
    "GLASSDOOR_SENDER_QUERY",
    "GLASSDOOR_SUBDOMAIN_HOSTS",
    "GlassdoorJob",
    "canonicalize_job_url",
    "extract_glassdoor_jobs",
    "extract_glassdoor_jobs_from_messages",
    "glassdoor_search_query",
    "is_glassdoor_host",
    "is_glassdoor_job_email",
    "is_glassdoor_job_url",
    "is_glassdoor_sender",
]
