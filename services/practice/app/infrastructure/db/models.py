"""Relational schema.

Every table is owned by one user and keyed `(user_id, id)`, so two users can never touch each other's rows.
Dates are kept as `yyyy-MM-dd` text (they compare and sort correctly and have no time zone to get wrong);
instants are timezone-aware timestamps. Small nested lists that are never queried into live in JSON columns.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKeyConstraint, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from stiamo_common.db import Base, UTCDateTime

Json = JSON().with_variant(JSONB(), "postgresql")


class Owned:
    """Columns every table shares."""

    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)


def sync_index(table: str) -> Index:
    """Lets a client ask for "everything changed since…" cheaply."""
    return Index(f"ix_{table}_user_updated", "user_id", "updated_at")


class CounselModel(Owned, Base):
    __tablename__ = "counsel"
    kind: Mapped[str] = mapped_column(String(10))
    name: Mapped[str] = mapped_column(String(300))
    firm: Mapped[str | None] = mapped_column(String(300))
    phone: Mapped[str | None] = mapped_column(String(60))
    email: Mapped[str | None] = mapped_column(String(320))
    city: Mapped[str | None] = mapped_column(String(120))
    courts: Mapped[str | None] = mapped_column(Text)
    expertise: Mapped[list[str]] = mapped_column(Json)
    pro_bono: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(10))
    notes: Mapped[str] = mapped_column(Text, default="")
    __table_args__ = (sync_index("counsel"),)


class ClientModel(Owned, Base):
    __tablename__ = "clients"
    kind: Mapped[str] = mapped_column(String(10))
    name: Mapped[str] = mapped_column(String(300))
    contact_name: Mapped[str | None] = mapped_column(String(300))
    phone: Mapped[str | None] = mapped_column(String(60))
    email: Mapped[str | None] = mapped_column(String(320))
    website: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(10))
    notes: Mapped[str] = mapped_column(Text, default="")
    __table_args__ = (sync_index("clients"),)


class ProjectModel(Owned, Base):
    __tablename__ = "projects"
    title: Mapped[str] = mapped_column(String(300))
    client_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(12))
    description: Mapped[str] = mapped_column(Text, default="")
    start_date: Mapped[str | None] = mapped_column(String(10))
    due_date: Mapped[str | None] = mapped_column(String(10))
    __table_args__ = (
        ForeignKeyConstraint(["user_id", "client_id"], ["clients.user_id", "clients.id"], ondelete="CASCADE"),
        sync_index("projects"),
    )


class LinkModel(Owned, Base):
    __tablename__ = "links"
    a_kind: Mapped[str] = mapped_column(String(12))
    a_id: Mapped[str] = mapped_column(String(64))
    b_kind: Mapped[str] = mapped_column(String(12))
    b_id: Mapped[str] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        Index("ix_links_a", "user_id", "a_kind", "a_id"),
        Index("ix_links_b", "user_id", "b_kind", "b_id"),
        sync_index("links"),
    )


class ActivityModel(Owned, Base):
    __tablename__ = "activity"
    at: Mapped[datetime] = mapped_column(UTCDateTime)
    ref_kind: Mapped[str] = mapped_column(String(12))
    ref_id: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(500))
    action: Mapped[str] = mapped_column(String(12))
    summary: Mapped[str] = mapped_column(Text)
    changes: Mapped[list[dict[str, Any]] | None] = mapped_column(Json)
    related_kind: Mapped[str | None] = mapped_column(String(12))
    related_id: Mapped[str | None] = mapped_column(String(64))
    __table_args__ = (Index("ix_activity_ref", "user_id", "ref_kind", "ref_id", "at"), sync_index("activity"))
