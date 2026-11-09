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
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from app.core.exceptions import InvalidOperationError
from app.models.resume import Resume
from app.models.user import User
from app.services.resume_storage import ResumeStorage, ResumeStorageError, get_resume_storage


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
        handle, temp_path = tempfile.mkstemp(prefix="applypilot-upload-", suffix=".pdf")
        os.close(handle)
        try:
            await asyncio.to_thread(Path(temp_path).write_bytes, data)
            yield temp_path
        finally:
            with contextlib.suppress(OSError):
                os.unlink(temp_path)


__all__ = ["ResumeUploadHandler"]
