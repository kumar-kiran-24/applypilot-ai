"""add resumes table

Revision ID: b7c2d9e4f1a3
Revises: 9d4f1a6c2b70
Create Date: 2026-11-04 21:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = 'b7c2d9e4f1a3'
down_revision: str | None = '9d4f1a6c2b70'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'resumes',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('original_filename', sa.String(length=255), nullable=False),
        sa.Column('storage_key', sa.String(length=500), nullable=False),
        sa.Column('storage_backend', sa.String(length=32), nullable=False),
        sa.Column('content_type', sa.String(length=100), nullable=False),
        sa.Column('file_size', sa.BigInteger(), nullable=False),
        sa.Column('checksum', sa.String(length=64), nullable=False),
        sa.Column('is_default', sa.Boolean(), nullable=False),
        sa.Column(
            'parsing_status',
            sa.Enum(
                'pending',
                'processing',
                'completed',
                'ocr_required',
                'failed',
                name='resume_parsing_status',
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column('parsing_error', sa.String(length=100), nullable=True),
        sa.Column('parsed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('parsed_data', sa.JSON(), nullable=False),
        sa.Column('skills', sa.JSON(), nullable=False),
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
            name=op.f('fk_resumes_user_id_users'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_resumes')),
    )
    op.create_index(op.f('ix_resumes_user_id'), 'resumes', ['user_id'], unique=False)
    op.create_index(
        'ix_resumes_user_id_is_default', 'resumes', ['user_id', 'is_default'], unique=False
    )


def downgrade() -> None:
    op.drop_index('ix_resumes_user_id_is_default', table_name='resumes')
    op.drop_index(op.f('ix_resumes_user_id'), table_name='resumes')
    op.drop_table('resumes')
