"""Candidate profile endpoints.

Endpoints stay thin: validation comes from Pydantic schemas, business rules from
``CandidateService``, and SQL from ``CandidateRepository``.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.repositories.candidate_repository import CandidateRepository
from app.schemas.application_answer import (
    ApplicationAnswerCreate,
    ApplicationAnswerResponse,
    ApplicationAnswerUpdate,
)
from app.schemas.candidate import (
    CandidateCreate,
    CandidateDetailResponse,
    CandidateResponse,
    CandidateUpdate,
)
from app.schemas.common import DeleteResponse, ErrorResponse
from app.schemas.education import EducationCreate, EducationResponse
from app.schemas.experience import ExperienceCreate, ExperienceResponse
from app.schemas.preference import (
    JobPreferenceCreate,
    JobPreferenceResponse,
    JobPreferenceUpdate,
)
from app.schemas.project import ProjectCreate, ProjectResponse
from app.services.candidate_service import CandidateService

router = APIRouter()

NOT_FOUND: dict[int | str, dict[str, object]] = {404: {"model": ErrorResponse}}
CONFLICT: dict[int | str, dict[str, object]] = {
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
}


async def get_candidate_service(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> CandidateService:
    """Dependency wiring the service with its repository for one request."""
    return CandidateService(CandidateRepository(session))


ServiceDep = Annotated[CandidateService, Depends(get_candidate_service)]
CandidateId = Annotated[UUID, Path(description="Candidate UUID")]


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------
@router.post(
    "/candidates",
    response_model=CandidateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a candidate profile",
    responses=CONFLICT,
    tags=["candidates"],
)
async def create_candidate(payload: CandidateCreate, service: ServiceDep) -> CandidateResponse:
    candidate = await service.create_candidate(payload)
    return CandidateResponse.model_validate(candidate)


@router.get(
    "/candidates/{candidate_id}",
    response_model=CandidateDetailResponse,
    summary="Get a candidate profile",
    responses=NOT_FOUND,
    tags=["candidates"],
)
async def get_candidate(candidate_id: CandidateId, service: ServiceDep) -> CandidateDetailResponse:
    candidate = await service.get_candidate(candidate_id)
    return CandidateDetailResponse.model_validate(candidate)


@router.put(
    "/candidates/{candidate_id}",
    response_model=CandidateResponse,
    summary="Update a candidate profile",
    responses=CONFLICT,
    tags=["candidates"],
)
async def update_candidate(
    candidate_id: CandidateId, payload: CandidateUpdate, service: ServiceDep
) -> CandidateResponse:
    candidate = await service.update_candidate(candidate_id, payload)
    return CandidateResponse.model_validate(candidate)


@router.delete(
    "/candidates/{candidate_id}",
    response_model=DeleteResponse,
    summary="Delete a candidate profile",
    responses=NOT_FOUND,
    tags=["candidates"],
)
async def delete_candidate(candidate_id: CandidateId, service: ServiceDep) -> DeleteResponse:
    deleted_id = await service.delete_candidate(candidate_id)
    return DeleteResponse(id=deleted_id)


# ---------------------------------------------------------------------------
# Education
# ---------------------------------------------------------------------------
@router.post(
    "/candidates/{candidate_id}/education",
    response_model=EducationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add an education entry",
    responses=NOT_FOUND,
    tags=["education"],
)
async def add_education(
    candidate_id: CandidateId, payload: EducationCreate, service: ServiceDep
) -> EducationResponse:
    education = await service.add_education(candidate_id, payload)
    return EducationResponse.model_validate(education)


@router.get(
    "/candidates/{candidate_id}/education",
    response_model=list[EducationResponse],
    summary="List education entries",
    responses=NOT_FOUND,
    tags=["education"],
)
async def list_education(candidate_id: CandidateId, service: ServiceDep) -> list[EducationResponse]:
    entries = await service.list_education(candidate_id)
    return [EducationResponse.model_validate(entry) for entry in entries]


@router.delete(
    "/education/{education_id}",
    response_model=DeleteResponse,
    summary="Delete an education entry",
    responses=NOT_FOUND,
    tags=["education"],
)
async def delete_education(
    education_id: Annotated[UUID, Path(description="Education UUID")], service: ServiceDep
) -> DeleteResponse:
    deleted_id = await service.delete_education(education_id)
    return DeleteResponse(id=deleted_id)


# ---------------------------------------------------------------------------
# Experience
# ---------------------------------------------------------------------------
@router.post(
    "/candidates/{candidate_id}/experience",
    response_model=ExperienceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a work experience",
    responses=NOT_FOUND,
    tags=["experience"],
)
async def add_experience(
    candidate_id: CandidateId, payload: ExperienceCreate, service: ServiceDep
) -> ExperienceResponse:
    experience = await service.add_experience(candidate_id, payload)
    return ExperienceResponse.model_validate(experience)


@router.get(
    "/candidates/{candidate_id}/experience",
    response_model=list[ExperienceResponse],
    summary="List work experience",
    responses=NOT_FOUND,
    tags=["experience"],
)
async def list_experience(
    candidate_id: CandidateId, service: ServiceDep
) -> list[ExperienceResponse]:
    entries = await service.list_experience(candidate_id)
    return [ExperienceResponse.model_validate(entry) for entry in entries]


@router.delete(
    "/experience/{experience_id}",
    response_model=DeleteResponse,
    summary="Delete a work experience",
    responses=NOT_FOUND,
    tags=["experience"],
)
async def delete_experience(
    experience_id: Annotated[UUID, Path(description="Experience UUID")], service: ServiceDep
) -> DeleteResponse:
    deleted_id = await service.delete_experience(experience_id)
    return DeleteResponse(id=deleted_id)


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------
@router.post(
    "/candidates/{candidate_id}/projects",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a project",
    responses=NOT_FOUND,
    tags=["projects"],
)
async def add_project(
    candidate_id: CandidateId, payload: ProjectCreate, service: ServiceDep
) -> ProjectResponse:
    project = await service.add_project(candidate_id, payload)
    return ProjectResponse.model_validate(project)


@router.get(
    "/candidates/{candidate_id}/projects",
    response_model=list[ProjectResponse],
    summary="List projects",
    responses=NOT_FOUND,
    tags=["projects"],
)
async def list_projects(candidate_id: CandidateId, service: ServiceDep) -> list[ProjectResponse]:
    projects = await service.list_projects(candidate_id)
    return [ProjectResponse.model_validate(project) for project in projects]


@router.delete(
    "/projects/{project_id}",
    response_model=DeleteResponse,
    summary="Delete a project",
    responses=NOT_FOUND,
    tags=["projects"],
)
async def delete_project(
    project_id: Annotated[UUID, Path(description="Project UUID")], service: ServiceDep
) -> DeleteResponse:
    deleted_id = await service.delete_project(project_id)
    return DeleteResponse(id=deleted_id)


# ---------------------------------------------------------------------------
# Job preferences
# ---------------------------------------------------------------------------
@router.post(
    "/candidates/{candidate_id}/preferences",
    response_model=JobPreferenceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create job preferences",
    responses=CONFLICT,
    tags=["preferences"],
)
async def create_preference(
    candidate_id: CandidateId, payload: JobPreferenceCreate, service: ServiceDep
) -> JobPreferenceResponse:
    preference = await service.create_preference(candidate_id, payload)
    return JobPreferenceResponse.model_validate(preference)


@router.get(
    "/candidates/{candidate_id}/preferences",
    response_model=JobPreferenceResponse,
    summary="Get job preferences",
    responses=NOT_FOUND,
    tags=["preferences"],
)
async def get_preference(candidate_id: CandidateId, service: ServiceDep) -> JobPreferenceResponse:
    preference = await service.get_preference(candidate_id)
    return JobPreferenceResponse.model_validate(preference)


@router.put(
    "/candidates/{candidate_id}/preferences",
    response_model=JobPreferenceResponse,
    summary="Create or update job preferences",
    responses=NOT_FOUND,
    tags=["preferences"],
)
async def update_preference(
    candidate_id: CandidateId, payload: JobPreferenceUpdate, service: ServiceDep
) -> JobPreferenceResponse:
    preference = await service.update_preference(candidate_id, payload)
    return JobPreferenceResponse.model_validate(preference)


# ---------------------------------------------------------------------------
# Reusable application answers
# ---------------------------------------------------------------------------
@router.post(
    "/candidates/{candidate_id}/answers",
    response_model=ApplicationAnswerResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a reusable application answer",
    responses=CONFLICT,
    tags=["application-answers"],
)
async def create_answer(
    candidate_id: CandidateId, payload: ApplicationAnswerCreate, service: ServiceDep
) -> ApplicationAnswerResponse:
    answer = await service.add_answer(candidate_id, payload)
    return ApplicationAnswerResponse.model_validate(answer)


@router.get(
    "/candidates/{candidate_id}/answers",
    response_model=list[ApplicationAnswerResponse],
    summary="List reusable application answers",
    responses=NOT_FOUND,
    tags=["application-answers"],
)
async def list_answers(
    candidate_id: CandidateId,
    service: ServiceDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ApplicationAnswerResponse]:
    answers = await service.list_answers(candidate_id)
    return [
        ApplicationAnswerResponse.model_validate(answer)
        for answer in answers[offset : offset + limit]
    ]


@router.put(
    "/candidates/{candidate_id}/answers/{question_key}",
    response_model=ApplicationAnswerResponse,
    summary="Update (or create) a reusable application answer",
    responses=NOT_FOUND,
    tags=["application-answers"],
)
async def update_answer(
    candidate_id: CandidateId,
    service: ServiceDep,
    payload: ApplicationAnswerUpdate,
    question_key: Annotated[
        str,
        Path(
            min_length=1,
            max_length=100,
            pattern=r"^[a-z0-9][a-z0-9_.-]*$",
            description="Stable identifier of the application question",
        ),
    ],
) -> ApplicationAnswerResponse:
    answer = await service.upsert_answer(candidate_id, question_key, payload)
    return ApplicationAnswerResponse.model_validate(answer)


__all__ = ["get_candidate_service", "router"]
