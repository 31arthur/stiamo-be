"""the daily planner: each person's evening settings, and the plan it made for each day

Revision ID: 0008
Revises: 0007
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    op.add_column('agent_settings', sa.Column('planner', JSON, nullable=True))
    op.create_table(
        'day_plans',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('user_id', sa.String(36), nullable=False),
        sa.Column('day', sa.String(10), nullable=False),
        sa.Column('status', sa.String(12), nullable=False),
        sa.Column('briefing', sa.Text, nullable=False),
        sa.Column('blocks', JSON, nullable=False),
        sa.Column('run_id', sa.String(36), nullable=True),
        sa.Column('trigger', sa.String(12), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint('user_id', 'day', name='uq_day_plans_user_day'),
    )


def downgrade() -> None:
    op.drop_table('day_plans')
    op.drop_column('agent_settings', 'planner')
