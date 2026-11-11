"""Phase 2F.2.4 multi-resume comparison for one owned job.

This service does not score anything itself. It resolves the user's resumes,
reuses the persisted Phase 2F.2.3 matches produced by ``MatchingService`` (the
same evidence engine and the same cache), classifies each resume into an explicit
processing state, ranks the comparable results deterministically and delegates
the recommendation to :class:`ResumeRecommendationService`.

Only records owned by the authenticated user are read; the job lookup already
reports another account's posting as missing. Resume titles are the display
filenames the existing resume API already exposes - storage keys, file paths and
raw document content are never returned or logged.

Partial availability is normal: a resume whose text could not be extracted, whose
analysis failed or that could not be linked to a stored match is reported with a
reason and excluded from ranking instead of failing the whole comparison.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.job import Job
from app.models.job_match import JobResumeMatch
from app.models.resume import Resume
from app.models.user import User
from app.services.advanced_matching_engine import (
    MATCHING_ALGORITHM_VERSION,
    STATE_INSUFFICIENT,
    STATE_READY,
    STATE_RESUME_FAILED,
    STATUS_CURRENT,
    STATUS_FAILED,
    STATUS_MISSING,
    canonical_skill_key,
)
from app.services.advanced_matching_service import AdvancedMatchingService
from app.services.job_requirements import JobRequirements
from app.services.job_service import JobService
from app.services.matching_service import MatchingService
from app.services.resume_recommendation_service import (
    DEFAULT_CLOSE_SCORE_THRESHOLD,
    RecommendationOutcome,
    ResumeRecommendationService,
)

logger = get_logger(__name__)

#: Per-resume availability states. Uppercase to match the phase specification;
#: the engine's lowercase ``processing_state`` is kept separately.
RESUME_READY = "READY"
RESUME_PARTIAL = "PARTIAL"
RESUME_ANALYSIS_REQUIRED = "ANALYSIS_REQUIRED"
RESUME_EXTRACTION_FAILED = "EXTRACTION_FAILED"
RESUME_INVALID = "INVALID"
RESUME_INACCESSIBLE = "INACCESSIBLE"

RESUME_STATES = (
    RESUME_READY,
    RESUME_PARTIAL,
    RESUME_ANALYSIS_REQUIRED,
    RESUME_EXTRACTION_FAILED,
    RESUME_INVALID,
    RESUME_INACCESSIBLE,
)

#: States whose stored compatibility can participate in ranking.
RANKABLE_STATES = frozenset(
    {RESUME_READY, RESUME_PARTIAL, RESUME_ANALYSIS_REQUIRED}
)

#: States that are reported but excluded from ranking, with a reason.
UNCOMPARABLE_STATES = frozenset(
    {RESUME_EXTRACTION_FAILED, RESUME_INVALID, RESUME_INACCESSIBLE}
)

#: Match states that mean the engine found no usable resume evidence.
UNSUITABLE_MATCH_STATES = frozenset({STATE_INSUFFICIENT, STATE_RESUME_FAILED})

COMPARISON_COMPLETED = "completed"
COMPARISON_PARTIAL = "partial"
COMPARISON_NO_SUITABLE = "no_suitable_resume"
COMPARISON_NO_RESUMES = "no_resumes"
COMPARISON_NOT_COMPUTED = "not_computed"

CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1}

COMPARISON_DISCLAIMER = (
    "Compatibility scores describe overlap between a resume and the posting's "
    "stated requirements. They are not hiring probabilities, and a recommendation "
    "is only a comparison of the resumes on file."
)


def _confidence_rank(value: str | None) -> int:
    return CONFIDENCE_RANK.get((value or "").lower(), 0)


def _has_usable_content(resume: Resume) -> bool:
    """Whether any stored resume evidence exists for the engine to read."""
    parsed = resume.parsed_data if isinstance(resume.parsed_data, dict) else {}
    return bool(parsed) or bool(resume.skills)


def _required_coverage(
    row: JobResumeMatch, requirements: JobRequirements
) -> tuple[float | None, int | None, int | None]:
    """Project the stored match onto the posting's required-skill coverage.

    The engine already recorded which required skills it matched; the posting's
    effective requirements give the denominator. This is a projection of stored
    results, not a second scoring pass.
    """
    required = {canonical_skill_key(skill) for skill in requirements.required_skills}
    required.discard("")
    if not required:
        return None, None, None
    matched = {
        canonical_skill_key(skill) for skill in (row.matched_required_skills or [])
    }
    overlap = len(required & matched)
    return round(overlap / len(required), 4), overlap, len(required)


@dataclass(slots=True)
class ComparisonEntry:
    """One resume's comparable (or explicitly unavailable) comparison result."""

    resume: Resume
    state: str
    reason: str
    row: JobResumeMatch | None = None
    rank: int | None = None
    required_skill_coverage: float | None = None
    required_skills_matched: int | None = None
    required_skills_total: int | None = None
    is_recommended: bool = False

    @property
    def resume_id(self) -> UUID:
        return self.resume.id

    @property
    def resume_title(self) -> str:
        # The filename is already public in the resume and match APIs.
        return self.resume.original_filename

    @property
    def overall_score(self) -> float | None:
        return self.row.overall_score if self.row is not None else None

    @property
    def evidence_confidence(self) -> str | None:
        return self.row.confidence if self.row is not None else None

    @property
    def match_processing_state(self) -> str | None:
        if self.row is None or not isinstance(self.row.details, dict):
            return None
        return self.row.details.get("processing_state")


