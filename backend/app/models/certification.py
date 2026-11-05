"""Professional certifications belonging to a candidate.

Kept as its own table (rather than free text on the profile) so multiple
certifications can be listed, edited and reused in application questionnaires.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import Date, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.candidate import Candidate


class Certification(Base):
    """A credential or certification held by a candidate."""

    __tablename__ = "certifications"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("candidates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    issuer: Mapped[str | None] = mapped_column(String(200))
    issue_date: Mapped[date | None] = mapped_column(Date)
    expiry_date: Mapped[date | None] = mapped_column(Date)
    credential_id: Mapped[str | None] = mapped_column(String(200))
    credential_url: Mapped[str | None] = mapped_column(String(500))

    candidate: Mapped[Candidate] = relationship(back_populates="certifications")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Certification id={self.id} name={self.name!r}>"


__all__ = ["Certification"]
