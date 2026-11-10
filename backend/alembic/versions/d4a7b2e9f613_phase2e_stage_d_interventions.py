"""phase 2e stage d human intervention queue

Revision ID: d4a7b2e9f613
Revises: c9f2a4d7e810
Create Date: 2026-11-09 16:00:00.000000

Stores only non-secret intervention metadata: field identifier/label/category,
reason, type, status and timestamps. It never stores field values, legal
declarations, CAPTCHA/OTP/MFA answers, passwords, cookies or browser storage.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4a7b2e9f613"
down_revision: str | None = "c9f2a4d7e810"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "application_interventions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("browser_session_id", sa.Uuid(), nullable=True),
        sa.Column("application_field_execution_id", sa.Uuid(), nullable=True),
        sa.Column("analysis_id", sa.Uuid(), nullable=True),
        sa.Column("analysis_version", sa.Integer(), nullable=True),
        sa.Column("field_id", sa.String(length=255), nullable=True),
        sa.Column("field_label", sa.String(length=500), nullable=False),
        sa.Column("field_category", sa.String(length=60), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("intervention_type", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("resolution_reason", sa.Text(), nullable=True),
        sa.Column("user_working_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expired_at", sa.DateTime(timezone=True), nullable=True),
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
            name="fk_application_interventions_user_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["applications.id"],
            name="fk_application_interventions_application_id_applications",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["browser_session_id"],
            ["browser_sessions.id"],
            name="fk_application_interventions_browser_session",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["application_field_execution_id"],
            ["application_field_executions.id"],
            name="fk_application_interventions_execution",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["analysis_id"],
            ["application_form_analyses.id"],
            name="fk_application_interventions_analysis",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_application_interventions"),
    )
    op.create_index(
        "ix_application_interventions_user_id",
        "application_interventions",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_application_interventions_application_id",
        "application_interventions",
        ["application_id"],
        unique=False,
    )
    op.create_index(
        "ix_application_interventions_status",
        "application_interventions",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_application_interventions_application_status",
        "application_interventions",
        ["application_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_application_interventions_execution_field",
        "application_interventions",
        ["application_field_execution_id", "field_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_application_interventions_execution_field",
        table_name="application_interventions",
    )
    op.drop_index(
        "ix_application_interventions_application_status",
        table_name="application_interventions",
    )
    op.drop_index(
        "ix_application_interventions_status",
        table_name="application_interventions",
    )
    op.drop_index(
        "ix_application_interventions_application_id",
        table_name="application_interventions",
    )
    op.drop_index(
        "ix_application_interventions_user_id",
        table_name="application_interventions",
    )
    op.drop_table("application_interventions")
