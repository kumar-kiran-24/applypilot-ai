"""add cached phase 2f.2 resume intelligence

Revision ID: d2f7a1c96b04
Revises: f3a8c1d92e47
Create Date: 2026-11-11 12:00:00.000000

One cached structured reading of a stored resume per extractor version and
content fingerprint. Older rows are kept so a failed re-extraction can fall back
to the last successful reading instead of losing it. The table stores no PDF
bytes and no storage keys.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d2f7a1c96b04"
down_revision: str | None = "f3a8c1d92e47"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "resume_intelligence",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("resume_id", sa.Uuid(), nullable=False),
        sa.Column("extractor_version", sa.String(length=16), nullable=False),
        sa.Column("content_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("extraction_status", sa.String(length=16), nullable=False),
        sa.Column("confidence", sa.String(length=16), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("intelligence_data", sa.JSON(), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("field_confidence", sa.JSON(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_resume_intelligence_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["resume_id"],
            ["resumes.id"],
            name=op.f("fk_resume_intelligence_resume_id_resumes"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_resume_intelligence")),
        sa.UniqueConstraint(
            "resume_id",
            "extractor_version",
            "content_fingerprint",
            name="uq_resume_intelligence_resume_version_fingerprint",
        ),
    )
    op.create_index(
        op.f("ix_resume_intelligence_user_id"),
        "resume_intelligence",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_resume_intelligence_user_id_resume_id",
        "resume_intelligence",
        ["user_id", "resume_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_resume_intelligence_user_id_resume_id",
        table_name="resume_intelligence",
    )
    op.drop_index(
        op.f("ix_resume_intelligence_user_id"),
        table_name="resume_intelligence",
    )
    op.drop_table("resume_intelligence")
