"""workflow executions: one row for each real run of a workflow agent

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    op.create_table('workflow_executions',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('agent_id', sa.String(length=64), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('duration_ms', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=8), nullable=False),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('counts', JSON, nullable=False),
    sa.Column('stats', JSON, nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_workflow_executions_agent', 'workflow_executions', ['agent_id', 'started_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_workflow_executions_agent', table_name='workflow_executions')
    op.drop_table('workflow_executions')
