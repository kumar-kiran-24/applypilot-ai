"""phase 2e browser session metadata

Revision ID: a1c9d3f7e620
Revises: e7f3b9c2a610
Create Date: 2026-11-07 17:00:00.000000

Stores execution state only. Employer credentials, OTP/MFA secrets, CAPTCHA
answers, cookies, storage state and authentication tokens are never persisted.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1c9d3f7e620"
down_revision: str | None = "e7f3b9c2a610"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "browser_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("run_number", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("browser_status", sa.String(length=32), nullable=False),
        sa.Column("current_url", sa.String(length=1000), nullable=True),
        sa.Column("allowed_origins", sa.JSON(), nullable=False),
        sa.Column("paused_from_state", sa.String(length=32), nullable=True),
        sa.Column("pause_reason", sa.Text(), nullable=True),
        sa.Column("failure_code", sa.String(length=80), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("checkpoint", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
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
            name="fk_browser_sessions_user_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["applications.id"],
            name="fk_browser_sessions_application_id_applications",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_browser_sessions"),
        sa.UniqueConstraint(
            "application_id", name="uq_browser_sessions_application_id"
        ),
    )
    op.create_index(
        "ix_browser_sessions_user_id", "browser_sessions", ["user_id"], unique=False
    )
    op.create_index(
        "ix_browser_sessions_application_id",
        "browser_sessions",
        ["application_id"],
        unique=False,
    )
    op.create_index(
        "ix_browser_sessions_user_id_browser_status",
        "browser_sessions",
        ["user_id", "browser_status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_browser_sessions_user_id_browser_status", table_name="browser_sessions"
    )
    op.drop_index("ix_browser_sessions_application_id", table_name="browser_sessions")
    op.drop_index("ix_browser_sessions_user_id", table_name="browser_sessions")
    op.drop_table("browser_sessions")
