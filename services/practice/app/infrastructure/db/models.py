"""Relational schema.

Every table is owned by one user and keyed `(user_id, id)`, so two users can never touch each other's rows.
Dates are kept as `yyyy-MM-dd` text (they compare and sort correctly and have no time zone to get wrong);
instants are timezone-aware timestamps. Small nested lists that are never queried into live in JSON columns.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKeyConstraint, Index, Integer, String, Text
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


class OutcomeCols:
    """How the record turned out; `open` until someone records a result."""

    outcome_status: Mapped[str] = mapped_column(String(10), default="open")
    outcome_comment: Mapped[str | None] = mapped_column(Text)
    outcome_next_action: Mapped[str | None] = mapped_column(Text)
    outcome_recorded_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


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


class TaskModel(Owned, OutcomeCols, Base):
    __tablename__ = "tasks"
    title: Mapped[str] = mapped_column(String(500))
    notes: Mapped[str] = mapped_column(Text, default="")
    workspace: Mapped[str] = mapped_column(String(10))
    priority: Mapped[str] = mapped_column(String(8))
    due_date: Mapped[str | None] = mapped_column(String(10))
    slot_date: Mapped[str | None] = mapped_column(String(10))
    slot_start: Mapped[int | None] = mapped_column(Integer)
    slot_duration: Mapped[int | None] = mapped_column(Integer)
    estimate: Mapped[int] = mapped_column(Integer)
    plan_id: Mapped[str | None] = mapped_column(String(64))
    step_order: Mapped[int | None] = mapped_column(Integer)
    related_kind: Mapped[str | None] = mapped_column(String(12))
    related_id: Mapped[str | None] = mapped_column(String(64))
    __table_args__ = (
        Index("ix_tasks_due", "user_id", "due_date"),
        Index("ix_tasks_slot", "user_id", "slot_date"),
        Index("ix_tasks_plan", "user_id", "plan_id"),
        Index("ix_tasks_related", "user_id", "related_kind", "related_id"),
        Index("ix_tasks_open", "user_id", "outcome_status"),
        sync_index("tasks"),
    )


class FollowUpModel(Owned, OutcomeCols, Base):
    __tablename__ = "follow_ups"
    title: Mapped[str] = mapped_column(String(500))
    channel: Mapped[str] = mapped_column(String(10))
    workspace: Mapped[str] = mapped_column(String(10))
    contact: Mapped[str | None] = mapped_column(String(300))
    due_date: Mapped[str | None] = mapped_column(String(10))
    due_time: Mapped[int | None] = mapped_column(Integer)
    duration: Mapped[int] = mapped_column(Integer)
    subject_kind: Mapped[str | None] = mapped_column(String(12))
    subject_id: Mapped[str | None] = mapped_column(String(64))
    trigger: Mapped[str | None] = mapped_column(String(12))
    notes: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[list[dict[str, Any]] | None] = mapped_column(Json)
    plan_id: Mapped[str | None] = mapped_column(String(64))
    step_order: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (
        Index("ix_follow_ups_due", "user_id", "due_date"),
        Index("ix_follow_ups_subject", "user_id", "subject_kind", "subject_id"),
        Index("ix_follow_ups_plan", "user_id", "plan_id"),
        Index("ix_follow_ups_open", "user_id", "outcome_status"),
        sync_index("follow_ups"),
    )


class MeetingModel(Owned, OutcomeCols, Base):
    __tablename__ = "meetings"
    kind: Mapped[str] = mapped_column(String(12))
    title: Mapped[str] = mapped_column(String(500))
    workspace: Mapped[str] = mapped_column(String(10))
    slot_date: Mapped[str] = mapped_column(String(10))
    slot_start: Mapped[int] = mapped_column(Integer)
    slot_duration: Mapped[int] = mapped_column(Integer)
    mode: Mapped[str] = mapped_column(String(10))
    location: Mapped[str | None] = mapped_column(String(500))
    link: Mapped[str | None] = mapped_column(String(1000))
    counterpart: Mapped[str | None] = mapped_column(String(300))
    contact: Mapped[str | None] = mapped_column(String(300))
    attendees: Mapped[list[str]] = mapped_column(Json)
    purpose: Mapped[str | None] = mapped_column(Text)
    agenda: Mapped[list[dict[str, Any]]] = mapped_column(Json)
    confirmations: Mapped[list[str]] = mapped_column(Json)
    prep_plan_id: Mapped[str | None] = mapped_column(String(64))
    plan_id: Mapped[str | None] = mapped_column(String(64))
    step_order: Mapped[int | None] = mapped_column(Integer)
    related_kind: Mapped[str | None] = mapped_column(String(12))
    related_id: Mapped[str | None] = mapped_column(String(64))
    minutes: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        Index("ix_meetings_slot", "user_id", "slot_date"),
        Index("ix_meetings_plan", "user_id", "plan_id"),
        Index("ix_meetings_related", "user_id", "related_kind", "related_id"),
        sync_index("meetings"),
    )
