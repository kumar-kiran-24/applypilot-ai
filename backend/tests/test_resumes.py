"""Tests for the resume library: upload, parsing, ownership and file handling."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from app.core.config import settings
from app.core.security import create_access_token
from app.models.user import User
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.pdf_factory import RESUME_LINES, make_pdf, scanned_pdf

BASE = "/api/v1/resumes"

SessionFactory = async_sessionmaker[AsyncSession]


@pytest.fixture(autouse=True)
def resume_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep every upload inside the test's temporary directory."""
    storage_dir = tmp_path / "resumes"
    monkeypatch.setattr(settings, "RESUME_STORAGE_DIR", str(storage_dir))
    monkeypatch.setattr(settings, "RESUME_MAX_FILE_SIZE_BYTES", 1024 * 1024)
    return storage_dir


async def create_user(
    session_factory: SessionFactory,
    *,
    email: str = "ada@example.com",
    google_sub: str = "google-sub-resume",
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


async def upload(
    client: AsyncClient,
    user: User,
    data: bytes,
    *,
    filename: str = "resume.pdf",
    content_type: str = "application/pdf",
) -> Any:
    return await client.post(
        f"{BASE}/upload",
        files={"file": (filename, data, content_type)},
        headers=bearer(user),
    )


def pdf_bytes() -> bytes:
    return make_pdf(RESUME_LINES)


def stored_pdfs(storage_dir: Path) -> list[Path]:
    """Blocking filesystem helper kept out of the async test bodies."""
    return list(storage_dir.rglob("*.pdf"))


# ---------------------------------------------------------------------------
# Upload + parsing
# ---------------------------------------------------------------------------
async def test_upload_parses_resume_and_becomes_default(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory)

    response = await upload(client, user, pdf_bytes())

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["original_filename"] == "resume.pdf"
    assert body["file_size"] == len(pdf_bytes())
    assert body["content_type"] == "application/pdf"
    assert body["is_default"] is True
    assert body["parsing_status"] == "completed"
    assert body["parsing_error"] is None
    assert body["parsed_at"]
    assert body["uploaded_at"]

    data = body["parsed_data"]
    assert data["full_name"] == "Ada Lovelace"
    assert data["email"] == "ada@example.com"
    assert data["phone"] == "+1 555 010 0199"
    assert "data platforms" in data["professional_summary"]
    assert {"Python", "FastAPI", "PostgreSQL"} <= set(body["skills"])
    assert data["education"][0]["institution"] == "University of London"
    assert data["education"][0]["start_date"] == "2014"
    assert data["experience"][0]["title"] == "Senior Backend Engineer"
    assert data["experience"][0]["is_current"] is True
    assert data["experience"][1]["company"] == "Babbage Systems"
    assert data["projects"][0]["name"] == "ApplyPilot"
    assert data["certifications"] == [
        "AWS Certified Solutions Architect",
        "Certified Kubernetes Administrator",
    ]

    # Storage internals never leave the server.
    assert "storage_key" not in response.text
    assert "var/" not in response.text


async def test_upload_rejects_non_pdf(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory)

    response = await upload(
        client, user, b"plain text", filename="resume.txt", content_type="text/plain"
    )

    assert response.status_code == 415
    assert response.json()["code"] == "invalid_file_type"


async def test_upload_rejects_fabricated_pdf(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory)

    response = await upload(client, user, b"not really a pdf")

    assert response.status_code == 415
    assert response.json()["code"] == "invalid_pdf"


async def test_upload_rejects_empty_file(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory)

    response = await upload(client, user, b"")

    assert response.status_code == 400
    assert response.json()["code"] == "empty_file"


async def test_upload_enforces_size_limit(
    client: AsyncClient,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "RESUME_MAX_FILE_SIZE_BYTES", 1024)
    user = await create_user(session_factory)

    oversized = b"%PDF-1.4\n" + b"A" * 2048
    response = await upload(client, user, oversized)

    assert response.status_code == 413
    assert response.json()["code"] == "file_too_large"


async def test_scanned_pdf_reports_ocr_required(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory)

    response = await upload(client, user, scanned_pdf())

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["parsing_status"] == "ocr_required"
    assert body["parsing_error"] == "ocr_required"
    assert body["parsed_at"] is None
    assert body["skills"] == []
    assert body["parsed_data"]["full_name"] is None


async def test_malformed_pdf_is_kept_but_marked_failed(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory)

    response = await upload(client, user, b"%PDF-1.4\nthis is not a pdf body")

    assert response.status_code == 201, response.text
    assert response.json()["parsing_status"] == "failed"
    assert response.json()["parsing_error"] == "pdf_unreadable"


async def test_upload_sanitizes_filename(
    client: AsyncClient,
    session_factory: SessionFactory,
    resume_settings: Path,
) -> None:
    user = await create_user(session_factory)

    response = await upload(client, user, pdf_bytes(), filename="../../../etc/passwd.pdf")

    assert response.status_code == 201, response.text
    assert response.json()["original_filename"] == "passwd.pdf"
    stored = stored_pdfs(resume_settings)
    assert len(stored) == 1
    assert stored[0].parent == resume_settings / str(user.id)


async def test_upload_ignores_duplicate_content(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory)

    first = await upload(client, user, pdf_bytes())
    second = await upload(client, user, pdf_bytes(), filename="resume-v2.pdf")

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] != second.json()["id"]
    assert second.json()["is_default"] is False


