"""phase 2c missing information queue

Revision ID: c7a2f5d81e40
Revises: d1c4e7a90f32
Create Date: 2026-11-07 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7a2f5d81e40"
down_revision: str | None = "d1c4e7a90f32"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # One persisted summary per application. Individual questions and answers
    # stay in application_questions / application_answers: this table only
    # stores the recalculated state and the user's dismissals.
    op.create_table(
        "missing_information_queues",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("required_total", sa.Integer(), nullable=False),
        sa.Column("required_resolved", sa.Integer(), nullable=False),
        sa.Column("required_remaining", sa.Integer(), nullable=False),
        sa.Column("optional_unanswered", sa.Integer(), nullable=False),
        sa.Column("stale_count", sa.Integer(), nullable=False),
        sa.Column("conflict_count", sa.Integer(), nullable=False),
        sa.Column("sensitive_confirmation_count", sa.Integer(), nullable=False),
        sa.Column("matched_count", sa.Integer(), nullable=False),
        sa.Column("progress_percent", sa.Integer(), nullable=False),
        sa.Column("dismissed_question_keys", sa.JSON(), nullable=False),
        sa.Column("last_recalculated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
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
            ["application_id"],
            ["applications.id"],
            name="fk_missing_information_queues_application_id_applications",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_missing_information_queues"),
        sa.UniqueConstraint(
            "application_id", name="uq_missing_information_queues_application_id"
        ),
    )
    op.create_index(
        "ix_missing_information_queues_application_id",
        "missing_information_queues",
        ["application_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_missing_information_queues_application_id",
        table_name="missing_information_queues",
    )
    op.drop_table("missing_information_queues")
