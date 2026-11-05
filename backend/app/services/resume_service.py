"""Resume library business logic.

Every read and write is scoped by the authenticated user id. A resume that
belongs to somebody else is reported as missing (404), never as forbidden, so
resume ids cannot be probed across accounts.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timezone
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any
from uuid import UUID

from fastapi import UploadFile
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AppError, ResourceNotFoundError
from app.core.logging import get_logger
from app.models.resume import Resume, ResumeParsingStatus
from app.models.user import User
from app.services.resume_parser import (
    ResumeExtractionError,
    ResumeOcrRequiredError,
    parse_resume,
)
from app.services.resume_storage import ResumeStorage, build_storage_key

logger = get_logger(__name__)

PDF_MAGIC = b"%PDF-"
PDF_CONTENT_TYPES = {"application/pdf", "application/x-pdf", "application/octet-stream"}
READ_CHUNK_SIZE = 64 * 1024
MAX_FILENAME_LENGTH = 255


class ResumeFileError(AppError):
    """Base class for rejected uploads."""

    status_code = 400
    code = "invalid_resume_file"
    message = "The uploaded file is not a valid resume."


class ResumeFileTypeError(ResumeFileError):
    """The file is not a PDF by name, content type or signature."""

    status_code = 415
    code = "invalid_file_type"
    message = "Only PDF resumes are supported."


class ResumeFileTooLargeError(ResumeFileError):
    """The upload exceeds the configured limit."""

    status_code = 413
    code = "file_too_large"
    message = "The resume exceeds the maximum allowed file size."


class ResumeEmptyFileError(ResumeFileError):
    """The upload has no bytes."""

    status_code = 400
    code = "empty_file"
    message = "The uploaded file is empty."


class ResumeNotFoundError(ResourceNotFoundError):
    """The resume does not exist for this user."""

    code = "resume_not_found"
    message = "Resume not found."


def sanitize_filename(raw_name: str | None) -> str:
    """Return a display-only filename with no directory component."""
    candidate = (raw_name or "").strip().replace("\\", "/")
    # Strip both POSIX and Windows directory components (``..\\x.pdf``).
    candidate = PurePosixPath(PureWindowsPath(candidate).name).name
    candidate = re.sub(r"[\x00-\x1f\x7f]", "", candidate).strip().strip(".")
    if not candidate:
        return "resume.pdf"
    if len(candidate) > MAX_FILENAME_LENGTH:
        stem, _, suffix = candidate.rpartition(".")
        if stem and len(suffix) <= 10:
            candidate = f"{stem[: MAX_FILENAME_LENGTH - len(suffix) - 1]}.{suffix}"
        else:
            candidate = candidate[:MAX_FILENAME_LENGTH]
    return candidate


def pdf_filename_for_header(filename: str) -> str:
    """Return an ASCII-only filename safe for a Content-Disposition header."""
    cleaned = re.sub(r"[^A-Za-z0-9._ -]", "_", filename).strip() or "resume"
    if not cleaned.lower().endswith(".pdf"):
        cleaned = f"{cleaned}.pdf"
    return cleaned[:MAX_FILENAME_LENGTH]


class ResumeService:
    """Coordinates upload, parsing, default selection and deletion."""

    def __init__(self, session: AsyncSession, storage: ResumeStorage) -> None:
        self.session = session
        self.storage = storage

    # --- Queries -------------------------------------------------------------
    async def list_resumes(self, user: User) -> list[Resume]:
        result = await self.session.execute(
            select(Resume)
            .where(Resume.user_id == user.id)
            .order_by(Resume.is_default.desc(), Resume.created_at.desc())
        )
        return list(result.scalars())

    async def get_resume(self, user: User, resume_id: UUID) -> Resume:
        resume = await self.session.scalar(
            select(Resume).where(Resume.id == resume_id, Resume.user_id == user.id)
        )
        if resume is None:
            raise ResumeNotFoundError()
        return resume

    # --- Upload --------------------------------------------------------------
    async def create_resume(self, user: User, upload: UploadFile) -> Resume:
        filename = sanitize_filename(upload.filename)
        if not filename.lower().endswith(".pdf"):
            raise ResumeFileTypeError()
        if upload.content_type and upload.content_type.split(";")[0].strip() not in (
            PDF_CONTENT_TYPES
        ):
            raise ResumeFileTypeError()

        data = await self._read_limited(upload)
        if not data:
            raise ResumeEmptyFileError()
        if not data.lstrip()[:5].startswith(PDF_MAGIC):
            raise ResumeFileTypeError("The file is not a PDF document.", code="invalid_pdf")

        resume_id = uuid.uuid4()
        storage_key = build_storage_key(user.id, resume_id)
        existing_count = await self.session.scalar(
            select(func.count(Resume.id)).where(Resume.user_id == user.id)
        )

        parsed_data: dict[str, Any] = {}
        skills: list[str] = []
        status = ResumeParsingStatus.FAILED
        parsing_error: str | None = None
        try:
            parsed = parse_resume(data)
        except ResumeOcrRequiredError as exc:
            status = ResumeParsingStatus.OCR_REQUIRED
            parsing_error = exc.code
        except ResumeExtractionError as exc:
            status = ResumeParsingStatus.FAILED
            parsing_error = exc.code
        except Exception as exc:  # defensive: never leak a parser traceback
            logger.warning(
                "Resume parsing failed unexpectedly",
                extra={"stage": "resume_parse", "error_class": type(exc).__name__},
            )
            status = ResumeParsingStatus.FAILED
            parsing_error = "parsing_failed"
        else:
            parsed_data = parsed.to_dict()
            skills = parsed.skills
            status = ResumeParsingStatus.COMPLETED

        self.storage.save(storage_key, data)

        resume = Resume(
            id=resume_id,
            user_id=user.id,
            original_filename=filename,
            storage_key=storage_key,
            storage_backend=self.storage.backend_name,
            content_type="application/pdf",
            file_size=len(data),
            checksum=hashlib.sha256(data).hexdigest(),
            is_default=not existing_count,
            parsing_status=status,
            parsing_error=parsing_error,
            parsed_at=datetime.now(timezone.utc)
            if status is ResumeParsingStatus.COMPLETED
            else None,
            parsed_data=parsed_data,
            skills=skills,
        )
        self.session.add(resume)
        try:
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            # Do not leave an unreferenced file behind if the row was rejected.
            self.storage.delete(storage_key)
            raise
        await self.session.refresh(resume)

        logger.info(
            "Resume uploaded",
            extra={
                "stage": "resume_upload",
                "user_id": str(user.id),
                "resume_id": str(resume.id),
                "parsing_status": status.value,
                "file_size": resume.file_size,
            },
        )
        return resume

    async def _read_limited(self, upload: UploadFile) -> bytes:
        limit = settings.RESUME_MAX_FILE_SIZE_BYTES
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = await upload.read(READ_CHUNK_SIZE)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise ResumeFileTooLargeError(
                    f"Resume files must be {limit // (1024 * 1024)} MB or smaller."
                )
            chunks.append(chunk)
        return b"".join(chunks)

    # --- Default -------------------------------------------------------------
    async def set_default(self, user: User, resume_id: UUID) -> Resume:
        resume = await self.get_resume(user, resume_id)
        await self.session.execute(
            update(Resume).where(Resume.user_id == user.id).values(is_default=False)
        )
        resume.is_default = True
        await self.session.commit()
        await self.session.refresh(resume)
        logger.info(
            "Default resume selected",
            extra={"stage": "resume_default", "user_id": str(user.id), "resume_id": str(resume.id)},
        )
        return resume

    # --- Download ------------------------------------------------------------
    async def read_file(self, user: User, resume_id: UUID) -> tuple[Resume, bytes]:
        resume = await self.get_resume(user, resume_id)
        return resume, self.storage.read(resume.storage_key)

    # --- Delete --------------------------------------------------------------
    async def delete_resume(self, user: User, resume_id: UUID) -> UUID:
        resume = await self.get_resume(user, resume_id)
        storage_key = resume.storage_key
        was_default = resume.is_default

        await self.session.delete(resume)
        await self.session.commit()

        if was_default:
            replacement = await self.session.scalar(
                select(Resume)
                .where(Resume.user_id == user.id)
                .order_by(Resume.created_at.desc())
                .limit(1)
            )
            if replacement is not None:
                replacement.is_default = True
                await self.session.commit()

        self.storage.delete(storage_key)
        logger.info(
            "Resume deleted",
            extra={"stage": "resume_delete", "user_id": str(user.id), "resume_id": str(resume_id)},
        )
        return resume_id


__all__ = [
    "PDF_MAGIC",
    "ResumeEmptyFileError",
    "ResumeFileError",
    "ResumeFileTooLargeError",
    "ResumeFileTypeError",
    "ResumeNotFoundError",
    "ResumeService",
    "pdf_filename_for_header",
    "sanitize_filename",
]
