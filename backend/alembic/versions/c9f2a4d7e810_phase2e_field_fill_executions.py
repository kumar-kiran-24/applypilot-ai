"""phase 2e stage c field fill execution records

Revision ID: c9f2a4d7e810
Revises: b5e8f1a94c27
Create Date: 2026-11-09 12:00:00.000000

Stores only non-secret execution metadata: analysis/run identifiers, statuses,
per-field outcomes, verification flags and short reasons. Filled values, resume
contents, cookies, credentials, OTP/MFA codes and CAPTCHA answers are absent.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c9f2a4d7e810"
down_revision: str | None = "b5e8f1a94c27"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "application_field_executions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("browser_session_id", sa.Uuid(), nullable=False),
        sa.Column("analysis_id", sa.Uuid(), nullable=False),
        sa.Column("analysis_version", sa.Integer(), nullable=False),
        sa.Column("run_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("selected_resume_id", sa.Uuid(), nullable=True),
        sa.Column("selected_resume_filename", sa.String(length=255), nullable=True),
        sa.Column("plan", sa.JSON(), nullable=False),
        sa.Column("results", sa.JSON(), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("pause_reason", sa.Text(), nullable=True),
        sa.Column("failure_code", sa.String(length=80), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
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
            name="fk_application_field_executions_user_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["applications.id"],
            name="fk_application_field_executions_application_id_applications",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["browser_session_id"],
            ["browser_sessions.id"],
            name="fk_application_field_executions_browser_session",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["analysis_id"],
            ["application_form_analyses.id"],
            name="fk_application_field_executions_analysis",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_application_field_executions"),
        sa.UniqueConstraint(
            "application_id",
            name="uq_application_field_executions_application_id",
        ),
    )
    op.create_index(
        "ix_application_field_executions_user_id",
        "application_field_executions",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_application_field_executions_application_id",
        "application_field_executions",
        ["application_id"],
        unique=False,
    )
    op.create_index(
        "ix_application_field_executions_browser_session_id",
        "application_field_executions",
        ["browser_session_id"],
        unique=False,
    )
    op.create_index(
        "ix_application_field_executions_analysis_id",
        "application_field_executions",
        ["analysis_id"],
        unique=False,
    )
    op.create_index(
        "ix_application_field_executions_user_id_status",
        "application_field_executions",
        ["user_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_application_field_executions_user_id_status",
        table_name="application_field_executions",
    )
    op.drop_index(
        "ix_application_field_executions_analysis_id",
        table_name="application_field_executions",
    )
    op.drop_index(
        "ix_application_field_executions_browser_session_id",
        table_name="application_field_executions",
    )
    op.drop_index(
        "ix_application_field_executions_application_id",
        table_name="application_field_executions",
    )
    op.drop_index(
        "ix_application_field_executions_user_id",
        table_name="application_field_executions",
    )
    op.drop_table("application_field_executions")
