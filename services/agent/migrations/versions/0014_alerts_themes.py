"""organisation themes, and the urgent-email alerts raised on the phone

Revision ID: 0014
Revises: 0013
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0014'
down_revision = '0013'
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    op.add_column('agent_settings', sa.Column('themes', JSON, nullable=True))
    op.create_table(
        'mail_alerts',
        sa.Column('id', sa.String(12), primary_key=True),
        sa.Column('user_id', sa.String(36), nullable=False),
        sa.Column('item_id', sa.String(200), nullable=False),
        sa.Column('workflow_id', sa.String(64)),
        sa.Column('workspace', sa.String(16), nullable=False, server_default=''),
        sa.Column('account_id', sa.String(64), nullable=False, server_default=''),
        sa.Column('folder', sa.String(300), nullable=False, server_default='INBOX'),
        sa.Column('uid', sa.Integer, nullable=False, server_default='0'),
        sa.Column('subject', sa.String(400), nullable=False, server_default=''),
        sa.Column('sender_name', sa.String(200), nullable=False, server_default=''),
        sa.Column('sender_address', sa.String(320), nullable=False, server_default=''),
        sa.Column('message_id', sa.String(998), nullable=False, server_default=''),
        sa.Column('summary', sa.Text, nullable=False, server_default=''),
        sa.Column('action', sa.String(300), nullable=False, server_default=''),
        sa.Column('due', sa.String(10), nullable=False, server_default=''),
        sa.Column('priority', sa.String(8), nullable=False, server_default='high'),
        sa.Column('replies', JSON),
        sa.Column('theme_id', sa.String(40)),
        sa.Column('status', sa.String(10), nullable=False, server_default='open'),
        sa.Column('draft', JSON),
        sa.Column('awaiting', JSON),
        sa.Column('chats', JSON),
        sa.Column('sample', sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('user_id', 'item_id', name='uq_mail_alerts_item'),
    )
    op.create_index('ix_mail_alerts_user', 'mail_alerts', ['user_id', 'created_at'])


def downgrade() -> None:
    op.drop_index('ix_mail_alerts_user', table_name='mail_alerts')
    op.drop_table('mail_alerts')
    op.drop_column('agent_settings', 'themes')
