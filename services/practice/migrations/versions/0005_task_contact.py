"""a task can name who it is about or who to reach for it

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('tasks', sa.Column('contact', sa.String(length=300), nullable=True))


def downgrade() -> None:
    op.drop_column('tasks', 'contact')
