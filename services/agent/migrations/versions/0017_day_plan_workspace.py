"""a day plan belongs to a workspace: each workspace plans its own day

Revision ID: 0017
Revises: 0016
"""
from alembic import op
import sqlalchemy as sa

revision = '0017'
down_revision = '0016'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('day_plans') as batch:
        batch.add_column(sa.Column('workspace', sa.String(16), nullable=False, server_default=''))
        batch.drop_constraint('uq_day_plans_user_day', type_='unique')
        batch.create_unique_constraint('uq_day_plans_user_day_ws', ['user_id', 'day', 'workspace'])


def downgrade() -> None:
    with op.batch_alter_table('day_plans') as batch:
        batch.drop_constraint('uq_day_plans_user_day_ws', type_='unique')
        batch.create_unique_constraint('uq_day_plans_user_day', ['user_id', 'day'])
        batch.drop_column('workspace')
