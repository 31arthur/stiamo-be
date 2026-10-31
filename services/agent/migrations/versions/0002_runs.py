"""agent runs: what each agent was given, did and answered

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    op.create_table('agent_runs',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('agent_id', sa.String(length=64), nullable=False),
    sa.Column('trigger', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=8), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('duration_ms', sa.Integer(), nullable=False),
    sa.Column('provider', sa.String(length=16), nullable=False),
    sa.Column('model', sa.String(length=200), nullable=False),
    sa.Column('thinking', sa.String(length=8), nullable=False),
    sa.Column('input', sa.Text(), nullable=False),
    sa.Column('user_message', sa.Text(), nullable=False),
    sa.Column('system_prompt', sa.Text(), nullable=False),
    sa.Column('tools', JSON, nullable=False),
    sa.Column('steps', JSON, nullable=False),
    sa.Column('output', sa.Text(), nullable=False),
    sa.Column('found_count', sa.Integer(), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('usage', JSON, nullable=False),
    sa.Column('channel', sa.String(length=36), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_agent_runs_agent', 'agent_runs', ['user_id', 'agent_id', 'started_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_agent_runs_agent', table_name='agent_runs')
    op.drop_table('agent_runs')
