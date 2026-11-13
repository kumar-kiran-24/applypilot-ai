"""User-scoped access to discovered job postings.

Every query filters on the authenticated user id. A job that belongs to another
account is reported as missing, never as forbidden, so job ids cannot be probed
across accounts.

Listing supports search, filters, sorting and pagination so the UI never has to
load the whole library to find one posting. Only columns that actually exist on
``Job`` are filterable, and the search covers the stored posting text.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ResourceNotFoundError
from app.models.job import Job
from app.models.job_match import JobResumeMatch
from app.models.job_state import JobState
from app.models.user import User

JobStateFilter = Literal["active", "saved", "dismissed", "all"]
JobSort = Literal[
    "received_desc",
    "received_asc",
    "title_asc",
    "title_desc",
    "company_asc",
    "company_desc",
    "saved_desc",
]

JOB_STATE_FILTERS: tuple[JobStateFilter, ...] = ("active", "saved", "dismissed", "all")
JOB_SORTS: tuple[JobSort, ...] = (
    "received_desc",
    "received_asc",
    "title_asc",
    "title_desc",
    "company_asc",
    "company_desc",
    "saved_desc",
)

#: Maximum length accepted for a free-text search term.
MAX_SEARCH_LENGTH = 200


class JobNotFoundError(ResourceNotFoundError):
    """The job does not exist for this user."""

    code = "job_not_found"
    message = "Job not found."


def _escape_like(value: str) -> str:
    """Escape LIKE wildcards so a search term is matched literally."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class JobService:
    """Queries over persisted, user-owned job postings."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------ read

    def _state_join(self, user: User) -> tuple[object, ...]:
        """Join the user's own state rows; another user's rows never leak in."""
        return (
            JobState,
            and_(JobState.job_id == Job.id, JobState.user_id == user.id),
        )

    def _conditions(
        self,
        user: User,
        *,
        q: str | None = None,
        company: str | None = None,
        location: str | None = None,
        source: str | None = None,
        has_matches: bool | None = None,
        state: JobStateFilter = "active",
    ) -> list[object]:
        conditions: list[object] = [Job.user_id == user.id]

        term = (q or "").strip()
        if term:
            pattern = f"%{_escape_like(term[:MAX_SEARCH_LENGTH])}%"
            conditions.append(
                or_(
                    Job.job_title.ilike(pattern, escape="\\"),
                    Job.company.ilike(pattern, escape="\\"),
                    Job.location.ilike(pattern, escape="\\"),
                    Job.job_description.ilike(pattern, escape="\\"),
                )
            )

        if company and company.strip():
            conditions.append(
                Job.company.ilike(f"%{_escape_like(company.strip())}%", escape="\\")
            )
        if location and location.strip():
            conditions.append(
                Job.location.ilike(f"%{_escape_like(location.strip())}%", escape="\\")
            )
        if source and source.strip():
            conditions.append(Job.source == source.strip())

        if has_matches is not None:
            matched = (
                select(JobResumeMatch.id)
                .where(JobResumeMatch.job_id == Job.id)
                .exists()
            )
            conditions.append(matched if has_matches else ~matched)

        if state == "active":
            conditions.append(
                or_(JobState.id.is_(None), JobState.is_dismissed.is_(False))
            )
        elif state == "saved":
            conditions.append(JobState.is_saved.is_(True))
        elif state == "dismissed":
            conditions.append(JobState.is_dismissed.is_(True))

        return conditions

    def _base_query(self, user: User, **filters: object) -> Select[tuple[Job]]:
        return (
            select(Job)
            .outerjoin(*self._state_join(user))
            .where(*self._conditions(user, **filters))
        )

    @staticmethod
    def _order_by(sort: JobSort) -> list[object]:
        if sort == "received_asc":
            return [Job.received_at.asc().nulls_last(), Job.created_at.asc()]
        if sort == "title_asc":
            return [Job.job_title.asc(), Job.created_at.desc()]
        if sort == "title_desc":
            return [Job.job_title.desc(), Job.created_at.desc()]
        if sort == "company_asc":
            return [Job.company.asc().nulls_last(), Job.created_at.desc()]
        if sort == "company_desc":
            return [Job.company.desc().nulls_last(), Job.created_at.desc()]
        if sort == "saved_desc":
            return [JobState.saved_at.desc().nulls_last(), Job.created_at.desc()]
        return [Job.received_at.desc().nulls_last(), Job.created_at.desc()]

    async def list_jobs(
        self,
        user: User,
        *,
        q: str | None = None,
        company: str | None = None,
        location: str | None = None,
        source: str | None = None,
        has_matches: bool | None = None,
        state: JobStateFilter = "active",
        sort: JobSort = "received_desc",
        limit: int = 50,
        offset: int = 0,
    ) -> list[Job]:
        filters = {
            "q": q,
            "company": company,
            "location": location,
            "source": source,
            "has_matches": has_matches,
            "state": state,
        }
        result = await self.session.scalars(
            self._base_query(user, **filters)
            .order_by(*self._order_by(sort))
            .offset(offset)
            .limit(limit)
        )
        return list(result.unique().all())

    async def count_jobs(
        self,
        user: User,
        *,
        q: str | None = None,
        company: str | None = None,
        location: str | None = None,
        source: str | None = None,
        has_matches: bool | None = None,
        state: JobStateFilter = "active",
    ) -> int:
        """Count the filtered set, so pagination reflects the visible rows."""
        return int(
            await self.session.scalar(
                select(func.count(Job.id))
                .select_from(Job)
                .outerjoin(*self._state_join(user))
                .where(
                    *self._conditions(
                        user,
                        q=q,
                        company=company,
                        location=location,
                        source=source,
                        has_matches=has_matches,
                        state=state,
                    )
                )
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

    # ----------------------------------------------------------- user state

    async def states_for(
        self, user: User, job_ids: list[UUID]
    ) -> dict[UUID, JobState]:
        """Load the user's state rows for a page of jobs in one query."""
        unique_ids = list(dict.fromkeys(job_ids))
        if not unique_ids:
            return {}
        rows = await self.session.scalars(
            select(JobState).where(
                JobState.user_id == user.id, JobState.job_id.in_(unique_ids)
            )
        )
        return {row.job_id: row for row in rows}

    async def state_for(self, user: User, job: Job) -> JobState | None:
        return await self.session.scalar(
            select(JobState).where(
                JobState.user_id == user.id, JobState.job_id == job.id
            )
        )

    async def _ensure_state(self, user: User, job: Job) -> JobState:
        row = await self.state_for(user, job)
        if row is None:
            row = JobState(user_id=user.id, job_id=job.id, is_saved=False, is_dismissed=False)
            self.session.add(row)
        return row

    async def set_saved(
        self, user: User, job_id: UUID, *, saved: bool
    ) -> tuple[Job, JobState]:
        """Save or unsave a posting. Saving also clears a dismissal.

        Saved and dismissed are mutually exclusive: a posting the user asked to
        stop seeing should not silently come back as saved, and a saved posting
        should not also count as dismissed.
        """
        job = await self.get_job(user, job_id)
        row = await self._ensure_state(user, job)
        row.is_saved = saved
        row.saved_at = datetime.now(timezone.utc) if saved else None
        if saved:
            row.is_dismissed = False
            row.dismissed_at = None
        await self.session.commit()
        await self.session.refresh(row)
        return job, row

    async def set_dismissed(
        self, user: User, job_id: UUID, *, dismissed: bool
    ) -> tuple[Job, JobState]:
        """Dismiss a recommendation, or restore a dismissed one.

        Dismissing clears the saved flag so a dismissed posting cannot be
        double-counted as saved; restoring returns it to the active list.
        """
        job = await self.get_job(user, job_id)
        row = await self._ensure_state(user, job)
        row.is_dismissed = dismissed
        row.dismissed_at = datetime.now(timezone.utc) if dismissed else None
        if dismissed:
            row.is_saved = False
            row.saved_at = None
        await self.session.commit()
        await self.session.refresh(row)
        return job, row


__all__ = [
    "JOB_SORTS",
    "JOB_STATE_FILTERS",
    "MAX_SEARCH_LENGTH",
    "JobNotFoundError",
    "JobService",
    "JobSort",
    "JobStateFilter",
]
