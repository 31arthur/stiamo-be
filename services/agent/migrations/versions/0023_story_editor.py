"""the story editor's memory: which step of the ladder each story has had, questions waiting for a tap, the writers' room

Revision ID: 0023
Revises: 0022
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0023'
down_revision = '0022'
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    op.add_column('agent_settings', sa.Column('editor', JSON, nullable=True))


def downgrade() -> None:
    op.drop_column('agent_settings', 'editor')
