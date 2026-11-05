"""Uploaded resumes and their parsed representation.

A resume belongs to an authenticated user. Extracted data is stored on the
resume itself as structured JSON: the candidate profile tables are deliberately
left untouched so parsing can never overwrite a profile the user has curated.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Uuid,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.user import User


class ResumeParsingStatus(str, Enum):
    """Lifecycle of the text-extraction step."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    OCR_REQUIRED = "ocr_required"
    FAILED = "failed"


class Resume(Base, TimestampMixin):
    """A PDF resume uploaded by a user."""

    __tablename__ = "resumes"
    __table_args__ = (
        Index("ix_resumes_user_id_is_default", "user_id", "is_default"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    # Opaque storage handle. Never exposed through the API: clients only ever
    # receive a resume id and stream the file through an authenticated endpoint.
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    storage_backend: Mapped[str] = mapped_column(String(32), nullable=False, default="local")
    content_type: Mapped[str] = mapped_column(
        String(100), nullable=False, default="application/pdf"
    )
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    parsing_status: Mapped[ResumeParsingStatus] = mapped_column(
        SAEnum(
            ResumeParsingStatus,
            name="resume_parsing_status",
            native_enum=False,
            length=32,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        default=ResumeParsingStatus.PENDING,
    )
    # Safe, machine-readable failure code - never a parser traceback.
    parsing_error: Mapped[str | None] = mapped_column(String(100))
    parsed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    parsed_data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    skills: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    user: Mapped[User] = relationship()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Resume id={self.id} filename={self.original_filename!r}>"


__all__ = ["Resume", "ResumeParsingStatus"]
