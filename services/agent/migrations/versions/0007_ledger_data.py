"""ledger entries carry what the agent worked out about an item

Revision ID: 0007
Revises: 0006
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    op.add_column('workflow_ledger', sa.Column('data', JSON, nullable=True))


def downgrade() -> None:
    op.drop_column('workflow_ledger', 'data')
