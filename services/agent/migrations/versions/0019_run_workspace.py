"""a run records which workspace it ran under, so an execution can be traced and audited

Revision ID: 0019
Revises: 0018
"""
from alembic import op
import sqlalchemy as sa

revision = '0019'
down_revision = '0018'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('agent_runs', sa.Column('workspace', sa.String(16), nullable=False, server_default=''))


def downgrade() -> None:
    op.drop_column('agent_runs', 'workspace')
