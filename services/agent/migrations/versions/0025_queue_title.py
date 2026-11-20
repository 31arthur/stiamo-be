"""the worklist says what it is and where it came from

Revision ID: 0025
Revises: 0024
"""
from alembic import op
import sqlalchemy as sa

revision = '0025'
down_revision = '0024'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('action_queues', sa.Column('title', sa.String(80), nullable=False, server_default=''))
    op.add_column('action_queues', sa.Column('source', sa.String(12), nullable=False, server_default='ask'))


def downgrade() -> None:
    op.drop_column('action_queues', 'source')
    op.drop_column('action_queues', 'title')
