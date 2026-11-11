"""add cached phase 2f job intelligence

Revision ID: f3a8c1d92e47
Revises: e5c3f9a1b724
Create Date: 2026-11-10 12:00:00.000000

One cached structured reading of a stored posting per job. The cache is keyed by
the extractor version and a fingerprint of the posting text, so an edited
description is re-analysed instead of serving stale requirements.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f3a8c1d92e47"
down_revision: str | None = "e5c3f9a1b724"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "job_intelligence",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("extractor_version", sa.String(length=16), nullable=False),
        sa.Column("job_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("confidence", sa.String(length=16), nullable=False),
        sa.Column("llm_used", sa.Boolean(), nullable=False),
        sa.Column("llm_status", sa.String(length=32), nullable=False),
        sa.Column("llm_model", sa.String(length=100), nullable=True),
        sa.Column("data", sa.JSON(), nullable=False),
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
            name=op.f("fk_job_intelligence_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            name=op.f("fk_job_intelligence_job_id_jobs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_intelligence")),
        sa.UniqueConstraint("job_id", name="uq_job_intelligence_job_id"),
    )
    op.create_index(
        op.f("ix_job_intelligence_user_id"),
        "job_intelligence",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_job_intelligence_user_id_job_id",
        "job_intelligence",
        ["user_id", "job_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_job_intelligence_user_id_job_id", table_name="job_intelligence")
    op.drop_index(
        op.f("ix_job_intelligence_user_id"), table_name="job_intelligence"
    )
    op.drop_table("job_intelligence")
