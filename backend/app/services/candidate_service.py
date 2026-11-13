"""Business logic for candidate profiles.

Responsibilities: uniqueness checks, existence checks, transaction boundaries
and answer-verification rules. Persistence details stay in the repository.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from app.core.exceptions import (
    DuplicateResourceError,
    InvalidOperationError,
    ResourceNotFoundError,
)
from app.core.logging import get_logger
from app.models.application import AnswerSource
from app.models.application_answer import ApplicationAnswer, VerificationStatus
from app.models.candidate import Candidate
from app.models.certification import Certification
from app.models.education import Education
from app.models.experience import Experience
from app.models.preference import JobPreference
from app.models.project import Project
from app.models.user import User
from app.repositories.candidate_repository import CandidateRepository
from app.schemas.application_answer import (
    AnswerConflictResolution,
    ApplicationAnswerCreate,
    ApplicationAnswerUpdate,
)
from app.schemas.candidate import CandidateCreate, CandidateUpdate
from app.schemas.certification import CertificationCreate
from app.schemas.education import EducationCreate, EducationUpdate
from app.schemas.experience import ExperienceCreate, ExperienceUpdate
from app.schemas.preference import JobPreferenceCreate, JobPreferenceUpdate
from app.schemas.project import ProjectCreate, ProjectUpdate
from app.services.answer_library import (
    AnswerEvidence,
    AnswerLibrary,
    KnowledgeReport,
    SavedAnswerView,
)
from app.services.answer_safety import is_forbidden_answer_storage
from app.services.application_answer_matching import (
    AnswerBankReview,
    ApplicationAnswerMatcher,
)
from app.services.application_questionnaire import profile_answers

logger = get_logger(__name__)

_REQUIRED_CANDIDATE_FIELDS = ("full_name", "email")


#: Fields shown on the profile completion meter, in display order.
PROFILE_FIELDS: tuple[tuple[str, str, str, bool], ...] = (
    ("personal.full_name", "Full legal name", "personal", True),
    ("personal.preferred_name", "Preferred name", "personal", False),
    ("contact.email", "Email address", "contact", True),
    ("contact.phone", "Phone number", "contact", True),
    ("contact.linkedin", "LinkedIn profile", "contact", False),
    ("contact.github", "GitHub profile", "contact", False),
    ("contact.portfolio", "Portfolio URL", "contact", False),
    ("address.line1", "Address line 1", "address", True),
    ("address.city", "City", "address", True),
    ("address.region", "State / region", "address", False),
    ("address.postal_code", "Postal code", "address", False),
    ("address.country", "Country", "address", True),
    ("education.entries", "At least one education record", "education", True),
    ("experience.entries", "At least one employment record", "experience", True),
    ("skills", "Technical skills", "skills", True),
    ("projects.entries", "At least one project", "projects", False),
    ("certifications.entries", "At least one certification", "certifications", False),
    ("authorization.status", "Work authorization", "authorization", True),
    ("authorization.sponsorship", "Visa sponsorship requirement", "authorization", True),
    ("compensation.expected_salary", "Expected salary", "compensation", False),
    ("availability.notice_period", "Notice period", "availability", False),
    ("availability.available_from", "Available from", "availability", False),
    ("availability.work_mode", "Work mode preference", "availability", False),
    ("availability.relocation", "Relocation preference", "availability", False),
)


def _filled(value: Any) -> bool:
    """Whether a profile value counts as answered without inventing anything."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) > 0
    return True


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

    async def completeness_for_user(self, user: User) -> dict[str, Any]:
        """Report which profile fields are filled, without judging their truth."""
        candidate = await self.ensure_profile_for_user(user)
        preference = candidate.preference
        values: dict[str, Any] = {
            "personal.full_name": candidate.full_name,
            "personal.preferred_name": candidate.preferred_name,
            "contact.email": candidate.email,
            "contact.phone": candidate.phone,
            "contact.linkedin": candidate.linkedin_url,
            "contact.github": candidate.github_url,
            "contact.portfolio": candidate.portfolio_url,
            "address.line1": candidate.address_line1,
            "address.city": candidate.city,
            "address.region": candidate.region,
            "address.postal_code": candidate.postal_code,
            "address.country": candidate.country,
            "education.entries": candidate.education,
            "experience.entries": candidate.experiences,
            "skills": candidate.skills,
            "projects.entries": candidate.projects,
            "certifications.entries": candidate.certifications,
            "authorization.status": candidate.work_authorization,
            "authorization.sponsorship": candidate.requires_sponsorship,
            "compensation.expected_salary": preference.expected_salary if preference else None,
            "availability.notice_period": candidate.notice_period_days,
            "availability.available_from": candidate.available_from,
            "availability.work_mode": preference.preferred_work_modes if preference else None,
            "availability.relocation": (
                preference.relocation_preference
                if preference and preference.relocation_preference.value != "unknown"
                else None
            ),
        }
        fields = [
            {
                "field": field,
                "label": label,
                "section": section,
                "required": required,
                "complete": _filled(values.get(field)),
            }
            for field, label, section, required in PROFILE_FIELDS
        ]
        completed = sum(1 for item in fields if item["complete"])
        missing = [item["field"] for item in fields if not item["complete"]]
        return {
            "percent": round((completed / len(fields)) * 100) if fields else 0,
            "completed": completed,
            "total": len(fields),
            "missing_fields": missing,
            "missing_required": [
                item["field"] for item in fields if item["required"] and not item["complete"]
            ],
            "fields": fields,
        }

    # --- Authenticated sub-resources ----------------------------------------
    async def update_education_for_user(
        self, user: User, education_id: UUID, payload: EducationUpdate
    ) -> Education:
        candidate = await self.ensure_profile_for_user(user)
        education = await self.repository.get_education(education_id)
        if education is None or education.candidate_id != candidate.id:
            raise ResourceNotFoundError("Education entry was not found.")
        values = payload.model_dump(exclude_unset=True)
        await self.repository.update_education(education, values)
        await self.repository.session.commit()
        await self.repository.session.refresh(education)
        return education

    async def delete_education_for_user(self, user: User, education_id: UUID) -> UUID:
        candidate = await self.ensure_profile_for_user(user)
        education = await self.repository.get_education(education_id)
        if education is None or education.candidate_id != candidate.id:
            raise ResourceNotFoundError("Education entry was not found.")
        await self.repository.delete_education(education)
        await self.repository.session.commit()
        return education_id

    async def update_experience_for_user(
        self, user: User, experience_id: UUID, payload: ExperienceUpdate
    ) -> Experience:
        candidate = await self.ensure_profile_for_user(user)
        experience = await self.repository.get_experience(experience_id)
        if experience is None or experience.candidate_id != candidate.id:
            raise ResourceNotFoundError("Experience entry was not found.")
        values = payload.model_dump(exclude_unset=True)
        await self.repository.update_experience(experience, values)
        await self.repository.session.commit()
        await self.repository.session.refresh(experience)
        return experience

    async def delete_experience_for_user(self, user: User, experience_id: UUID) -> UUID:
        candidate = await self.ensure_profile_for_user(user)
        experience = await self.repository.get_experience(experience_id)
        if experience is None or experience.candidate_id != candidate.id:
            raise ResourceNotFoundError("Experience entry was not found.")
        await self.repository.delete_experience(experience)
        await self.repository.session.commit()
        return experience_id

    async def update_project_for_user(
        self, user: User, project_id: UUID, payload: ProjectUpdate
    ) -> Project:
        candidate = await self.ensure_profile_for_user(user)
        project = await self.repository.get_project(project_id)
        if project is None or project.candidate_id != candidate.id:
            raise ResourceNotFoundError("Project was not found.")
        values = _coerce_urls(payload.model_dump(exclude_unset=True))
        await self.repository.update_project(project, values)
        await self.repository.session.commit()
        await self.repository.session.refresh(project)
        return project

    async def delete_project_for_user(self, user: User, project_id: UUID) -> UUID:
        candidate = await self.ensure_profile_for_user(user)
        project = await self.repository.get_project(project_id)
        if project is None or project.candidate_id != candidate.id:
            raise ResourceNotFoundError("Project was not found.")
        await self.repository.delete_project(project)
        await self.repository.session.commit()
        return project_id

    async def get_preferences_for_user(self, user: User) -> JobPreference | None:
        candidate = await self.ensure_profile_for_user(user)
        return await self.repository.get_preference(candidate.id)

    async def update_preferences_for_user(
        self, user: User, payload: JobPreferenceUpdate
    ) -> JobPreference:
        candidate = await self.ensure_profile_for_user(user)
        preference = await self.repository.get_preference(candidate.id)
        values = self._serialise_preference(payload.model_dump(exclude_unset=True))
        if preference is None:
            preference = await self.repository.create_preference(candidate.id, values)
        else:
            await self.repository.update_preference(preference, values)
        await self.repository.session.commit()
        await self.repository.session.refresh(preference)
        return preference

    async def list_answers_for_user(
        self,
        user: User,
        *,
        query: str | None = None,
        category: str | None = None,
        trust: str | None = None,
    ) -> list[ApplicationAnswer]:
        """List the user's saved answers, optionally filtered by the library.

        Filtering is deterministic and read-only: a category is derived from
        the question wording and a trust level from stored provenance.
        """
        candidate = await self.ensure_profile_for_user(user)
        answers = list(await self.repository.get_answers(candidate.id))
        if query or category or trust:
            library = AnswerLibrary()
            answers = [
                answer
                for answer in answers
                if library.matches(
                    answer,
                    query=query,
                    category=category,
                    trust=trust,
                    library=library,
                )
            ]
        return answers

    async def saved_answers_for_user(
        self,
        user: User,
        *,
        query: str | None = None,
        category: str | None = None,
        trust: str | None = None,
    ) -> list[SavedAnswerView]:
        """Saved answers with derived category, trust and freshness."""
        candidate = await self.ensure_profile_for_user(user)
        answers = await self.list_answers_for_user(
            user, query=query, category=category, trust=trust
        )
        library = AnswerLibrary()
        return list(
            library.describe_many(
                answers,
                candidate=candidate,
                profile_values=profile_answers(candidate, user),
            )
        )

    async def answer_detail_for_user(
        self, user: User, question_key: str
    ) -> tuple[SavedAnswerView, tuple[AnswerEvidence, ...]]:
        """Return one saved answer with its evidence and detected conflicts."""
        candidate = await self.ensure_profile_for_user(user)
        normalised = question_key.strip().lower()
        answer = await self.repository.get_answer_by_key(candidate.id, normalised)
        if answer is None:
            raise ResourceNotFoundError(
                f"No reusable answer is stored for '{normalised}'."
            )
        values = profile_answers(candidate, user)
        library = AnswerLibrary()
        all_answers = list(await self.repository.get_answers(candidate.id))
        views = library.describe_many(
            all_answers, candidate=candidate, profile_values=values
        )
        view = next(
            (item for item in views if item.answer.id == answer.id), None
        ) or library.describe(answer)
        return view, library.evidence_for(answer, profile_values=values)

    async def answer_categories_for_user(self, user: User) -> dict[str, Any]:
        """Category and trust counts for the answer library filters."""
        candidate = await self.ensure_profile_for_user(user)
        answers = list(await self.repository.get_answers(candidate.id))
        library = AnswerLibrary()
        counts, present = library.category_counts(answers)
        trust_counts: dict[str, int] = {}
        for answer in answers:
            level = library.trust_for(answer).level
            trust_counts[level] = trust_counts.get(level, 0) + 1
        return {
            "total": len(answers),
            "categories": [
                {
                    "category": category,
                    "label": library.category_label(category),
                    "count": counts[category],
                }
                for category in present
            ],
            "trust_levels": trust_counts,
        }

    async def knowledge_for_user(self, user: User) -> KnowledgeReport:
        """Report missing, conflicting, outdated and unverified knowledge.

        Read-only: nothing here edits the profile, a saved answer or a verified
        fact. Every item must be resolved explicitly by the user.
        """
        candidate = await self.ensure_profile_for_user(user)
        answers = list(await self.repository.get_answers(candidate.id))
        completeness = await self.completeness_for_user(user)
        library = AnswerLibrary()
        return library.knowledge_report(
            answers,
            candidate=candidate,
            profile_values=profile_answers(candidate, user),
            profile_fields=completeness.get("fields", []),
        )

    async def upsert_answer_for_user(
        self, user: User, question_key: str, payload: ApplicationAnswerUpdate
    ) -> ApplicationAnswer:
        candidate = await self.ensure_profile_for_user(user)
        return await self.upsert_answer(candidate.id, question_key, payload)

    async def review_answer_bank_for_user(self, user: User) -> AnswerBankReview:
        """Report stale, conflicting and unverified reusable answers.

        Only answers owned by this user are inspected. Stale answers stay in
        the bank, but they are never reused without a fresh confirmation.
        """
        candidate = await self.ensure_profile_for_user(user)
        answers = list(await self.repository.get_answers(candidate.id))
        matcher = ApplicationAnswerMatcher()
        stale = matcher.detect_stale_answers(answers)
        conflicts = matcher.detect_answer_conflicts(
            candidate,
            answers,
            profile_values=profile_answers(candidate, user),
        )
        unverified = tuple(
            answer.question_key
            for answer in answers
            if answer.verification_status is not VerificationStatus.VERIFIED
        )
        return AnswerBankReview(
            total=len(answers),
            verified=sum(
                1
                for answer in answers
                if answer.verification_status is VerificationStatus.VERIFIED
            ),
            stale=stale,
            conflicts=conflicts,
            unverified=unverified,
        )

    async def resolve_answer_conflict_for_user(
        self, user: User, question_key: str, payload: AnswerConflictResolution
    ) -> ApplicationAnswer:
        """Resolve a stale/conflicting reusable answer with explicit intent.

        ``keep_stored`` re-confirms the stored value, ``use_profile`` adopts
        the current verified profile value, and ``edited`` stores a corrected
        value. Every path records a fresh confirmation timestamp.
        """
        candidate = await self.ensure_profile_for_user(user)
        normalised = question_key.strip().lower()
        answer = await self.repository.get_answer_by_key(candidate.id, normalised)
        if answer is None:
            raise ResourceNotFoundError(
                f"No reusable answer is stored for '{normalised}'."
            )
        if answer.is_legal:
            raise InvalidOperationError(
                "This answer is a legal declaration and is never reused.",
                code="legal_answer_not_reusable",
            )
        if is_forbidden_answer_storage(answer.question_key, answer.question_text):
            raise InvalidOperationError(
                "Passwords, one-time codes, authentication cookies and payment "
                "details are never stored or reused.",
                code="forbidden_answer",
            )

        matcher = ApplicationAnswerMatcher()
        if payload.resolution == "use_profile":
            category = matcher.identify_field_category(
                f"{answer.question_key} {answer.question_text}"
            )
            values = profile_answers(candidate, user)
            value = values.get(category or "")
            if not value:
                raise InvalidOperationError(
                    "No verified profile value exists for this question. "
                    "Edit the answer instead.",
                    code="no_profile_value",
                )
        elif payload.resolution == "edited":
            value = (payload.answer or "").strip()
            if not value:
                raise InvalidOperationError(
                    "Enter the corrected answer value.", code="invalid_answer"
                )
        else:
            value = answer.answer or ""
            if not value:
                raise InvalidOperationError(
                    "The stored answer is empty. Edit it instead.",
                    code="invalid_answer",
                )

        now = datetime.now(timezone.utc)
        answer.answer = value
        answer.verification_status = VerificationStatus.VERIFIED
        answer.verified_at = now
        answer.last_confirmed_at = now
        answer.answer_source = AnswerSource.CONFIRMED_REUSABLE_ANSWER
        answer.confidence = 1.0
        await self.repository.session.commit()
        await self.repository.session.refresh(answer)
        return answer

    async def delete_answer_for_user(self, user: User, question_key: str) -> str:
        candidate = await self.ensure_profile_for_user(user)
        normalised = question_key.strip().lower()
        answer = await self.repository.get_answer_by_key(candidate.id, normalised)
        if answer is None:
            raise ResourceNotFoundError(
                f"No reusable answer is stored for '{normalised}'."
            )
        await self.repository.delete_answer(answer)
        await self.repository.session.commit()
        return normalised

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
        if is_forbidden_answer_storage(payload.question_key, payload.question_text):
            raise InvalidOperationError(
                "Passwords, one-time codes, authentication cookies and payment "
                "details are never stored or reused.",
                code="forbidden_answer",
            )
        if payload.is_legal:
            raise InvalidOperationError(
                "Legal declarations are confirmed per application and cannot be "
                "stored as reusable answers.",
                code="legal_answer_not_reusable",
            )
        if await self.repository.get_answer_by_key(candidate_id, payload.question_key) is not None:
            raise DuplicateResourceError(
                f"An answer for question '{payload.question_key}' already exists.",
                code="duplicate_answer",
            )

        verified = payload.verification_status is VerificationStatus.VERIFIED
        now = datetime.now(timezone.utc)
        values = payload.model_dump()
        values["verified_at"] = now if verified else None
        values["last_confirmed_at"] = now if verified else None
        values["answer_source"] = payload.answer_source or (
            AnswerSource.CONFIRMED_REUSABLE_ANSWER
            if verified
            else AnswerSource.USER_ENTERED
        )
        values["confidence"] = (
            payload.confidence
            if payload.confidence is not None
            else (1.0 if verified else 0.5)
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
        question_text = values.get("question_text") or (
            existing.question_text if existing is not None else ""
        )
        if is_forbidden_answer_storage(normalised_key, question_text):
            raise InvalidOperationError(
                "Passwords, one-time codes, authentication cookies and payment "
                "details are never stored or reused.",
                code="forbidden_answer",
            )
        if payload.is_legal:
            raise InvalidOperationError(
                "Legal declarations are confirmed per application and cannot be "
                "stored as reusable answers.",
                code="legal_answer_not_reusable",
            )

        if existing is not None and existing.is_legal:
            raise InvalidOperationError(
                "This answer is a legal declaration and is never reused.",
                code="legal_answer_not_reusable",
            )
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
                "is_sensitive": bool(values.get("is_sensitive")),
                "is_legal": bool(values.get("is_legal")),
                "answer_source": values.get("answer_source"),
                "confidence": values.get("confidence"),
                "source_record_id": values.get("source_record_id"),
                "freshness_days": values.get("freshness_days"),
            }
            create_payload = ApplicationAnswerCreate.model_validate(create_values)
            return await self.add_answer(candidate_id, create_payload)

        previous_value = (existing.answer or "").strip()
        incoming_value = (values.get("answer") or "").strip()
        answer_changed = "answer" in values and incoming_value != previous_value

        answer = await self.repository.update_answer(existing, values)
        if answer_changed and payload.verification_status is None:
            # Changed wording is no longer the value the user confirmed. Keep
            # explicit provenance overrides, but never leave a stale
            # "verified" label on an edited answer.
            answer.verification_status = VerificationStatus.UNKNOWN
            answer.verified_at = None
            answer.last_confirmed_at = None
            if "answer_source" not in values:
                answer.answer_source = AnswerSource.USER_ENTERED
            if "confidence" not in values:
                answer.confidence = 0.5
        self._sync_verification(
            answer,
            payload.verification_status,
            source_explicit="answer_source" in values,
            confidence_explicit="confidence" in values,
        )
        await self.repository.session.commit()
        await self.repository.session.refresh(answer)
        return answer

    @staticmethod
    def _sync_verification(
        answer: ApplicationAnswer,
        status: VerificationStatus | None,
        *,
        source_explicit: bool = False,
        confidence_explicit: bool = False,
    ) -> None:
        """Keep verification, provenance and confirmation timestamps aligned."""
        if status is None:
            return
        if status is VerificationStatus.VERIFIED:
            now = datetime.now(timezone.utc)
            if answer.verified_at is None:
                answer.verified_at = now
            # A fresh confirmation always refreshes the reuse timestamp.
            answer.last_confirmed_at = now
            if not source_explicit:
                answer.answer_source = AnswerSource.CONFIRMED_REUSABLE_ANSWER
            if not confidence_explicit:
                answer.confidence = 1.0
        else:
            answer.verified_at = None
            answer.last_confirmed_at = None
