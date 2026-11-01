"""google accounts, their calendars, the events read from them, what was sent back, and each person's time zone

Revision ID: 0001
Revises:
"""
from alembic import op
import sqlalchemy as sa

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'calendar_accounts',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('user_id', sa.String(36), nullable=False),
        sa.Column('workspace', sa.String(16), nullable=False),
        sa.Column('email', sa.String(320), nullable=False),
        sa.Column('refresh_sealed', sa.Text, nullable=False),
        sa.Column('status', sa.String(10), nullable=False),
        sa.Column('error', sa.String(300), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_sync_at', sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint('user_id', 'workspace', 'email', name='uq_calendar_account'),
    )
    op.create_index('ix_calendar_accounts_user', 'calendar_accounts', ['user_id'])
    op.create_table(
        'calendar_sources',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('account_id', sa.String(36), nullable=False),
        sa.Column('user_id', sa.String(36), nullable=False),
        sa.Column('google_id', sa.String(300), nullable=False),
        sa.Column('name', sa.String(300), nullable=False),
        sa.Column('color', sa.String(9), nullable=False),
        sa.Column('role', sa.String(20), nullable=False),
        sa.Column('timezone', sa.String(64), nullable=False),
        sa.Column('enabled', sa.Boolean, nullable=False),
        sa.Column('export', sa.Boolean, nullable=False),
        sa.Column('sync_token', sa.Text, nullable=True),
        sa.Column('synced_through', sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint('account_id', 'google_id', name='uq_calendar_source'),
    )
    op.create_index('ix_calendar_sources_user', 'calendar_sources', ['user_id'])
    op.create_table(
        'calendar_events',
        sa.Column('id', sa.String(200), primary_key=True),
        sa.Column('user_id', sa.String(36), nullable=False),
        sa.Column('source_id', sa.String(36), nullable=False),
        sa.Column('google_id', sa.String(200), nullable=False),
        sa.Column('title', sa.String(300), nullable=False),
        sa.Column('description', sa.Text, nullable=False),
        sa.Column('location', sa.String(300), nullable=False),
        sa.Column('all_day', sa.Boolean, nullable=False),
        sa.Column('start', sa.DateTime(timezone=True), nullable=False),
        sa.Column('end', sa.DateTime(timezone=True), nullable=False),
        sa.Column('link', sa.Text, nullable=False),
        sa.Column('meet', sa.Text, nullable=False),
        sa.Column('attendees', sa.Integer, nullable=False),
        sa.Column('updated', sa.String(40), nullable=False),
    )
    op.create_index('ix_calendar_events_range', 'calendar_events', ['user_id', 'start'])
    op.create_index('ix_calendar_events_source', 'calendar_events', ['source_id'])
    op.create_table(
        'calendar_links',
        sa.Column('user_id', sa.String(36), primary_key=True),
        sa.Column('ref', sa.String(80), primary_key=True),
        sa.Column('source_id', sa.String(36), nullable=False),
        sa.Column('google_id', sa.String(200), nullable=False),
        sa.Column('digest', sa.String(40), nullable=False),
    )
    op.create_table(
        'calendar_prefs',
        sa.Column('user_id', sa.String(36), primary_key=True),
        sa.Column('timezone', sa.String(64), nullable=False),
    )


def downgrade() -> None:
    for t in ('calendar_prefs', 'calendar_links', 'calendar_events', 'calendar_sources', 'calendar_accounts'):
        op.drop_table(t)
