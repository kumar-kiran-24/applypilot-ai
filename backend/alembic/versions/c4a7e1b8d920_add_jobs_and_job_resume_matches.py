"""add jobs and job resume matches

Revision ID: c4a7e1b8d920
Revises: b7c2d9e4f1a3
Create Date: 2026-11-04 23:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = 'c4a7e1b8d920'
down_revision: str | None = 'b7c2d9e4f1a3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'jobs',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('dedupe_key', sa.String(length=500), nullable=False),
        sa.Column('source', sa.String(length=32), nullable=False),
        sa.Column('job_title', sa.String(length=300), nullable=False),
        sa.Column('company', sa.String(length=200), nullable=True),
        sa.Column('location', sa.String(length=200), nullable=True),
        sa.Column('job_url', sa.String(length=1000), nullable=False),
        sa.Column('job_description', sa.Text(), nullable=True),
        sa.Column('email_subject', sa.String(length=500), nullable=False),
        sa.Column('message_id', sa.String(length=200), nullable=False),
        sa.Column('received_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['user_id'],
            ['users.id'],
            name=op.f('fk_jobs_user_id_users'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_jobs')),
        sa.UniqueConstraint('user_id', 'dedupe_key', name='uq_jobs_user_id_dedupe_key'),
    )
    op.create_index(op.f('ix_jobs_user_id'), 'jobs', ['user_id'], unique=False)
    op.create_index(
        'ix_jobs_user_id_received_at', 'jobs', ['user_id', 'received_at'], unique=False
    )

    op.create_table(
        'job_resume_matches',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('job_id', sa.Uuid(), nullable=False),
        sa.Column('resume_id', sa.Uuid(), nullable=False),
        sa.Column('overall_score', sa.Float(), nullable=False),
        sa.Column('skill_score', sa.Float(), nullable=True),
        sa.Column('experience_score', sa.Float(), nullable=True),
        sa.Column('education_score', sa.Float(), nullable=True),
        sa.Column('title_score', sa.Float(), nullable=True),
        sa.Column('project_score', sa.Float(), nullable=True),
        sa.Column('rank', sa.Integer(), nullable=False),
        sa.Column('is_recommended', sa.Boolean(), nullable=False),
        sa.Column('matched_required_skills', sa.JSON(), nullable=False),
        sa.Column('matched_preferred_skills', sa.JSON(), nullable=False),
        sa.Column('missing_required_skills', sa.JSON(), nullable=False),
        sa.Column('missing_preferred_skills', sa.JSON(), nullable=False),
        sa.Column('strengths', sa.JSON(), nullable=False),
        sa.Column('suggestions', sa.JSON(), nullable=False),
        sa.Column('explanation', sa.Text(), nullable=False),
        sa.Column('confidence', sa.String(length=16), nullable=False),
        sa.Column('details', sa.JSON(), nullable=False),
        sa.Column('job_fingerprint', sa.String(length=64), nullable=False),
        sa.Column('resume_fingerprint', sa.String(length=64), nullable=False),
        sa.Column('computed_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['user_id'],
            ['users.id'],
            name=op.f('fk_job_resume_matches_user_id_users'),
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['job_id'],
            ['jobs.id'],
            name=op.f('fk_job_resume_matches_job_id_jobs'),
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['resume_id'],
            ['resumes.id'],
            name=op.f('fk_job_resume_matches_resume_id_resumes'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_job_resume_matches')),
        sa.UniqueConstraint(
            'job_id',
            'resume_id',
            name='uq_job_resume_matches_job_id_resume_id',
        ),
    )
    op.create_index(
        op.f('ix_job_resume_matches_user_id'),
        'job_resume_matches',
        ['user_id'],
        unique=False,
    )
    op.create_index(
        'ix_job_resume_matches_user_id_job_id',
        'job_resume_matches',
        ['user_id', 'job_id'],
        unique=False,
    )
    op.create_index(
        'ix_job_resume_matches_user_id_resume_id',
        'job_resume_matches',
        ['user_id', 'resume_id'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        'ix_job_resume_matches_user_id_resume_id', table_name='job_resume_matches'
    )
    op.drop_index('ix_job_resume_matches_user_id_job_id', table_name='job_resume_matches')
    op.drop_index(op.f('ix_job_resume_matches_user_id'), table_name='job_resume_matches')
    op.drop_table('job_resume_matches')
    op.drop_index('ix_jobs_user_id_received_at', table_name='jobs')
    op.drop_index(op.f('ix_jobs_user_id'), table_name='jobs')
    op.drop_table('jobs')
