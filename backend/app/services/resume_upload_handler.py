"""Phase 2E Stage C: attach the application's own PDF resume safely.

The employer's page never receives a path chosen by the user. The handler
checks ownership, reads the opaque storage key through the existing resume
storage backend, writes a short-lived temporary copy and removes it in a
``finally`` block. Nothing about the file, the storage key or its contents is
logged.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import shutil
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from app.core.exceptions import InvalidOperationError
from app.models.resume import Resume
from app.models.user import User
from app.services.resume_storage import ResumeStorage, ResumeStorageError, get_resume_storage

#: Characters a browser may safely show as an uploaded filename.
_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._ -]+")

#: Portable length limit for the name shown by the employer's form.
_MAX_UPLOAD_NAME = 120


def _safe_upload_name(original: str | None) -> str:
    """Return a harmless filename that still identifies the candidate's resume.

    The employer's form displays the uploaded name, so a random temporary name
    would present "applypilot-upload-xxxx.pdf" to a recruiter. Only the replica's
    own basename is used and every non-portable character is replaced; the path
    is always chosen by ApplyPilot, never by the user.
    """
    name = os.path.basename((original or "").replace("\\", "/"))
    name = _UNSAFE_FILENAME.sub("_", name).strip(" ._-")
    if not name:
        name = "resume"
    if not name.casefold().endswith(".pdf"):
        name = f"{name}.pdf"
    if len(name) > _MAX_UPLOAD_NAME:
        # Truncate the stem, never the extension: the employer must still see a
        # PDF name even for an unusually long original filename.
        stem = name[: _MAX_UPLOAD_NAME - 4].rstrip(" ._-") or "resume"
        name = f"{stem}{name[-4:]}"
    return name


class ResumeUploadHandler:
    """Validates and materialises the selected resume for one upload."""

    def __init__(self, storage: ResumeStorage | None = None) -> None:
        self.storage = storage or get_resume_storage()

    def ensure_owned(self, user: User, resume: Resume | None) -> Resume:
        if resume is None:
            raise InvalidOperationError(
                "Select a resume before filling a resume file field.",
                code="resume_required",
            )
        if resume.user_id != user.id:
            # Do not reveal whether the record exists for another account.
            raise InvalidOperationError(
                "The selected resume is not available for this application.",
                code="resume_not_owned",
            )
        return resume

    @asynccontextmanager
    async def materialize(self, user: User, resume: Resume | None) -> AsyncIterator[str]:
        """Yield a temporary PDF path and always delete it afterwards."""
        owned = self.ensure_owned(user, resume)
        try:
            data = await asyncio.to_thread(self.storage.read, owned.storage_key)
        except ResumeStorageError as exc:
            raise InvalidOperationError(
                "The selected resume file could not be read.",
                code="resume_unavailable",
            ) from exc
        directory = tempfile.mkdtemp(prefix="applypilot-upload-")
        temp_path = os.path.join(directory, _safe_upload_name(owned.original_filename))
        try:
            await asyncio.to_thread(Path(temp_path).write_bytes, data)
            yield temp_path
        finally:
            with contextlib.suppress(OSError):
                shutil.rmtree(directory, ignore_errors=True)


__all__ = ["ResumeUploadHandler"]
