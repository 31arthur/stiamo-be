"""Ports: what the use cases need from persistence, file storage and the practice service."""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.domain.attachment import Attachment, EntityRef


class AttachmentRepository(ABC):
    @abstractmethod
    async def list(self, user_id: str, *, owner: EntityRef | None, after: str | None, limit: int) -> list[Attachment]:
        """Ordered by id; `owner` narrows to one record's attachments."""

    @abstractmethod
    async def get(self, user_id: str, id: str) -> Attachment | None: ...

    @abstractmethod
    async def save(self, attachment: Attachment) -> Attachment: ...

    @abstractmethod
    async def remove(self, user_id: str, id: str) -> None: ...

    @abstractmethod
    async def of_owner(self, user_id: str, owner: EntityRef) -> list[Attachment]: ...

    @abstractmethod
    async def transfer(self, user_id: str, src: EntityRef, dst: EntityRef, now: str) -> int:
        """Points every attachment of `src` at `dst`; returns how many moved."""


class FileStorage(ABC):
    """Where the bytes live. Paths are relative and chosen by the application."""

    @abstractmethod
    async def put(self, path: str, data: bytes) -> None: ...

    @abstractmethod
    async def read(self, path: str) -> bytes: ...

    @abstractmethod
    async def delete(self, path: str) -> None:
        """Idempotent."""


class HistoryNotifier(ABC):
    """Tells the practice service to write a line in a record's history. Best effort: never blocks the change."""

    @abstractmethod
    async def record(self, token: str, ref: EntityRef, title: str, action: str, summary: str) -> None: ...
