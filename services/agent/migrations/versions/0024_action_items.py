"""action items, their history, change sets and the queue Chittu works through

Revision ID: 0024
Revises: 0023
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0024'
down_revision = '0023'
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    op.create_table(
        'action_items',
        sa.Column('id', sa.String(36), primary_key=True), sa.Column('user_id', sa.String(36), nullable=False), sa.Column('kind', sa.String(12), nullable=False),
        sa.Column('title', sa.String(300), nullable=False), sa.Column('workspace', sa.String(16), nullable=False), sa.Column('day', sa.String(10)), sa.Column('start', sa.Integer()),
        sa.Column('duration', sa.Integer()), sa.Column('due', sa.String(10)), sa.Column('status', sa.String(10), nullable=False), sa.Column('priority', sa.String(8), nullable=False),
        sa.Column('source', sa.String(24), nullable=False), sa.Column('links', JSON), sa.Column('notes', sa.Text()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False), sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_action_items_user', 'action_items', ['user_id', 'status', 'day'])
    op.create_table(
        'action_events',
        sa.Column('id', sa.String(36), primary_key=True), sa.Column('user_id', sa.String(36), nullable=False), sa.Column('item_ref', sa.String(80), nullable=False),
        sa.Column('event', sa.String(16), nullable=False), sa.Column('actor', sa.String(40), nullable=False), sa.Column('title', sa.String(300), nullable=False),
        sa.Column('before', JSON), sa.Column('after', JSON), sa.Column('reason', sa.String(400), nullable=False), sa.Column('changeset_id', sa.String(36)), sa.Column('run_id', sa.String(36)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_action_events_item', 'action_events', ['user_id', 'item_ref', 'created_at'])
    op.create_index('ix_action_events_changeset', 'action_events', ['changeset_id'])
    op.create_table(
        'action_changesets',
        sa.Column('id', sa.String(36), primary_key=True), sa.Column('user_id', sa.String(36), nullable=False), sa.Column('title', sa.String(300), nullable=False),
        sa.Column('status', sa.String(10), nullable=False), sa.Column('ops', JSON), sa.Column('summary', JSON), sa.Column('actor', sa.String(40), nullable=False),
        sa.Column('channel_id', sa.String(36)), sa.Column('created_at', sa.DateTime(timezone=True), nullable=False), sa.Column('decided_at', sa.DateTime(timezone=True)),
    )
    op.create_index('ix_action_changesets_user', 'action_changesets', ['user_id', 'status'])
    op.create_table(
        'action_queues',
        sa.Column('id', sa.String(36), primary_key=True), sa.Column('user_id', sa.String(36), nullable=False), sa.Column('day', sa.String(10), nullable=False),
        sa.Column('status', sa.String(10), nullable=False), sa.Column('entries', JSON), sa.Column('cursor', sa.Integer(), nullable=False), sa.Column('shown', JSON),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False), sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_action_queues_user', 'action_queues', ['user_id', 'status'])


def downgrade() -> None:
    for t in ('action_queues', 'action_changesets', 'action_events', 'action_items'):
        op.drop_table(t)
