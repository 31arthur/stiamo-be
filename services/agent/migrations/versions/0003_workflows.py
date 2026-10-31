"""workflow agents: the agents a person builds, their versions and their ledger

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    op.add_column('agent_runs', sa.Column('version', sa.Integer(), nullable=False, server_default='0'))
    op.create_table('workflow_agents',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('description', sa.String(length=300), nullable=False),
    sa.Column('config', JSON, nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_run_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_workflow_agents_user', 'workflow_agents', ['user_id'], unique=False)
    op.create_table('workflow_versions',
    sa.Column('agent_id', sa.String(length=64), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('config', JSON, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('agent_id', 'version')
    )
    op.create_table('workflow_ledger',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('agent_id', sa.String(length=64), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('item_id', sa.String(length=200), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('excerpt', sa.Text(), nullable=False),
    sa.Column('label', sa.String(length=40), nullable=True),
    sa.Column('confidence', sa.Float(), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('run_id', sa.String(length=36), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('agent_id', 'item_id', name='uq_ledger_item')
    )
    op.create_index('ix_ledger_status', 'workflow_ledger', ['agent_id', 'status'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_ledger_status', table_name='workflow_ledger')
    op.drop_table('workflow_ledger')
    op.drop_table('workflow_versions')
    op.drop_index('ix_workflow_agents_user', table_name='workflow_agents')
    op.drop_table('workflow_agents')
    op.drop_column('agent_runs', 'version')
