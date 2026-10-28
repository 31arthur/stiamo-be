from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from stiamo_common.db import Base, UTCDateTime

JsonDocument = JSON().with_variant(JSONB(), "postgresql")


class RecordModel(Base):
    __tablename__ = "records"

    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    collection: Mapped[str] = mapped_column(String(40), primary_key=True)
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    data: Mapped[dict[str, Any]] = mapped_column(JsonDocument)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)

    __table_args__ = (Index("ix_records_user_collection_updated", "user_id", "collection", "updated_at"),)
