"""Pydantic v2 request/response schemas."""

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
from app.schemas.common import DeleteResponse, ErrorResponse, HealthResponse, ORMModel
from app.schemas.education import EducationCreate, EducationResponse, EducationUpdate
from app.schemas.experience import ExperienceCreate, ExperienceResponse, ExperienceUpdate
from app.schemas.preference import (
    JobPreferenceCreate,
    JobPreferenceResponse,
    JobPreferenceUpdate,
)
from app.schemas.project import ProjectCreate, ProjectResponse, ProjectUpdate

__all__ = [
    "ApplicationAnswerCreate",
    "ApplicationAnswerResponse",
    "ApplicationAnswerUpdate",
    "CandidateCreate",
    "CandidateDetailResponse",
    "CandidateResponse",
    "CandidateUpdate",
    "DeleteResponse",
    "EducationCreate",
    "EducationResponse",
    "EducationUpdate",
    "ErrorResponse",
    "ExperienceCreate",
    "ExperienceResponse",
    "ExperienceUpdate",
    "HealthResponse",
    "JobPreferenceCreate",
    "JobPreferenceResponse",
    "JobPreferenceUpdate",
    "ORMModel",
    "ProjectCreate",
    "ProjectResponse",
    "ProjectUpdate",
]
