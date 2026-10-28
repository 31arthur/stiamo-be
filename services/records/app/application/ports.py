from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from app.domain.records import Page, Record


class RecordRepository(ABC):
    @abstractmethod
    async def list(self, user_id: str, collection: str, *, after: str | None, limit: int) -> Page: ...

    @abstractmethod
    async def get(self, user_id: str, collection: str, record_id: str) -> Record | None: ...

    @abstractmethod
    async def upsert_many(self, records: list[Record]) -> None:
        """Insert or replace. `created_at` of an existing record is preserved."""

    @abstractmethod
    async def delete(self, user_id: str, collection: str, record_id: str) -> None: ...


class Clock(ABC):
    @abstractmethod
    def now(self) -> datetime: ...
