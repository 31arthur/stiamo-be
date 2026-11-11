"""workflows that asked a question on the phone and are waiting for the answer

Revision ID: 0022
Revises: 0021
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0022'
down_revision = '0021'
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    op.add_column('agent_settings', sa.Column('flow_waits', JSON, nullable=True))


def downgrade() -> None:
    op.drop_column('agent_settings', 'flow_waits')
