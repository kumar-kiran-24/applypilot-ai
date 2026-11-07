"""Asynchronous persistence layer for the candidate aggregate.

Only this layer issues SQL. Services orchestrate it and API endpoints never see
a ``Session`` or a query.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.application_answer import ApplicationAnswer
from app.models.candidate import Candidate
from app.models.certification import Certification
from app.models.education import Education
from app.models.experience import Experience
from app.models.preference import JobPreference
from app.models.project import Project

_CANDIDATE_DETAIL_OPTIONS = (
    selectinload(Candidate.education),
    selectinload(Candidate.experiences),
    selectinload(Candidate.projects),
    selectinload(Candidate.preference),
    selectinload(Candidate.certifications),
    selectinload(Candidate.application_answers),
)


class CandidateRepository:
    """Data access for candidates and their nested records."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- Candidates ----------------------------------------------------------
    async def create_candidate(self, values: Mapping[str, Any]) -> Candidate:
        candidate = Candidate(**dict(values))
        self.session.add(candidate)
        await self.session.flush()
        await self.session.refresh(candidate)
        return candidate

    async def get_candidate(self, candidate_id: UUID) -> Candidate | None:
        return await self.session.get(Candidate, candidate_id)

    async def get_candidate_detail(self, candidate_id: UUID) -> Candidate | None:
        stmt: Select[tuple[Candidate]] = (
            select(Candidate)
            .where(Candidate.id == candidate_id)
            .options(*_CANDIDATE_DETAIL_OPTIONS)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_candidate_by_email(self, email: str) -> Candidate | None:
        stmt = select(Candidate).where(Candidate.email == email.lower())
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_candidate_by_user_id(self, user_id: UUID) -> Candidate | None:
        stmt = select(Candidate).where(Candidate.user_id == user_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_candidates(self, *, limit: int = 50, offset: int = 0) -> Sequence[Candidate]:
        stmt = select(Candidate).order_by(Candidate.created_at.desc()).limit(limit).offset(offset)
        return (await self.session.execute(stmt)).scalars().all()

    async def update_candidate(self, candidate: Candidate, values: Mapping[str, Any]) -> Candidate:
        for field, value in values.items():
            setattr(candidate, field, value)
        await self.session.flush()
        return candidate

    async def delete_candidate(self, candidate: Candidate) -> None:
        await self.session.delete(candidate)
        await self.session.flush()

    # --- Education -----------------------------------------------------------
    async def add_education(self, candidate_id: UUID, values: Mapping[str, Any]) -> Education:
        education = Education(candidate_id=candidate_id, **dict(values))
        self.session.add(education)
        await self.session.flush()
        await self.session.refresh(education)
        return education

    async def get_education_entries(self, candidate_id: UUID) -> Sequence[Education]:
        stmt = (
            select(Education)
            .where(Education.candidate_id == candidate_id)
            .order_by(Education.start_date.desc().nullslast(), Education.institution)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def get_education(self, education_id: UUID) -> Education | None:
        return await self.session.get(Education, education_id)

    async def update_education(
        self, education: Education, values: Mapping[str, Any]
    ) -> Education:
        for field, value in values.items():
            setattr(education, field, value)
        await self.session.flush()
        return education

    async def delete_education(self, education: Education) -> None:
        await self.session.delete(education)
        await self.session.flush()

    # --- Experience ----------------------------------------------------------
    async def add_experience(self, candidate_id: UUID, values: Mapping[str, Any]) -> Experience:
        experience = Experience(candidate_id=candidate_id, **dict(values))
        self.session.add(experience)
        await self.session.flush()
        await self.session.refresh(experience)
        return experience

    async def get_experience_entries(self, candidate_id: UUID) -> Sequence[Experience]:
        stmt = (
            select(Experience)
            .where(Experience.candidate_id == candidate_id)
            .order_by(Experience.start_date.desc().nullslast(), Experience.company)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def get_experience(self, experience_id: UUID) -> Experience | None:
        return await self.session.get(Experience, experience_id)

    async def update_experience(
        self, experience: Experience, values: Mapping[str, Any]
    ) -> Experience:
        for field, value in values.items():
            setattr(experience, field, value)
        await self.session.flush()
        return experience

    async def delete_experience(self, experience: Experience) -> None:
        await self.session.delete(experience)
        await self.session.flush()

    # --- Projects ------------------------------------------------------------
    async def add_project(self, candidate_id: UUID, values: Mapping[str, Any]) -> Project:
        project = Project(candidate_id=candidate_id, **dict(values))
        self.session.add(project)
        await self.session.flush()
        await self.session.refresh(project)
        return project

    async def get_projects(self, candidate_id: UUID) -> Sequence[Project]:
        stmt = (
            select(Project)
            .where(Project.candidate_id == candidate_id)
            .order_by(Project.start_date.desc().nullslast(), Project.name)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def get_project(self, project_id: UUID) -> Project | None:
        return await self.session.get(Project, project_id)

    async def update_project(self, project: Project, values: Mapping[str, Any]) -> Project:
        for field, value in values.items():
            setattr(project, field, value)
        await self.session.flush()
        return project

    async def delete_project(self, project: Project) -> None:
        await self.session.delete(project)
        await self.session.flush()

    # --- Job preferences -----------------------------------------------------
    async def get_preference(self, candidate_id: UUID) -> JobPreference | None:
        stmt = select(JobPreference).where(JobPreference.candidate_id == candidate_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def create_preference(
        self, candidate_id: UUID, values: Mapping[str, Any]
    ) -> JobPreference:
        preference = JobPreference(candidate_id=candidate_id, **dict(values))
        self.session.add(preference)
        await self.session.flush()
        await self.session.refresh(preference)
        return preference

    async def update_preference(
        self, preference: JobPreference, values: Mapping[str, Any]
    ) -> JobPreference:
        for field, value in values.items():
            setattr(preference, field, value)
        await self.session.flush()
        return preference

    # --- Application answers -------------------------------------------------
    async def create_answer(
        self, candidate_id: UUID, values: Mapping[str, Any]
    ) -> ApplicationAnswer:
        answer = ApplicationAnswer(candidate_id=candidate_id, **dict(values))
        self.session.add(answer)
        await self.session.flush()
        await self.session.refresh(answer)
        return answer

    async def get_answers(self, candidate_id: UUID) -> Sequence[ApplicationAnswer]:
        stmt = (
            select(ApplicationAnswer)
            .where(ApplicationAnswer.candidate_id == candidate_id)
            .order_by(ApplicationAnswer.question_key)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def get_answer_by_key(
        self, candidate_id: UUID, question_key: str
    ) -> ApplicationAnswer | None:
        stmt = select(ApplicationAnswer).where(
            ApplicationAnswer.candidate_id == candidate_id,
            ApplicationAnswer.question_key == question_key,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def update_answer(
        self, answer: ApplicationAnswer, values: Mapping[str, Any]
    ) -> ApplicationAnswer:
        for field, value in values.items():
            setattr(answer, field, value)
        await self.session.flush()
        return answer

    async def delete_answer(self, answer: ApplicationAnswer) -> None:
        await self.session.delete(answer)
        await self.session.flush()

    # --- Certifications ------------------------------------------------------
    async def add_certification(
        self, candidate_id: UUID, values: Mapping[str, Any]
    ) -> Certification:
        certification = Certification(candidate_id=candidate_id, **dict(values))
        self.session.add(certification)
        await self.session.flush()
        await self.session.refresh(certification)
        return certification

    async def get_certifications(self, candidate_id: UUID) -> Sequence[Certification]:
        stmt = (
            select(Certification)
            .where(Certification.candidate_id == candidate_id)
            .order_by(Certification.name)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def get_certification(self, certification_id: UUID) -> Certification | None:
        return await self.session.get(Certification, certification_id)

    async def delete_certification(self, certification: Certification) -> None:
        await self.session.delete(certification)
        await self.session.flush()
