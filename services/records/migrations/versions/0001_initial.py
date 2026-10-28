"""records table

Revision ID: 0001
Revises:
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "records",
        sa.Column("user_id", sa.String(36), primary_key=True),
        sa.Column("collection", sa.String(40), primary_key=True),
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("data", sa.JSON().with_variant(JSONB(), "postgresql"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_records_user_collection_updated", "records", ["user_id", "collection", "updated_at"])


def downgrade() -> None:
    op.drop_table("records")
