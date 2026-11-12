"""stories told in chapters, with their own checkpoints, and arc templates per workspace

Revision ID: 0007
Revises: 0006
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0007'
down_revision = '0006'
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
        'stories', *_owned(),
        sa.Column('title', sa.String(length=300), nullable=False),
        sa.Column('workspace', sa.String(length=10), nullable=False),
        sa.Column('ending', sa.Text(), nullable=False, server_default=''),
        sa.Column('stakes', sa.Integer(), nullable=False, server_default='3'),
        sa.Column('stakes_amount', sa.String(length=200), nullable=True),
        sa.Column('chance', sa.Integer(), nullable=True),
        sa.Column('pacing', sa.String(length=10), nullable=False),
        sa.Column('state', sa.String(length=10), nullable=False),
        sa.Column('waiting_on', sa.Text(), nullable=True),
        sa.Column('waiting_until', sa.String(length=10), nullable=True),
        sa.Column('checkpoints', Json, nullable=False),
        sa.Column('bible', Json, nullable=False),
        sa.Column('links', Json, nullable=False),
        sa.Column('recap', sa.Text(), nullable=False, server_default=''),
        sa.Column('ended_on', sa.String(length=10), nullable=True),
    )
    op.create_index('ix_stories_workspace', 'stories', ['user_id', 'workspace'])
    op.create_index('ix_stories_user_updated', 'stories', ['user_id', 'updated_at'])

    op.create_table(
        'chapters', *_owned(),
        sa.Column('story_id', sa.String(length=64), nullable=False),
        sa.Column('number', sa.Integer(), nullable=False),
        sa.Column('date', sa.String(length=10), nullable=False),
        sa.Column('title', sa.String(length=300), nullable=False, server_default=''),
        sa.Column('prose', sa.Text(), nullable=False),
        sa.Column('beat', sa.String(length=14), nullable=False),
        sa.Column('source', sa.String(length=10), nullable=False),
        sa.Column('chance_after', sa.Integer(), nullable=True),
        sa.Column('notes', Json, nullable=False),
        sa.Column('hunches', Json, nullable=False),
        sa.Column('cliffhanger', Json, nullable=True),
        sa.Column('people', Json, nullable=False),
        sa.ForeignKeyConstraint(['user_id', 'story_id'], ['stories.user_id', 'stories.id'], ondelete='CASCADE'),
    )
    op.create_index('ix_chapters_story', 'chapters', ['user_id', 'story_id', 'number'])
    op.create_index('ix_chapters_user_updated', 'chapters', ['user_id', 'updated_at'])

    op.create_table(
        'arc_templates', *_owned(),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('workspace', sa.String(length=10), nullable=False),
        sa.Column('checkpoints', Json, nullable=False),
        sa.Column('ending', sa.Text(), nullable=False, server_default=''),
    )
    op.create_index('ix_arc_templates_user_updated', 'arc_templates', ['user_id', 'updated_at'])


def downgrade() -> None:
    op.drop_table('arc_templates')
    op.drop_table('chapters')
    op.drop_table('stories')
