"""what the person said about their time inside events: free for calls, free for anything, or busy

Revision ID: 0018
Revises: 0017
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0018'
down_revision = '0017'
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    op.add_column('agent_settings', sa.Column('availability', JSON, nullable=True))


def downgrade() -> None:
    op.drop_column('agent_settings', 'availability')
