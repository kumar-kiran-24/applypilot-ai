"""track Easy Apply redirects to a validated application destination

Revision ID: c3f9a72d1e05
Revises: d2f7a1c96b04
Create Date: 2026-11-11 12:00:00.000000

The page ApplyPilot opens (``application_url``) and the discovered listing
(``job_listing_url``) are preserved. A listing hand-off records the validated
final form separately, so the original Glassdoor URL is never overwritten.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3f9a72d1e05"
down_revision: str | None = "d2f7a1c96b04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "applications",
        sa.Column("application_destination_url", sa.String(length=1000), nullable=True),
    )
    op.add_column(
        "applications",
        sa.Column("destination_platform", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "applications",
        sa.Column(
            "redirect_status",
            sa.String(length=32),
            nullable=False,
            server_default="not_started",
        ),
    )
    op.add_column(
        "applications",
        sa.Column(
            "destination_validated",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "applications",
        sa.Column("redirect_detected_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("applications", "redirect_detected_at")
    op.drop_column("applications", "destination_validated")
    op.drop_column("applications", "redirect_status")
    op.drop_column("applications", "destination_platform")
    op.drop_column("applications", "application_destination_url")
