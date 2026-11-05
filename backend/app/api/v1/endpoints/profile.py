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
from app.schemas.candidate import CandidateDetailResponse, CandidateUpdate
from app.schemas.certification import CertificationCreate, CertificationResponse
from app.schemas.common import DeleteResponse, ErrorResponse
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
