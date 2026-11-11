"""agents as graphs: each agent's workflow (when it was changed from the default), and the node-by-node trace of every run

Revision ID: 0020
Revises: 0019
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0020'
down_revision = '0019'
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    op.add_column('agent_settings', sa.Column('graphs', JSON, nullable=True))
    op.add_column('agent_runs', sa.Column('trace', JSON, nullable=True))


def downgrade() -> None:
    op.drop_column('agent_runs', 'trace')
    op.drop_column('agent_settings', 'graphs')
