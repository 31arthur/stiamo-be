"""what a conversation has been about, and which phone chat a thread belongs to

Revision ID: 0012
Revises: 0011
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    op.add_column('agent_conversations', sa.Column('context', JSON, nullable=True))
    op.add_column('agent_conversations', sa.Column('channel_key', sa.String(160), nullable=True))
    op.create_index('ix_agent_conversations_channel', 'agent_conversations', ['user_id', 'channel_key'])


def downgrade() -> None:
    op.drop_index('ix_agent_conversations_channel', table_name='agent_conversations')
    op.drop_column('agent_conversations', 'channel_key')
    op.drop_column('agent_conversations', 'context')
