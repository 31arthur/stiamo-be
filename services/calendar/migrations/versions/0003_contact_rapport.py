"""what the owner knows about a contact (tags, profile, zone), when it first appeared, and whether a rapport plan is wanted

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('contacts', sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('contacts', sa.Column('baseline', sa.Boolean, nullable=False, server_default=sa.false()))
    op.add_column('contacts', sa.Column('google_updated', sa.String(40), nullable=False, server_default=''))
    op.add_column('contacts', sa.Column('tags', sa.JSON, nullable=True))
    op.add_column('contacts', sa.Column('profile', sa.JSON, nullable=True))
    op.add_column('contacts', sa.Column('timezone', sa.String(64), nullable=False, server_default=''))
    op.add_column('contacts', sa.Column('rapport_excluded', sa.Boolean, nullable=False, server_default=sa.false()))
    # what is already here is the baseline: it is not "new"
    op.execute("UPDATE contacts SET baseline = true, first_seen_at = CURRENT_TIMESTAMP")


def downgrade() -> None:
    for c in ('rapport_excluded', 'timezone', 'profile', 'tags', 'google_updated', 'baseline', 'first_seen_at'):
        op.drop_column('contacts', c)
