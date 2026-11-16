"""Phase 2J.7: fixes found during live ATS validation (fast, no browser).

Live filling against Lever showed the employer's form receiving
``applypilot-upload-<random>.pdf`` instead of the candidate's resume filename.
The temporary copy must still be chosen by ApplyPilot (never a user path), but it
must present the resume's own, sanitised name to the employer.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.models.resume import Resume
from app.models.user import User
from app.schemas.application_fill import FillAction, FillFieldStatus
from app.schemas.application_form import (
    DetectedFormField,
    FieldCategory,
    FormFieldType,
)
from app.services.application_field_planner import FieldExecutionPlanner
from app.services.resume_upload_handler import ResumeUploadHandler, _safe_upload_name


class _Storage:
    """Minimal storage that returns a tiny PDF for any owned key."""

    def read(self, key: str) -> bytes:
        assert key.endswith(".pdf")
        return b"%PDF-1.4\n%%EOF\n"


def _exists(path: str) -> bool:
    """Synchronous existence check kept out of the async test body."""
    return Path(path).exists()


def _directory_name(path: str) -> str:
    """Synchronous directory-name read kept out of the async test body."""
    return Path(path).parent.name


def _starts_with_pdf_magic(path: str) -> bool:
    """Synchronous file read kept out of the async test body."""
    return Path(path).read_bytes().startswith(b"%PDF")


def _resume(user: User, filename: str) -> Resume:
    return Resume(
        user_id=user.id,
        original_filename=filename,
        storage_key=f"{user.id}/{uuid.uuid4()}.pdf",
        storage_backend="local",
        content_type="application/pdf",
        file_size=20,
        checksum=uuid.uuid4().hex + uuid.uuid4().hex,
    )


@pytest.mark.parametrize(
    ("original", "expected"),
    [
        ("ada-resume.pdf", "ada-resume.pdf"),
        ("Ada Resume (final).pdf", "Ada Resume _final_.pdf"),
        ("../../etc/passwd", "passwd.pdf"),
        ("resume.PDF", "resume.PDF"),
        ("", "resume.pdf"),
        ("strange\x00name.pdf", "strange_name.pdf"),
        # A long name must be shortened without losing the PDF extension.
        ("a" * 130 + ".pdf", "a" * 116 + ".pdf"),
    ],
)
def test_upload_name_is_portable_and_keeps_the_candidate_filename(
    original: str, expected: str
) -> None:
    assert _safe_upload_name(original) == expected


async def test_materialize_presents_the_resume_filename_and_cleans_up() -> None:
    user = User(
        id=uuid.uuid4(),
        google_sub=f"google-{uuid.uuid4().hex}",
        email=f"upload-{uuid.uuid4().hex[:8]}@example.com",
        email_verified=True,
        is_active=True,
    )
    resume = _resume(user, "Ada Lovelace - Resume.pdf")
    handler = ResumeUploadHandler(storage=_Storage())  # type: ignore[arg-type]

    path_in_use = ""
    async with handler.materialize(user, resume) as path:
        path_in_use = path
        assert Path(path).name == "Ada Lovelace - Resume.pdf"
        assert _starts_with_pdf_magic(path)
        assert _directory_name(path).startswith("applypilot-upload-")

    # The short-lived copy and its directory are removed afterwards.
    assert not _exists(path_in_use)
    assert not _exists(os.path.dirname(path_in_use))


def _file_field(
    field_id: str, classification: FieldCategory, label: str = "Attach"
) -> DetectedFormField:
    return DetectedFormField(
        field_id=field_id,
        form_index=0,
        label=label,
        field_type=FormFieldType.FILE,
        classification=classification,
        required=False,
        visible=True,
        confidence=0.94,
        requires_human_input=classification is not FieldCategory.RESUME_UPLOAD,
        requires_confirmation=False,
        is_sensitive=False,
        is_legal=False,
        automation_eligible=True,
        classification_reason="test fixture",
        html_id=field_id,
    )


def test_only_the_resume_slot_receives_the_resume_file() -> None:
    """Regression: a cover-letter file slot must never be sent the resume.

    Greenhouse exposes two ``input[type=file]`` controls, both labelled
    "Attach". The planner previously gave every file control ``UPLOAD`` and
    collected all of them into ``resume.file_field_ids``, so the resume PDF was
    attached to the employer's cover-letter slot as well. Only a control
    classified as a resume upload may receive the file.
    """
    plan = FieldExecutionPlanner().build(
        [
            _file_field("resume", FieldCategory.RESUME_UPLOAD),
            _file_field("cover-letter", FieldCategory.COVER_LETTER),
            _file_field("portfolio", FieldCategory.OTHER),
        ],
        candidate=None,
        resume=SimpleNamespace(
            id=uuid.uuid4(), original_filename="ada-lovelace-test-resume.pdf"
        ),
        resume_owned=True,
    )
    fields = {row.field_id: row for row in plan.fields}

    assert plan.resume.will_upload is True
    assert plan.resume.file_field_ids == ("resume",)
    assert fields["resume"].action is FillAction.UPLOAD
    for other in ("cover-letter", "portfolio"):
        assert fields[other].action is FillAction.NONE
        assert fields[other].status is FillFieldStatus.NEEDS_HUMAN_INPUT


async def test_a_foreign_resume_cannot_be_materialized() -> None:
    owner = User(
        id=uuid.uuid4(),
        google_sub=f"google-{uuid.uuid4().hex}",
        email=f"owner-{uuid.uuid4().hex[:8]}@example.com",
        email_verified=True,
        is_active=True,
    )
    intruder = User(
        id=uuid.uuid4(),
        google_sub=f"google-{uuid.uuid4().hex}",
        email=f"intruder-{uuid.uuid4().hex[:8]}@example.com",
        email_verified=True,
        is_active=True,
    )
    handler = ResumeUploadHandler(storage=_Storage())  # type: ignore[arg-type]
    with pytest.raises(Exception) as excinfo:
        async with handler.materialize(intruder, _resume(owner, "private.pdf")):
            pass
    assert getattr(excinfo.value, "code", "") == "resume_not_owned"
