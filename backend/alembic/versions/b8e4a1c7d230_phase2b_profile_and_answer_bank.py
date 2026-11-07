"""phase 2b candidate skills and reusable answer classification

Revision ID: b8e4a1c7d230
Revises: f2b8c1d4e690
Create Date: 2026-11-06 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8e4a1c7d230"
down_revision: str | None = "f2b8c1d4e690"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "candidates",
        sa.Column("skills", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )
    op.alter_column("candidates", "skills", server_default=None)

    op.add_column(
        "application_answers",
        sa.Column(
            "is_sensitive",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "application_answers",
        sa.Column("is_legal", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("application_answers", "is_sensitive", server_default=None)
    op.alter_column("application_answers", "is_legal", server_default=None)


def downgrade() -> None:
    op.drop_column("application_answers", "is_legal")
    op.drop_column("application_answers", "is_sensitive")
    op.drop_column("candidates", "skills")
