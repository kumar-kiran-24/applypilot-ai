"""add auth codes table

Revision ID: 9d4f1a6c2b70
Revises: 6b874a3eece7
Create Date: 2026-11-04 20:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '9d4f1a6c2b70'
down_revision: str | None = '6b874a3eece7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('auth_codes',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('code_hash', sa.String(length=64), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('consumed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_auth_codes_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_auth_codes'))
    )
    op.create_index(op.f('ix_auth_codes_code_hash'), 'auth_codes', ['code_hash'], unique=True)
    op.create_index(op.f('ix_auth_codes_user_id'), 'auth_codes', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_auth_codes_user_id'), table_name='auth_codes')
    op.drop_index(op.f('ix_auth_codes_code_hash'), table_name='auth_codes')
    op.drop_table('auth_codes')
