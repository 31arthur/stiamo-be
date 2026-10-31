"""mail accounts

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
    op.create_table('mail_accounts',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('owner_id', sa.String(length=36), nullable=False),
    sa.Column('workspace', sa.String(length=16), nullable=False),
    sa.Column('label', sa.String(length=80), nullable=False),
    sa.Column('address', sa.String(length=320), nullable=False),
    sa.Column('host', sa.String(length=255), nullable=False),
    sa.Column('port', sa.Integer(), nullable=False),
    sa.Column('security', sa.String(length=8), nullable=False),
    sa.Column('username', sa.String(length=320), nullable=False),
    sa.Column('password_sealed', sa.Text(), nullable=False),
    sa.Column('error', sa.String(length=300), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_mail_accounts_owner', 'mail_accounts', ['owner_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_mail_accounts_owner', table_name='mail_accounts')
    op.drop_table('mail_accounts')
