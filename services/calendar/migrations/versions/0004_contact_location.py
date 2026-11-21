"""where a contact is (read from the address in Google, or written by the owner)

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('contacts', sa.Column('location', sa.String(200), nullable=False, server_default=''))


def downgrade() -> None:
    op.drop_column('contacts', 'location')
