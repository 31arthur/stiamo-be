"""reminders about calls that were not marked done

Revision ID: 0013
Revises: 0012
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0013'
down_revision = '0012'
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    op.add_column('agent_settings', sa.Column('reminders', JSON, nullable=True))


def downgrade() -> None:
    op.drop_column('agent_settings', 'reminders')