@dataclass(slots=True)
class ComparisonOutcome:
    """Everything one comparison or recommendation read returns."""

    job_id: UUID
    status: str
    total_resumes: int
    compared_resumes: int
    entries: list[ComparisonEntry] = field(default_factory=list)
    recommendation: RecommendationOutcome | None = None
    close_score: bool = False
    close_score_threshold: float = DEFAULT_CLOSE_SCORE_THRESHOLD
    processing_failures: list[ComparisonEntry] = field(default_factory=list)
    algorithm_version: str = MATCHING_ALGORITHM_VERSION
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    message: str | None = None
    disclaimer: str = COMPARISON_DISCLAIMER


def _rank_key(entry: ComparisonEntry) -> tuple[Any, ...]:
    """Deterministic comparison order.

    Tie-break chain, documented in ``phase2f-2-4-resume-comparison.md``:

    1. overall compatibility score (descending);
    2. required-skill coverage (descending);
    3. experience score (descending);
    4. project score (descending);
    5. education score (descending);
    6. evidence confidence (high > medium > low);
    7. resume creation date (oldest first);
    8. resume id (lexicographic) as the final stable key.

    ``None`` ranks below any present value. The keys never depend on request
    order, so repeated comparisons return the same ranking.
    """
    row = entry.row
    return (
        -(entry.overall_score if entry.overall_score is not None else -1.0),
        -(entry.required_skill_coverage if entry.required_skill_coverage is not None else -1.0),
        -(row.experience_score if row is not None and row.experience_score is not None else -1.0),
        -(row.project_score if row is not None and row.project_score is not None else -1.0),
        -(row.education_score if row is not None and row.education_score is not None else -1.0),
        -_confidence_rank(entry.evidence_confidence),
        entry.resume.created_at,
        str(entry.resume.id),
    )


def _unavailable_key(entry: ComparisonEntry) -> tuple[Any, ...]:
    return (entry.resume_title.casefold(), str(entry.resume.id))


