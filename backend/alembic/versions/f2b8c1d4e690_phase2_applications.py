"""phase 2 candidate profile expansion and applications

Revision ID: f2b8c1d4e690
Revises: c4a7e1b8d920
Create Date: 2026-11-05 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = 'f2b8c1d4e690'
down_revision: str | None = 'c4a7e1b8d920'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- Candidate profile expansion ----------------------------------------
    op.add_column('candidates', sa.Column('user_id', sa.Uuid(), nullable=True))
    op.add_column('candidates', sa.Column('preferred_name', sa.String(length=200), nullable=True))
    op.add_column(
        'candidates', sa.Column('phone_country_code', sa.String(length=8), nullable=True)
    )
    op.add_column(
        'candidates', sa.Column('address_line1', sa.String(length=300), nullable=True)
    )
    op.add_column(
        'candidates', sa.Column('address_line2', sa.String(length=300), nullable=True)
    )
    op.add_column('candidates', sa.Column('city', sa.String(length=120), nullable=True))
    op.add_column('candidates', sa.Column('region', sa.String(length=120), nullable=True))
    op.add_column('candidates', sa.Column('postal_code', sa.String(length=32), nullable=True))
    op.add_column('candidates', sa.Column('country', sa.String(length=100), nullable=True))
    op.add_column(
        'candidates', sa.Column('work_authorization', sa.String(length=200), nullable=True)
    )
    op.add_column(
        'candidates', sa.Column('requires_sponsorship', sa.Boolean(), nullable=True)
    )
    op.add_column(
        'candidates', sa.Column('notice_period_days', sa.Integer(), nullable=True)
    )
    op.add_column('candidates', sa.Column('available_from', sa.Date(), nullable=True))
    op.create_index(op.f('ix_candidates_user_id'), 'candidates', ['user_id'], unique=True)
    op.create_foreign_key(
        op.f('fk_candidates_user_id_users'),
        'candidates',
        'users',
        ['user_id'],
        ['id'],
        ondelete='SET NULL',
    )

    op.create_table(
        'certifications',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('candidate_id', sa.Uuid(), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('issuer', sa.String(length=200), nullable=True),
        sa.Column('issue_date', sa.Date(), nullable=True),
        sa.Column('expiry_date', sa.Date(), nullable=True),
        sa.Column('credential_id', sa.String(length=200), nullable=True),
        sa.Column('credential_url', sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(
            ['candidate_id'],
            ['candidates.id'],
            name=op.f('fk_certifications_candidate_id_candidates'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_certifications')),
    )
    op.create_index(
        op.f('ix_certifications_candidate_id'), 'certifications', ['candidate_id'], unique=False
    )

    # --- Applications --------------------------------------------------------
    op.create_table(
        'applications',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('job_id', sa.Uuid(), nullable=False),
        sa.Column('candidate_id', sa.Uuid(), nullable=True),
        sa.Column('platform', sa.String(length=32), nullable=False),
        sa.Column('platform_name', sa.String(length=100), nullable=False),
        sa.Column('application_url', sa.String(length=1000), nullable=False),
        sa.Column('automation_mode', sa.String(length=32), nullable=False),
        sa.Column('inspection_mode', sa.String(length=32), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('stage', sa.String(length=32), nullable=True),
        sa.Column('requires_account', sa.Boolean(), nullable=False),
        sa.Column('resume_id', sa.Uuid(), nullable=True),
        sa.Column('resume_reason', sa.Text(), nullable=False),
        sa.Column('resume_score', sa.Float(), nullable=True),
        sa.Column('resume_filename', sa.String(length=255), nullable=True),
        sa.Column('notes', sa.JSON(), nullable=False),
        sa.Column('missing_fields', sa.JSON(), nullable=False),
        sa.Column('approval_fingerprint', sa.String(length=64), nullable=True),
        sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('submission_reference', sa.String(length=255), nullable=True),
        sa.Column('failure_reason', sa.Text(), nullable=True),
        sa.Column('automation_details', sa.JSON(), nullable=False),
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
            name=op.f('fk_applications_user_id_users'),
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['job_id'],
            ['jobs.id'],
            name=op.f('fk_applications_job_id_jobs'),
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['candidate_id'],
            ['candidates.id'],
            name=op.f('fk_applications_candidate_id_candidates'),
            ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['resume_id'],
            ['resumes.id'],
            name=op.f('fk_applications_resume_id_resumes'),
            ondelete='SET NULL',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_applications')),
        sa.UniqueConstraint('user_id', 'job_id', name='uq_applications_user_id_job_id'),
    )
    op.create_index(op.f('ix_applications_user_id'), 'applications', ['user_id'], unique=False)
    op.create_index(op.f('ix_applications_job_id'), 'applications', ['job_id'], unique=False)
    op.create_index(
        'ix_applications_user_id_status', 'applications', ['user_id', 'status'], unique=False
    )

    op.create_table(
        'application_questions',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('application_id', sa.Uuid(), nullable=False),
        sa.Column('question_key', sa.String(length=100), nullable=False),
        sa.Column('field_identifier', sa.String(length=200), nullable=True),
        sa.Column('section', sa.String(length=50), nullable=False),
        sa.Column('question_text', sa.Text(), nullable=False),
        sa.Column('help_text', sa.Text(), nullable=True),
        sa.Column('field_type', sa.String(length=32), nullable=False),
        sa.Column('required', sa.Boolean(), nullable=False),
        sa.Column('options', sa.JSON(), nullable=False),
        sa.Column('suggested_answer', sa.Text(), nullable=True),
        sa.Column('answer', sa.Text(), nullable=True),
        sa.Column('answer_source', sa.String(length=32), nullable=False),
        sa.Column('confidence', sa.Float(), nullable=False),
        sa.Column('requires_confirmation', sa.Boolean(), nullable=False),
        sa.Column('is_sensitive', sa.Boolean(), nullable=False),
        sa.Column('is_legal', sa.Boolean(), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('order_index', sa.Integer(), nullable=False),
        sa.Column('answered_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
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
            ['application_id'],
            ['applications.id'],
            name=op.f('fk_application_questions_application_id_applications'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_application_questions')),
        sa.UniqueConstraint(
            'application_id',
            'question_key',
            name='uq_application_questions_application_id_question_key',
        ),
    )
    op.create_index(
        op.f('ix_application_questions_application_id'),
        'application_questions',
        ['application_id'],
        unique=False,
    )
    op.create_index(
        'ix_application_questions_application_id_section',
        'application_questions',
        ['application_id', 'section'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        'ix_application_questions_application_id_section',
        table_name='application_questions',
    )
    op.drop_index(
        op.f('ix_application_questions_application_id'), table_name='application_questions'
    )
    op.drop_table('application_questions')

    op.drop_index('ix_applications_user_id_status', table_name='applications')
    op.drop_index(op.f('ix_applications_job_id'), table_name='applications')
    op.drop_index(op.f('ix_applications_user_id'), table_name='applications')
    op.drop_table('applications')

    op.drop_index(op.f('ix_certifications_candidate_id'), table_name='certifications')
    op.drop_table('certifications')

    op.drop_constraint(op.f('fk_candidates_user_id_users'), 'candidates', type_='foreignkey')
    op.drop_index(op.f('ix_candidates_user_id'), table_name='candidates')
    for column in (
        'available_from',
        'notice_period_days',
        'requires_sponsorship',
        'work_authorization',
        'country',
        'postal_code',
        'region',
        'city',
        'address_line2',
        'address_line1',
        'phone_country_code',
        'preferred_name',
        'user_id',
    ):
        op.drop_column('candidates', column)
