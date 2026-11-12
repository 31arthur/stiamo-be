"""gauging a person: evidence about them, lenses of the owner's own, the profile kept on the character, and which scenes are coded

Revision ID: 0009
Revises: 0008
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None

Json = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _owned() -> list[sa.Column]:
    return [
        sa.Column('user_id', sa.String(length=36), primary_key=True),
        sa.Column('id', sa.String(length=64), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        'evidence', *_owned(),
        sa.Column('character_id', sa.String(length=64), nullable=False),
        sa.Column('story_id', sa.String(length=64), nullable=True),
        sa.Column('workspace', sa.String(length=10), nullable=False),
        sa.Column('lens', sa.String(length=80), nullable=False),
        sa.Column('label', sa.String(length=40), nullable=False),
        sa.Column('source', sa.String(length=12), nullable=False),
        sa.Column('method', sa.String(length=12), nullable=False),
        sa.Column('strength', sa.String(length=6), nullable=False),
        sa.Column('specific', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('date', sa.String(length=10), nullable=False),
        sa.Column('quote', sa.Text(), nullable=True),
        sa.Column('scene_id', sa.String(length=64), nullable=True),
        sa.Column('predicted_by', sa.String(length=6), nullable=True),
        sa.Column('verdict', sa.String(length=8), nullable=True),
        sa.Column('about', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['user_id', 'character_id'], ['characters.user_id', 'characters.id'], ondelete='CASCADE'),
    )
    op.create_index('ix_evidence_character', 'evidence', ['user_id', 'character_id'])
    op.create_index('ix_evidence_user_updated', 'evidence', ['user_id', 'updated_at'])

    op.create_table(
        'lenses', *_owned(),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('pole_a', sa.String(length=120), nullable=False),
        sa.Column('pole_b', sa.String(length=120), nullable=False),
    )
    op.create_index('ix_lenses_user_updated', 'lenses', ['user_id', 'updated_at'])

    op.add_column('characters', sa.Column('profile', Json, nullable=True))
    op.add_column('scenes', sa.Column('coded', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column('scenes', 'coded')
    op.drop_column('characters', 'profile')
    op.drop_table('lenses')
    op.drop_table('evidence')
