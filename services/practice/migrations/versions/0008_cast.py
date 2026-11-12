"""the cast: characters shared across stories, the parts they play and their scenes; tasks and calls can point at a character

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
        'characters', *_owned(),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('organisation', sa.String(length=300), nullable=True),
        sa.Column('title', sa.String(length=200), nullable=True),
        sa.Column('phones', Json, nullable=False),
        sa.Column('emails', Json, nullable=False),
        sa.Column('google_contact_id', sa.String(length=80), nullable=True),
        sa.Column('home', sa.String(length=10), nullable=False),
    )
    op.create_index('ix_characters_user_updated', 'characters', ['user_id', 'updated_at'])

    op.create_table(
        'parts', *_owned(),
        sa.Column('character_id', sa.String(length=64), nullable=False),
        sa.Column('story_id', sa.String(length=64), nullable=False),
        sa.Column('workspace', sa.String(length=10), nullable=False),
        sa.Column('part', sa.String(length=200), nullable=False, server_default=''),
        sa.Column('known_as', Json, nullable=False),
        sa.Column('stance', sa.String(length=10), nullable=True),
        sa.Column('sway', sa.String(length=10), nullable=True),
        sa.ForeignKeyConstraint(['user_id', 'character_id'], ['characters.user_id', 'characters.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id', 'story_id'], ['stories.user_id', 'stories.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('user_id', 'character_id', 'story_id', name='uq_parts_character_story'),
    )
    op.create_index('ix_parts_story', 'parts', ['user_id', 'story_id'])
    op.create_index('ix_parts_user_updated', 'parts', ['user_id', 'updated_at'])

    op.create_table(
        'scenes', *_owned(),
        sa.Column('character_id', sa.String(length=64), nullable=False),
        sa.Column('story_id', sa.String(length=64), nullable=False),
        sa.Column('workspace', sa.String(length=10), nullable=False),
        sa.Column('chapter_id', sa.String(length=64), nullable=True),
        sa.Column('date', sa.String(length=10), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('quote', sa.Text(), nullable=True),
        sa.Column('source', sa.String(length=10), nullable=False),
        sa.ForeignKeyConstraint(['user_id', 'character_id'], ['characters.user_id', 'characters.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id', 'story_id'], ['stories.user_id', 'stories.id'], ondelete='CASCADE'),
    )
    op.create_index('ix_scenes_character', 'scenes', ['user_id', 'character_id', 'date'])
    op.create_index('ix_scenes_story', 'scenes', ['user_id', 'story_id'])
    op.create_index('ix_scenes_user_updated', 'scenes', ['user_id', 'updated_at'])

    op.add_column('tasks', sa.Column('character_id', sa.String(length=64), nullable=True))
    op.add_column('follow_ups', sa.Column('character_id', sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column('follow_ups', 'character_id')
    op.drop_column('tasks', 'character_id')
    op.drop_table('scenes')
    op.drop_table('parts')
    op.drop_table('characters')
