"""the evening plan keeps the briefing as a card, not only as text

Revision ID: 0011
Revises: 0010
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    op.add_column('day_plans', sa.Column('card', JSON, nullable=True))


def downgrade() -> None:
    op.drop_column('day_plans', 'card')
