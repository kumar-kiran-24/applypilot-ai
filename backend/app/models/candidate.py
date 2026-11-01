"""Candidate aggregate root."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.application_answer import ApplicationAnswer
    from app.models.education import Education
    from app.models.experience import Experience
    from app.models.preference import JobPreference
    from app.models.project import Project


class Candidate(Base, TimestampMixin):
    """A job seeker whose profile drives every downstream agent."""

    __tablename__ = "candidates"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(32))
    location: Mapped[str | None] = mapped_column(String(200))
    linkedin_url: Mapped[str | None] = mapped_column(String(500))
    github_url: Mapped[str | None] = mapped_column(String(500))
    portfolio_url: Mapped[str | None] = mapped_column(String(500))
    professional_summary: Mapped[str | None] = mapped_column(Text)

    education: Mapped[list[Education]] = relationship(
        back_populates="candidate",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Education.start_date.desc()",
    )
    experiences: Mapped[list[Experience]] = relationship(
        back_populates="candidate",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Experience.start_date.desc()",
    )
    projects: Mapped[list[Project]] = relationship(
        back_populates="candidate",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Project.start_date.desc()",
    )
    preference: Mapped[JobPreference | None] = relationship(
        back_populates="candidate",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )
    application_answers: Mapped[list[ApplicationAnswer]] = relationship(
        back_populates="candidate",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ApplicationAnswer.question_key",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Candidate id={self.id} email={self.email!r}>"
