"""phase 2e form detection analysis records

Revision ID: b5e8f1a94c27
Revises: a1c9d3f7e620
Create Date: 2026-11-07 19:30:00.000000

Stores non-secret field metadata only. No cookies, credentials, OTP/MFA codes,
CAPTCHA answers or hidden security values are persisted.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b5e8f1a94c27"
down_revision: str | None = "a1c9d3f7e620"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "application_form_analyses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("browser_session_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("page_url", sa.String(length=1000), nullable=True),
        sa.Column("form_count", sa.Integer(), nullable=False),
        sa.Column("field_count", sa.Integer(), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("fields", sa.JSON(), nullable=False),
        sa.Column("analyzed_at", sa.DateTime(timezone=True), nullable=False),
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
            name="fk_application_form_analyses_user_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["applications.id"],
            name="fk_application_form_analyses_application_id_applications",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["browser_session_id"],
            ["browser_sessions.id"],
            # PostgreSQL identifiers are capped at 63 characters.
            name="fk_application_form_analyses_browser_session",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_application_form_analyses"),
        sa.UniqueConstraint(
            "application_id",
            "version",
            name="uq_application_form_analyses_application_version",
        ),
    )
    op.create_index(
        "ix_application_form_analyses_user_id",
        "application_form_analyses",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_application_form_analyses_application_id",
        "application_form_analyses",
        ["application_id"],
        unique=False,
    )
    op.create_index(
        "ix_application_form_analyses_browser_session_id",
        "application_form_analyses",
        ["browser_session_id"],
        unique=False,
    )
    op.create_index(
        "ix_application_form_analyses_application_version",
        "application_form_analyses",
        ["application_id", "version"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_application_form_analyses_application_version",
        table_name="application_form_analyses",
    )
    op.drop_index(
        "ix_application_form_analyses_browser_session_id",
        table_name="application_form_analyses",
    )
    op.drop_index(
        "ix_application_form_analyses_application_id",
        table_name="application_form_analyses",
    )
    op.drop_index(
        "ix_application_form_analyses_user_id",
        table_name="application_form_analyses",
    )
    op.drop_table("application_form_analyses")
