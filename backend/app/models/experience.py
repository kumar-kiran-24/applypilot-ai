"""Work experience entries belonging to a candidate."""

from __future__ import annotations

import uuid
from datetime import date
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Boolean, Date, ForeignKey, String, Text, Uuid
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.candidate import Candidate


class EmploymentType(str, Enum):
    """Supported employment types."""

    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    CONTRACT = "contract"
    INTERNSHIP = "internship"
    FREELANCE = "freelance"
    TEMPORARY = "temporary"


class Experience(Base):
    """A role held by a candidate."""

    __tablename__ = "experiences"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("candidates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    company: Mapped[str] = mapped_column(String(200), nullable=False)
    designation: Mapped[str] = mapped_column(String(200), nullable=False)
    employment_type: Mapped[EmploymentType | None] = mapped_column(
        SAEnum(
            EmploymentType,
            name="employment_type",
            native_enum=False,
            length=32,
            values_callable=lambda enum: [member.value for member in enum],
        )
    )
    location: Mapped[str | None] = mapped_column(String(200))
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    description: Mapped[str | None] = mapped_column(Text)
    technologies: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    candidate: Mapped[Candidate] = relationship(back_populates="experiences")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Experience id={self.id} company={self.company!r}>"
