"""the priority mail watch: which workspaces have it switched off

Revision ID: 0015
Revises: 0014
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0015'
down_revision = '0014'
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    op.add_column('agent_settings', sa.Column('mailwatch', JSON, nullable=True))


def downgrade() -> None:
    op.drop_column('agent_settings', 'mailwatch')
