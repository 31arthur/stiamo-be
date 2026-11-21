"""campaigns: one message, link or picture to many contacts, and what came of it

Revision ID: 0027
Revises: 0026
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0027'
down_revision = '0026'
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    op.create_table(
        'campaigns',
        sa.Column('id', sa.String(36), primary_key=True), sa.Column('user_id', sa.String(36), nullable=False), sa.Column('name', sa.String(200), nullable=False),
        sa.Column('message', sa.Text, nullable=False, server_default=''), sa.Column('url', sa.Text, nullable=False, server_default=''), sa.Column('image_url', sa.Text, nullable=False, server_default=''),
        sa.Column('preview', JSON), sa.Column('track_clicks', sa.Boolean, nullable=False, server_default=sa.true()), sa.Column('status', sa.String(10), nullable=False, server_default='active'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False), sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_campaigns_user', 'campaigns', ['user_id', 'status'])
    op.create_table(
        'campaign_members',
        sa.Column('id', sa.String(36), primary_key=True), sa.Column('campaign_id', sa.String(36), nullable=False), sa.Column('user_id', sa.String(36), nullable=False),
        sa.Column('contact_key', sa.String(200), nullable=False), sa.Column('contact_id', sa.String(80), nullable=False, server_default=''), sa.Column('name', sa.String(200), nullable=False),
        sa.Column('phone', sa.String(32), nullable=False, server_default=''), sa.Column('state', sa.String(10), nullable=False, server_default='pending'), sa.Column('token', sa.String(32), nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True)), sa.Column('replied_at', sa.DateTime(timezone=True)), sa.Column('clicked_at', sa.DateTime(timezone=True)),
        sa.Column('clicks', sa.Integer, nullable=False, server_default='0'), sa.Column('note', sa.Text, nullable=False, server_default=''),
        sa.UniqueConstraint('campaign_id', 'contact_key', name='uq_campaign_member'),
    )
    op.create_index('ix_campaign_members_campaign', 'campaign_members', ['campaign_id', 'state'])
    op.create_index('ix_campaign_members_token', 'campaign_members', ['token'])


def downgrade() -> None:
    op.drop_table('campaign_members')
    op.drop_table('campaigns')
