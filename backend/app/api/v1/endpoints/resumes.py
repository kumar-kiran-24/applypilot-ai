"""Authenticated resume library endpoints.

Ownership is enforced in the service layer from the authenticated user, never
from a client-supplied id alone. Storage paths are never returned: files are
streamed through the download endpoint only.
"""

from __future__ import annotations

from typing import Annotated, Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, File, Path, Query, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_authenticated_user
from app.db.session import get_db
from app.models.resume import Resume
from app.models.user import User
from app.schemas.common import DeleteResponse, ErrorResponse
from app.schemas.resume import ParsedResumeData, ResumeResponse
from app.services.resume_service import ResumeService, pdf_filename_for_header
from app.services.resume_storage import get_resume_storage

router = APIRouter(prefix="/resumes", tags=["Resumes"])

ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    400: {"model": ErrorResponse},
    401: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    413: {"model": ErrorResponse},
    415: {"model": ErrorResponse},
}


async def get_resume_service(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> ResumeService:
    """Wire the service with the configured storage backend for one request."""
    return ResumeService(session, get_resume_storage())


ServiceDep = Annotated[ResumeService, Depends(get_resume_service)]
CurrentUser = Annotated[User, Depends(get_authenticated_user)]
ResumeId = Annotated[UUID, Path(description="Resume UUID")]


def _to_response(resume: Resume) -> ResumeResponse:
    return ResumeResponse(
        id=resume.id,
        original_filename=resume.original_filename,
        file_size=resume.file_size,
        content_type=resume.content_type,
        is_default=resume.is_default,
        parsing_status=resume.parsing_status,
        parsing_error=resume.parsing_error,
        parsed_at=resume.parsed_at,
        uploaded_at=resume.created_at,
        skills=list(resume.skills or []),
        parsed_data=ParsedResumeData.model_validate(resume.parsed_data or {}),
    )


@router.post(
    "/upload",
    response_model=ResumeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a PDF resume",
    description=(
        "Accepts a single PDF (validated by extension, content type and the "
        "`%PDF-` signature), stores it safely and parses its text. Scanned PDFs "
        "are kept with status `ocr_required` rather than being reported as "
        "empty. The first resume for a user becomes the default."
    ),
    responses=ERROR_RESPONSES,
)
async def upload_resume(
    current_user: CurrentUser,
    service: ServiceDep,
    file: Annotated[UploadFile, File(description="PDF resume to upload")],
) -> ResumeResponse:
    resume = await service.create_resume(current_user, file)
    return _to_response(resume)


@router.get(
    "",
    response_model=list[ResumeResponse],
    summary="List resumes",
    responses={401: {"model": ErrorResponse}},
)
async def list_resumes(current_user: CurrentUser, service: ServiceDep) -> list[ResumeResponse]:
    resumes = await service.list_resumes(current_user)
    return [_to_response(resume) for resume in resumes]


@router.get(
    "/{resume_id}",
    response_model=ResumeResponse,
    summary="Get one resume",
    responses=ERROR_RESPONSES,
)
async def get_resume(
    resume_id: ResumeId, current_user: CurrentUser, service: ServiceDep
) -> ResumeResponse:
    resume = await service.get_resume(current_user, resume_id)
    return _to_response(resume)


@router.get(
    "/{resume_id}/download",
    summary="Download or preview a resume",
    description=(
        "Streams the original PDF. Use `disposition=inline` to preview it in the "
        "browser; the default downloads it. The file is read through the storage "
        "abstraction, so no local path is ever exposed."
    ),
    responses={
        200: {"content": {"application/pdf": {}}, "description": "The resume PDF"},
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def download_resume(
    resume_id: ResumeId,
    current_user: CurrentUser,
    service: ServiceDep,
    disposition: Annotated[
        Literal["attachment", "inline"],
        Query(description="Download (attachment) or preview (inline)"),
    ] = "attachment",
) -> Response:
    resume, data = await service.read_file(current_user, resume_id)
    ascii_name = pdf_filename_for_header(resume.original_filename)
    utf8_name = quote(resume.original_filename, safe="")
    return Response(
        content=data,
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f'{disposition}; filename="{ascii_name}"; filename*=UTF-8\'\'{utf8_name}'
            ),
            "Content-Length": str(len(data)),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


@router.patch(
    "/{resume_id}/default",
    response_model=ResumeResponse,
    summary="Set the default resume",
    responses=ERROR_RESPONSES,
)
async def set_default_resume(
    resume_id: ResumeId, current_user: CurrentUser, service: ServiceDep
) -> ResumeResponse:
    resume = await service.set_default(current_user, resume_id)
    return _to_response(resume)


@router.delete(
    "/{resume_id}",
    response_model=DeleteResponse,
    summary="Delete a resume",
    description=(
        "Deletes the database row and the stored file. When the default resume "
        "is deleted, the most recently uploaded remaining resume becomes the "
        "default."
    ),
    responses=ERROR_RESPONSES,
)
async def delete_resume(
    resume_id: ResumeId, current_user: CurrentUser, service: ServiceDep
) -> DeleteResponse:
    deleted_id = await service.delete_resume(current_user, resume_id)
    return DeleteResponse(id=deleted_id)


__all__ = ["get_resume_service", "router"]