# ---------------------------------------------------------------------------
# Listing, default selection, download and delete
# ---------------------------------------------------------------------------
async def test_multiple_resumes_and_default_selection(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory)
    first = (await upload(client, user, pdf_bytes(), filename="first.pdf")).json()
    second = (await upload(client, user, pdf_bytes(), filename="second.pdf")).json()

    listed = await client.get(BASE, headers=bearer(user))
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [first["id"], second["id"]]
    assert listed.json()[0]["is_default"] is True
    assert listed.json()[1]["is_default"] is False

    promoted = await client.patch(f"{BASE}/{second['id']}/default", headers=bearer(user))
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["is_default"] is True

    refreshed = (await client.get(BASE, headers=bearer(user))).json()
    assert [item["is_default"] for item in refreshed] == [True, False]
    assert refreshed[0]["id"] == second["id"]

    detail = await client.get(f"{BASE}/{second['id']}", headers=bearer(user))
    assert detail.status_code == 200
    assert detail.json()["original_filename"] == "second.pdf"


async def test_download_returns_original_pdf(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory)
    created = (await upload(client, user, pdf_bytes(), filename="My Résumé.pdf")).json()

    response = await client.get(f"{BASE}/{created['id']}/download", headers=bearer(user))

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert "attachment" in response.headers["content-disposition"]
    assert 'filename="My R_sum_.pdf"' in response.headers["content-disposition"]
    assert "filename*=UTF-8''My%20R%C3%A9sum%C3%A9.pdf" in response.headers["content-disposition"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.content == pdf_bytes()

    inline = await client.get(
        f"{BASE}/{created['id']}/download?disposition=inline", headers=bearer(user)
    )
    assert inline.status_code == 200
    assert inline.headers["content-disposition"].startswith("inline;")


async def test_delete_removes_row_and_file(
    client: AsyncClient,
    session_factory: SessionFactory,
    resume_settings: Path,
) -> None:
    user = await create_user(session_factory)
    created = (await upload(client, user, pdf_bytes())).json()
    assert stored_pdfs(resume_settings)

    response = await client.delete(f"{BASE}/{created['id']}", headers=bearer(user))

    assert response.status_code == 200
    assert response.json() == {"id": created["id"], "deleted": True}
    assert (await client.get(f"{BASE}/{created['id']}", headers=bearer(user))).status_code == 404
    assert stored_pdfs(resume_settings) == []
    assert (await client.get(BASE, headers=bearer(user))).json() == []


async def test_deleting_default_promotes_remaining_resume(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory)
    first = (await upload(client, user, pdf_bytes(), filename="first.pdf")).json()
    second = (await upload(client, user, pdf_bytes(), filename="second.pdf")).json()

    await client.delete(f"{BASE}/{first['id']}", headers=bearer(user))
    remaining = (await client.get(BASE, headers=bearer(user))).json()

    assert len(remaining) == 1
    assert remaining[0]["id"] == second["id"]
    assert remaining[0]["is_default"] is True


# ---------------------------------------------------------------------------
# Authentication and ownership
# ---------------------------------------------------------------------------
async def test_endpoints_require_authentication(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory)
    created = (await upload(client, user, pdf_bytes())).json()
    resume_id = created["id"]

    calls = [
        client.post(f"{BASE}/upload", files={"file": ("r.pdf", pdf_bytes(), "application/pdf")}),
        client.get(BASE),
        client.get(f"{BASE}/{resume_id}"),
        client.get(f"{BASE}/{resume_id}/download"),
        client.patch(f"{BASE}/{resume_id}/default"),
        client.delete(f"{BASE}/{resume_id}"),
    ]
    for call in calls:
        response = await call
        assert response.status_code == 401, response.text
        assert response.headers["www-authenticate"] == "Bearer"


async def test_other_users_cannot_reach_a_resume(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    owner = await create_user(session_factory)
    intruder = await create_user(
        session_factory, email="grace@example.com", google_sub="google-sub-intruder"
    )
    created = (await upload(client, owner, pdf_bytes())).json()
    resume_id = created["id"]

    assert (await client.get(BASE, headers=bearer(intruder))).json() == []
    assert (
        await client.get(f"{BASE}/{resume_id}", headers=bearer(intruder))
    ).status_code == 404
    assert (
        await client.get(f"{BASE}/{resume_id}/download", headers=bearer(intruder))
    ).status_code == 404
    assert (
        await client.patch(f"{BASE}/{resume_id}/default", headers=bearer(intruder))
    ).status_code == 404
    assert (
        await client.delete(f"{BASE}/{resume_id}", headers=bearer(intruder))
    ).status_code == 404

    # The owner's resume is untouched and still the default.
    still_there = await client.get(f"{BASE}/{resume_id}", headers=bearer(owner))
    assert still_there.status_code == 200
    assert still_there.json()["is_default"] is True


async def test_unknown_resume_id_returns_404(
    client: AsyncClient, session_factory: SessionFactory
) -> None:
    user = await create_user(session_factory)

    response = await client.get(
        f"{BASE}/00000000-0000-0000-0000-000000000000", headers=bearer(user)
    )

    assert response.status_code == 404
    assert response.json()["code"] == "resume_not_found"
