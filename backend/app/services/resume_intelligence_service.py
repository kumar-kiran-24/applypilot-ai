"""Phase 2F.2 resume intelligence: fingerprinting, persistence and caching.

Flow for one resume:

1. Ownership is enforced by ``ResumeService``: a resume owned by another
   account is reported as missing, exactly like every other resume endpoint.
2. A fingerprint of the extraction inputs (the stored PDF checksum, the parsed
   data and the skill list) is computed without reading the file, so a cache hit
   never touches storage and never repeats parsing.
3. A completed row for the same fingerprint and extractor version is returned
   as-is. Changing the resume content or the extractor version produces a new
   row; an explicit ``force`` re-runs the extraction even when the cache is
   valid.
4. A failed extraction never deletes a previous successful row. If one exists
   for the same content it is returned with a warning; otherwise the failure is
   recorded with a safe error code and no data.

Concurrency is handled by a per-resume in-process lock (one worker) plus the
unique constraint on ``(resume_id, extractor_version, content_fingerprint)``
(many workers): a losing insert is rolled back and the winner's row is returned.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.resume import Resume
from app.models.resume_intelligence import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    ResumeIntelligence,
)
from app.models.user import User
from app.schemas.resume_intelligence import (
    CONFIDENCE_LOW,
    ResumeIntelligenceFields,
    ResumeIntelligencePayload,
)
from app.services.resume_intelligence_extraction import (
    EXTRACTOR_VERSION,
    ResumeUnderstanding,
    extract_resume_intelligence,
)
from app.services.resume_parser import (
    ResumeExtractionError,
    ResumeOcrRequiredError,
    extract_text_from_pdf,
)
from app.services.resume_service import ResumeService
from app.services.resume_storage import (
    ResumeStorage,
    ResumeStorageError,
    get_resume_storage,
)

logger = get_logger(__name__)

#: The stored file is gone (deleted outside the app, or a broken storage root).
ERROR_FILE_MISSING = "resume_file_missing"
#: The file exists but the storage backend could not return it.
ERROR_FILE_UNAVAILABLE = "resume_file_unavailable"
#: The PDF could not be read at all.
ERROR_PDF_UNREADABLE = "pdf_unreadable"
#: The PDF has no selectable text, so OCR would be required.
ERROR_OCR_REQUIRED = "ocr_required"
#: The file could not be turned into text for an unexpected reason.
ERROR_TEXT_UNAVAILABLE = "resume_text_unavailable"
#: The extractor itself failed.
ERROR_EXTRACTION_FAILED = "extraction_failed"

#: Bumped whenever the fingerprint inputs change shape, not the extractor.
FINGERPRINT_FORMAT = "resume-intelligence-inputs-v1"

#: API-facing analysis states. ``ready`` and ``failed`` describe a stored row
#: for the current content; ``stale`` and ``missing`` describe a reading that was
#: computed for the response only because no current row was available.
STATE_READY = "ready"
STATE_FAILED = "failed"
STATE_STALE = "stale"
STATE_MISSING = "missing"
ANALYSIS_STATES = frozenset({STATE_READY, STATE_FAILED, STATE_STALE, STATE_MISSING})

#: One lock per (user, resume) so a single worker never extracts twice.
_locks: dict[str, asyncio.Lock] = {}


def _analysis_lock(user_id: UUID, resume_id: UUID) -> asyncio.Lock:
    """Return the process-local lock guarding one resume's analysis."""
    key = f"{user_id}:{resume_id}"
    lock = _locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _locks[key] = lock
    return lock


