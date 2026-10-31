from datetime import datetime

from sqlalchemy import Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from stiamo_common.db import Base, UTCDateTime


class AccountModel(Base):
    """A mailbox the person connected to one of their workspaces. Messages are not stored: they are read from the server."""

    __tablename__ = "mail_accounts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36))
    workspace: Mapped[str] = mapped_column(String(16))
    label: Mapped[str] = mapped_column(String(80))
    address: Mapped[str] = mapped_column(String(320))
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column(Integer)
    security: Mapped[str] = mapped_column(String(8))
    username: Mapped[str] = mapped_column(String(320))
    password_sealed: Mapped[str] = mapped_column(Text)
    error: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)

    __table_args__ = (Index("ix_mail_accounts_owner", "owner_id"),)
