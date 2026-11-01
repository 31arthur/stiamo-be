from datetime import datetime

from sqlalchemy import Boolean, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from stiamo_common.db import Base, UTCDateTime


class AccountModel(Base):
    """A Google account the person connected to one of their workspaces. Only its refresh token is kept, sealed."""

    __tablename__ = "calendar_accounts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36))
    workspace: Mapped[str] = mapped_column(String(16))
    email: Mapped[str] = mapped_column(String(320))
    refresh_sealed: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(10), default="ok")
    error: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    last_sync_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    __table_args__ = (Index("ix_calendar_accounts_user", "user_id"), UniqueConstraint("user_id", "workspace", "email", name="uq_calendar_account"))


class SourceModel(Base):
    """One calendar inside a Google account."""

    __tablename__ = "calendar_sources"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(36))
    user_id: Mapped[str] = mapped_column(String(36))
    google_id: Mapped[str] = mapped_column(String(300))
    name: Mapped[str] = mapped_column(String(300))
    color: Mapped[str] = mapped_column(String(9))
    role: Mapped[str] = mapped_column(String(20))
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    export: Mapped[bool] = mapped_column(Boolean, default=False)
    sync_token: Mapped[str | None] = mapped_column(Text)
    synced_through: Mapped[datetime | None] = mapped_column(UTCDateTime)

    __table_args__ = (Index("ix_calendar_sources_user", "user_id"), UniqueConstraint("account_id", "google_id", name="uq_calendar_source"))


class EventModel(Base):
    """What Google has on a calendar, kept so the combined calendar opens at once. Google stays the truth: it is refreshed from there."""

    __tablename__ = "calendar_events"

    id: Mapped[str] = mapped_column(String(200), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36))
    source_id: Mapped[str] = mapped_column(String(36))
    google_id: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    location: Mapped[str] = mapped_column(String(300), default="")
    all_day: Mapped[bool] = mapped_column(Boolean, default=False)
    start: Mapped[datetime] = mapped_column(UTCDateTime)
    end: Mapped[datetime] = mapped_column(UTCDateTime)
    link: Mapped[str] = mapped_column(Text, default="")
    meet: Mapped[str] = mapped_column(Text, default="")
    attendees: Mapped[int] = mapped_column(Integer, default=0)
    updated: Mapped[str] = mapped_column(String(40), default="")

    __table_args__ = (Index("ix_calendar_events_range", "user_id", "start"), Index("ix_calendar_events_source", "source_id"))


class LinkModel(Base):
    """A meeting or hearing Stiamo sent to Google, and the event it became there: what lets a change or a delete follow it."""

    __tablename__ = "calendar_links"

    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    ref: Mapped[str] = mapped_column(String(80), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(36))
    google_id: Mapped[str] = mapped_column(String(200))
    digest: Mapped[str] = mapped_column(String(40))


class PrefsModel(Base):
    __tablename__ = "calendar_prefs"

    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    timezone: Mapped[str] = mapped_column(String(64))
