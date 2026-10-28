"""Ports: what the use cases need from persistence and the outside world."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Generic, TypeVar

from app.domain.common import Entity, EntityRef, to_iso
from app.domain.directory import Client, Counsel, Project
from app.domain.history import Activity, Link

T = TypeVar("T", bound=Entity)


class Repository(ABC, Generic[T]):
    """Persistence for one aggregate. Every call is scoped to one user."""

    @abstractmethod
    async def list(
        self, user_id: str, *, after: str | None = None, limit: int | None = None, updated_after: str | None = None
    ) -> list[T]:
        """Ordered by id. `after` is an id cursor; `updated_after` an ISO instant for incremental sync."""

    @abstractmethod
    async def get(self, user_id: str, id: str) -> T | None: ...

    @abstractmethod
    async def save(self, entity: T) -> T:
        """Insert or replace."""

    @abstractmethod
    async def save_many(self, entities: list[T]) -> None: ...

    @abstractmethod
    async def remove(self, user_id: str, id: str) -> None:
        """Idempotent."""


class LinkRepository(Repository[Link], ABC):
    @abstractmethod
    async def touching(self, user_id: str, ref: EntityRef) -> list[Link]: ...


class ActivityRepository(Repository[Activity], ABC):
    @abstractmethod
    async def for_refs(self, user_id: str, refs: list[EntityRef], limit: int) -> list[Activity]:
        """Newest first, across all the given records."""


class Clock(ABC):
    @abstractmethod
    def now(self) -> datetime: ...


class IdGenerator(ABC):
    @abstractmethod
    def next(self) -> str: ...


@dataclass
class Stores:
    """Every repository the use cases can reach, all bound to one unit of work."""

    counsel: Repository[Counsel]
    client: Repository[Client]
    project: Repository[Project]
    link: LinkRepository
    activity: ActivityRepository


@dataclass
class Env:
    stores: Stores
    clock: Clock
    ids: IdGenerator

    def now_iso(self) -> str:
        return to_iso(self.clock.now())
