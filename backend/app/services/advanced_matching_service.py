"""Phase 2F.2.3 orchestration between stored intelligence and the match engine.

The service resolves *owned* Phase 2F records for one job and one resume and
tells the engine whether they are current:

* a job intelligence row is current when its extractor version and posting
  fingerprint match the stored posting;
* a resume intelligence row is current when its extractor version and content
  fingerprint match the stored resume, and its extraction completed.

It never generates intelligence here: a missing or stale record is reported as
such so the engine can fall back to deterministic/parsed data and label the
result as ``partial``. LLM assistance is never required for matching.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.job import Job
from app.models.job_intelligence import JobIntelligence
from app.models.resume import Resume
from app.models.resume_intelligence import (
    STATUS_COMPLETED as ROW_STATUS_COMPLETED,
)
from app.models.resume_intelligence import (
    STATUS_FAILED as ROW_STATUS_FAILED,
)
from app.models.resume_intelligence import (
    ResumeIntelligence,
)
from app.models.user import User
from app.schemas.job_intelligence import JobIntelligencePayload
from app.schemas.resume_intelligence import ResumeIntelligencePayload
from app.services.advanced_matching_engine import (
    STATUS_CURRENT,
    STATUS_FAILED,
    STATUS_MISSING,
    STATUS_STALE,
    AdvancedMatchResult,
    match_resume_with_evidence,
    merge_requirements,
)
from app.services.job_intelligence_extraction import (
    EXTRACTOR_VERSION as JOB_INTELLIGENCE_EXTRACTOR_VERSION,
)
from app.services.job_requirements import JobRequirements, build_job_requirements
from app.services.job_understanding_service import job_intelligence_fingerprint
from app.services.matching_engine import ResumeProfile
from app.services.resume_intelligence_extraction import (
    EXTRACTOR_VERSION as RESUME_INTELLIGENCE_EXTRACTOR_VERSION,
)
from app.services.resume_intelligence_service import resume_content_fingerprint

logger = get_logger(__name__)


@dataclass(slots=True, frozen=True)
class JobIntelligenceInput:
    """One job's current (or missing/stale) Phase 2F.1 understanding."""

    state: str
    payload: JobIntelligencePayload | None = None
    extractor_version: str | None = None
    fingerprint: str | None = None


@dataclass(slots=True, frozen=True)
class ResumeIntelligenceInput:
    """One resume's current, stale, missing or failed Phase 2F.2 reading."""

    state: str
    payload: ResumeIntelligencePayload | None = None
    extractor_version: str | None = None
    fingerprint: str | None = None
    status: str | None = None
    error_code: str | None = None


class AdvancedMatchingService:
    """Resolves intelligence inputs and evaluates one resume against one job."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- Inputs -------------------------------------------------------------
    async def job_input(self, user: User, job: Job) -> JobIntelligenceInput:
        """Return the user's job intelligence, marked current only when valid."""
        row = await self.session.scalar(
            select(JobIntelligence).where(
                JobIntelligence.user_id == user.id,
                JobIntelligence.job_id == job.id,
            )
        )
        if row is None:
            return JobIntelligenceInput(state=STATUS_MISSING)

        stale = JobIntelligenceInput(
            state=STATUS_STALE,
            extractor_version=row.extractor_version,
            fingerprint=row.job_fingerprint,
        )
        if row.extractor_version != JOB_INTELLIGENCE_EXTRACTOR_VERSION:
            return stale
        if row.job_fingerprint != job_intelligence_fingerprint(job):
            return stale
        try:
            payload = JobIntelligencePayload.model_validate(row.data or {})
        except ValidationError:
            logger.warning(
                "Stored job intelligence is unreadable; using deterministic requirements",
                extra={"stage": "advanced_matching", "job_id": str(job.id)},
            )
            return stale
        return JobIntelligenceInput(
            state=STATUS_CURRENT,
            payload=payload,
            extractor_version=row.extractor_version,
            fingerprint=row.job_fingerprint,
        )

    async def resume_input(
        self, user: User, resume: Resume
    ) -> ResumeIntelligenceInput:
        """Return the newest stored reading, marked current only when reusable."""
        row = await self.session.scalar(
            select(ResumeIntelligence)
            .where(
                ResumeIntelligence.user_id == user.id,
                ResumeIntelligence.resume_id == resume.id,
            )
            .order_by(ResumeIntelligence.extracted_at.desc())
            .limit(1)
        )
        if row is None:
            return ResumeIntelligenceInput(state=STATUS_MISSING)

        metadata = {
            "extractor_version": row.extractor_version,
            "fingerprint": row.content_fingerprint,
            "status": row.extraction_status,
            "error_code": row.error_code,
        }
        if (
            row.extractor_version != RESUME_INTELLIGENCE_EXTRACTOR_VERSION
            or row.content_fingerprint != resume_content_fingerprint(resume)
        ):
            return ResumeIntelligenceInput(state=STATUS_STALE, **metadata)
        if row.extraction_status == ROW_STATUS_FAILED:
            return ResumeIntelligenceInput(state=STATUS_FAILED, **metadata)
        if row.extraction_status != ROW_STATUS_COMPLETED:
            return ResumeIntelligenceInput(state=STATUS_STALE, **metadata)
        try:
            payload = ResumeIntelligencePayload.model_validate(
                row.intelligence_data or {}
            )
        except ValidationError:
            logger.warning(
                "Stored resume intelligence is unreadable; using parsed resume data",
                extra={
                    "stage": "advanced_matching",
                    "resume_id": str(resume.id),
                },
            )
            return ResumeIntelligenceInput(state=STATUS_STALE, **metadata)
        return ResumeIntelligenceInput(
            state=STATUS_CURRENT, payload=payload, **metadata
        )

    # --- Evaluation ---------------------------------------------------------
    def effective_requirements(
        self, job: Job, job_input: JobIntelligenceInput
    ) -> JobRequirements:
        """Build matching requirements, letting current intelligence fill gaps."""
        requirements = build_job_requirements(
            job_title=job.job_title,
            company=job.company,
            location=job.location,
            description=job.job_description,
        )
        fields = (
            job_input.payload.fields
            if job_input.state == STATUS_CURRENT and job_input.payload is not None
            else None
        )
        return merge_requirements(requirements, fields)

    def evaluate(
        self,
        job: Job,
        resume: Resume,
        *,
        profile: ResumeProfile,
        job_input: JobIntelligenceInput,
        resume_input: ResumeIntelligenceInput,
        as_of: date,
    ) -> AdvancedMatchResult:
        """Explain one owned resume against one owned job."""
        return match_resume_with_evidence(
            self.effective_requirements(job, job_input),
            profile,
            as_of=as_of,
            job_intelligence=job_input.payload,
            job_intelligence_current=job_input.state == STATUS_CURRENT,
            job_intelligence_state=job_input.state,
            resume_intelligence=resume_input.payload,
            resume_intelligence_current=resume_input.state == STATUS_CURRENT,
            resume_intelligence_state=resume_input.state,
            resume_error_code=resume_input.error_code,
        )


__all__ = [
    "AdvancedMatchingService",
    "JobIntelligenceInput",
    "ResumeIntelligenceInput",
]
