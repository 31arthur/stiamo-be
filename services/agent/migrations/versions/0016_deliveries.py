"""what was sent to the phone, and whether it got there

Revision ID: 0016
Revises: 0015
"""
from alembic import op
import sqlalchemy as sa

revision = '0016'
down_revision = '0015'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'deliveries',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('user_id', sa.String(36), nullable=False),
        sa.Column('kind', sa.String(12), nullable=False),
        sa.Column('channel', sa.String(12), nullable=False),
        sa.Column('target', sa.String(160), nullable=False, server_default=''),
        sa.Column('ok', sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column('error', sa.Text, nullable=False, server_default=''),
        sa.Column('summary', sa.String(300), nullable=False, server_default=''),
        sa.Column('workspace', sa.String(16), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_deliveries_user', 'deliveries', ['user_id', 'created_at'])


def downgrade() -> None:
    op.drop_index('ix_deliveries_user', table_name='deliveries')
    op.drop_table('deliveries')