def resume_content_fingerprint(resume: Resume) -> str:
    """Return a stable SHA-256 of every input the extraction reads.

    The stored PDF checksum, the parsed data and the skill list identify the
    content, so the fingerprint is independent of the file path, the storage
    backend and the upload time. Unreadable/missing values are canonicalised to
    empty containers, which keeps the fingerprint of a resume whose file was
    lost identical to the one its successful analysis was computed from.
    """
    parsed = resume.parsed_data if isinstance(resume.parsed_data, dict) else {}
    skills = resume.skills if isinstance(resume.skills, list) else []
    payload = json.dumps(
        {
            "format": FINGERPRINT_FORMAT,
            "checksum": resume.checksum or "",
            "parsed_data": parsed,
            "skills": skills,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class ResumeIntelligenceResult:
    """One analysis of a resume, persisted or computed for this request."""

    fields: ResumeIntelligenceFields
    confidence: str
    provenance: dict[str, str] = field(default_factory=dict)
    field_confidence: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    status: str = STATUS_COMPLETED
    error_code: str | None = None
    extractor_version: str = EXTRACTOR_VERSION
    computed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    cached: bool = False
    #: One of ``ANALYSIS_STATES``; how this reading relates to the stored cache.
    analysis_state: str = STATE_READY

    @property
    def succeeded(self) -> bool:
        """True when a usable reading exists (possibly in degraded mode)."""
        return self.status == STATUS_COMPLETED


def _failure_warning(error_code: str) -> str:
    return (
        "Resume intelligence could not be extracted from this resume "
        f"({error_code}). Nothing was inferred from the missing content."
    )


def _degraded_warning(error_code: str) -> str:
    return (
        "The original resume file could not be read "
        f"({error_code}); only the stored parsed data was used."
    )


def _is_empty(understanding: ResumeUnderstanding) -> bool:
    """True when nothing at all could be extracted."""
    fields = understanding.fields
    return not any(
        (
            fields.summary,
            fields.skills,
            fields.experience,
            fields.projects,
            fields.education,
            fields.certifications,
        )
    )


class ResumeIntelligenceService:
    """Deterministic resume intelligence with a content-fingerprinted cache."""

    def __init__(
        self,
        session: AsyncSession,
        storage: ResumeStorage | None = None,
    ) -> None:
        self.session = session
        self.storage = storage or get_resume_storage()
        self.resumes = ResumeService(session, self.storage)

    # --- Public API ---------------------------------------------------------
    async def analyze_resume(
        self,
        user: User,
        resume_id: UUID,
        *,
        force: bool = False,
    ) -> tuple[Resume, ResumeIntelligenceResult]:
        """Analyse one owned resume, reusing the cache unless ``force`` is set."""
        async with _analysis_lock(user.id, resume_id):
            resume = await self.resumes.get_resume(user, resume_id)
            fingerprint = resume_content_fingerprint(resume)
            if not force:
                cached = await self._completed_result(user, resume, fingerprint)
                if cached is not None:
                    return resume, cached
            return resume, await self._compute_and_store(user, resume, fingerprint)

    async def refresh_resume_intelligence(
        self, user: User, resume_id: UUID
    ) -> tuple[Resume, ResumeIntelligenceResult]:
        """Recompute one owned resume even when the cached reading is valid."""
        return await self.analyze_resume(user, resume_id, force=True)

    async def get_resume_intelligence(
        self, user: User, resume_id: UUID
    ) -> tuple[Resume, ResumeIntelligenceResult]:
        """Read one owned resume's analysis without writing anything.

        A stored completed row for the current content is returned; a recorded
        failure is surfaced instead of being retried on every read; otherwise
        the deterministic analysis is computed for this response only, so a
        dashboard read cannot trigger an unexpected write.
        """
        resume = await self.resumes.get_resume(user, resume_id)
        row = await self._current_row(
            user.id, resume.id, resume_content_fingerprint(resume)
        )
        if row is not None:
            result = self._result_from_row(row, cached=True)
            if result is not None:
                # A current row may be a completed reading or a recorded failure.
                result.analysis_state = (
                    STATE_READY if result.succeeded else STATE_FAILED
                )
                return resume, result
        result = await self._compute(resume)
        # The reading below was computed for this response only. Say whether an
        # older stored reading exists so a client can tell "never analysed" from
        # "stored analysis is out of date".
        has_older = await self._has_any_row(user.id, resume.id)
        result.analysis_state = STATE_STALE if has_older else STATE_MISSING
        return resume, result

    async def invalidate_resume_intelligence(self, user: User, resume_id: UUID) -> int:
        """Delete every cached reading of one owned resume; return the count."""
        resume = await self.resumes.get_resume(user, resume_id)
        deleted = await self.session.execute(
            delete(ResumeIntelligence).where(
                ResumeIntelligence.user_id == user.id,
                ResumeIntelligence.resume_id == resume.id,
            )
        )
        await self.session.commit()
        count = int(deleted.rowcount or 0)
        logger.info(
            "Resume intelligence invalidated",
            extra={
                "stage": "resume_intelligence",
                "user_id": str(user.id),
                "resume_id": str(resume.id),
                "deleted": count,
            },
        )
        return count

    # --- Compute ------------------------------------------------------------
    async def _compute(self, resume: Resume) -> ResumeIntelligenceResult:
        """Deterministic analysis for this response, never persisted."""
        understanding, text_error, failure = await self._understand(resume)
        if failure is not None:
            return self._failure_result(failure)
        assert understanding is not None
        if text_error:
            understanding.warnings.append(_degraded_warning(text_error))
        return self._result_from_understanding(understanding, error_code=text_error)

    async def _compute_and_store(
        self, user: User, resume: Resume, fingerprint: str
    ) -> ResumeIntelligenceResult:
        understanding, text_error, failure = await self._understand(resume)
        if failure is None:
            assert understanding is not None
            if text_error:
                understanding.warnings.append(_degraded_warning(text_error))
            return await self._store(
                user,
                resume,
                fingerprint,
                payload=understanding.to_payload(),
                understanding=understanding,
                status=STATUS_COMPLETED,
                error_code=text_error,
                warnings=understanding.warnings,
            )

        previous = await self._completed_result(user, resume, fingerprint)
        if previous is not None:
            # A transient failure must never replace a good reading.
            previous.cached = True
            previous.warnings.append(
                "The previous analysis was kept because the latest attempt "
                f"failed ({failure})."
            )
            logger.warning(
                "Resume intelligence refresh failed; previous reading kept",
                extra={
                    "stage": "resume_intelligence",
                    "user_id": str(user.id),
                    "resume_id": str(resume.id),
                    "error_code": failure,
                },
            )
            return previous

        return await self._store(
            user,
            resume,
            fingerprint,
            payload=None,
            understanding=None,
            status=STATUS_FAILED,
            error_code=failure,
            warnings=[_failure_warning(failure)],
        )

    async def _understand(
        self, resume: Resume
    ) -> tuple[ResumeUnderstanding | None, str | None, str | None]:
        """Return ``(understanding, degraded_error_code, failure_code)``."""
        text, text_error = self._read_text(resume)
        try:
            understanding = extract_resume_intelligence(
                text=text,
                parsed_data=resume.parsed_data,
                stored_skills=resume.skills,
            )
        except Exception as exc:  # extraction must never fail the request silently
            logger.warning(
                "Resume intelligence extraction failed",
                extra={
                    "stage": "resume_intelligence",
                    "resume_id": str(resume.id),
                    "error": type(exc).__name__,
                },
            )
            return None, None, ERROR_EXTRACTION_FAILED

        if text is None and _is_empty(understanding):
            # Nothing to read and nothing stored: record why, invent nothing.
            return None, None, text_error or ERROR_TEXT_UNAVAILABLE
        return understanding, text_error, None

    def _read_text(self, resume: Resume) -> tuple[str | None, str | None]:
        """Return ``(text, error_code)``; storage and PDF problems never raise.

        Only the exception class and a safe code are logged: the file content,
        its bytes and its storage key never reach a log sink.
        """
        try:
            data = self.storage.read(resume.storage_key)
        except ResumeStorageError as exc:
            code = (
                ERROR_FILE_MISSING
                if exc.code == ERROR_FILE_MISSING
                else ERROR_FILE_UNAVAILABLE
            )
            logger.warning(
                "Resume file is not readable for intelligence extraction",
                extra={
                    "stage": "resume_intelligence",
                    "resume_id": str(resume.id),
                    "error_code": code,
                },
            )
            return None, code
        except Exception as exc:  # defensive: a backend must not crash analysis
            logger.warning(
                "Resume storage backend failed",
                extra={
                    "stage": "resume_intelligence",
                    "resume_id": str(resume.id),
                    "error": type(exc).__name__,
                },
            )
            return None, ERROR_FILE_UNAVAILABLE

        try:
            return extract_text_from_pdf(data), None
        except ResumeOcrRequiredError:
            return None, ERROR_OCR_REQUIRED
        except ResumeExtractionError as exc:
            return None, getattr(exc, "code", None) or ERROR_PDF_UNREADABLE
        except Exception as exc:  # defensive: never leak a traceback
            logger.warning(
                "Resume text extraction failed unexpectedly",
                extra={
                    "stage": "resume_intelligence",
                    "resume_id": str(resume.id),
                    "error": type(exc).__name__,
                },
            )
            return None, ERROR_TEXT_UNAVAILABLE

    # --- Persistence --------------------------------------------------------
    async def _store(
        self,
        user: User,
        resume: Resume,
        fingerprint: str,
        *,
        payload: ResumeIntelligencePayload | None,
        understanding: ResumeUnderstanding | None,
        status: str,
        error_code: str | None,
        warnings: list[str],
    ) -> ResumeIntelligenceResult:
        """Insert or update the row for this content and extractor version."""
        # Rollback expires ORM state, so keep the identifiers needed by the
        # conflict handler before anything can be committed or rolled back.
        user_id = user.id
        resume_id = resume.id
        row = await self._current_row(user_id, resume_id, fingerprint)
        if row is None:
            row = ResumeIntelligence(
                user_id=user_id,
                resume_id=resume_id,
                extractor_version=EXTRACTOR_VERSION,
                content_fingerprint=fingerprint,
            )
            self.session.add(row)

        row.extraction_status = status
        row.error_code = error_code
        row.warnings = list(warnings)
        row.confidence = understanding.confidence if understanding else CONFIDENCE_LOW
        row.provenance = dict(understanding.provenance) if understanding else {}
        row.field_confidence = (
            dict(understanding.field_confidence) if understanding else {}
        )
        row.intelligence_data = payload.model_dump(mode="json") if payload else {}
        row.extracted_at = datetime.now(timezone.utc)

        try:
            await self.session.commit()
        except IntegrityError:
            # Another worker inserted the same (resume, version, fingerprint).
            await self.session.rollback()
            existing = await self._current_row(user_id, resume_id, fingerprint)
            result = (
                self._result_from_row(existing, cached=True)
                if existing is not None
                else None
            )
            if result is not None:
                logger.info(
                    "Concurrent resume intelligence insert detected",
                    extra={
                        "stage": "resume_intelligence",
                        "resume_id": str(resume_id),
                    },
                )
                return result
            raise

        logger.info(
            "Resume intelligence computed",
            extra={
                "stage": "resume_intelligence",
                "user_id": str(user_id),
                "resume_id": str(resume_id),
                "status": status,
                "error_code": error_code,
                "confidence": row.confidence,
            },
        )
        if payload is not None:
            return self._result_from_payload(payload, row, cached=False)
        return self._failure_result(error_code or ERROR_EXTRACTION_FAILED)

    # --- Cache helpers ------------------------------------------------------
    async def _current_row(
        self, user_id: UUID, resume_id: UUID, fingerprint: str
    ) -> ResumeIntelligence | None:
        """Return this user's row for the current extractor version/content."""
        return await self.session.scalar(
            select(ResumeIntelligence).where(
                ResumeIntelligence.user_id == user_id,
                ResumeIntelligence.resume_id == resume_id,
                ResumeIntelligence.extractor_version == EXTRACTOR_VERSION,
                ResumeIntelligence.content_fingerprint == fingerprint,
            )
        )

    async def _has_any_row(self, user_id: UUID, resume_id: UUID) -> bool:
        """Whether any stored reading exists for this resume, at any version."""
        found = await self.session.scalar(
            select(ResumeIntelligence.id)
            .where(
                ResumeIntelligence.user_id == user_id,
                ResumeIntelligence.resume_id == resume_id,
            )
            .limit(1)
        )
        return found is not None

    async def _completed_result(
        self, user: User, resume: Resume, fingerprint: str
    ) -> ResumeIntelligenceResult | None:
        row = await self._current_row(user.id, resume.id, fingerprint)
        if row is None or row.extraction_status != STATUS_COMPLETED:
            return None
        return self._result_from_row(row, cached=True)

    def _load_payload(
        self, row: ResumeIntelligence
    ) -> ResumeIntelligencePayload | None:
        try:
            return ResumeIntelligencePayload.model_validate(row.intelligence_data or {})
        except ValidationError:
            logger.warning(
                "Stored resume intelligence is unreadable; recomputing",
                extra={
                    "stage": "resume_intelligence",
                    "resume_id": str(row.resume_id),
                },
            )
            return None

    def _result_from_row(
        self, row: ResumeIntelligence, *, cached: bool
    ) -> ResumeIntelligenceResult | None:
        if row.extraction_status != STATUS_COMPLETED:
            return ResumeIntelligenceResult(
                fields=ResumeIntelligenceFields(),
                confidence=row.confidence or CONFIDENCE_LOW,
                warnings=list(row.warnings or []),
                status=STATUS_FAILED,
                error_code=row.error_code,
                extractor_version=row.extractor_version,
                computed_at=row.extracted_at,
                cached=cached,
            )
        payload = self._load_payload(row)
        if payload is None:
            return None
        return self._result_from_payload(payload, row, cached=cached)

    @staticmethod
    def _result_from_payload(
        payload: ResumeIntelligencePayload,
        row: ResumeIntelligence,
        *,
        cached: bool,
    ) -> ResumeIntelligenceResult:
        return ResumeIntelligenceResult(
            fields=payload.fields,
            confidence=payload.confidence,
            provenance=dict(payload.provenance),
            field_confidence=dict(payload.field_confidence),
            warnings=list(payload.warnings),
            status=STATUS_COMPLETED,
            error_code=row.error_code,
            extractor_version=row.extractor_version,
            computed_at=row.extracted_at,
            cached=cached,
        )

    def _result_from_understanding(
        self, understanding: ResumeUnderstanding, *, error_code: str | None
    ) -> ResumeIntelligenceResult:
        return ResumeIntelligenceResult(
            fields=understanding.fields,
            confidence=understanding.confidence,
            provenance=dict(understanding.provenance),
            field_confidence=dict(understanding.field_confidence),
            warnings=list(understanding.warnings),
            status=STATUS_COMPLETED,
            error_code=error_code,
            extractor_version=EXTRACTOR_VERSION,
            computed_at=datetime.now(timezone.utc),
            cached=False,
        )

    def _failure_result(self, error_code: str) -> ResumeIntelligenceResult:
        return ResumeIntelligenceResult(
            fields=ResumeIntelligenceFields(),
            confidence=CONFIDENCE_LOW,
            warnings=[_failure_warning(error_code)],
            status=STATUS_FAILED,
            error_code=error_code,
            extractor_version=EXTRACTOR_VERSION,
            computed_at=datetime.now(timezone.utc),
            cached=False,
            analysis_state=STATE_FAILED,
        )


__all__ = [
    "ANALYSIS_STATES",
    "ERROR_EXTRACTION_FAILED",
    "ERROR_FILE_MISSING",
    "ERROR_FILE_UNAVAILABLE",
    "ERROR_OCR_REQUIRED",
    "ERROR_PDF_UNREADABLE",
    "ERROR_TEXT_UNAVAILABLE",
    "FINGERPRINT_FORMAT",
    "STATE_FAILED",
    "STATE_MISSING",
    "STATE_READY",
    "STATE_STALE",
    "ResumeIntelligenceResult",
    "ResumeIntelligenceService",
    "resume_content_fingerprint",
]
