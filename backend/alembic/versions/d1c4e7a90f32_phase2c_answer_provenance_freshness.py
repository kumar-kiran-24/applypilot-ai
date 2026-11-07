"""phase 2c answer provenance and freshness

Revision ID: d1c4e7a90f32
Revises: b8e4a1c7d230
Create Date: 2026-11-07 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d1c4e7a90f32"
down_revision: str | None = "b8e4a1c7d230"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- Reusable answer provenance and freshness --------------------------
    op.add_column(
        "application_answers",
        sa.Column(
            "answer_source",
            sa.String(length=32),
            nullable=False,
            server_default="user_input",
        ),
    )
    op.add_column(
        "application_answers",
        sa.Column(
            "confidence",
            sa.Float(),
            nullable=False,
            server_default="1",
        ),
    )
    op.add_column(
        "application_answers",
        sa.Column("source_record_id", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "application_answers",
        sa.Column("last_confirmed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "application_answers",
        sa.Column("freshness_days", sa.Integer(), nullable=True),
    )

    # Existing verified answers were explicitly confirmed by the user; use the
    # old verification timestamp (or the update timestamp) as their baseline.
    op.execute(
        """
        UPDATE application_answers
        SET last_confirmed_at = COALESCE(verified_at, updated_at)
        WHERE verification_status = 'verified'
        """
    )
    op.execute(
        """
        UPDATE application_answers
        SET answer_source = 'previously_approved'
        WHERE verification_status = 'verified'
        """
    )

    op.alter_column("application_answers", "answer_source", server_default=None)
    op.alter_column("application_answers", "confidence", server_default=None)

    # --- Per-question provenance on application forms ----------------------
    op.add_column(
        "application_questions",
        sa.Column("source_record_id", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "application_questions",
        sa.Column("last_confirmed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        """
        UPDATE application_questions
        SET last_confirmed_at = answered_at
        WHERE status = 'confirmed' AND answered_at IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_column("application_questions", "last_confirmed_at")
    op.drop_column("application_questions", "source_record_id")
    op.drop_column("application_answers", "freshness_days")
    op.drop_column("application_answers", "last_confirmed_at")
    op.drop_column("application_answers", "source_record_id")
    op.drop_column("application_answers", "confidence")
    op.drop_column("application_answers", "answer_source")
