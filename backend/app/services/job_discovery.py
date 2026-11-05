"""Fetch Glassdoor recommendation emails and persist the postings.

Discovery still reads only emails the user already received. Persisting the
extracted postings gives matching a stable, user-scoped job id and prevents the
same Glassdoor posting from being stored twice.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.gmail_connection import GmailConnection
from app.models.job import Job
from app.models.user import User
from app.services.glassdoor_jobs import (
    GlassdoorJob,
    extract_glassdoor_jobs_from_messages,
    glassdoor_search_query,
)
from app.services.gmail_service import (
    GmailClient,
    GmailCredentialsError,
    GmailNotConnectedError,
    refresh_gmail_access_token,
)

DEDUPE_KEY_MAX_LENGTH = 500


@dataclass(slots=True)
class GlassdoorDiscoveryResult:
    """Extracted postings plus how many emails were inspected."""

    jobs: list[GlassdoorJob]
    messages_scanned: int


def _clip(value: str | None, limit: int) -> str:
    return (value or "")[:limit]


def normalized_dedupe_key(job: GlassdoorJob) -> str:
    """Return a stable, column-safe identity for a discovered posting."""
    key = job.dedupe_key
    if len(key) <= DEDUPE_KEY_MAX_LENGTH:
        return key
    digest = hashlib.sha256(job.job_url.encode("utf-8")).hexdigest()
    return f"glassdoor-url-sha256:{digest}"


async def fetch_glassdoor_jobs(
    db: AsyncSession,
    user: User,
    *,
    days: int,
    max_messages: int,
) -> GlassdoorDiscoveryResult:
    """Read the user's Glassdoor alert emails and return extracted postings."""
    connection = await db.scalar(
        select(GmailConnection).where(
            GmailConnection.user_id == user.id,
            GmailConnection.is_active.is_(True),
        )
    )
    if connection is None:
        raise GmailNotConnectedError()

    try:
        access = await refresh_gmail_access_token(connection)
    except GmailCredentialsError:
        # The stored credential is dead: reflect that in the status endpoint.
        connection.is_active = False
        await db.commit()
        raise

    async with GmailClient(access.access_token) as gmail:
        message_ids = await gmail.list_message_ids(
            query=glassdoor_search_query(days),
            max_results=max_messages,
        )
        messages = await gmail.get_messages(message_ids)

    return GlassdoorDiscoveryResult(
        jobs=extract_glassdoor_jobs_from_messages(messages),
        messages_scanned=len(messages),
    )


async def store_glassdoor_jobs(
    db: AsyncSession,
    user: User,
    jobs: Iterable[GlassdoorJob],
) -> tuple[list[Job], int, int]:
    """Upsert extracted postings for ``user``; return rows plus counts."""
    extracted = list(jobs)
    if not extracted:
        return [], 0, 0

    keys = {normalized_dedupe_key(job) for job in extracted}
    existing = {
        row.dedupe_key: row
        for row in (
            await db.scalars(
                select(Job).where(Job.user_id == user.id, Job.dedupe_key.in_(keys))
            )
        ).all()
    }

    created = 0
    updated = 0
    stored: list[Job] = []
    for extracted_job in extracted:
        key = normalized_dedupe_key(extracted_job)
        row = existing.get(key)
        if row is None:
            row = Job(
                id=uuid.uuid4(),
                user_id=user.id,
                dedupe_key=key,
                source="glassdoor",
                job_title=_clip(extracted_job.job_title, 300),
                company=_clip(extracted_job.company, 200) or None,
                location=_clip(extracted_job.location, 200) or None,
                job_url=_clip(extracted_job.job_url, 1000),
                job_description=extracted_job.job_description,
                email_subject=_clip(extracted_job.email_subject, 500),
                message_id=_clip(extracted_job.message_id, 200),
                received_at=extracted_job.received_at,
            )
            db.add(row)
            existing[key] = row
            created += 1
        else:
            # Refresh metadata the alert provides, but never erase a description
            # that a previous email did include.
            row.job_title = _clip(extracted_job.job_title, 300) or row.job_title
            row.company = _clip(extracted_job.company, 200) or row.company
            row.location = _clip(extracted_job.location, 200) or row.location
            row.job_url = _clip(extracted_job.job_url, 1000) or row.job_url
            row.email_subject = _clip(extracted_job.email_subject, 500) or row.email_subject
            row.message_id = _clip(extracted_job.message_id, 200) or row.message_id
            row.received_at = extracted_job.received_at or row.received_at
            if extracted_job.job_description:
                row.job_description = extracted_job.job_description
            updated += 1
        stored.append(row)

    await db.commit()
    # A single email (or two emails in one scan) can repeat a posting; return
    # each stored row once while preserving discovery order.
    unique_stored = list({row.id: row for row in stored}.values())
    for row in unique_stored:
        await db.refresh(row)
    return unique_stored, created, updated


__all__ = [
    "GlassdoorDiscoveryResult",
    "fetch_glassdoor_jobs",
    "normalized_dedupe_key",
    "store_glassdoor_jobs",
]
