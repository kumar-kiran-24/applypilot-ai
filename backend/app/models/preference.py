"""Job search preferences - one row per candidate."""

from __future__ import annotations

import uuid
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import JSON, ForeignKey, Numeric, String, Uuid
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.candidate import Candidate


class WorkMode(str, Enum):
    """Supported working arrangements."""

    ONSITE = "onsite"
    HYBRID = "hybrid"
    REMOTE = "remote"


class RelocationPreference(str, Enum):
    """Willingness to relocate."""

    YES = "yes"
    NO = "no"
    NEGOTIABLE = "negotiable"
    UNKNOWN = "unknown"


class JobPreference(Base, TimestampMixin):
    """Declared job-search preferences for a candidate."""

    __tablename__ = "job_preferences"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("candidates.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    preferred_roles: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    preferred_locations: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    preferred_job_types: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    preferred_work_modes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    relocation_preference: Mapped[RelocationPreference] = mapped_column(
        SAEnum(
            RelocationPreference,
            name="relocation_preference",
            native_enum=False,
            length=32,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        default=RelocationPreference.UNKNOWN,
    )
    minimum_salary: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    expected_salary: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")

    candidate: Mapped[Candidate] = relationship(back_populates="preference")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<JobPreference id={self.id} candidate_id={self.candidate_id}>"
