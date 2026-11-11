"""Persisted, explainable resume matching for discovered jobs.

Responsibilities:

* build deterministic resume profiles from the structured data already parsed
  at upload time (no resume text is re-read and no external service is called);
* evaluate every resume the user owns for a job;
* cache a result per ``(job, resume)`` and recompute only when the job text or
  the parsed resume changed;
* rank results with a deterministic tie-break and mark the best resume.

Ownership is enforced from the authenticated user on every query. Storage keys
and files never enter this layer.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.logging import get_logger
from app.models.job import Job
from app.models.job_match import JobResumeMatch
from app.models.resume import Resume
from app.models.user import User
from app.schemas.resume import ParsedResumeData
from app.services.advanced_matching_engine import MATCHING_ALGORITHM_VERSION
from app.services.advanced_matching_service import AdvancedMatchingService
from app.services.job_requirements import (
    REQUIREMENTS_VERSION,
    JobRequirements,
    build_job_requirements,
)
from app.services.job_service import JobNotFoundError, JobService
from app.services.matching_engine import (
    EducationEntry,
    ExperienceEntry,
    MatchResult,
    ProjectEntry,
    ResumeProfile,
    education_level_rank,
    normalize_skill_key,
    rank_results,
)
from app.services.resume_parser import detect_known_skills

logger = get_logger(__name__)


def job_fingerprint(
    job: Job,
    *,
    intelligence_extractor_version: str = "",
    intelligence_fingerprint: str = "",
) -> str:
    """Identity of the inputs that affect a job's score.

    Includes the matching algorithm version and, when job intelligence is used,
    that extraction's version and posting fingerprint. A change to any of them
    invalidates cached matches without a schema migration.
    """
    payload = "\x1f".join(
        (
            MATCHING_ALGORITHM_VERSION,
            REQUIREMENTS_VERSION,
            job.job_title or "",
            job.company or "",
            job.location or "",
            job.job_description or "",
            intelligence_extractor_version,
            intelligence_fingerprint,
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def resume_fingerprint(
    resume: Resume,
    *,
    intelligence_extractor_version: str = "",
    intelligence_fingerprint: str = "",
) -> str:
    """Identity of the parsed resume used for matching.

    ``is_default`` is deliberately excluded: toggling the default resume must
    not invalidate cached scores. The resume-intelligence version and content
    fingerprint are included so new or re-extracted evidence invalidates the
    cached score.
    """
    parsed_at = resume.parsed_at.isoformat() if resume.parsed_at else ""
    payload = "\x1f".join(
        (
            MATCHING_ALGORITHM_VERSION,
            resume.checksum or "",
            parsed_at,
            resume.parsing_status.value,
            intelligence_extractor_version,
            intelligence_fingerprint,
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def requirements_for(job: Job) -> JobRequirements:
    """Extract requirements for a job exactly as the engine expects."""
    return build_job_requirements(
        job_title=job.job_title,
        company=job.company,
        location=job.location,
        description=job.job_description,
    )


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = " ".join((value or "").split())
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            result.append(cleaned)
    return result


def _skill_keys_for(*texts: str | None) -> frozenset[str]:
    joined = "\n".join(text for text in texts if text)
    return frozenset(normalize_skill_key(skill) for skill in detect_known_skills(joined))


def build_resume_profile(resume: Resume) -> ResumeProfile:
    """Convert stored structured resume data into a scoring profile."""
    try:
        data = ParsedResumeData.model_validate(resume.parsed_data or {})
    except ValidationError:
        # A hand-edited or legacy row must not break matching; use its skills.
        data = ParsedResumeData()

    declared_skills = [skill for skill in (resume.skills or []) if isinstance(skill, str)]
    project_technologies = [
        tech for project in data.projects for tech in project.technologies
    ]
    free_text = "\n".join(
        part
        for part in (
            data.professional_summary,
            *(entry.description or "" for entry in data.experience),
            *(project.description or "" for project in data.projects),
        )
        if part
    )
    skills = _dedupe(
        [*declared_skills, *project_technologies, *detect_known_skills(free_text)]
    )
    skill_keys = frozenset(normalize_skill_key(skill) for skill in skills)

    experience: list[ExperienceEntry] = []
    for entry in data.experience:
        experience.append(
            ExperienceEntry(
                title=entry.title,
                company=entry.company,
                description=entry.description,
                technologies=(),
                start_date=entry.start_date,
                end_date=entry.end_date,
                is_current=entry.is_current,
                skill_keys=_skill_keys_for(
                    entry.title,
                    entry.company,
                    entry.description,
                ),
            )
        )

    education: list[EducationEntry] = []
    for entry in data.education:
        degree_text = " ".join(
            part for part in (entry.degree, entry.field_of_study) if part
        )
        education.append(
            EducationEntry(
                degree=entry.degree,
                field_of_study=entry.field_of_study,
                institution=entry.institution,
                details=entry.details,
                level=education_level_rank(degree_text),
            )
        )

    projects: list[ProjectEntry] = []
    for project in data.projects:
        projects.append(
            ProjectEntry(
                name=project.name,
                description=project.description,
                technologies=tuple(project.technologies),
                skill_keys=_skill_keys_for(
                    project.name,
                    project.description,
                    " ".join(project.technologies),
                ),
            )
        )

    return ResumeProfile(
        resume_id=str(resume.id),
        filename=resume.original_filename,
        skills=tuple(skills),
        skill_keys=skill_keys,
        summary=data.professional_summary,
        titles=tuple(
            entry.title for entry in data.experience if entry.title
        ),
        experience=tuple(experience),
        education=tuple(education),
        projects=tuple(projects),
        certifications=tuple(data.certifications),
    )


def _apply_result(
    row: JobResumeMatch,
    *,
    result: MatchResult,
    job_fp: str,
    resume_fp: str,
    computed_at: datetime,
) -> None:
    row.job_fingerprint = job_fp
    row.resume_fingerprint = resume_fp
    row.computed_at = computed_at
    row.overall_score = result.overall_score
    row.skill_score = result.skill_score
    row.experience_score = result.experience_score
    row.education_score = result.education_score
    row.title_score = result.title_score
    row.project_score = result.project_score
    row.matched_required_skills = list(result.matched_required_skills)
    row.matched_preferred_skills = list(result.matched_preferred_skills)
    row.missing_required_skills = list(result.missing_required_skills)
    row.missing_preferred_skills = list(result.missing_preferred_skills)
    row.strengths = list(result.strengths)
    row.suggestions = list(result.suggestions)
    row.explanation = result.explanation
    row.confidence = result.confidence
    row.details = result.details


class MatchingService:
    """Evaluates, caches and ranks resume compatibility for owned jobs."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.jobs = JobService(session)
        self.advanced = AdvancedMatchingService(session)

    # --- Reads --------------------------------------------------------------
    async def get_job_matches(self, user: User, job: Job) -> list[JobResumeMatch]:
        rows = await self.session.scalars(
            select(JobResumeMatch)
            .where(
                JobResumeMatch.user_id == user.id,
                JobResumeMatch.job_id == job.id,
            )
            .options(selectinload(JobResumeMatch.resume))
            .order_by(JobResumeMatch.rank.asc())
        )
        return list(rows.all())

    async def best_matches_for_jobs(
        self, user: User, job_ids: list[UUID]
    ) -> dict[UUID, tuple[JobResumeMatch, int]]:
        """Return each job's recommended match and total match count."""
        if not job_ids:
            return {}
        recommended = await self.session.scalars(
            select(JobResumeMatch)
            .where(
                JobResumeMatch.user_id == user.id,
                JobResumeMatch.job_id.in_(job_ids),
                JobResumeMatch.is_recommended.is_(True),
            )
            .options(selectinload(JobResumeMatch.resume))
        )
        counts = dict(
            (
                await self.session.execute(
                    select(JobResumeMatch.job_id, func.count(JobResumeMatch.id))
                    .where(
                        JobResumeMatch.user_id == user.id,
                        JobResumeMatch.job_id.in_(job_ids),
                    )
                    .group_by(JobResumeMatch.job_id)
                )
            ).all()
        )
        best: dict[UUID, tuple[JobResumeMatch, int]] = {}
        for row in recommended.all():
            best[row.job_id] = (row, int(counts.get(row.job_id, 1)))
        return best

    # --- Compute ------------------------------------------------------------
    async def ensure_job_matches(
        self,
        user: User,
        job: Job,
        *,
        force: bool = False,
        as_of: date | None = None,
    ) -> list[JobResumeMatch]:
        """Compute missing/stale matches for one job and return them ranked."""
        as_of = as_of or datetime.now(timezone.utc).date()
        resumes = list(
            (
                await self.session.scalars(
                    select(Resume)
                    .where(Resume.user_id == user.id)
                    .order_by(Resume.created_at.desc())
                )
            ).all()
        )
        existing = {
            row.resume_id: row
            for row in (
                await self.session.scalars(
                    select(JobResumeMatch).where(
                        JobResumeMatch.user_id == user.id,
                        JobResumeMatch.job_id == job.id,
                    )
                )
            ).all()
        }
        resume_ids = {resume.id for resume in resumes}
        for resume_id, row in list(existing.items()):
            if resume_id not in resume_ids:
                await self.session.delete(row)
                existing.pop(resume_id)

        if not resumes:
            await self.session.commit()
            return []

        job_input = await self.advanced.job_input(user, job)
        job_fp = job_fingerprint(
            job,
            intelligence_extractor_version=job_input.extractor_version or "",
            intelligence_fingerprint=job_input.fingerprint or "",
        )
        now = datetime.now(timezone.utc)
        recomputed = 0

        for resume in resumes:
            resume_input = await self.advanced.resume_input(user, resume)
            resume_fp = resume_fingerprint(
                resume,
                intelligence_extractor_version=resume_input.extractor_version or "",
                intelligence_fingerprint=resume_input.fingerprint or "",
            )
            row = existing.get(resume.id)
            if (
                row is not None
                and not force
                and row.job_fingerprint == job_fp
                and row.resume_fingerprint == resume_fp
            ):
                continue
            result = self.advanced.evaluate(
                job,
                resume,
                profile=build_resume_profile(resume),
                job_input=job_input,
                resume_input=resume_input,
                as_of=as_of,
            )
            if row is None:
                row = JobResumeMatch(
                    user_id=user.id,
                    job_id=job.id,
                    resume_id=resume.id,
                    rank=1,
                    is_recommended=False,
                )
                self.session.add(row)
                existing[resume.id] = row
            _apply_result(
                row,
                result=result,
                job_fp=job_fp,
                resume_fp=resume_fp,
                computed_at=now,
            )
            recomputed += 1

        rows = list(existing.values())
        filename_by_id = {resume.id: resume.original_filename for resume in resumes}
        ordered = rank_results(
            [
                _RankableMatch(row, filename_by_id.get(row.resume_id, ""))
                for row in rows
            ]
        )
        for index, entry in enumerate(ordered, start=1):
            entry.row.rank = index
            entry.row.is_recommended = index == 1

        try:
            await self.session.commit()
        except IntegrityError:
            # A concurrent request inserted the same (job, resume) pair.
            await self.session.rollback()
            logger.warning(
                "Concurrent job match insert detected",
                extra={"stage": "job_matching", "job_id": str(job.id)},
            )
            return await self.get_job_matches(user, job)

        logger.info(
            "Job matching completed",
            extra={
                "stage": "job_matching",
                "user_id": str(user.id),
                "job_id": str(job.id),
                "resumes_evaluated": len(resumes),
                "recomputed": recomputed,
            },
        )
        return await self.get_job_matches(user, job)

    async def match_job(
        self,
        user: User,
        job_id: UUID,
        *,
        force: bool = False,
    ) -> tuple[Job, list[JobResumeMatch]]:
        job = await self.jobs.get_job(user, job_id)
        return job, await self.ensure_job_matches(user, job, force=force)

    async def bulk_match(
        self,
        user: User,
        *,
        job_ids: list[UUID] | None = None,
        limit: int = 25,
        force: bool = False,
    ) -> list[tuple[Job, list[JobResumeMatch]]]:
        """Match a bounded set of jobs; explicit ids must all be owned."""
        if job_ids:
            jobs = await self.jobs.get_jobs(user, job_ids)
        else:
            jobs = await self.jobs.list_jobs(user, limit=limit)
        results: list[tuple[Job, list[JobResumeMatch]]] = []
        for job in jobs:
            results.append((job, await self.ensure_job_matches(user, job, force=force)))
        return results

    # --- Summary ------------------------------------------------------------
    async def summary(self, user: User) -> dict[str, Any]:
        resumes = list(
            (
                await self.session.scalars(
                    select(Resume)
                    .where(Resume.user_id == user.id)
                    .order_by(Resume.is_default.desc(), Resume.created_at.desc())
                )
            ).all()
        )
        matches = list(
            (
                await self.session.scalars(
                    select(JobResumeMatch).where(JobResumeMatch.user_id == user.id)
                )
            ).all()
        )
        jobs_total = await self.jobs.count_jobs(user)
        recommended = [row for row in matches if row.is_recommended]
        jobs_matched = len({row.job_id for row in matches})
        average_best = (
            round(sum(row.overall_score for row in recommended) / len(recommended), 1)
            if recommended
            else None
        )
        last_computed = max((row.computed_at for row in matches), default=None)

        best_payload: dict[str, Any] | None = None
        if recommended:
            best = sorted(
                recommended,
                key=lambda row: (-row.overall_score, str(row.job_id)),
            )[0]
            job = await self.jobs.get_job(user, best.job_id)
            resume = next(
                (item for item in resumes if item.id == best.resume_id), None
            )
            best_payload = {
                "job_id": job.id,
                "job_title": job.job_title,
                "company": job.company,
                "resume_id": best.resume_id,
                "resume_filename": resume.original_filename if resume else None,
                "overall_score": best.overall_score,
            }

        stats: list[dict[str, Any]] = []
        for resume in resumes:
            own = [row for row in matches if row.resume_id == resume.id]
            recommended_count = sum(1 for row in own if row.is_recommended)
            stats.append(
                {
                    "resume_id": resume.id,
                    "resume_filename": resume.original_filename,
                    "is_default": resume.is_default,
                    "recommended_jobs": recommended_count,
                    "matches_count": len(own),
                    "average_score": (
                        round(sum(row.overall_score for row in own) / len(own), 1)
                        if own
                        else None
                    ),
                }
            )

        return {
            "jobs_total": jobs_total,
            "jobs_matched": jobs_matched,
            "resumes_total": len(resumes),
            "resumes_matched": len({row.resume_id for row in matches}),
            "average_best_score": average_best,
            "best_match": best_payload,
            "resume_stats": stats,
            "last_computed_at": last_computed,
        }


class _RankableMatch:
    """Small adapter giving ORM rows the fields ``rank_results`` sorts on."""

    __slots__ = ("filename", "row")

    def __init__(self, row: JobResumeMatch, filename: str) -> None:
        self.row = row
        self.filename = filename

    @property
    def overall_score(self) -> float:
        return self.row.overall_score

    @property
    def skill_score(self) -> float | None:
        return self.row.skill_score

    @property
    def resume_filename(self) -> str:
        return self.filename

    @property
    def resume_id(self) -> str:
        return str(self.row.resume_id)


__all__ = [
    "JobNotFoundError",
    "MatchingService",
    "build_resume_profile",
    "job_fingerprint",
    "requirements_for",
    "resume_fingerprint",
]
