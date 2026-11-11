"""Phase 2F.2.5 tests for the resume intelligence analyze/read APIs.

Synthetic PDFs are generated in memory and stored in a per-test temporary
directory. No real candidate document is read or logged.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from app.core.config import settings
from app.core.security import create_access_token
from app.models.resume import Resume
from app.models.resume_intelligence import (
    STATUS_COMPLETED,
    ResumeIntelligence,
)
from app.models.user import User
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.pdf_factory import RESUME_LINES, make_pdf

BASE = "/api/v1/resumes"

SessionFactory = async_sessionmaker[AsyncSession]


@pytest.fixture(autouse=True)
def resume_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep every stored upload inside this test's temporary directory."""
    storage_dir = (tmp_path / "resumes").resolve()
    monkeypatch.setattr(settings, "RESUME_STORAGE_DIR", str(storage_dir))
    monkeypatch.setattr(settings, "RESUME_MAX_FILE_SIZE_BYTES", 1024 * 1024)
    return storage_dir


async def create_user(
    session_factory: SessionFactory,
    *,
    email: str = "ada@example.com",
    google_sub: str = "google-sub-resume-api",
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


def bearer(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


async def upload_resume(
    client: AsyncClient,
    user: User,
    *,
    lines: tuple[str, ...] = RESUME_LINES,
    filename: str = "ada.pdf",
) -> dict[str, Any]:
    response = await client.post(
        f"{BASE}/upload",
        files={"file": (filename, make_pdf(lines), "application/pdf")},
        headers=bearer(user),
    )
    assert response.status_code == 201, response.text
    return response.json()


async def intelligence_rows(
    session_factory: SessionFactory, resume_id: Any
) -> list[ResumeIntelligence]:
    resume_uuid = uuid.UUID(str(resume_id))
    async with session_factory() as session:
        return list(
            (
                await session.scalars(
                    select(ResumeIntelligence).where(
                        ResumeIntelligence.resume_id == resume_uuid
                    )
                )
            ).all()
        )


def safe_body(body: dict[str, Any]) -> str:
    return json.dumps(body)


def remove_stored_pdfs(storage_dir: Path) -> None:
    """Blocking filesystem cleanup kept out of the async test body."""
    for path in storage_dir.rglob("*.pdf"):
        path.unlink()


# ---------------------------------------------------------------------------
# Analyze and read
# ---------------------------------------------------------------------------
async def test_analyze_persists_intelligence_and_returns_structured_fields(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory)
    resume = await upload_resume(client, user)

    response = await client.post(
        f"{BASE}/{resume['id']}/analyze", json={}, headers=bearer(user)
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["resume_id"] == resume["id"]
    assert body["extraction"]["status"] == STATUS_COMPLETED
    assert body["extraction"]["analysis_state"] == "ready"
    assert body["extraction"]["cached"] is False
    assert body["extraction"]["confidence"] in {"high", "medium", "low"}
    assert body["extraction"]["extractor_version"]
    assert body["extraction"]["computed_at"]
    assert body["fields"]["skills"]
    assert {skill["name"] for skill in body["fields"]["skills"]} >= {
        "Python",
        "FastAPI",
    }
    assert body["disclaimer"]
    # First run is stored, so the second run is a cache hit.
    assert len(await intelligence_rows(session_factory, resume["id"])) == 1


async def test_repeated_analyze_returns_the_cached_reading(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory)
    resume = await upload_resume(client, user)
    headers = bearer(user)

    first = await client.post(f"{BASE}/{resume['id']}/analyze", json={}, headers=headers)
    second = await client.post(f"{BASE}/{resume['id']}/analyze", json={}, headers=headers)

    assert first.status_code == second.status_code == 200
    assert second.json()["extraction"]["cached"] is True
    assert second.json()["extraction"]["analysis_state"] == "ready"
    assert second.json()["extraction"]["computed_at"] == (
        first.json()["extraction"]["computed_at"]
    )
    assert "Cached analysis reused" in (second.json()["message"] or "")
    assert len(await intelligence_rows(session_factory, resume["id"])) == 1


async def test_force_refresh_recomputes_without_duplicate_rows(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory)
    resume = await upload_resume(client, user)
    headers = bearer(user)

    first = await client.post(f"{BASE}/{resume['id']}/analyze", json={}, headers=headers)
    refreshed = await client.post(
        f"{BASE}/{resume['id']}/analyze",
        json={"force": True},
        headers=headers,
    )

    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["extraction"]["cached"] is False
    assert refreshed.json()["extraction"]["analysis_state"] == "ready"
    assert refreshed.json()["extraction"]["computed_at"] >= (
        first.json()["extraction"]["computed_at"]
    )
    assert len(await intelligence_rows(session_factory, resume["id"])) == 1


async def test_read_before_analysis_reports_missing_without_saving(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory)
    resume = await upload_resume(client, user)

    response = await client.get(
        f"{BASE}/{resume['id']}/intelligence", headers=bearer(user)
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["extraction"]["analysis_state"] == "missing"
    assert body["extraction"]["cached"] is False
    assert "No resume analysis has been stored yet" in (body["message"] or "")
    # A read must not trigger the expensive persisted extraction.
    assert await intelligence_rows(session_factory, resume["id"]) == []


async def test_read_after_analysis_returns_the_current_cached_reading(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory)
    resume = await upload_resume(client, user)
    headers = bearer(user)
    await client.post(f"{BASE}/{resume['id']}/analyze", json={}, headers=headers)

    response = await client.get(f"{BASE}/{resume['id']}/intelligence", headers=headers)

    assert response.status_code == 200, response.text
    assert response.json()["extraction"]["analysis_state"] == "ready"
    assert response.json()["extraction"]["cached"] is True


async def test_read_marks_an_outdated_reading_as_stale(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory)
    resume = await upload_resume(client, user)
    headers = bearer(user)
    await client.post(f"{BASE}/{resume['id']}/analyze", json={}, headers=headers)

    # Simulate a re-upload that changed the stored parsed content.
    async with session_factory() as session:
        stored = await session.get(Resume, uuid.UUID(str(resume["id"])))
        assert stored is not None
        stored.parsed_data = {
            **(stored.parsed_data or {}),
            "professional_summary": "Updated summary for the stale test.",
        }
        await session.commit()

    response = await client.get(f"{BASE}/{resume['id']}/intelligence", headers=headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["extraction"]["analysis_state"] == "stale"
    assert body["extraction"]["cached"] is False
    assert "out of date" in (body["message"] or "")
    # The stale read is not persisted.
    assert len(await intelligence_rows(session_factory, resume["id"])) == 1


async def test_failed_extraction_is_reported_safely(
    client: AsyncClient,
    session_factory: SessionFactory,
    resume_settings: Path,
) -> None:
    user = await create_user(session_factory)
    resume = await upload_resume(client, user)
    # Remove both the stored file and the parsed fallback: with nothing usable
    # the analysis must report a safe failure instead of inventing content.
    async with session_factory() as session:
        stored = await session.get(Resume, uuid.UUID(str(resume["id"])))
        assert stored is not None
        stored.parsed_data = {}
        stored.skills = []
        await session.commit()
    remove_stored_pdfs(resume_settings)

    response = await client.post(
        f"{BASE}/{resume['id']}/analyze", json={}, headers=bearer(user)
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["extraction"]["status"] == "failed"
    assert body["extraction"]["analysis_state"] == "failed"
    assert body["extraction"]["error_code"]
    assert "Resume analysis failed" in (body["message"] or "")
    assert body["fields"]["skills"] == []
    serialized = safe_body(body)
    assert str(resume_settings) not in serialized
    assert "storage_key" not in serialized
    assert "%PDF" not in serialized


# ---------------------------------------------------------------------------
# Ownership and authentication
# ---------------------------------------------------------------------------
async def test_resume_analysis_is_user_scoped(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    owner = await create_user(session_factory)
    other = await create_user(
        session_factory, email="other@example.com", google_sub="google-sub-other"
    )
    resume = await upload_resume(client, owner)

    assert (
        await client.post(
            f"{BASE}/{resume['id']}/analyze", json={}, headers=bearer(other)
        )
    ).status_code == 404
    assert (
        await client.get(f"{BASE}/{resume['id']}/intelligence", headers=bearer(other))
    ).status_code == 404
    assert await intelligence_rows(session_factory, resume["id"]) == []


async def test_unknown_resume_ids_are_not_found(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory)
    missing = "11111111-1111-1111-1111-111111111111"

    assert (
        await client.post(
            f"{BASE}/{missing}/analyze", json={}, headers=bearer(user)
        )
    ).status_code == 404
    assert (
        await client.get(f"{BASE}/{missing}/intelligence", headers=bearer(user))
    ).status_code == 404


async def test_malformed_resume_ids_are_rejected_safely(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory)

    analyzed = await client.post(
        f"{BASE}/not-a-uuid/analyze", json={}, headers=bearer(user)
    )
    read = await client.get(f"{BASE}/not-a-uuid/intelligence", headers=bearer(user))

    assert analyzed.status_code == 422
    assert analyzed.json()["code"] == "validation_error"
    assert read.status_code == 422
    assert read.json()["code"] == "validation_error"


async def test_resume_analysis_requires_authentication(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory)
    resume = await upload_resume(client, user)

    assert (
        await client.post(f"{BASE}/{resume['id']}/analyze", json={})
    ).status_code == 401
    assert (
        await client.get(f"{BASE}/{resume['id']}/intelligence")
    ).status_code == 401


async def test_intelligence_response_never_exposes_storage_internals(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory)
    resume = await upload_resume(client, user)

    response = await client.post(
        f"{BASE}/{resume['id']}/analyze", json={}, headers=bearer(user)
    )

    body = response.json()
    serialized = safe_body(body)
    assert "storage_key" not in serialized
    assert "parsed_data" not in serialized
    assert "%PDF" not in serialized
    assert "users/" not in serialized
    assert resume["original_filename"] not in serialized


async def test_intelligence_timestamps_are_utc(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory)
    resume = await upload_resume(client, user)
    await client.post(f"{BASE}/{resume['id']}/analyze", json={}, headers=bearer(user))

    body = (
        await client.get(f"{BASE}/{resume['id']}/intelligence", headers=bearer(user))
    ).json()
    computed_at = datetime.fromisoformat(
        body["extraction"]["computed_at"].replace("Z", "+00:00")
    )

    assert computed_at.tzinfo is not None
    assert computed_at.astimezone(timezone.utc) <= datetime.now(timezone.utc)


async def test_row_count_is_bounded_after_multiple_analyses(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory)
    resume = await upload_resume(client, user)
    headers = bearer(user)
    for _ in range(3):
        response = await client.post(
            f"{BASE}/{resume['id']}/analyze", json={}, headers=headers
        )
        assert response.status_code == 200, response.text

    async with session_factory() as session:
        count = await session.scalar(
            select(func.count(ResumeIntelligence.id)).where(
                ResumeIntelligence.resume_id == uuid.UUID(str(resume["id"]))
            )
        )
    assert int(count or 0) == 1
