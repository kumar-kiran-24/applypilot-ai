"""distinguish job listing, employer careers and application form URLs

Revision ID: e5c3f9a1b724
Revises: b8c1f4a2d907
Create Date: 2026-11-10 10:00:00.000000

The job-board URL a posting was discovered from is preserved separately from
the employer's actual application form URL. Existing rows are backfilled from
their job so no original listing is lost.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5c3f9a1b724"
down_revision: str | None = "b8c1f4a2d907"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "applications",
        sa.Column("job_listing_url", sa.String(length=1000), nullable=True),
    )
    op.add_column(
        "applications",
        sa.Column("employer_careers_url", sa.String(length=1000), nullable=True),
    )
    op.execute(
        """
        UPDATE applications
        SET job_listing_url = jobs.job_url
        FROM jobs
        WHERE applications.job_id = jobs.id
          AND applications.job_listing_url IS NULL
        """
    )


def downgrade() -> None:
    op.drop_column("applications", "employer_careers_url")
    op.drop_column("applications", "job_listing_url")
