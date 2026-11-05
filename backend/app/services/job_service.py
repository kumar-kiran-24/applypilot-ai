"""User-scoped access to discovered job postings.

Every query filters on the authenticated user id. A job that belongs to another
account is reported as missing, never as forbidden, so job ids cannot be probed
across accounts.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ResourceNotFoundError
from app.models.job import Job
from app.models.user import User


class JobNotFoundError(ResourceNotFoundError):
    """The job does not exist for this user."""

    code = "job_not_found"
    message = "Job not found."


class JobService:
    """Queries over persisted, user-owned job postings."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_jobs(self, user: User, *, limit: int = 50, offset: int = 0) -> list[Job]:
        result = await self.session.scalars(
            select(Job)
            .where(Job.user_id == user.id)
            .order_by(Job.received_at.desc().nulls_last(), Job.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(result.all())

    async def count_jobs(self, user: User) -> int:
        return int(
            await self.session.scalar(
                select(func.count(Job.id)).where(Job.user_id == user.id)
            )
            or 0
        )

    async def get_job(self, user: User, job_id: UUID) -> Job:
        job = await self.session.scalar(
            select(Job).where(Job.id == job_id, Job.user_id == user.id)
        )
        if job is None:
            raise JobNotFoundError()
        return job

    async def get_jobs(self, user: User, job_ids: list[UUID]) -> list[Job]:
        """Return the owned subset of ``job_ids``; missing ids raise 404."""
        if not job_ids:
            return []
        unique_ids = list(dict.fromkeys(job_ids))
        rows = list(
            (
                await self.session.scalars(
                    select(Job).where(Job.user_id == user.id, Job.id.in_(unique_ids))
                )
            ).all()
        )
        found = {row.id for row in rows}
        if any(job_id not in found for job_id in unique_ids):
            raise JobNotFoundError()
        by_id = {row.id: row for row in rows}
        return [by_id[job_id] for job_id in unique_ids]


__all__ = ["JobNotFoundError", "JobService"]
