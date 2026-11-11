"""Phase 2F.2.2 tests: resume intelligence persistence, caching and ownership.

Every resume fixture is synthetic: the PDFs are generated in memory and stored
in a per-test temporary directory, so no real candidate document is read or
logged.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from app.core.config import settings
from app.models.resume import Resume, ResumeParsingStatus
from app.models.resume_intelligence import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    ResumeIntelligence,
)
from app.models.user import User
from app.services import resume_intelligence_service as service_module
from app.services.resume_intelligence_extraction import EXTRACTOR_VERSION
from app.services.resume_intelligence_service import (
    ERROR_FILE_MISSING,
    ERROR_OCR_REQUIRED,
    ERROR_PDF_UNREADABLE,
    ResumeIntelligenceService,
    resume_content_fingerprint,
)
from app.services.resume_parser import (
    ResumeExtractionError,
    ResumeOcrRequiredError,
    parse_resume,
)
from app.services.resume_service import ResumeNotFoundError, ResumeService
from app.services.resume_storage import (
    LocalResumeStorage,
    build_storage_key,
)
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.pdf_factory import RESUME_LINES, make_pdf, scanned_pdf

SessionFactory = async_sessionmaker[AsyncSession]

EXTRA_LINES: tuple[str, ...] = (*RESUME_LINES, "Terraform, Go, Kubernetes")


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def resume_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep every stored file inside this test's temporary directory."""
    storage_dir = (tmp_path / "resumes").resolve()
    monkeypatch.setattr(settings, "RESUME_STORAGE_DIR", str(storage_dir))
    return storage_dir


@pytest.fixture
def storage(resume_settings: Path) -> LocalResumeStorage:
    return LocalResumeStorage(resume_settings)


@pytest.fixture
def extraction_calls(monkeypatch: pytest.MonkeyPatch) -> list[str | None]:
    """Record each extraction attempt without changing its behaviour."""
    calls: list[str | None] = []
    real = service_module.extract_resume_intelligence

    def counting(**kwargs: Any) -> Any:
        calls.append(kwargs.get("text"))
        return real(**kwargs)

    monkeypatch.setattr(service_module, "extract_resume_intelligence", counting)
    return calls


