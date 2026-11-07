"""phase 2d auto-apply settings and eligibility approvals

Revision ID: e7f3b9c2a610
Revises: c7a2f5d81e40
Create Date: 2026-11-07 15:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e7f3b9c2a610"
down_revision: str | None = "c7a2f5d81e40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_auto_apply_settings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("automation_enabled", sa.Boolean(), nullable=False),
        sa.Column("require_user_approval", sa.Boolean(), nullable=False),
        sa.Column("require_user_review", sa.Boolean(), nullable=False),
        sa.Column("max_applications_per_day", sa.Integer(), nullable=False),
        sa.Column("minimum_resume_match_percentage", sa.Integer(), nullable=False),
        sa.Column("preferred_job_titles", sa.JSON(), nullable=False),
        sa.Column("excluded_job_titles", sa.JSON(), nullable=False),
        sa.Column("preferred_companies", sa.JSON(), nullable=False),
        sa.Column("excluded_companies", sa.JSON(), nullable=False),
        sa.Column("preferred_locations", sa.JSON(), nullable=False),
        sa.Column("work_modes", sa.JSON(), nullable=False),
        sa.Column("employment_types", sa.JSON(), nullable=False),
        sa.Column("minimum_salary", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("maximum_salary", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("relocation_willingness", sa.String(length=32), nullable=False),
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
            name="fk_user_auto_apply_settings_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_user_auto_apply_settings"),
        sa.UniqueConstraint("user_id", name="uq_user_auto_apply_settings_user_id"),
    )
    op.create_index(
        "ix_user_auto_apply_settings_user_id",
        "user_auto_apply_settings",
        ["user_id"],
        unique=False,
    )

    op.create_table(
        "auto_apply_approvals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("report_version", sa.String(length=80), nullable=False),
        sa.Column("report_decision", sa.String(length=32), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
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
            name="fk_auto_apply_approvals_user_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["applications.id"],
            name="fk_auto_apply_approvals_application_id_applications",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_auto_apply_approvals"),
        sa.UniqueConstraint(
            "application_id", name="uq_auto_apply_approvals_application_id"
        ),
    )
    op.create_index(
        "ix_auto_apply_approvals_application_id",
        "auto_apply_approvals",
        ["application_id"],
        unique=False,
    )
    op.create_index(
        "ix_auto_apply_approvals_user_id",
        "auto_apply_approvals",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_auto_apply_approvals_user_id_application_id",
        "auto_apply_approvals",
        ["user_id", "application_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_auto_apply_approvals_user_id_application_id",
        table_name="auto_apply_approvals",
    )
    op.drop_index(
        "ix_auto_apply_approvals_user_id", table_name="auto_apply_approvals"
    )
    op.drop_index(
        "ix_auto_apply_approvals_application_id", table_name="auto_apply_approvals"
    )
    op.drop_table("auto_apply_approvals")

    op.drop_index(
        "ix_user_auto_apply_settings_user_id", table_name="user_auto_apply_settings"
    )
    op.drop_table("user_auto_apply_settings")
