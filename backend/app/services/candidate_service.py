"""Business logic for candidate profiles.

Responsibilities: uniqueness checks, existence checks, transaction boundaries
and answer-verification rules. Persistence details stay in the repository.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from app.core.exceptions import DuplicateResourceError, ResourceNotFoundError
from app.core.logging import get_logger
from app.models.application_answer import ApplicationAnswer, VerificationStatus
from app.models.candidate import Candidate
from app.models.certification import Certification
from app.models.education import Education
from app.models.experience import Experience
from app.models.preference import JobPreference
from app.models.project import Project
from app.models.user import User
from app.repositories.candidate_repository import CandidateRepository
from app.schemas.application_answer import ApplicationAnswerCreate, ApplicationAnswerUpdate
from app.schemas.candidate import CandidateCreate, CandidateUpdate
from app.schemas.certification import CertificationCreate
from app.schemas.education import EducationCreate
from app.schemas.experience import ExperienceCreate
from app.schemas.preference import JobPreferenceCreate, JobPreferenceUpdate
from app.schemas.project import ProjectCreate

logger = get_logger(__name__)

_REQUIRED_CANDIDATE_FIELDS = ("full_name", "email")


def _coerce_urls(values: dict[str, Any]) -> dict[str, Any]:
    """Convert pydantic URL objects into plain strings for the ORM."""
    for key, value in values.items():
        if key.endswith("_url") and value is not None and not isinstance(value, str):
            values[key] = str(value)
    return values


class CandidateService:
    """Coordinates candidate use-cases on top of the repository."""

    def __init__(self, repository: CandidateRepository) -> None:
        self.repository = repository

    # --- Candidates ----------------------------------------------------------
    async def create_candidate(self, payload: CandidateCreate) -> Candidate:
        email = str(payload.email)
        if await self.repository.get_candidate_by_email(email):
            raise DuplicateResourceError(
                f"A candidate with email '{email}' already exists.",
                code="duplicate_email",
            )

        values = _coerce_urls(payload.model_dump())
        values["email"] = email
        try:
            candidate = await self.repository.create_candidate(values)
            await self.repository.session.commit()
        except IntegrityError as exc:  # concurrent insert of the same email
            await self.repository.session.rollback()
            raise DuplicateResourceError(
                f"A candidate with email '{email}' already exists.",
                code="duplicate_email",
            ) from exc

        logger.info("Candidate created", extra={"candidate_id": str(candidate.id)})
        return candidate

    async def get_candidate(self, candidate_id: UUID) -> Candidate:
        candidate = await self.repository.get_candidate_detail(candidate_id)
        if candidate is None:
            raise ResourceNotFoundError(f"Candidate '{candidate_id}' was not found.")
        return candidate

    async def update_candidate(self, candidate_id: UUID, payload: CandidateUpdate) -> Candidate:
        candidate = await self.repository.get_candidate(candidate_id)
        if candidate is None:
            raise ResourceNotFoundError(f"Candidate '{candidate_id}' was not found.")

        values = _coerce_urls(payload.model_dump(exclude_unset=True, exclude_none=False))
        for field in _REQUIRED_CANDIDATE_FIELDS:
            if values.get(field) is None:
                values.pop(field, None)
        new_email = values.get("email")
        if isinstance(new_email, str) and new_email != candidate.email:
            existing = await self.repository.get_candidate_by_email(new_email)
            if existing is not None and existing.id != candidate.id:
                raise DuplicateResourceError(
                    f"A candidate with email '{new_email}' already exists.",
                    code="duplicate_email",
                )

        await self.repository.update_candidate(candidate, values)
        await self.repository.session.commit()
        await self.repository.session.refresh(candidate)
        logger.info("Candidate updated", extra={"candidate_id": str(candidate.id)})
        return candidate

    async def delete_candidate(self, candidate_id: UUID) -> UUID:
        candidate = await self.repository.get_candidate(candidate_id)
        if candidate is None:
            raise ResourceNotFoundError(f"Candidate '{candidate_id}' was not found.")
        await self.repository.delete_candidate(candidate)
        await self.repository.session.commit()
        logger.info("Candidate deleted", extra={"candidate_id": str(candidate_id)})
        return candidate_id

    # --- Authenticated profile ----------------------------------------------
    async def ensure_profile_for_user(self, user: User) -> Candidate:
        """Return the profile owned by ``user``, linking or creating it once.

        Candidate CRUD predates authentication, so a profile may already exist
        under the verified Google email. Linking it is safe because the email is
        verified by the login flow; a profile owned by another user is never
        reused.
        """
        candidate = await self.repository.get_candidate_by_user_id(user.id)
        if candidate is None:
            existing = await self.repository.get_candidate_by_email(user.email)
            if existing is not None and existing.user_id in (None, user.id):
                if existing.user_id is None:
                    existing.user_id = user.id
                    await self.repository.session.commit()
                candidate = existing
            else:
                candidate = await self.repository.create_candidate(
                    {
                        "user_id": user.id,
                        "full_name": user.full_name,
                        "email": user.email.lower(),
                    }
                )
                await self.repository.session.commit()

        detail = await self.repository.get_candidate_detail(candidate.id)
        if detail is None:  # pragma: no cover - deleted concurrently
            raise ResourceNotFoundError("Candidate profile was not found.")
        return detail

    async def update_profile_for_user(self, user: User, payload: CandidateUpdate) -> Candidate:
        candidate = await self.ensure_profile_for_user(user)
        values = _coerce_urls(payload.model_dump(exclude_unset=True, exclude_none=False))
        for field in _REQUIRED_CANDIDATE_FIELDS:
            if values.get(field) is None:
                values.pop(field, None)
        new_email = values.get("email")
        if isinstance(new_email, str) and new_email != candidate.email:
            clash = await self.repository.get_candidate_by_email(new_email)
            if clash is not None and clash.id != candidate.id:
                raise DuplicateResourceError(
                    f"A candidate with email '{new_email}' already exists.",
                    code="duplicate_email",
                )

        await self.repository.update_candidate(candidate, values)
        await self.repository.session.commit()
        await self.repository.session.refresh(candidate)
        logger.info(
            "Candidate profile updated", extra={"candidate_id": str(candidate.id)}
        )
        return await self.get_candidate(candidate.id)

    async def add_certification_for_user(
        self, user: User, payload: CertificationCreate
    ) -> Certification:
        candidate = await self.ensure_profile_for_user(user)
        values = _coerce_urls(payload.model_dump())
        certification = await self.repository.add_certification(candidate.id, values)
        await self.repository.session.commit()
        await self.repository.session.refresh(certification)
        return certification

    async def delete_certification_for_user(self, user: User, certification_id: UUID) -> UUID:
        candidate = await self.ensure_profile_for_user(user)
        certification = await self.repository.get_certification(certification_id)
        if certification is None or certification.candidate_id != candidate.id:
            raise ResourceNotFoundError("Certification was not found.")
        await self.repository.delete_certification(certification)
        await self.repository.session.commit()
        return certification_id

    # --- Education -----------------------------------------------------------
    async def _require_candidate(self, candidate_id: UUID) -> Candidate:
        candidate = await self.repository.get_candidate(candidate_id)
        if candidate is None:
            raise ResourceNotFoundError(f"Candidate '{candidate_id}' was not found.")
        return candidate

    async def add_education(self, candidate_id: UUID, payload: EducationCreate) -> Education:
        await self._require_candidate(candidate_id)
        education = await self.repository.add_education(candidate_id, payload.model_dump())
        await self.repository.session.commit()
        await self.repository.session.refresh(education)
        return education

    async def list_education(self, candidate_id: UUID) -> list[Education]:
        await self._require_candidate(candidate_id)
        return list(await self.repository.get_education_entries(candidate_id))

    async def delete_education(self, education_id: UUID) -> UUID:
        education = await self.repository.get_education(education_id)
        if education is None:
            raise ResourceNotFoundError(f"Education '{education_id}' was not found.")
        await self.repository.delete_education(education)
        await self.repository.session.commit()
        return education_id

    # --- Experience ----------------------------------------------------------
    async def add_experience(self, candidate_id: UUID, payload: ExperienceCreate) -> Experience:
        await self._require_candidate(candidate_id)
        experience = await self.repository.add_experience(candidate_id, payload.model_dump())
        await self.repository.session.commit()
        await self.repository.session.refresh(experience)
        return experience

    async def list_experience(self, candidate_id: UUID) -> list[Experience]:
        await self._require_candidate(candidate_id)
        return list(await self.repository.get_experience_entries(candidate_id))

    async def delete_experience(self, experience_id: UUID) -> UUID:
        experience = await self.repository.get_experience(experience_id)
        if experience is None:
            raise ResourceNotFoundError(f"Experience '{experience_id}' was not found.")
        await self.repository.delete_experience(experience)
        await self.repository.session.commit()
        return experience_id

    # --- Projects ------------------------------------------------------------
    async def add_project(self, candidate_id: UUID, payload: ProjectCreate) -> Project:
        await self._require_candidate(candidate_id)
        values = _coerce_urls(payload.model_dump())
        project = await self.repository.add_project(candidate_id, values)
        await self.repository.session.commit()
        await self.repository.session.refresh(project)
        return project

    async def list_projects(self, candidate_id: UUID) -> list[Project]:
        await self._require_candidate(candidate_id)
        return list(await self.repository.get_projects(candidate_id))

    async def delete_project(self, project_id: UUID) -> UUID:
        project = await self.repository.get_project(project_id)
        if project is None:
            raise ResourceNotFoundError(f"Project '{project_id}' was not found.")
        await self.repository.delete_project(project)
        await self.repository.session.commit()
        return project_id

    # --- Preferences ---------------------------------------------------------
    async def create_preference(
        self, candidate_id: UUID, payload: JobPreferenceCreate
    ) -> JobPreference:
        await self._require_candidate(candidate_id)
        if await self.repository.get_preference(candidate_id) is not None:
            raise DuplicateResourceError(
                f"Preferences for candidate '{candidate_id}' already exist.",
                code="duplicate_preference",
            )

        values = self._serialise_preference(payload.model_dump())
        preference = await self.repository.create_preference(candidate_id, values)
        await self.repository.session.commit()
        await self.repository.session.refresh(preference)
        return preference

    async def get_preference(self, candidate_id: UUID) -> JobPreference:
        await self._require_candidate(candidate_id)
        preference = await self.repository.get_preference(candidate_id)
        if preference is None:
            raise ResourceNotFoundError(
                f"Preferences for candidate '{candidate_id}' were not found."
            )
        return preference

    async def update_preference(
        self, candidate_id: UUID, payload: JobPreferenceUpdate
    ) -> JobPreference:
        await self._require_candidate(candidate_id)
        preference = await self.repository.get_preference(candidate_id)
        values = self._serialise_preference(payload.model_dump(exclude_unset=True))

        if preference is None:
            preference = await self.repository.create_preference(candidate_id, values)
        else:
            await self.repository.update_preference(preference, values)

        await self.repository.session.commit()
        await self.repository.session.refresh(preference)
        return preference

    @staticmethod
    def _serialise_preference(values: dict[str, Any]) -> dict[str, Any]:
        """Store enums as plain strings inside JSON-friendly columns."""
        for key in ("preferred_job_types", "preferred_work_modes"):
            items = values.get(key)
            if isinstance(items, list):
                values[key] = [
                    item.value if hasattr(item, "value") else str(item) for item in items
                ]
        relocation = values.get("relocation_preference")
        if hasattr(relocation, "value"):
            values["relocation_preference"] = relocation.value
        return values

    # --- Application answers -------------------------------------------------
    async def add_answer(
        self, candidate_id: UUID, payload: ApplicationAnswerCreate
    ) -> ApplicationAnswer:
        await self._require_candidate(candidate_id)
        if await self.repository.get_answer_by_key(candidate_id, payload.question_key) is not None:
            raise DuplicateResourceError(
                f"An answer for question '{payload.question_key}' already exists.",
                code="duplicate_answer",
            )

        values = payload.model_dump()
        values["verified_at"] = (
            datetime.now(timezone.utc)
            if payload.verification_status is VerificationStatus.VERIFIED
            else None
        )
        answer = await self.repository.create_answer(candidate_id, values)
        await self.repository.session.commit()
        await self.repository.session.refresh(answer)
        return answer

    async def list_answers(self, candidate_id: UUID) -> list[ApplicationAnswer]:
        await self._require_candidate(candidate_id)
        return list(await self.repository.get_answers(candidate_id))

    async def upsert_answer(
        self, candidate_id: UUID, question_key: str, payload: ApplicationAnswerUpdate
    ) -> ApplicationAnswer:
        """Update an existing answer or create it when it does not exist yet."""
        await self._require_candidate(candidate_id)
        normalised_key = question_key.strip().lower()
        values = payload.model_dump(exclude_unset=True)

        existing = await self.repository.get_answer_by_key(candidate_id, normalised_key)
        if existing is None:
            if not values.get("question_text"):
                raise ResourceNotFoundError(
                    f"No answer stored for question '{normalised_key}'; "
                    "question_text is required to create it."
                )
            create_values = {
                "question_key": normalised_key,
                "question_text": values["question_text"],
                "answer": values.get("answer"),
                "answer_type": values.get("answer_type") or "text",
                "verification_status": values.get("verification_status") or "unknown",
            }
            create_payload = ApplicationAnswerCreate.model_validate(create_values)
            return await self.add_answer(candidate_id, create_payload)

        answer = await self.repository.update_answer(existing, values)
        self._sync_verification(answer, payload.verification_status)
        await self.repository.session.commit()
        await self.repository.session.refresh(answer)
        return answer

    @staticmethod
    def _sync_verification(answer: ApplicationAnswer, status: VerificationStatus | None) -> None:
        """Keep ``verified_at`` consistent with the verification status."""
        if status is None:
            return
        if status is VerificationStatus.VERIFIED:
            if answer.verified_at is None:
                answer.verified_at = datetime.now(timezone.utc)
        else:
            answer.verified_at = None
