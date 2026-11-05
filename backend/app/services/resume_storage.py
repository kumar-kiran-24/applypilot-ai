"""Configurable storage abstraction for uploaded resume files.

Only opaque, server-generated keys are ever handed to a storage backend, so a
filename supplied by a user can never influence a filesystem path.
"""

from __future__ import annotations

import re
import uuid
from abc import ABC, abstractmethod
from pathlib import Path

from app.core.config import settings
from app.core.exceptions import AppError
from app.core.logging import get_logger

logger = get_logger(__name__)

# A key is two UUID path segments and a .pdf suffix. Anything else is rejected
# before it reaches the filesystem, which rules out traversal and absolute paths.
_SAFE_KEY = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.pdf$"
)


class ResumeStorageError(AppError):
    """Stored resume file is unavailable or could not be written."""

    status_code = 500
    code = "resume_storage_error"
    message = "The resume file could not be stored. Please try again."


class ResumeStorage(ABC):
    """Persists resume bytes behind an opaque key."""

    backend_name: str

    @abstractmethod
    def save(self, key: str, data: bytes) -> None: ...

    @abstractmethod
    def read(self, key: str) -> bytes: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...


def build_storage_key(user_id: uuid.UUID, resume_id: uuid.UUID) -> str:
    """Return a safe, unguessable key owned by ``user_id``."""
    return f"{user_id}/{resume_id}.pdf"


class LocalResumeStorage(ResumeStorage):
    """Stores files under a configurable local directory."""

    backend_name = "local"

    def __init__(self, base_dir: str | Path) -> None:
        path = Path(base_dir).expanduser()
        self.base_dir = (path if path.is_absolute() else Path.cwd() / path).resolve()

    def _path_for(self, key: str) -> Path:
        if not _SAFE_KEY.match(key):
            raise ResumeStorageError(code="invalid_storage_key")
        candidate = (self.base_dir / key).resolve()
        # Defence in depth: even a (buggy) generated key may never escape the
        # configured storage root.
        if candidate != self.base_dir and self.base_dir not in candidate.parents:
            raise ResumeStorageError(code="invalid_storage_key")
        return candidate

    def save(self, key: str, data: bytes) -> None:
        path = self._path_for(key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Write-then-rename so a crash never leaves a truncated resume.
            temporary = path.with_suffix(".pdf.part")
            temporary.write_bytes(data)
            temporary.replace(path)
        except OSError as exc:
            logger.error(
                "Resume file could not be written",
                extra={"stage": "resume_storage", "error_class": type(exc).__name__},
            )
            raise ResumeStorageError() from None

    def read(self, key: str) -> bytes:
        path = self._path_for(key)
        try:
            return path.read_bytes()
        except OSError as exc:
            logger.warning(
                "Resume file could not be read",
                extra={"stage": "resume_storage", "error_class": type(exc).__name__},
            )
            raise ResumeStorageError(code="resume_file_missing") from None

    def delete(self, key: str) -> None:
        path = self._path_for(key)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            # A failed cleanup must not fail the API call: the database row is
            # already gone, so the file is unreachable either way.
            logger.warning(
                "Resume file could not be deleted",
                extra={"stage": "resume_storage", "error_class": type(exc).__name__},
            )


def get_resume_storage() -> ResumeStorage:
    """Build the configured storage backend for one request."""
    backend = settings.RESUME_STORAGE_BACKEND
    if backend == "local":
        return LocalResumeStorage(settings.RESUME_STORAGE_DIR)
    raise ResumeStorageError(code="unsupported_storage_backend")


__all__ = [
    "LocalResumeStorage",
    "ResumeStorage",
    "ResumeStorageError",
    "build_storage_key",
    "get_resume_storage",
]
