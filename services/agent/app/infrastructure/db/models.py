from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from stiamo_common.db import Base, UTCDateTime

Json = JSON().with_variant(JSONB(), "postgresql")


class SettingsModel(Base):
    """One row per person: their AI keys (sealed), the model in use, the decision model and the prompt."""

    __tablename__ = "agent_settings"

    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), default="")
    keys_sealed: Mapped[str | None] = mapped_column(Text)
    selection: Mapped[dict[str, Any] | None] = mapped_column(Json)
    decision: Mapped[dict[str, Any] | None] = mapped_column(Json)
    prompt: Mapped[dict[str, Any] | None] = mapped_column(Json)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)


class ChannelModel(Base):
    """A Telegram bot or a WhatsApp (Twilio) number the person has connected."""

    __tablename__ = "agent_channels"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36))
    kind: Mapped[str] = mapped_column(String(12))
    secrets_sealed: Mapped[str | None] = mapped_column(Text)
    public: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    state: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)

    __table_args__ = (Index("ix_agent_channels_user", "user_id"),)