async def create_user(
    session_factory: SessionFactory,
    *,
    email: str = "ada@example.com",
    google_sub: str = "google-sub-resume-intelligence",
) -> User:
    async with session_factory() as session:
        user = User(
            google_sub=google_sub,
            email=email,
            email_verified=True,
            full_name="Ada Lovelace",
            is_active=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def create_resume(
    session_factory: SessionFactory,
    storage: LocalResumeStorage,
    user: User,
    *,
    data: bytes | None = None,
    lines: tuple[str, ...] = RESUME_LINES,
    write_file: bool = True,
    parse_stored_data: bool = True,
) -> Resume:
    """Insert a synthetic resume, optionally without its stored PDF."""
    pdf = data if data is not None else make_pdf(lines)
    resume_id = uuid.uuid4()
    storage_key = build_storage_key(user.id, resume_id)
    if write_file:
        storage.save(storage_key, pdf)

    parsed_data: dict[str, Any] = {}
    skills: list[str] = []
    status = ResumeParsingStatus.FAILED
    parsing_error: str | None = "parsing_failed"
    if parse_stored_data:
        try:
            parsed = parse_resume(pdf)
        except ResumeOcrRequiredError as exc:
            status, parsing_error = ResumeParsingStatus.OCR_REQUIRED, exc.code
        except ResumeExtractionError as exc:
            status, parsing_error = ResumeParsingStatus.FAILED, exc.code
        else:
            status, parsing_error = ResumeParsingStatus.COMPLETED, None
            parsed_data, skills = parsed.to_dict(), parsed.skills

    resume = Resume(
        id=resume_id,
        user_id=user.id,
        original_filename="resume.pdf",
        storage_key=storage_key,
        storage_backend=storage.backend_name,
        content_type="application/pdf",
        file_size=len(pdf),
        checksum=hashlib.sha256(pdf).hexdigest(),
        is_default=True,
        parsing_status=status,
        parsing_error=parsing_error,
        parsed_at=datetime.now(timezone.utc)
        if status is ResumeParsingStatus.COMPLETED
        else None,
        parsed_data=parsed_data,
        skills=skills,
    )
    async with session_factory() as session:
        session.add(resume)
        await session.commit()
        await session.refresh(resume)
    return resume


async def replace_content(
    session_factory: SessionFactory,
    storage: LocalResumeStorage,
    resume: Resume,
    lines: tuple[str, ...],
) -> None:
    """Rewrite the stored PDF and the row's parsed data, as a re-upload would."""
    pdf = make_pdf(lines)
    storage.save(resume.storage_key, pdf)
    parsed = parse_resume(pdf)
    async with session_factory() as session:
        row = await session.get(Resume, resume.id)
        assert row is not None
        row.checksum = hashlib.sha256(pdf).hexdigest()
        row.parsed_data = parsed.to_dict()
        row.skills = parsed.skills
        await session.commit()


async def analyze(
    session_factory: SessionFactory,
    storage: LocalResumeStorage,
    user: User,
    resume_id: uuid.UUID,
    *,
    force: bool = False,
) -> Any:
    async with session_factory() as session:
        service = ResumeIntelligenceService(session, storage)
        return await service.analyze_resume(user, resume_id, force=force)


async def read_intelligence(
    session_factory: SessionFactory,
    storage: LocalResumeStorage,
    user: User,
    resume_id: uuid.UUID,
) -> Any:
    async with session_factory() as session:
        service = ResumeIntelligenceService(session, storage)
        return await service.get_resume_intelligence(user, resume_id)


async def rows_for(
    session_factory: SessionFactory, resume_id: uuid.UUID
) -> list[ResumeIntelligence]:
    async with session_factory() as session:
        return list(
            (
                await session.scalars(
                    select(ResumeIntelligence).where(
                        ResumeIntelligence.resume_id == resume_id
                    )
                )
            ).all()
        )


async def row_count(session_factory: SessionFactory) -> int:
    async with session_factory() as session:
        return int(
            await session.scalar(select(func.count(ResumeIntelligence.id))) or 0
        )


# ---------------------------------------------------------------------------
# Fingerprinting
# ---------------------------------------------------------------------------
def resume_stub(**overrides: Any) -> Resume:
    data: dict[str, Any] = {
        "id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "original_filename": "resume.pdf",
        "storage_key": "user/resume.pdf",
        "storage_backend": "local",
        "content_type": "application/pdf",
        "file_size": 1024,
        "checksum": "a" * 64,
        "is_default": True,
        "parsing_status": ResumeParsingStatus.COMPLETED,
        "parsed_data": {"professional_summary": "Backend engineer"},
        "skills": ["Python", "FastAPI"],
    }
    data.update(overrides)
    return Resume(**data)


def test_fingerprint_is_deterministic_for_identical_content() -> None:
    assert resume_content_fingerprint(resume_stub()) == resume_content_fingerprint(
        resume_stub()
    )


def test_fingerprint_ignores_storage_location() -> None:
    first = resume_stub()
    second = resume_stub(
        storage_key="other/user/file.pdf",
        original_filename="different-name.pdf",
        file_size=999,
    )
    assert resume_content_fingerprint(first) == resume_content_fingerprint(second)


def test_fingerprint_changes_with_content_and_parsed_data() -> None:
    base = resume_content_fingerprint(resume_stub())
    assert base != resume_content_fingerprint(resume_stub(checksum="b" * 64))
    assert base != resume_content_fingerprint(
        resume_stub(parsed_data={"professional_summary": "Different"})
    )
    assert base != resume_content_fingerprint(resume_stub(skills=["Python"]))


# ---------------------------------------------------------------------------
# First analysis and persistence
# ---------------------------------------------------------------------------
async def test_first_analysis_persists_a_completed_row(
    session_factory: SessionFactory, storage: LocalResumeStorage
) -> None:
    user = await create_user(session_factory)
    resume = await create_resume(session_factory, storage, user)

    returned, result = await analyze(session_factory, storage, user, resume.id)

    assert returned.id == resume.id
    assert result.status == STATUS_COMPLETED
    assert result.cached is False
    assert result.error_code is None
    assert result.fields.summary
    assert "Python" in result.fields.skill_names()
    assert result.provenance["skills"] == "resume_text"
    assert result.confidence in {"high", "medium", "low"}

    rows = await rows_for(session_factory, resume.id)
    assert len(rows) == 1
    row = rows[0]
    assert row.user_id == user.id
    assert row.extractor_version == EXTRACTOR_VERSION
    assert row.content_fingerprint == resume_content_fingerprint(resume)
    assert row.extraction_status == STATUS_COMPLETED
    assert row.error_code is None
    assert row.confidence == result.confidence
    assert row.provenance["skills"] == "resume_text"
    assert row.intelligence_data["fields"]["summary"] == result.fields.summary


async def test_stored_row_never_contains_file_bytes_or_storage_key(
    session_factory: SessionFactory, storage: LocalResumeStorage
) -> None:
    user = await create_user(session_factory)
    resume = await create_resume(session_factory, storage, user)

    await analyze(session_factory, storage, user, resume.id)

    row = (await rows_for(session_factory, resume.id))[0]
    serialized = json.dumps(
        {
            "data": row.intelligence_data,
            "warnings": row.warnings,
            "error_code": row.error_code,
            "provenance": row.provenance,
        }
    )
    assert "%PDF" not in serialized
    assert resume.storage_key not in serialized
    assert "resume.pdf" not in serialized


# ---------------------------------------------------------------------------
# Cache behaviour
# ---------------------------------------------------------------------------
async def test_cache_hit_skips_extraction_and_storage(
    session_factory: SessionFactory,
    storage: LocalResumeStorage,
    extraction_calls: list[str | None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await create_user(session_factory)
    resume = await create_resume(session_factory, storage, user)

    _, first = await analyze(session_factory, storage, user, resume.id)

    def forbidden_read(key: str) -> bytes:
        raise AssertionError("a cache hit must not read the stored file")

    monkeypatch.setattr(storage, "read", forbidden_read)
    _, second = await analyze(session_factory, storage, user, resume.id)

    assert len(extraction_calls) == 1
    assert second.cached is True
    assert second.status == STATUS_COMPLETED
    assert second.fields.summary == first.fields.summary
    assert len(await rows_for(session_factory, resume.id)) == 1


async def test_content_change_produces_a_new_reading_and_keeps_the_old_one(
    session_factory: SessionFactory,
    storage: LocalResumeStorage,
    extraction_calls: list[str | None],
) -> None:
    user = await create_user(session_factory)
    resume = await create_resume(session_factory, storage, user)
    old_fingerprint = resume_content_fingerprint(resume)
    _, first = await analyze(session_factory, storage, user, resume.id)

    await replace_content(session_factory, storage, resume, EXTRA_LINES)
    async with session_factory() as session:
        updated = await session.get(Resume, resume.id)
        assert updated is not None
        new_fingerprint = resume_content_fingerprint(updated)
    _, second = await analyze(session_factory, storage, user, resume.id)

    assert new_fingerprint != old_fingerprint
    assert len(extraction_calls) == 2
    assert second.cached is False
    assert second.status == STATUS_COMPLETED
    assert "Terraform" in second.fields.skill_names()
    rows = await rows_for(session_factory, resume.id)
    assert len(rows) == 2
    assert {row.content_fingerprint for row in rows} == {
        old_fingerprint,
        new_fingerprint,
    }
    old = next(row for row in rows if row.content_fingerprint == old_fingerprint)
    assert old.intelligence_data["fields"]["summary"] == first.fields.summary


async def test_extractor_version_change_regenerates(
    session_factory: SessionFactory,
    storage: LocalResumeStorage,
    extraction_calls: list[str | None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await create_user(session_factory)
    resume = await create_resume(session_factory, storage, user)
    _, first = await analyze(session_factory, storage, user, resume.id)
    assert first.extractor_version == EXTRACTOR_VERSION

    monkeypatch.setattr(service_module, "EXTRACTOR_VERSION", "2f.2.2-test")
    _, second = await analyze(session_factory, storage, user, resume.id)

    assert len(extraction_calls) == 2
    assert second.cached is False
    assert second.extractor_version == "2f.2.2-test"
    rows = await rows_for(session_factory, resume.id)
    assert {row.extractor_version for row in rows} == {
        EXTRACTOR_VERSION,
        "2f.2.2-test",
    }


async def test_force_refresh_reextracts_without_duplicating_the_row(
    session_factory: SessionFactory,
    storage: LocalResumeStorage,
    extraction_calls: list[str | None],
) -> None:
    user = await create_user(session_factory)
    resume = await create_resume(session_factory, storage, user)
    await analyze(session_factory, storage, user, resume.id)

    _, refreshed = await analyze(session_factory, storage, user, resume.id, force=True)

    assert len(extraction_calls) == 2
    assert refreshed.cached is False
    assert refreshed.status == STATUS_COMPLETED
    assert len(await rows_for(session_factory, resume.id)) == 1


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------
async def test_scanned_resume_records_a_safe_failure(
    session_factory: SessionFactory,
    storage: LocalResumeStorage,
    extraction_calls: list[str | None],
) -> None:
    user = await create_user(session_factory)
    resume = await create_resume(session_factory, storage, user, data=scanned_pdf())

    _, result = await analyze(session_factory, storage, user, resume.id)

    assert extraction_calls == [None]
    assert result.status == STATUS_FAILED
    assert result.error_code == ERROR_OCR_REQUIRED
    assert result.fields.skills == []
    assert result.cached is False
    assert any("Nothing was inferred" in warning for warning in result.warnings)
    assert not any("Traceback" in warning for warning in result.warnings)

    row = (await rows_for(session_factory, resume.id))[0]
    assert row.extraction_status == STATUS_FAILED
    assert row.error_code == ERROR_OCR_REQUIRED
    assert row.intelligence_data == {}
    assert row.provenance == {}


async def test_unreadable_pdf_records_a_safe_failure(
    session_factory: SessionFactory, storage: LocalResumeStorage
) -> None:
    user = await create_user(session_factory)
    resume = await create_resume(
        session_factory, storage, user, data=b"this is not a pdf"
    )

    _, result = await analyze(session_factory, storage, user, resume.id)

    assert result.status == STATUS_FAILED
    assert result.error_code == ERROR_PDF_UNREADABLE
    assert result.fields.summary is None


async def test_missing_file_falls_back_to_stored_parsed_data(
    session_factory: SessionFactory,
    storage: LocalResumeStorage,
    extraction_calls: list[str | None],
) -> None:
    user = await create_user(session_factory)
    resume = await create_resume(
        session_factory, storage, user, write_file=False
    )

    _, result = await analyze(session_factory, storage, user, resume.id)

    assert extraction_calls == [None]
    assert result.status == STATUS_COMPLETED
    assert result.error_code == ERROR_FILE_MISSING
    assert result.fields.experience
    assert any("could not be read" in warning for warning in result.warnings)


async def test_missing_file_without_stored_data_fails(
    session_factory: SessionFactory, storage: LocalResumeStorage
) -> None:
    user = await create_user(session_factory)
    resume = await create_resume(
        session_factory,
        storage,
        user,
        data=b"this is not a pdf",
        write_file=False,
    )

    _, result = await analyze(session_factory, storage, user, resume.id)

    assert result.status == STATUS_FAILED
    assert result.error_code == ERROR_FILE_MISSING
    assert (await rows_for(session_factory, resume.id))[0].extraction_status == (
        STATUS_FAILED
    )


async def test_failed_refresh_preserves_the_previous_success(
    session_factory: SessionFactory,
    storage: LocalResumeStorage,
    extraction_calls: list[str | None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await create_user(session_factory)
    resume = await create_resume(session_factory, storage, user)
    _, first = await analyze(session_factory, storage, user, resume.id)

    def broken(**kwargs: Any) -> Any:
        raise RuntimeError("extractor exploded with resume text inside")

    monkeypatch.setattr(service_module, "extract_resume_intelligence", broken)
    _, second = await analyze(session_factory, storage, user, resume.id, force=True)

    assert second.cached is True
    assert second.status == STATUS_COMPLETED
    assert second.fields.summary == first.fields.summary
    assert any("previous analysis was kept" in w for w in second.warnings)
    assert not any("exploded" in w for w in second.warnings)
    rows = await rows_for(session_factory, resume.id)
    assert len(rows) == 1
    assert rows[0].extraction_status == STATUS_COMPLETED
    assert rows[0].error_code is None


# ---------------------------------------------------------------------------
# Read path
# ---------------------------------------------------------------------------
async def test_get_without_a_cache_does_not_persist(
    session_factory: SessionFactory,
    storage: LocalResumeStorage,
    extraction_calls: list[str | None],
) -> None:
    user = await create_user(session_factory)
    resume = await create_resume(session_factory, storage, user)

    _, first = await read_intelligence(session_factory, storage, user, resume.id)
    await read_intelligence(session_factory, storage, user, resume.id)

    assert first.status == STATUS_COMPLETED
    assert first.cached is False
    assert len(extraction_calls) == 2
    assert await rows_for(session_factory, resume.id) == []


async def test_get_returns_a_recorded_failure_without_retrying(
    session_factory: SessionFactory,
    storage: LocalResumeStorage,
    extraction_calls: list[str | None],
) -> None:
    user = await create_user(session_factory)
    resume = await create_resume(session_factory, storage, user, data=scanned_pdf())
    await analyze(session_factory, storage, user, resume.id)
    assert len(extraction_calls) == 1

    _, result = await read_intelligence(session_factory, storage, user, resume.id)

    assert result.status == STATUS_FAILED
    assert result.error_code == ERROR_OCR_REQUIRED
    assert result.cached is True
    assert len(extraction_calls) == 1


async def test_corrupt_payload_is_recomputed(
    session_factory: SessionFactory,
    storage: LocalResumeStorage,
    extraction_calls: list[str | None],
) -> None:
    user = await create_user(session_factory)
    resume = await create_resume(session_factory, storage, user)
    await analyze(session_factory, storage, user, resume.id)

    async with session_factory() as session:
        row = (
            await session.scalars(
                select(ResumeIntelligence).where(
                    ResumeIntelligence.resume_id == resume.id
                )
            )
        ).one()
        row.intelligence_data = {"fields": {"unexpected": True}}
        await session.commit()

    _, result = await analyze(session_factory, storage, user, resume.id)

    assert result.cached is False
    assert result.status == STATUS_COMPLETED
    assert result.fields.summary
    assert len(extraction_calls) == 2


# ---------------------------------------------------------------------------
# Invalidation
# ---------------------------------------------------------------------------
async def test_invalidation_removes_only_that_resumes_rows(
    session_factory: SessionFactory,
    storage: LocalResumeStorage,
    extraction_calls: list[str | None],
) -> None:
    user = await create_user(session_factory)
    first = await create_resume(session_factory, storage, user)
    second = await create_resume(session_factory, storage, user)
    await analyze(session_factory, storage, user, first.id)
    await analyze(session_factory, storage, user, second.id)
    assert await row_count(session_factory) == 2

    async with session_factory() as session:
        deleted = await ResumeIntelligenceService(
            session, storage
        ).invalidate_resume_intelligence(user, first.id)

    assert deleted == 1
    assert await rows_for(session_factory, first.id) == []
    assert len(await rows_for(session_factory, second.id)) == 1

    await analyze(session_factory, storage, user, first.id)
    assert len(extraction_calls) == 3
    assert len(await rows_for(session_factory, first.id)) == 1


async def test_invalidation_without_cache_returns_zero(
    session_factory: SessionFactory, storage: LocalResumeStorage
) -> None:
    user = await create_user(session_factory)
    resume = await create_resume(session_factory, storage, user)

    async with session_factory() as session:
        deleted = await ResumeIntelligenceService(
            session, storage
        ).invalidate_resume_intelligence(user, resume.id)

    assert deleted == 0


# ---------------------------------------------------------------------------
# Ownership and deletion
# ---------------------------------------------------------------------------
async def test_cross_user_access_is_reported_as_missing(
    session_factory: SessionFactory, storage: LocalResumeStorage
) -> None:
    owner = await create_user(session_factory, email="owner@example.com")
    other = await create_user(
        session_factory, email="other@example.com", google_sub="google-sub-other"
    )
    resume = await create_resume(session_factory, storage, owner)
    await analyze(session_factory, storage, owner, resume.id)

    with pytest.raises(ResumeNotFoundError):
        await analyze(session_factory, storage, other, resume.id)
    with pytest.raises(ResumeNotFoundError):
        await read_intelligence(session_factory, storage, other, resume.id)
    async with session_factory() as session:
        with pytest.raises(ResumeNotFoundError):
            await ResumeIntelligenceService(
                session, storage
            ).invalidate_resume_intelligence(other, resume.id)

    assert len(await rows_for(session_factory, resume.id)) == 1
    assert (await rows_for(session_factory, resume.id))[0].user_id == owner.id


async def test_deleted_resume_returns_no_intelligence(
    session_factory: SessionFactory, storage: LocalResumeStorage
) -> None:
    user = await create_user(session_factory)
    resume = await create_resume(session_factory, storage, user)
    await analyze(session_factory, storage, user, resume.id)

    async with session_factory() as session:
        await ResumeService(session, storage).delete_resume(user, resume.id)

    with pytest.raises(ResumeNotFoundError):
        await analyze(session_factory, storage, user, resume.id)
    with pytest.raises(ResumeNotFoundError):
        await read_intelligence(session_factory, storage, user, resume.id)


# ---------------------------------------------------------------------------
# Concurrency and database constraints
# ---------------------------------------------------------------------------
async def test_unique_constraint_blocks_duplicate_rows(
    session_factory: SessionFactory, storage: LocalResumeStorage
) -> None:
    user = await create_user(session_factory)
    resume = await create_resume(session_factory, storage, user)
    fingerprint = resume_content_fingerprint(resume)

    def row() -> ResumeIntelligence:
        return ResumeIntelligence(
            user_id=user.id,
            resume_id=resume.id,
            extractor_version=EXTRACTOR_VERSION,
            content_fingerprint=fingerprint,
            extraction_status=STATUS_COMPLETED,
            confidence="high",
            provenance={},
            field_confidence={},
            intelligence_data={},
            warnings=[],
            extracted_at=datetime.now(timezone.utc),
        )

    async with session_factory() as session:
        session.add(row())
        await session.commit()
        session.add(row())
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

    assert len(await rows_for(session_factory, resume.id)) == 1


async def test_concurrent_analysis_persists_one_row_and_extracts_once(
    session_factory: SessionFactory,
    storage: LocalResumeStorage,
    extraction_calls: list[str | None],
) -> None:
    user = await create_user(session_factory)
    resume = await create_resume(session_factory, storage, user)

    async def run_once() -> Any:
        return await analyze(session_factory, storage, user, resume.id)

    results = await asyncio.gather(run_once(), run_once(), run_once())

    assert len(extraction_calls) == 1
    assert sum(1 for _, result in results if not result.cached) == 1
    assert all(result.status == STATUS_COMPLETED for _, result in results)
    assert len(await rows_for(session_factory, resume.id)) == 1


async def test_failed_commit_rolls_back_without_a_partial_row(
    session_factory: SessionFactory,
    storage: LocalResumeStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await create_user(session_factory)
    resume = await create_resume(session_factory, storage, user)

    async with session_factory() as session:
        service = ResumeIntelligenceService(session, storage)

        async def failing_commit() -> None:
            raise IntegrityError("insert", {}, Exception("duplicate key"))

        monkeypatch.setattr(session, "commit", failing_commit)
        with pytest.raises(IntegrityError):
            await service.analyze_resume(user, resume.id)

    assert await row_count(session_factory) == 0


# ---------------------------------------------------------------------------
# Logging
async def test_analysis_logs_never_contain_resume_text_or_storage_keys(
    session_factory: SessionFactory,
    storage: LocalResumeStorage,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Structured logs carry identifiers and safe codes, never the document."""
    user = await create_user(session_factory)
    resume = await create_resume(session_factory, storage, user)
    caplog.set_level("INFO", logger="app.services.resume_intelligence_service")

    await analyze(session_factory, storage, user, resume.id)

    def broken(**kwargs: Any) -> Any:
        raise RuntimeError("failed while reading Ada's private notes")

    monkeypatch.setattr(service_module, "extract_resume_intelligence", broken)
    _, kept = await analyze(session_factory, storage, user, resume.id, force=True)
    assert kept.status == STATUS_COMPLETED and kept.cached is True

    # Only the application's own records: the sqlite driver logs bound
    # parameters at DEBUG in tests and is not part of the service's logging.
    app_records = [r for r in caplog.records if r.name.startswith("app.")]
    assert app_records, "the analysis should log something"
    rendered = "\n".join(
        f"{record.getMessage()} {record.__dict__}" for record in app_records
    )
    for secret in (
        "Ada Lovelace",
        "ada@example.com",
        "+1 555 010 0199",
        "Analytical Engines",
        "private notes",
        resume.storage_key,
        "%PDF",
    ):
        assert secret not in rendered


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------
async def test_service_defaults_to_the_configured_storage(
    session_factory: SessionFactory, resume_settings: Path
) -> None:
    async with session_factory() as session:
        service = ResumeIntelligenceService(session)

    assert service.storage.backend_name == "local"
    assert service.storage.base_dir == resume_settings
