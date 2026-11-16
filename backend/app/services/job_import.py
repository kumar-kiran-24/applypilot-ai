"""Import a job listing from a URL the user pasted themselves.

Scope and safety:

* only an exact, supported Glassdoor hostname with a job-listing path is
  accepted - a look-alike domain or another job board is refused;
* the URL passes the same static validation and DNS/public-address check the
  browser session uses, so a private, loopback or rebinding destination cannot
  be imported;
* **no request is made to the pasted URL**. ApplyPilot never scrapes Glassdoor,
  never follows a redirect and never touches anti-bot protection. What can be
  read from the URL itself (canonical link, Glassdoor job id, a title slug) is
  used; everything else stays unknown instead of being invented;
* an existing record with the same Glassdoor id or canonical URL is reused, so
  importing twice cannot create a duplicate posting.

The created row is owned by the authenticated user and uses the same
``dedupe_key`` scheme as email discovery, so direct imports and discovered
postings share one job library.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from urllib.parse import urlsplit

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import InvalidOperationError
from app.core.logging import get_logger
from app.models.job import Job
from app.models.user import User
from app.services.application_platforms import (
    is_job_board_url,
    listing_source_for_url,
    normalize_application_url,
)
from app.services.application_url_security import (
    HostResolver,
    validate_application_destination,
)
from app.services.glassdoor_jobs import (
    GlassdoorJob,
    canonicalize_job_url,
    is_glassdoor_job_url,
)
from app.services.job_discovery import normalized_dedupe_key

logger = get_logger(__name__)

#: Only these listing sources may be imported directly. Other boards are
#: rejected explicitly rather than treated as unknown URLs.
SUPPORTED_IMPORT_SOURCES: frozenset[str] = frozenset({"glassdoor"})

IMPORTED_SUBJECT = "Imported from a pasted Glassdoor job link"

_JOB_LISTING_SEGMENT = re.compile(r"/job-listing/([^/]+)", re.IGNORECASE)
_GLASSDOOR_ID_SUFFIX = re.compile(r"-JV_[A-Za-z0-9_,]+$", re.IGNORECASE)
_ACRONYMS = frozenset(
    {
        "ai",
        "api",
        "aws",
        "bi",
        "css",
        "devops",
        "gcp",
        "hr",
        "ios",
        "it",
        "llm",
        "ml",
        "nlp",
        "qa",
        "sre",
        "sql",
        "ui",
        "ux",
    }
)


class JobImportError(InvalidOperationError):
    """The pasted URL cannot be imported."""

    code = "job_import_rejected"
    message = "That job link cannot be imported."


@dataclass(frozen=True)
class ImportExtraction:
    """What was read from the URL, and what deliberately was not."""

    source: str
    title_source: str
    description_available: bool
    missing_fields: tuple[str, ...]
    notes: tuple[str, ...]

    def to_payload(self) -> dict[str, object]:
        return {
            "source": self.source,
            "title_source": self.title_source,
            "description_available": self.description_available,
            "missing_fields": list(self.missing_fields),
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class JobImportOutcome:
    """The stored posting plus truthful import metadata."""

    job: Job
    created: bool
    extraction: ImportExtraction


def _title_from_url(url: str) -> tuple[str, bool]:
    """Derive a readable title from a Glassdoor listing slug.

    Returns ``(title, derived)``. ``derived`` is False when the URL carries no
    usable slug, in which case a neutral placeholder is stored and the response
    says the title is unavailable rather than pretending it was read.
    """
    # Parse the path only: the query string carries the job id, never the title.
    path = urlsplit(url).path
    match = _JOB_LISTING_SEGMENT.search(path)
    slug = match.group(1) if match else ""
    # Strip the file extension first, then Glassdoor's encoded id suffix; the
    # suffix regex stops at the dot, so the other order would leave id tokens.
    slug = re.sub(r"\.html?$", "", slug, flags=re.IGNORECASE)
    slug = _GLASSDOOR_ID_SUFFIX.sub("", slug)
    words = [
        word
        for word in re.split(r"[-_]+", slug)
        if word and not word.isdigit()
    ]
    if not words:
        return "Glassdoor job posting", False
    title = " ".join(
        word.upper()
        if word.lower() in _ACRONYMS
        else word[:1].upper() + word[1:].lower()
        for word in words
    )
    return title[:300], True


def _glassdoor_job(url: str) -> GlassdoorJob:
    """Reuse the discovery model so direct imports share its dedupe identity."""
    title, _derived = _title_from_url(url)
    return GlassdoorJob(
        job_title=title,
        company=None,
        location=None,
        job_url=url,
        email_subject=IMPORTED_SUBJECT,
        received_at=None,
        job_description=None,
        message_id="",
    )


def _reject(url: str) -> JobImportError:
    """Explain precisely why this host/path is not importable."""
    source = listing_source_for_url(url)
    if source and source not in SUPPORTED_IMPORT_SOURCES:
        return JobImportError(
            f"Importing from {source.title()} is not supported yet. Paste a "
            "Glassdoor job listing URL.",
            code="unsupported_job_listing_source",
        )
    if is_job_board_url(url):
        return JobImportError(
            "That link is not a Glassdoor job posting. Open the listing and "
            "copy its job-listing URL.",
            code="unsupported_job_url_shape",
        )
    return JobImportError(
        "Only public Glassdoor job listing URLs can be imported. Other "
        "websites and non-listing links are not supported.",
        code="unsupported_job_listing_domain",
    )


async def import_job_url(
    session: AsyncSession,
    user: User,
    raw_url: str,
    *,
    resolver: HostResolver | None = None,
) -> JobImportOutcome:
    """Validate and store a directly pasted Glassdoor listing for ``user``.

    Raises:
        ApplicationUrlError: the URL is malformed, uses credentials, a
            non-standard port or a private IP literal.
        JobImportError: the domain/path is not a supported Glassdoor listing.
        UnsafeNavigationError: the hostname does not resolve to a public
            address.
    """
    normalized = normalize_application_url(raw_url)
    if not is_glassdoor_job_url(normalized):
        raise _reject(normalized)

    # Resolve (never fetch) before anything is stored. This refuses a public
    # name that points at a private/internal address and is repeated by the
    # browser session with a pinned address, so a later rebind cannot help.
    await validate_application_destination(normalized, resolver=resolver)

    canonical = canonicalize_job_url(normalized)
    model = _glassdoor_job(canonical)
    dedupe_key = normalized_dedupe_key(model)
    title, derived = _title_from_url(canonical)

    existing = await _find_existing(session, user, dedupe_key, canonical)
    if existing is not None:
        logger.info(
            "Direct job import reused an existing posting",
            extra={
                "stage": "job_import",
                "user_id": str(user.id),
                "job_id": str(existing.id),
            },
        )
        return JobImportOutcome(
            job=existing,
            created=False,
            extraction=_extraction_for(existing, title_source="stored_record"),
        )

    job = Job(
        id=uuid.uuid4(),
        user_id=user.id,
        dedupe_key=dedupe_key,
        source="glassdoor",
        job_title=title,
        company=None,
        location=None,
        job_url=canonical,
        job_description=None,
        email_subject=IMPORTED_SUBJECT,
        message_id="",
        received_at=None,
    )
    session.add(job)
    try:
        await session.commit()
    except IntegrityError:
        # A concurrent import (or discovery) won the unique constraint. Adopt
        # that row instead of failing or creating a duplicate.
        await session.rollback()
        existing = await _find_existing(session, user, dedupe_key, canonical)
        if existing is None:  # pragma: no cover - constraint implies a row
            raise
        return JobImportOutcome(
            job=existing,
            created=False,
            extraction=_extraction_for(existing, title_source="stored_record"),
        )

    await session.refresh(job)
    logger.info(
        "Direct job import created a posting",
        extra={
            "stage": "job_import",
            "user_id": str(user.id),
            "job_id": str(job.id),
            "title_source": "url_slug" if derived else "unavailable",
        },
    )
    return JobImportOutcome(
        job=job,
        created=True,
        extraction=_extraction_for(
            job, title_source="url_slug" if derived else "unavailable"
        ),
    )


async def _find_existing(
    session: AsyncSession, user: User, dedupe_key: str, canonical_url: str
) -> Job | None:
    """Find an owned posting by its Glassdoor id or canonical URL."""
    return await session.scalar(
        select(Job)
        .where(
            Job.user_id == user.id,
            or_(Job.dedupe_key == dedupe_key, Job.job_url == canonical_url),
        )
        .limit(1)
    )


def _extraction_for(job: Job, *, title_source: str) -> ImportExtraction:
    """Describe honestly which fields this import could actually populate."""
    missing: list[str] = []
    if not job.company:
        missing.append("company")
    if not job.location:
        missing.append("location")
    if not job.job_description:
        missing.append("description")

    notes: list[str] = []
    if title_source == "url_slug":
        notes.append(
            "Glassdoor was not scraped. The stored title was derived from the "
            "listing URL; open the original posting to confirm it."
        )
    elif title_source == "unavailable":
        notes.append(
            "The URL carried no readable job title. Open the original posting "
            "and add the employer's application URL when you apply."
        )
    else:
        notes.append("An existing posting with the same identity was reused.")
    if missing:
        notes.append(
            "Missing details stay unknown rather than being guessed: "
            + ", ".join(missing)
            + "."
        )

    return ImportExtraction(
        source=job.source,
        title_source=title_source,
        description_available=bool(job.job_description),
        missing_fields=tuple(missing),
        notes=tuple(notes),
    )


__all__ = [
    "IMPORTED_SUBJECT",
    "SUPPORTED_IMPORT_SOURCES",
    "ImportExtraction",
    "JobImportError",
    "JobImportOutcome",
    "import_job_url",
]
