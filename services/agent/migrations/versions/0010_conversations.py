"""conversations with the team's agents, kept per person

Revision ID: 0010
Revises: 0009
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    op.create_table(
        'agent_conversations',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('user_id', sa.String(36), nullable=False),
        sa.Column('agent_id', sa.String(64), nullable=False),
        sa.Column('workspace', sa.String(16), nullable=False),
        sa.Column('title', sa.String(120), nullable=False),
        sa.Column('turns', JSON, nullable=False),
        sa.Column('messages', JSON, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_agent_conversations_user', 'agent_conversations', ['user_id', 'updated_at'])


def downgrade() -> None:
    op.drop_index('ix_agent_conversations_user', table_name='agent_conversations')
    op.drop_table('agent_conversations')
