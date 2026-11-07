"""tasks and follow-ups can point at a contact, so a person's calls are found together

Revision ID: 0006
Revises: 0005
"""
from alembic import op
import sqlalchemy as sa

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('tasks', sa.Column('contact_id', sa.String(length=80), nullable=True))
    op.add_column('follow_ups', sa.Column('contact_id', sa.String(length=80), nullable=True))


def downgrade() -> None:
    op.drop_column('follow_ups', 'contact_id')
    op.drop_column('tasks', 'contact_id')
