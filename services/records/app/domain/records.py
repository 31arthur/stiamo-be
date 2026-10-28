"""Domain: a record is a JSON document owned by one user inside one named collection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from stiamo_common.errors import NotFound, ValidationFailed

# Mirrors the frontend's StoreKind tables.
COLLECTIONS = frozenset({
    "tasks", "meetings", "follow_ups", "cases", "hearings", "plans", "goals", "links", "activity",
    "attachments", "counsel", "clients", "projects",
})

MAX_ID_LENGTH = 128


def require_collection(name: str) -> str:
    if name not in COLLECTIONS:
        raise NotFound(f"Unknown collection '{name}'.")
    return name


def require_id(record_id: str) -> str:
    if not record_id or len(record_id) > MAX_ID_LENGTH or record_id != record_id.strip():
        raise ValidationFailed("Record id must be 1-128 characters with no surrounding spaces.")
    return record_id


@dataclass
class Record:
    user_id: str
    collection: str
    id: str
    data: dict[str, Any]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class Page:
    items: list[Record]
    next_cursor: str | None
