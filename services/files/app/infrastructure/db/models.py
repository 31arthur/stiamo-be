from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from stiamo_common.db import Base, UTCDateTime

Json = JSON().with_variant(JSONB(), "postgresql")


class AttachmentModel(Base):
    __tablename__ = "attachments"

    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_kind: Mapped[str] = mapped_column(String(12))
    owner_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(500))
    mime: Mapped[str] = mapped_column(String(200))
    size: Mapped[int] = mapped_column(BigInteger)
    backend: Mapped[str] = mapped_column(String(8))
    path: Mapped[str | None] = mapped_column(String(200))
    link: Mapped[dict[str, Any] | None] = mapped_column(Json)
    caption: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)

    __table_args__ = (
        Index("ix_attachments_owner", "user_id", "owner_kind", "owner_id"),
        Index("ix_attachments_user_updated", "user_id", "updated_at"),
    )
