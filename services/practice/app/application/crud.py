"""List/get/create/update/remove for one aggregate, with system fields, validation and history handled.

Every change to a record is written to its history; deleting a record also removes the links that touched it.
"""

from collections.abc import Callable
from typing import Any, Generic, TypeVar

from stiamo_common.errors import Conflict
from stiamo_common.errors import NotFound as NotFoundError

from app.application.activity_log import ActivityLog
from app.application.ports import Env, Repository
from app.domain.common import DomainModel, Entity, EntityRef

T = TypeVar("T", bound=Entity)
Normalizer = Callable[[Any], Any]


class Crud(Generic[T]):
    def __init__(
        self,
        env: Env,
        kind: str,
        model: type[T],
        repo: Repository[T],
        *,
        normalize: Normalizer | None = None,
        log: ActivityLog | None = None,
    ) -> None:
        self.env, self.kind, self.model, self.repo = env, kind, model, repo
        self._normalize = normalize or (lambda e: e)
        self.log = log or ActivityLog(env)

    # --- reads -------------------------------------------------------------------------------------

    async def list(self, user_id: str, *, after: str | None = None, limit: int | None = None,
                   updated_after: str | None = None) -> list[T]:
        return await self.repo.list(user_id, after=after, limit=limit, updated_after=updated_after)

    async def get(self, user_id: str, id: str) -> T | None:
        return await self.repo.get(user_id, id)

    async def require(self, user_id: str, id: str, label: str | None = None) -> T:
        found = await self.repo.get(user_id, id)
        if found is None:
            raise NotFoundError(f"{label or self.model.__name__} not found.")
        return found

    # --- writes ------------------------------------------------------------------------------------

    async def create(self, user_id: str, data: dict[str, Any]) -> T:
        explicit = data.get("id")
        if explicit and await self.repo.get(user_id, explicit):
            raise Conflict(f"A {self.model.__name__.lower()} with this id already exists.")
        now = self.env.now_iso()
        entity = self.model.model_validate(
            {**data, "id": explicit or self.env.ids.next(), "user_id": user_id, "created_at": now, "updated_at": now}
        )
        saved = await self.repo.save(self._normalize(entity))
        await self.log.created(user_id, self.kind, saved)
        return saved

    async def update(self, entity: T) -> T:
        before = await self.repo.get(entity.user_id, entity.id)
        revised = self.model.model_validate({**entity.model_dump(), "updated_at": self.env.now_iso()})
        saved = await self.repo.save(self._normalize(revised))
        if before is not None:
            await self.log.changed(entity.user_id, self.kind, before, saved)
        return saved

    async def save(self, user_id: str, id: str | None, draft: DomainModel) -> T:
        """Replace the record with the draft (PUT), creating it when it doesn't exist yet."""
        data = draft.model_dump(exclude={"id"})
        existing = await self.repo.get(user_id, id) if id else None
        if existing is None:
            return await self.create(user_id, {**data, "id": id})
        return await self.update(self.model.model_validate({
            **data, "id": existing.id, "user_id": user_id,
            "created_at": existing.created_at, "updated_at": existing.updated_at,
        }))

    async def remove(self, user_id: str, id: str) -> None:
        before = await self.repo.get(user_id, id)
        await self.repo.remove(user_id, id)
        for link in await self.env.stores.link.touching(user_id, EntityRef(kind=self.kind, id=id)):
            await self.env.stores.link.remove(user_id, link.id)
        if before is not None:
            await self.log.deleted(user_id, self.kind, before)
