"""Candidate aggregate root."""

from __future__ import annotations

import uuid
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Boolean, Date, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.application_answer import ApplicationAnswer
    from app.models.certification import Certification
    from app.models.education import Education
    from app.models.experience import Experience
    from app.models.preference import JobPreference
    from app.models.project import Project
    from app.models.user import User


class Candidate(Base, TimestampMixin):
    """A job seeker whose profile drives every downstream agent."""

    __tablename__ = "candidates"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Optional owner. Candidate CRUD predates authentication, so existing rows
    # may have no user; authenticated profile access links by verified email.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        unique=True,
        index=True,
    )
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    preferred_name: Mapped[str | None] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(32))
    phone_country_code: Mapped[str | None] = mapped_column(String(8))
    location: Mapped[str | None] = mapped_column(String(200))
    address_line1: Mapped[str | None] = mapped_column(String(300))
    address_line2: Mapped[str | None] = mapped_column(String(300))
    city: Mapped[str | None] = mapped_column(String(120))
    region: Mapped[str | None] = mapped_column(String(120))
    postal_code: Mapped[str | None] = mapped_column(String(32))
    country: Mapped[str | None] = mapped_column(String(100))
    linkedin_url: Mapped[str | None] = mapped_column(String(500))
    github_url: Mapped[str | None] = mapped_column(String(500))
    portfolio_url: Mapped[str | None] = mapped_column(String(500))
    professional_summary: Mapped[str | None] = mapped_column(Text)
    # Verified technical skills, curated by the user. Resume skills are only a
    # suggestion and are never copied here automatically.
    skills: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    # Application-form facts. Work authorization and sponsorship are legally
    # sensitive and must be confirmed by the user before submission.
    work_authorization: Mapped[str | None] = mapped_column(String(200))
    requires_sponsorship: Mapped[bool | None] = mapped_column(Boolean)
    notice_period_days: Mapped[int | None] = mapped_column(Integer)
    available_from: Mapped[date | None] = mapped_column(Date)

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
    certifications: Mapped[list[Certification]] = relationship(
        back_populates="candidate",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Certification.name",
    )
    user: Mapped[User | None] = relationship()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Candidate id={self.id} email={self.email!r}>"
