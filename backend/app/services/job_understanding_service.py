"""Phase 2F.1 job understanding: caching and optional LLM enrichment.

Flow for one posting:

1. Deterministic extraction (``job_intelligence_extraction``) reads the stored
   posting and records evidence, provenance and contradictions.
2. When Groq is configured and the caller allows it, the existing
   ``JobDescriptionAnalysisAgent`` produces a validated structured reading.
3. The model reading may only fill fields the deterministic pass left empty, and
   only with claims verified against the posting text.
4. The result is cached per job until the posting text or extractor version
   changes. A failed LLM call never fails the request: deterministic analysis is
   returned and retried on the next explicit ``/analyze``.

Ownership is enforced by ``JobService``: a job owned by another account is
reported as missing, exactly like every other job endpoint.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.job_analysis import (
    JobDescriptionAnalysisAgent,
    JobDescriptionAnalysisInput,
    JobDescriptionAnalysisOutput,
)
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.integrations.llm.groq_client import GroqLLMClient
from app.models.job import Job
from app.models.job_intelligence import JobIntelligence
from app.models.user import User
from app.schemas.job_intelligence import (
    LLM_STATUS_DISABLED,
    LLM_STATUS_FAILED,
    LLM_STATUS_NO_DESCRIPTION,
    LLM_STATUS_NOT_ANALYZED,
    LLM_STATUS_NOT_CONFIGURED,
    LLM_STATUS_USED,
    JobIntelligenceFields,
    JobIntelligencePayload,
)
from app.services.job_intelligence_extraction import (
    EXTRACTOR_VERSION,
    JobUnderstanding,
    extract_job_understanding,
    merge_llm_understanding,
)
from app.services.job_service import JobService

logger = get_logger(__name__)

MIN_LLM_DESCRIPTION_CHARS = 50
LLM_DESCRIPTION_LIMIT = 20_000


class JobAnalysisAgentProtocol(Protocol):
    """Minimal agent contract required by this service (injectable in tests)."""

    async def execute(
        self, payload: JobDescriptionAnalysisInput
    ) -> JobDescriptionAnalysisOutput: ...


def job_intelligence_fingerprint(job: Job) -> str:
    """Identity of every input that can change the extracted understanding."""
    payload = "\x1f".join(
        (
            EXTRACTOR_VERSION,
            job.job_title or "",
            job.company or "",
            job.location or "",
            job.job_url or "",
            job.job_description or "",
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class JobIntelligenceResult:
    """One understanding of a posting, persisted or computed for this request."""

    fields: JobIntelligenceFields
    confidence: str
    provenance: dict[str, str] = field(default_factory=dict)
    field_confidence: dict[str, str] = field(default_factory=dict)
    contradictions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    dropped_llm_claims: list[str] = field(default_factory=list)
    supplemented_fields: list[str] = field(default_factory=list)
    llm_used: bool = False
    llm_status: str = LLM_STATUS_DISABLED
    llm_model: str | None = None
    extractor_version: str = EXTRACTOR_VERSION
    computed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    cached: bool = False


class JobUnderstandingService:
    """Deterministic job analysis with guarded, cached LLM assistance."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        agent: JobAnalysisAgentProtocol | None = None,
        agent_model: str | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.session = session
        self.jobs = JobService(session)
        self._agent = agent
        self._agent_model = agent_model
        self._settings = settings or get_settings()

    # --- Public API ---------------------------------------------------------
    async def analyze(
        self,
        user: User,
        job_id: UUID,
        *,
        force: bool = False,
        use_llm: bool = True,
    ) -> tuple[Job, JobIntelligenceResult]:
        """Analyse one owned job, reusing the cache unless ``force`` is set."""
        job = await self.jobs.get_job(user, job_id)
        return job, await self.ensure(user, job, force=force, use_llm=use_llm)

    async def get(
        self, user: User, job_id: UUID
    ) -> tuple[Job, JobIntelligenceResult]:
        """Read the stored understanding, or compute a non-persisted baseline."""
        job = await self.jobs.get_job(user, job_id)
        return job, await self.intelligence_for(user, job)

    async def intelligence_for(
        self, user: User, job: Job
    ) -> JobIntelligenceResult:
        """Return cached understanding or a deterministic-only, unsaved view.

        This path never calls the LLM and never writes, so a dashboard read
        cannot trigger an unexpected external request.
        """
        row = await self._get_row(user, job)
        if row is not None and row.job_fingerprint == job_intelligence_fingerprint(job):
            payload = self._load(row)
            if payload is not None:
                return self._result_from_payload(payload, row, cached=True)

        understanding = self._extract(job)
        return JobIntelligenceResult(
            fields=understanding.fields,
            confidence=understanding.confidence,
            provenance=dict(understanding.provenance),
            field_confidence=dict(understanding.field_confidence),
            contradictions=list(understanding.contradictions),
            warnings=list(understanding.warnings),
            dropped_llm_claims=list(understanding.dropped_llm_claims),
            supplemented_fields=list(understanding.supplemented_fields),
            llm_used=False,
            llm_status=LLM_STATUS_NOT_ANALYZED,
            llm_model=None,
            extractor_version=EXTRACTOR_VERSION,
            computed_at=datetime.now(timezone.utc),
            cached=False,
        )

    async def ensure(
        self,
        user: User,
        job: Job,
        *,
        force: bool = False,
        use_llm: bool = True,
    ) -> JobIntelligenceResult:
        """Return the cached analysis or compute and persist a fresh one."""
        fingerprint = job_intelligence_fingerprint(job)
        row = await self._get_row(user, job)

        if row is not None and not force and self._can_reuse(row, fingerprint, use_llm):
            payload = self._load(row)
            if payload is not None:
                return self._result_from_payload(payload, row, cached=True)

        understanding, llm_status, llm_warning = await self._understand(
            job, use_llm=use_llm
        )
        if llm_warning:
            understanding.warnings.append(llm_warning)

        now = datetime.now(timezone.utc)
        payload = JobIntelligencePayload(
            fields=understanding.fields,
            provenance=dict(understanding.provenance),
            field_confidence=dict(understanding.field_confidence),
            confidence=understanding.confidence,
            contradictions=list(understanding.contradictions),
            warnings=list(understanding.warnings),
            dropped_llm_claims=list(understanding.dropped_llm_claims),
            supplemented_fields=list(understanding.supplemented_fields),
        )

        if row is None:
            row = JobIntelligence(user_id=user.id, job_id=job.id)
            self.session.add(row)
        row.extractor_version = EXTRACTOR_VERSION
        row.job_fingerprint = fingerprint
        row.confidence = understanding.confidence
        row.llm_used = llm_status == LLM_STATUS_USED
        row.llm_status = llm_status
        row.llm_model = self._model_name() if row.llm_used else None
        row.data = payload.model_dump(mode="json")
        row.extracted_at = now

        try:
            await self.session.commit()
        except IntegrityError:
            # A concurrent request inserted the same job row first.
            await self.session.rollback()
            logger.warning(
                "Concurrent job intelligence insert detected",
                extra={"stage": "job_intelligence", "job_id": str(job.id)},
            )
            existing = await self._get_row(user, job)
            existing_payload = self._load(existing) if existing is not None else None
            if existing is not None and existing_payload is not None:
                return self._result_from_payload(
                    existing_payload, existing, cached=True
                )
            raise

        logger.info(
            "Job intelligence computed",
            extra={
                "stage": "job_intelligence",
                "user_id": str(user.id),
                "job_id": str(job.id),
                "llm_status": llm_status,
                "confidence": understanding.confidence,
            },
        )
        return self._result_from_payload(payload, row, cached=False)

    # --- Extraction and LLM enrichment --------------------------------------
    def _extract(self, job: Job) -> JobUnderstanding:
        return extract_job_understanding(
            job_title=job.job_title,
            company=job.company,
            location=job.location,
            description=job.job_description,
            job_url=job.job_url,
        )

    async def _understand(
        self, job: Job, *, use_llm: bool
    ) -> tuple[JobUnderstanding, str, str | None]:
        base = self._extract(job)
        if not use_llm:
            return base, LLM_STATUS_DISABLED, None

        description = (job.job_description or "").strip()
        if len(description) < MIN_LLM_DESCRIPTION_CHARS:
            return base, LLM_STATUS_NO_DESCRIPTION, None
        if not self._llm_configured():
            return base, LLM_STATUS_NOT_CONFIGURED, None

        try:
            output = await self._run_agent(job, description)
        except Exception as exc:  # the LLM must never fail the analysis
            # Only the exception class is logged/stored: upstream messages can
            # contain request details that must not reach a user or a log sink.
            logger.warning(
                "Job intelligence LLM enrichment failed",
                extra={
                    "stage": "job_intelligence",
                    "job_id": str(job.id),
                    "error": type(exc).__name__,
                },
            )
            return (
                base,
                LLM_STATUS_FAILED,
                f"LLM enrichment failed ({type(exc).__name__}); deterministic "
                "analysis was used.",
            )

        merged = merge_llm_understanding(
            base, output, description=job.job_description or description
        )
        return merged, LLM_STATUS_USED, None

    async def _run_agent(
        self, job: Job, description: str
    ) -> JobDescriptionAnalysisOutput:
        if self._agent is None:
            self._agent = JobDescriptionAnalysisAgent(
                llm=GroqLLMClient(settings=self._settings)
            )
        payload = JobDescriptionAnalysisInput(
            job_description=description[:LLM_DESCRIPTION_LIMIT],
            job_title=job.job_title,
            company=job.company,
        )
        return await self._agent.execute(payload)

    def _llm_configured(self) -> bool:
        if self._agent is not None:
            return True
        return bool(self._settings.GROQ_API_KEY)

    def _model_name(self) -> str | None:
        return self._agent_model or self._settings.GROQ_MODEL

    # --- Cache helpers ------------------------------------------------------
    async def _get_row(
        self, user: User, job: Job
    ) -> JobIntelligence | None:
        return await self.session.scalar(
            select(JobIntelligence).where(
                JobIntelligence.user_id == user.id,
                JobIntelligence.job_id == job.id,
            )
        )

    def _can_reuse(
        self, row: JobIntelligence, fingerprint: str, use_llm: bool
    ) -> bool:
        if row.job_fingerprint != fingerprint:
            return False
        if not use_llm:
            return True
        if row.llm_used:
            return True
        if row.llm_status == LLM_STATUS_NOT_CONFIGURED:
            # Retry once a key becomes available; otherwise reuse the baseline.
            return not self._llm_configured()
        # A missing description cannot change while the fingerprint matches.
        # failed / disabled: an explicit analyze request with LLM allowed retries.
        return row.llm_status == LLM_STATUS_NO_DESCRIPTION

    @staticmethod
    def _load(row: JobIntelligence) -> JobIntelligencePayload | None:
        try:
            return JobIntelligencePayload.model_validate(row.data or {})
        except ValidationError:
            logger.warning(
                "Stored job intelligence is unreadable; recomputing",
                extra={
                    "stage": "job_intelligence",
                    "job_id": str(row.job_id),
                },
            )
            return None

    @staticmethod
    def _result_from_payload(
        payload: JobIntelligencePayload,
        row: JobIntelligence,
        *,
        cached: bool,
    ) -> JobIntelligenceResult:
        return JobIntelligenceResult(
            fields=payload.fields,
            confidence=payload.confidence,
            provenance=dict(payload.provenance),
            field_confidence=dict(payload.field_confidence),
            contradictions=list(payload.contradictions),
            warnings=list(payload.warnings),
            dropped_llm_claims=list(payload.dropped_llm_claims),
            supplemented_fields=list(payload.supplemented_fields),
            llm_used=row.llm_used,
            llm_status=row.llm_status,
            llm_model=row.llm_model,
            extractor_version=row.extractor_version,
            computed_at=row.extracted_at,
            cached=cached,
        )


__all__ = [
    "JobAnalysisAgentProtocol",
    "JobIntelligenceResult",
    "JobUnderstandingService",
    "job_intelligence_fingerprint",
]
