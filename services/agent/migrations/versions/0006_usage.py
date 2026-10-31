"""agent usage: tokens and cost of every run, kept apart from the run history

Revision ID: 0006
Revises: 0005
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    usage = op.create_table('agent_usage',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('agent_id', sa.String(length=64), nullable=False),
    sa.Column('agent_name', sa.String(length=100), nullable=False),
    sa.Column('kind', sa.String(length=8), nullable=False),
    sa.Column('trigger', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=8), nullable=False),
    sa.Column('at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('duration_ms', sa.Integer(), nullable=False),
    sa.Column('provider', sa.String(length=16), nullable=False),
    sa.Column('model', sa.String(length=200), nullable=False),
    sa.Column('input_tokens', sa.Integer(), nullable=False),
    sa.Column('output_tokens', sa.Integer(), nullable=False),
    sa.Column('cost', sa.Float(), nullable=True),
    sa.Column('items', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_agent_usage_user', 'agent_usage', ['user_id', 'at'], unique=False)
    op.create_index('ix_agent_usage_agent', 'agent_usage', ['user_id', 'agent_id', 'at'], unique=False)

    # What the runs kept so far already used: carry it over (their agents' names are filled in when the usage is shown).
    runs = sa.table('agent_runs', sa.column('id'), sa.column('user_id'), sa.column('agent_id'), sa.column('trigger'), sa.column('status'), sa.column('started_at', sa.DateTime(timezone=True)), sa.column('duration_ms'), sa.column('provider'), sa.column('model'), sa.column('usage', JSON), sa.column('steps_count'))
    rows = op.get_bind().execute(sa.select(runs.c.id, runs.c.user_id, runs.c.agent_id, runs.c.trigger, runs.c.status, runs.c.started_at, runs.c.duration_ms, runs.c.provider, runs.c.model, runs.c.usage, runs.c.steps_count)).fetchall()
    carried = []
    for r in rows:
        u = r.usage if isinstance(r.usage, dict) else {}
        cost = u.get('cost')
        carried.append(dict(
            id=r.id, user_id=r.user_id, agent_id=r.agent_id, agent_name='', kind='workflow' if str(r.agent_id).startswith('wf-') else 'chat',
            trigger=r.trigger, status=r.status, at=r.started_at, duration_ms=r.duration_ms or 0, provider=r.provider or '', model=r.model or '',
            input_tokens=int(u.get('inputTokens', 0) or 0), output_tokens=int(u.get('outputTokens', 0) or 0),
            cost=float(cost) if isinstance(cost, (int, float)) else None, items=int(r.steps_count or 0) if str(r.agent_id).startswith('wf-') else 0,
        ))
    if carried:
        op.bulk_insert(usage, carried)


def downgrade() -> None:
    op.drop_index('ix_agent_usage_agent', table_name='agent_usage')
    op.drop_index('ix_agent_usage_user', table_name='agent_usage')
    op.drop_table('agent_usage')
