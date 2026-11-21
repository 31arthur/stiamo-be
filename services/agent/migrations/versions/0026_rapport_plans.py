"""rapport plans: staying in touch with business contacts

Revision ID: 0026
Revises: 0025
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0026'
down_revision = '0025'
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    op.create_table(
        'rapport_plans',
        sa.Column('id', sa.String(36), primary_key=True), sa.Column('user_id', sa.String(36), nullable=False), sa.Column('contact_key', sa.String(200), nullable=False),
        sa.Column('contact_id', sa.String(80), nullable=False), sa.Column('name', sa.String(200), nullable=False), sa.Column('workspaces', JSON), sa.Column('phone', sa.String(32), nullable=False, server_default=''),
        sa.Column('zone', sa.String(64), nullable=False, server_default=''), sa.Column('about', JSON), sa.Column('tier', sa.String(1), nullable=False, server_default='B'),
        sa.Column('status', sa.String(10), nullable=False, server_default='active'), sa.Column('auto_send', sa.Boolean, nullable=False, server_default=sa.true()), sa.Column('plan', JSON),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False), sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('user_id', 'contact_key', name='uq_rapport_plans_contact'),
    )
    op.create_index('ix_rapport_plans_user', 'rapport_plans', ['user_id', 'status'])


def downgrade() -> None:
    op.drop_table('rapport_plans')
