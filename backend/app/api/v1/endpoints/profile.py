"""Authenticated candidate-profile endpoints.

These endpoints expose the profile that drives application questionnaires. They
are separate from the older unauthenticated candidate CRUD routes and always
resolve the profile from the registered user, never from a client-supplied id.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, status

from app.api.v1.endpoints.candidates import get_candidate_service
from app.core.dependencies import get_authenticated_user
from app.models.user import User
from app.schemas.application_answer import (
    AnswerBankReviewResponse,
    AnswerConflictResolution,
    AnswerConflictResponse,
    ApplicationAnswerDeleteResponse,
    ApplicationAnswerResponse,
    ApplicationAnswerUpdate,
    StaleAnswerResponse,
)
from app.schemas.candidate import (
    CandidateDetailResponse,
    CandidateUpdate,
    ProfileCompletenessResponse,
)
from app.schemas.certification import CertificationCreate, CertificationResponse
from app.schemas.common import DeleteResponse, ErrorResponse
from app.schemas.education import EducationCreate, EducationResponse, EducationUpdate
from app.schemas.experience import ExperienceCreate, ExperienceResponse, ExperienceUpdate
from app.schemas.preference import JobPreferenceResponse, JobPreferenceUpdate
from app.schemas.project import ProjectCreate, ProjectResponse, ProjectUpdate
from app.services.candidate_service import CandidateService

router = APIRouter(prefix="/profile", tags=["Profile"])

CurrentUser = Annotated[User, Depends(get_authenticated_user)]
ServiceDep = Annotated[CandidateService, Depends(get_candidate_service)]

ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    400: {"model": ErrorResponse},
    401: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}


@router.get(
    "",
    response_model=CandidateDetailResponse,
    summary="Get the authenticated candidate profile",
    description=(
        "Returns the profile linked to the signed-in user, creating or linking "
        "it once from the verified Google account when necessary."
    ),
    responses=ERROR_RESPONSES,
)
async def get_profile(current_user: CurrentUser, service: ServiceDep) -> CandidateDetailResponse:
    profile = await service.ensure_profile_for_user(current_user)
    return CandidateDetailResponse.model_validate(profile)


@router.put(
    "",
    response_model=CandidateDetailResponse,
    summary="Update the authenticated candidate profile",
    description=(
        "Partial update of the application-relevant profile fields: preferred name, "
        "contact details, address, work authorization, sponsorship, notice period "
        "and availability."
    ),
    responses=ERROR_RESPONSES,
)
async def update_profile(
    payload: CandidateUpdate, current_user: CurrentUser, service: ServiceDep
) -> CandidateDetailResponse:
    profile = await service.update_profile_for_user(current_user, payload)
    return CandidateDetailResponse.model_validate(profile)


@router.get(
    "/completeness",
    response_model=ProfileCompletenessResponse,
    summary="Get profile completion and missing fields",
    description=(
        "Reports which application-relevant profile fields are filled. It never "
        "infers a value: every missing field is something the user must add."
    ),
    responses=ERROR_RESPONSES,
)
async def get_completeness(
    current_user: CurrentUser, service: ServiceDep
) -> ProfileCompletenessResponse:
    return ProfileCompletenessResponse.model_validate(
        await service.completeness_for_user(current_user)
    )


# ---------------------------------------------------------------------------
# Education, experience and projects
# ---------------------------------------------------------------------------
@router.post(
    "/education",
    response_model=EducationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add an education record",
    responses=ERROR_RESPONSES,
)
async def add_education(
    payload: EducationCreate, current_user: CurrentUser, service: ServiceDep
) -> EducationResponse:
    profile = await service.ensure_profile_for_user(current_user)
    education = await service.add_education(profile.id, payload)
    return EducationResponse.model_validate(education)


@router.patch(
    "/education/{education_id}",
    response_model=EducationResponse,
    summary="Update an education record",
    responses=ERROR_RESPONSES,
)
async def update_education(
    education_id: Annotated[UUID, Path(description="Education UUID")],
    payload: EducationUpdate,
    current_user: CurrentUser,
    service: ServiceDep,
) -> EducationResponse:
    education = await service.update_education_for_user(current_user, education_id, payload)
    return EducationResponse.model_validate(education)


@router.delete(
    "/education/{education_id}",
    response_model=DeleteResponse,
    summary="Delete an education record",
    responses=ERROR_RESPONSES,
)
async def delete_education(
    education_id: Annotated[UUID, Path(description="Education UUID")],
    current_user: CurrentUser,
    service: ServiceDep,
) -> DeleteResponse:
    deleted_id = await service.delete_education_for_user(current_user, education_id)
    return DeleteResponse(id=deleted_id)


@router.post(
    "/experience",
    response_model=ExperienceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add an employment record",
    description=(
        "Supports every employment type, including internships, contract and "
        "freelance work."
    ),
    responses=ERROR_RESPONSES,
)
async def add_experience(
    payload: ExperienceCreate, current_user: CurrentUser, service: ServiceDep
) -> ExperienceResponse:
    profile = await service.ensure_profile_for_user(current_user)
    experience = await service.add_experience(profile.id, payload)
    return ExperienceResponse.model_validate(experience)


@router.patch(
    "/experience/{experience_id}",
    response_model=ExperienceResponse,
    summary="Update an employment record",
    responses=ERROR_RESPONSES,
)
async def update_experience(
    experience_id: Annotated[UUID, Path(description="Experience UUID")],
    payload: ExperienceUpdate,
    current_user: CurrentUser,
    service: ServiceDep,
) -> ExperienceResponse:
    experience = await service.update_experience_for_user(
        current_user, experience_id, payload
    )
    return ExperienceResponse.model_validate(experience)


@router.delete(
    "/experience/{experience_id}",
    response_model=DeleteResponse,
    summary="Delete an employment record",
    responses=ERROR_RESPONSES,
)
async def delete_experience(
    experience_id: Annotated[UUID, Path(description="Experience UUID")],
    current_user: CurrentUser,
    service: ServiceDep,
) -> DeleteResponse:
    deleted_id = await service.delete_experience_for_user(current_user, experience_id)
    return DeleteResponse(id=deleted_id)


@router.post(
    "/projects",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a project",
    responses=ERROR_RESPONSES,
)
async def add_project(
    payload: ProjectCreate, current_user: CurrentUser, service: ServiceDep
) -> ProjectResponse:
    profile = await service.ensure_profile_for_user(current_user)
    project = await service.add_project(profile.id, payload)
    return ProjectResponse.model_validate(project)


@router.patch(
    "/projects/{project_id}",
    response_model=ProjectResponse,
    summary="Update a project",
    responses=ERROR_RESPONSES,
)
async def update_project(
    project_id: Annotated[UUID, Path(description="Project UUID")],
    payload: ProjectUpdate,
    current_user: CurrentUser,
    service: ServiceDep,
) -> ProjectResponse:
    project = await service.update_project_for_user(current_user, project_id, payload)
    return ProjectResponse.model_validate(project)


@router.delete(
    "/projects/{project_id}",
    response_model=DeleteResponse,
    summary="Delete a project",
    responses=ERROR_RESPONSES,
)
async def delete_project(
    project_id: Annotated[UUID, Path(description="Project UUID")],
    current_user: CurrentUser,
    service: ServiceDep,
) -> DeleteResponse:
    deleted_id = await service.delete_project_for_user(current_user, project_id)
    return DeleteResponse(id=deleted_id)


@router.get(
    "/preferences",
    response_model=JobPreferenceResponse | None,
    summary="Get job preferences",
    responses=ERROR_RESPONSES,
)
async def get_preferences(
    current_user: CurrentUser, service: ServiceDep
) -> JobPreferenceResponse | None:
    preference = await service.get_preferences_for_user(current_user)
    return JobPreferenceResponse.model_validate(preference) if preference else None


@router.put(
    "/preferences",
    response_model=JobPreferenceResponse,
    summary="Create or update job preferences",
    responses=ERROR_RESPONSES,
)
async def update_preferences(
    payload: JobPreferenceUpdate, current_user: CurrentUser, service: ServiceDep
) -> JobPreferenceResponse:
    preference = await service.update_preferences_for_user(current_user, payload)
    return JobPreferenceResponse.model_validate(preference)


# ---------------------------------------------------------------------------
# Reusable answers
# ---------------------------------------------------------------------------
@router.get(
    "/answers",
    response_model=list[ApplicationAnswerResponse],
    summary="List reusable approved answers",
    description=(
        "Only answers owned by the authenticated user are returned. Legal "
        "declarations are never stored here and always require fresh consent."
    ),
    responses=ERROR_RESPONSES,
)
async def list_answers(
    current_user: CurrentUser, service: ServiceDep
) -> list[ApplicationAnswerResponse]:
    answers = await service.list_answers_for_user(current_user)
    return [ApplicationAnswerResponse.model_validate(answer) for answer in answers]


@router.get(
    "/answers/review",
    response_model=AnswerBankReviewResponse,
    summary="Review reusable answers for staleness and conflicts",
    description=(
        "Lists answers whose confirmation has expired, answers that disagree "
        "with the verified profile or with each other, and unverified answers "
        "that will not be reused. Only the authenticated user's answers are "
        "inspected."
    ),
    responses=ERROR_RESPONSES,
)
async def review_answers(
    current_user: CurrentUser, service: ServiceDep
) -> AnswerBankReviewResponse:
    review = await service.review_answer_bank_for_user(current_user)
    return AnswerBankReviewResponse(
        total=review.total,
        verified=review.verified,
        stale=[
            StaleAnswerResponse(
                question_key=item.question_key,
                question_text=item.question_text,
                last_confirmed_at=item.last_confirmed_at,
                age_days=item.age_days,
                max_age_days=item.max_age_days,
                reason=item.reason,
            )
            for item in review.stale
        ],
        conflicts=[
            AnswerConflictResponse(
                question_key=item.question_key,
                question_text=item.question_text,
                category=item.category,
                stored_answer=item.stored_answer,
                conflicting_answer=item.conflicting_answer,
                conflicting_source=item.conflicting_source,
                reason=item.reason,
                resolution_options=list(item.resolution_options),
            )
            for item in review.conflicts
        ],
        unverified=list(review.unverified),
    )


@router.post(
    "/answers/{question_key}/resolve",
    response_model=ApplicationAnswerResponse,
    summary="Resolve a stale or conflicting reusable answer",
    description=(
        "Re-confirm the stored value, adopt the current verified profile value, "
        "or store a corrected answer. The resolved answer is always recorded as "
        "freshly confirmed by you."
    ),
    responses=ERROR_RESPONSES,
)
async def resolve_answer_conflict(
    question_key: Annotated[
        str,
        Path(
            min_length=1,
            max_length=100,
            pattern=r"^[a-z0-9][a-z0-9_.-]*$",
            description="Stable identifier of the application question",
        ),
    ],
    payload: AnswerConflictResolution,
    current_user: CurrentUser,
    service: ServiceDep,
) -> ApplicationAnswerResponse:
    answer = await service.resolve_answer_conflict_for_user(
        current_user, question_key, payload
    )
    return ApplicationAnswerResponse.model_validate(answer)


@router.put(
    "/answers/{question_key}",
    response_model=ApplicationAnswerResponse,
    summary="Store or update a reusable answer",
    responses=ERROR_RESPONSES,
)
async def upsert_answer(
    question_key: Annotated[
        str,
        Path(
            min_length=1,
            max_length=100,
            pattern=r"^[a-z0-9][a-z0-9_.-]*$",
            description="Stable identifier of the application question",
        ),
    ],
    payload: ApplicationAnswerUpdate,
    current_user: CurrentUser,
    service: ServiceDep,
) -> ApplicationAnswerResponse:
    answer = await service.upsert_answer_for_user(current_user, question_key, payload)
    return ApplicationAnswerResponse.model_validate(answer)


@router.delete(
    "/answers/{question_key}",
    response_model=ApplicationAnswerDeleteResponse,
    summary="Forget a reusable answer",
    responses=ERROR_RESPONSES,
)
async def delete_answer(
    question_key: Annotated[
        str,
        Path(
            min_length=1,
            max_length=100,
            pattern=r"^[a-z0-9][a-z0-9_.-]*$",
        ),
    ],
    current_user: CurrentUser,
    service: ServiceDep,
) -> ApplicationAnswerDeleteResponse:
    deleted = await service.delete_answer_for_user(current_user, question_key)
    return ApplicationAnswerDeleteResponse(question_key=deleted)


@router.post(
    "/certifications",
    response_model=CertificationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a certification",
    responses=ERROR_RESPONSES,
)
async def add_certification(
    payload: CertificationCreate, current_user: CurrentUser, service: ServiceDep
) -> CertificationResponse:
    certification = await service.add_certification_for_user(current_user, payload)
    return CertificationResponse.model_validate(certification)


@router.delete(
    "/certifications/{certification_id}",
    response_model=DeleteResponse,
    summary="Delete a certification",
    responses=ERROR_RESPONSES,
)
async def delete_certification(
    certification_id: Annotated[UUID, Path(description="Certification UUID")],
    current_user: CurrentUser,
    service: ServiceDep,
) -> DeleteResponse:
    deleted_id = await service.delete_certification_for_user(
        current_user, certification_id
    )
    return DeleteResponse(id=deleted_id)


__all__ = ["router"]
