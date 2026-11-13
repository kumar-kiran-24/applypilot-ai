"""Authenticated candidate-profile endpoints.

These endpoints expose the profile that drives application questionnaires. They
are separate from the older unauthenticated candidate CRUD routes and always
resolve the profile from the registered user, never from a client-supplied id.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, status

from app.api.v1.endpoints.candidates import get_candidate_service
from app.api.v1.serializers import answer_conflict_response, stale_answer_response
from app.core.dependencies import get_authenticated_user
from app.models.user import User
from app.schemas.application_answer import (
    AnswerBankReviewResponse,
    AnswerCategoryCount,
    AnswerCategoryListResponse,
    AnswerConflictResolution,
    ApplicationAnswerDeleteResponse,
    ApplicationAnswerResponse,
    ApplicationAnswerUpdate,
    CandidateKnowledgeResponse,
    KnowledgeMissingItemResponse,
    KnowledgeUnverifiedItemResponse,
    SavedAnswerDetailResponse,
    SavedAnswerEvidenceResponse,
    SavedAnswerResponse,
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
from app.services.answer_library import (
    CATEGORY_PATTERN,
    TRUST_PATTERN,
    AnswerEvidence,
    SavedAnswerView,
)
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


def _saved_answer_payload(view: SavedAnswerView) -> SavedAnswerResponse:
    base = ApplicationAnswerResponse.model_validate(view.answer)
    return SavedAnswerResponse(
        **base.model_dump(),
        category=view.category,
        category_label=view.category_label,
        trust_level=view.trust.level,
        trust_label=view.trust.label,
        trust_reason=view.trust.reason,
        needs_review=view.trust.needs_review,
        needs_information=view.needs_information,
        is_stale=view.is_stale,
        freshness_reason=view.freshness_reason,
    )


def _evidence_payload(item: AnswerEvidence) -> SavedAnswerEvidenceResponse:
    return SavedAnswerEvidenceResponse(
        id=item.id,
        kind=item.kind,
        label=item.label,
        detail=item.detail,
        source=item.source,
        verified=item.verified,
    )


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
    response_model=list[SavedAnswerResponse],
    summary="List saved reusable answers",
    description=(
        "Only answers owned by the authenticated user are returned. Each answer "
        "carries a derived category, trust level and freshness so an AI draft or "
        "an unverified suggestion is never shown as a confirmed fact. Legal "
        "declarations are never stored here and always require fresh consent."
    ),
    responses=ERROR_RESPONSES,
)
async def list_answers(
    current_user: CurrentUser,
    service: ServiceDep,
    q: Annotated[
        str | None,
        Query(max_length=200, description="Search question text, key or answer"),
    ] = None,
    category: Annotated[
        str | None,
        Query(pattern=CATEGORY_PATTERN, description="Supported answer category"),
    ] = None,
    trust: Annotated[
        str | None,
        Query(pattern=TRUST_PATTERN, description="Derived trust level"),
    ] = None,
) -> list[SavedAnswerResponse]:
    views = await service.saved_answers_for_user(
        current_user, query=q, category=category, trust=trust
    )
    return [_saved_answer_payload(view) for view in views]


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
        stale=[stale_answer_response(item) for item in review.stale],
        conflicts=[answer_conflict_response(item) for item in review.conflicts],
        unverified=list(review.unverified),
    )


@router.get(
    "/answers/categories",
    response_model=AnswerCategoryListResponse,
    summary="List saved-answer categories and counts",
    description=(
        "Returns only the categories the user's own saved answers actually use, "
        "plus a count per trust level. Categories are derived deterministically "
        "from each saved question; no new candidate data is created."
    ),
    responses=ERROR_RESPONSES,
)
async def list_answer_categories(
    current_user: CurrentUser, service: ServiceDep
) -> AnswerCategoryListResponse:
    overview = await service.answer_categories_for_user(current_user)
    return AnswerCategoryListResponse(
        total=int(overview["total"]),
        categories=[
            AnswerCategoryCount(
                category=item["category"],
                label=item["label"],
                count=item["count"],
            )
            for item in overview["categories"]
        ],
        trust_levels=dict(overview["trust_levels"]),
    )


@router.get(
    "/answers/knowledge",
    response_model=CandidateKnowledgeResponse,
    summary="Review candidate knowledge consistency",
    description=(
        "Reports missing profile details, answers that need more information, "
        "conflicts with verified data, outdated confirmations and unverified "
        "answers. It never edits a verified fact or fabricates a missing value."
    ),
    responses=ERROR_RESPONSES,
)
async def review_candidate_knowledge(
    current_user: CurrentUser, service: ServiceDep
) -> CandidateKnowledgeResponse:
    report = await service.knowledge_for_user(current_user)
    return CandidateKnowledgeResponse(
        total_answers=report.total_answers,
        verified_answers=report.verified_answers,
        needs_review=report.needs_review,
        missing_information=[
            KnowledgeMissingItemResponse(
                field=item.field,
                label=item.label,
                section=item.section,
                required=item.required,
                kind=item.kind,
                question_key=item.question_key,
                reason=item.reason,
            )
            for item in report.missing_information
        ],
        conflicts=[answer_conflict_response(item) for item in report.conflicts],
        outdated=[stale_answer_response(item) for item in report.outdated],
        unverified=[
            KnowledgeUnverifiedItemResponse(
                question_key=item.question_key,
                question_text=item.question_text,
                trust_level=item.trust_level,
                trust_label=item.trust_label,
                reason=item.reason,
            )
            for item in report.unverified
        ],
        summary=report.summary,
    )


@router.get(
    "/answers/{question_key}",
    response_model=SavedAnswerDetailResponse,
    summary="Get one saved answer with evidence",
    description=(
        "Returns the saved value, its derived category and trust level, the "
        "recorded source, any verified profile value it relates to, and any "
        "detected conflicts. Another user's answer is never returned."
    ),
    responses=ERROR_RESPONSES,
)
async def get_answer_detail(
    question_key: Annotated[
        str,
        Path(
            min_length=1,
            max_length=100,
            pattern=r"^[a-z0-9][a-z0-9_.-]*$",
            description="Stable identifier of the application question",
        ),
    ],
    current_user: CurrentUser,
    service: ServiceDep,
) -> SavedAnswerDetailResponse:
    view, evidence = await service.answer_detail_for_user(current_user, question_key)
    return SavedAnswerDetailResponse(
        answer=_saved_answer_payload(view),
        evidence=[_evidence_payload(item) for item in evidence],
        conflicts=[answer_conflict_response(item) for item in view.conflicts],
    )


@router.post(
    "/answers/{question_key}/resolve",
    response_model=SavedAnswerResponse,
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
) -> SavedAnswerResponse:
    answer = await service.resolve_answer_conflict_for_user(
        current_user, question_key, payload
    )
    view, _ = await service.answer_detail_for_user(current_user, answer.question_key)
    return _saved_answer_payload(view)


@router.put(
    "/answers/{question_key}",
    response_model=SavedAnswerResponse,
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
) -> SavedAnswerResponse:
    answer = await service.upsert_answer_for_user(current_user, question_key, payload)
    view, _ = await service.answer_detail_for_user(current_user, answer.question_key)
    return _saved_answer_payload(view)


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
