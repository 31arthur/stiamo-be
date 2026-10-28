from __future__ import annotations

import json
from typing import Any

from stiamo_common.errors import NotFound, ValidationFailed

from app.application.ports import Clock, RecordRepository
from app.domain.records import Page, Record, require_collection, require_id


def _prepare(user_id: str, collection: str, record_id: str, document: dict[str, Any], now, max_bytes: int) -> Record:
    """Ownership comes from the token, never from the payload; id comes from the URL or the document."""
    if len(json.dumps(document, separators=(",", ":")).encode()) > max_bytes:
        raise ValidationFailed(f"Document is larger than {max_bytes} bytes.")
    data = {**document, "id": record_id, "userId": user_id}
    return Record(user_id, collection, record_id, data, created_at=now, updated_at=now)


class ListRecords:
    def __init__(self, repo: RecordRepository) -> None:
        self._repo = repo

    async def __call__(self, *, user_id: str, collection: str, after: str | None, limit: int) -> Page:
        return await self._repo.list(user_id, require_collection(collection), after=after, limit=limit)


class GetRecord:
    def __init__(self, repo: RecordRepository) -> None:
        self._repo = repo

    async def __call__(self, *, user_id: str, collection: str, record_id: str) -> Record:
        record = await self._repo.get(user_id, require_collection(collection), record_id)
        if record is None:
            raise NotFound("Record not found.")
        return record


class SaveRecord:
    def __init__(self, repo: RecordRepository, clock: Clock, max_document_bytes: int) -> None:
        self._repo, self._clock, self._max = repo, clock, max_document_bytes

    async def __call__(self, *, user_id: str, collection: str, record_id: str, document: dict[str, Any]) -> Record:
        collection, record_id = require_collection(collection), require_id(record_id)
        if "id" in document and document["id"] != record_id:
            raise ValidationFailed("Document id does not match the URL.")
        record = _prepare(user_id, collection, record_id, document, self._clock.now(), self._max)
        await self._repo.upsert_many([record])
        return record


class SaveManyRecords:
    def __init__(self, repo: RecordRepository, clock: Clock, max_document_bytes: int, max_batch: int) -> None:
        self._repo, self._clock, self._max, self._max_batch = repo, clock, max_document_bytes, max_batch

    async def __call__(self, *, user_id: str, collection: str, documents: list[dict[str, Any]]) -> int:
        collection = require_collection(collection)
        if len(documents) > self._max_batch:
            raise ValidationFailed(f"At most {self._max_batch} documents per batch.")
        now = self._clock.now()
        records: dict[str, Record] = {}
        for doc in documents:
            record_id = require_id(str(doc.get("id", "")))
            records[record_id] = _prepare(user_id, collection, record_id, doc, now, self._max)  # last one wins
        if records:
            await self._repo.upsert_many(list(records.values()))
        return len(records)


class DeleteRecord:
    """Idempotent: deleting something that is already gone succeeds."""

    def __init__(self, repo: RecordRepository) -> None:
        self._repo = repo

    async def __call__(self, *, user_id: str, collection: str, record_id: str) -> None:
        await self._repo.delete(user_id, require_collection(collection), record_id)
