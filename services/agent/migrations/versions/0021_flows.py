"""workflows as n8n draws them: each agent's flow, and the agents a person created from workflows

Revision ID: 0021
Revises: 0020
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0021'
down_revision = '0020'
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    op.add_column('agent_settings', sa.Column('flows', JSON, nullable=True))
    op.add_column('agent_settings', sa.Column('custom_agents', JSON, nullable=True))


def downgrade() -> None:
    op.drop_column('agent_settings', 'custom_agents')
    op.drop_column('agent_settings', 'flows')
