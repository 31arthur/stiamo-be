"""Google contacts: the accounts linked to a workspace for them, and the contacts read from each

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'contact_accounts',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('user_id', sa.String(36), nullable=False),
        sa.Column('workspace', sa.String(16), nullable=False),
        sa.Column('email', sa.String(320), nullable=False),
        sa.Column('refresh_sealed', sa.Text, nullable=False),
        sa.Column('status', sa.String(10), nullable=False),
        sa.Column('error', sa.String(300), nullable=False),
        sa.Column('count', sa.Integer, nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_sync_at', sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint('user_id', 'workspace', 'email', name='uq_contact_account'),
    )
    op.create_index('ix_contact_accounts_user', 'contact_accounts', ['user_id'])
    op.create_table(
        'contacts',
        sa.Column('id', sa.String(80), primary_key=True),
        sa.Column('user_id', sa.String(36), nullable=False),
        sa.Column('account_id', sa.String(36), nullable=False),
        sa.Column('workspace', sa.String(16), nullable=False),
        sa.Column('google_id', sa.String(200), nullable=False),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('organisation', sa.String(200), nullable=False, server_default=''),
        sa.Column('title', sa.String(200), nullable=False, server_default=''),
        sa.Column('emails', sa.JSON, nullable=True),
        sa.Column('phones', sa.JSON, nullable=True),
        sa.Column('notes', sa.Text, nullable=False, server_default=''),
        sa.Column('photo', sa.Text, nullable=False, server_default=''),
        sa.Column('search', sa.Text, nullable=False, server_default=''),
    )
    op.create_index('ix_contacts_user_ws', 'contacts', ['user_id', 'workspace'])
    op.create_index('ix_contacts_account', 'contacts', ['account_id'])


def downgrade() -> None:
    op.drop_index('ix_contacts_account', table_name='contacts')
    op.drop_index('ix_contacts_user_ws', table_name='contacts')
    op.drop_table('contacts')
    op.drop_index('ix_contact_accounts_user', table_name='contact_accounts')
    op.drop_table('contact_accounts')
