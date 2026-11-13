"""add per-user saved/dismissed state for stored jobs

Revision ID: a7c4e18f5b93
Revises: c3f9a72d1e05
Create Date: 2026-11-12 10:00:00.000000

Recommendation decisions are per user and must not change the deduplicated
posting rows, so they live in ``job_states`` keyed by ``(user_id, job_id)``.
The unique constraint makes duplicate state records impossible.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7c4e18f5b93"
down_revision: str | None = "c3f9a72d1e05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "job_states",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("is_saved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_dismissed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("saved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "job_id", name="uq_job_states_user_id_job_id"),
    )
    op.create_index("ix_job_states_user_id", "job_states", ["user_id"])
    op.create_index("ix_job_states_job_id", "job_states", ["job_id"])
    op.create_index(
        "ix_job_states_user_id_is_saved", "job_states", ["user_id", "is_saved"]
    )


def downgrade() -> None:
    op.drop_index("ix_job_states_user_id_is_saved", table_name="job_states")
    op.drop_index("ix_job_states_job_id", table_name="job_states")
    op.drop_index("ix_job_states_user_id", table_name="job_states")
    op.drop_table("job_states")
