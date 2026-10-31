"""agent settings and channels

Revision ID: 0001
Revises:
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    op.create_table('agent_settings',
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('keys_sealed', sa.Text(), nullable=True),
    sa.Column('selection', JSON, nullable=True),
    sa.Column('decision', JSON, nullable=True),
    sa.Column('prompt', JSON, nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('user_id')
    )
    op.create_table('agent_channels',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('kind', sa.String(length=12), nullable=False),
    sa.Column('secrets_sealed', sa.Text(), nullable=True),
    sa.Column('public', JSON, nullable=False),
    sa.Column('state', JSON, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_agent_channels_user', 'agent_channels', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_agent_channels_user', table_name='agent_channels')
    op.drop_table('agent_channels')
    op.drop_table('agent_settings')