class ResumeComparisonService:
    """Compares every eligible owned resume against one owned job."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        close_score_threshold: float = DEFAULT_CLOSE_SCORE_THRESHOLD,
    ) -> None:
        self.session = session
        self.jobs = JobService(session)
        self.matching = MatchingService(session)
        self.advanced = AdvancedMatchingService(session)
        self.recommender = ResumeRecommendationService(
            close_score_threshold=close_score_threshold
        )

    # --- Reads --------------------------------------------------------------
    async def _owned_resumes(self, user: User) -> list[Resume]:
        rows = await self.session.scalars(
            select(Resume)
            .where(Resume.user_id == user.id)
            .order_by(Resume.created_at.asc(), Resume.id.asc())
        )
        return list(rows.all())

    # --- Public API ---------------------------------------------------------
    async def compare(
        self,
        user: User,
        job_id: UUID,
        *,
        force: bool = False,
        as_of: date | None = None,
    ) -> ComparisonOutcome:
        """Compare all owned resumes, reusing cached match rows when fresh."""
        job = await self.jobs.get_job(user, job_id)
        resumes = await self._owned_resumes(user)
        generated_at = datetime.now(timezone.utc)
        if not resumes:
            return ComparisonOutcome(
                job_id=job.id,
                status=COMPARISON_NO_RESUMES,
                total_resumes=0,
                compared_resumes=0,
                close_score_threshold=self.recommender.close_score_threshold,
                algorithm_version=MATCHING_ALGORITHM_VERSION,
                generated_at=generated_at,
                message="Upload a resume to compare it against this job.",
            )

        job_input = await self.advanced.job_input(user, job)
        requirements = self.advanced.effective_requirements(job, job_input)
        rows = await self._load_rows(user, job, force=force, as_of=as_of)
        entries = [
            await self._build_entry(
                user,
                resume,
                rows.get(resume.id),
                requirements,
            )
            for resume in resumes
        ]
        return self._finalize(
            job=job,
            entries=entries,
            generated_at=generated_at,
            status_when_complete=COMPARISON_COMPLETED,
        )

    async def read(self, user: User, job_id: UUID) -> ComparisonOutcome:
        """Build the response from stored matches only; never recompute."""
        job = await self.jobs.get_job(user, job_id)
        resumes = await self._owned_resumes(user)
        generated_at = datetime.now(timezone.utc)
        if not resumes:
            return ComparisonOutcome(
                job_id=job.id,
                status=COMPARISON_NO_RESUMES,
                total_resumes=0,
                compared_resumes=0,
                close_score_threshold=self.recommender.close_score_threshold,
                algorithm_version=MATCHING_ALGORITHM_VERSION,
                generated_at=generated_at,
                message="Upload a resume to compare it against this job.",
            )

        job_input = await self.advanced.job_input(user, job)
        requirements = self.advanced.effective_requirements(job, job_input)
        stored = await self.matching.get_job_matches(user, job)
        if not stored:
            entries = [
                ComparisonEntry(
                    resume=resume,
                    state=RESUME_ANALYSIS_REQUIRED,
                    reason=(
                        "No stored comparison exists for this resume yet; run the "
                        "compare request to compute one."
                    ),
                )
                for resume in resumes
            ]
            return ComparisonOutcome(
                job_id=job.id,
                status=COMPARISON_NOT_COMPUTED,
                total_resumes=len(resumes),
                compared_resumes=0,
                entries=entries,
                close_score_threshold=self.recommender.close_score_threshold,
                algorithm_version=MATCHING_ALGORITHM_VERSION,
                generated_at=generated_at,
                message=(
                    "No comparison has been computed for this job yet. Run "
                    "POST /jobs/{job_id}/compare-resumes to compute one."
                ),
            )

        by_resume = {row.resume_id: row for row in stored}
        entries = [
            await self._build_entry(user, resume, by_resume.get(resume.id), requirements)
            for resume in resumes
        ]
        return self._finalize(
            job=job,
            entries=entries,
            generated_at=generated_at,
            status_when_complete=COMPARISON_COMPLETED,
        )

    # --- Internals ----------------------------------------------------------
    async def _load_rows(
        self,
        user: User,
        job: Job,
        *,
        force: bool,
        as_of: date | None,
    ) -> dict[UUID, JobResumeMatch]:
        """Use the persisted, fingerprint-checked match cache.

        A failure here must not lose the comparison: previously stored matches are
        returned instead and resumes without one are reported as inaccessible.
        """
        try:
            rows = await self.matching.ensure_job_matches(
                user, job, force=force, as_of=as_of
            )
        except Exception:  # defensive: one request must not fail every resume
            logger.warning(
                "Resume comparison fell back to stored matches",
                extra={"stage": "resume_comparison", "job_id": str(job.id)},
            )
            rows = await self.matching.get_job_matches(user, job)
        return {row.resume_id: row for row in rows}

    async def _build_entry(
        self,
        user: User,
        resume: Resume,
        row: JobResumeMatch | None,
        requirements: JobRequirements,
    ) -> ComparisonEntry:
        """Classify one resume and attach its persisted comparison result."""
        try:
            resume_input = await self.advanced.resume_input(user, resume)
        except Exception:  # pragma: no cover - defensive
            resume_input = None

        usable = _has_usable_content(resume)
        if not usable:
            if resume_input is not None and resume_input.state == STATUS_FAILED:
                code = resume_input.error_code
                reason = "Resume analysis failed and no usable resume content remains."
                if code:
                    reason = (
                        "Resume analysis failed and no usable resume content "
                        f"remains ({code})."
                    )
                return ComparisonEntry(
                    resume=resume, state=RESUME_EXTRACTION_FAILED, reason=reason
                )
            return ComparisonEntry(
                resume=resume,
                state=RESUME_INVALID,
                reason=(
                    "No usable resume content was extracted "
                    f"(parsing status: {resume.parsing_status.value})."
                ),
            )

        if row is None:
            return ComparisonEntry(
                resume=resume,
                state=RESUME_INACCESSIBLE,
                reason=(
                    "A compatibility result could not be produced for this resume "
                    "in this request."
                ),
            )

        state: str
        if resume_input is not None and resume_input.state == STATUS_FAILED:
            state = RESUME_PARTIAL
            reason = (
                "Resume analysis failed; compatibility uses the stored parsed "
                "resume data instead."
            )
        elif resume_input is not None and resume_input.state == STATUS_CURRENT:
            if self._match_state(row) == STATE_READY:
                state = RESUME_READY
                reason = "Resume analysis and job analysis are current."
            else:
                state = RESUME_PARTIAL
                reason = (
                    "Compatibility was computed with partially available "
                    "intelligence; see the match details for which input is missing."
                )
        elif resume_input is not None and resume_input.state == STATUS_MISSING:
            state = RESUME_ANALYSIS_REQUIRED
            reason = (
                "Resume analysis has not been run; compatibility uses the stored "
                "parsed resume data."
            )
        else:
            state = RESUME_PARTIAL
            reason = (
                "The stored resume analysis is out of date; compatibility uses the "
                "stored parsed resume data."
            )

        coverage, matched, total = _required_coverage(row, requirements)
        return ComparisonEntry(
            resume=resume,
            state=state,
            reason=reason,
            row=row,
            required_skill_coverage=coverage,
            required_skills_matched=matched,
            required_skills_total=total,
        )

    @staticmethod
    def _match_state(row: JobResumeMatch) -> str | None:
        if not isinstance(row.details, dict):
            return None
        return row.details.get("processing_state")

    def _finalize(
        self,
        *,
        job: Job,
        entries: list[ComparisonEntry],
        generated_at: datetime,
        status_when_complete: str,
    ) -> ComparisonOutcome:
        ranked = sorted(
            [entry for entry in entries if entry.state in RANKABLE_STATES],
            key=_rank_key,
        )
        for index, entry in enumerate(ranked, start=1):
            entry.rank = index
        unavailable = sorted(
            [entry for entry in entries if entry.state in UNCOMPARABLE_STATES],
            key=_unavailable_key,
        )

        recommendation = self.recommender.recommend(ranked)
        for entry in ranked:
            entry.is_recommended = (
                recommendation.recommended is not None
                and entry.resume_id == recommendation.recommended.resume_id
            )

        if recommendation.recommended is None:
            status = COMPARISON_NO_SUITABLE
            message = (
                recommendation.unavailable_reason
                or "No definitive recommendation is available."
            )
        elif unavailable:
            status = COMPARISON_PARTIAL
            message = (
                f"{len(unavailable)} of {len(entries)} resumes could not be "
                "compared; see processing_failures for the reasons."
            )
        else:
            status = status_when_complete
            message = None

        return ComparisonOutcome(
            job_id=job.id,
            status=status,
            total_resumes=len(entries),
            compared_resumes=len(ranked),
            entries=[*ranked, *unavailable],
            recommendation=recommendation,
            close_score=recommendation.close_call,
            close_score_threshold=self.recommender.close_score_threshold,
            processing_failures=unavailable,
            algorithm_version=MATCHING_ALGORITHM_VERSION,
            generated_at=generated_at,
            message=message,
        )


__all__ = [
    "COMPARISON_COMPLETED",
    "COMPARISON_DISCLAIMER",
    "COMPARISON_NOT_COMPUTED",
    "COMPARISON_NO_RESUMES",
    "COMPARISON_NO_SUITABLE",
    "COMPARISON_PARTIAL",
    "RANKABLE_STATES",
    "RESUME_ANALYSIS_REQUIRED",
    "RESUME_EXTRACTION_FAILED",
    "RESUME_INACCESSIBLE",
    "RESUME_INVALID",
    "RESUME_PARTIAL",
    "RESUME_READY",
    "RESUME_STATES",
    "UNCOMPARABLE_STATES",
    "ComparisonEntry",
    "ComparisonOutcome",
    "ResumeComparisonService",
]
