"""what an agent proposed to add or change, kept until the person approves or drops it

Revision ID: 0009
Revises: 0008
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    op.create_table(
        'agent_proposals',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('user_id', sa.String(36), nullable=False),
        sa.Column('agent_id', sa.String(64), nullable=False),
        sa.Column('action', sa.String(32), nullable=False),
        sa.Column('title', sa.String(300), nullable=False),
        sa.Column('lines', JSON, nullable=False),
        sa.Column('ops', JSON, nullable=False),
        sa.Column('status', sa.String(10), nullable=False),
        sa.Column('channel_id', sa.String(36), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_agent_proposals_user', 'agent_proposals', ['user_id', 'status'])


def downgrade() -> None:
    op.drop_index('ix_agent_proposals_user', table_name='agent_proposals')
    op.drop_table('agent_proposals')
