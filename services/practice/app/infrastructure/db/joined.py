"""Repositories for aggregates that keep a list of related ids in a join table (plan coverage, goal plans)."""

from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, select

from app.infrastructure.db.sql_repository import SqlRepository


@dataclass(frozen=True)
class Join:
    """`attr` on the entity is the ordered list of `child_col` values in `model`, keyed by `parent_col`."""

    model: type
    parent_col: str
    child_col: str
    attr: str
    #: What the entity holds when the list is empty (None for optional lists, [] for required ones).
    empty: Any = None


class SqlJoinedRepository(SqlRepository):
    def __init__(self, session, mapper, joins: list[Join]) -> None:
        super().__init__(session, mapper)
        self._joins = joins

    async def list(self, user_id, **kw):
        return await self._hydrate(user_id, await super().list(user_id, **kw))

    async def get(self, user_id, id):
        found = await super().get(user_id, id)
        return (await self._hydrate(user_id, [found]))[0] if found else None

    async def _upsert(self, entities) -> None:
        await super()._upsert(entities)
        for join in self._joins:
            J = join.model
            parent, child = getattr(J, join.parent_col), join.child_col
            rows: list[dict[str, Any]] = []
            for e in entities:
                await self._s.execute(delete(J).where(J.user_id == e.user_id, parent == e.id))
                for position, value in enumerate(dict.fromkeys(getattr(e, join.attr) or [])):
                    rows.append({"user_id": e.user_id, join.parent_col: e.id, child: value, "position": position})
            if rows:
                await self._s.execute(J.__table__.insert(), rows)
        self._s.expire_all()

    async def _hydrate(self, user_id: str, entities: list) -> list:
        if not entities:
            return entities
        ids = [e.id for e in entities]
        extra: dict[str, dict[str, list[str]]] = {j.attr: {} for j in self._joins}
        for join in self._joins:
            J = join.model
            q = (
                select(getattr(J, join.parent_col), getattr(J, join.child_col))
                .where(J.user_id == user_id, getattr(J, join.parent_col).in_(ids))
                .order_by(J.position)
            )
            for parent_id, child_id in (await self._s.execute(q)).all():
                extra[join.attr].setdefault(parent_id, []).append(child_id)
        return [
            e.model_copy(update={j.attr: extra[j.attr].get(e.id) or j.empty for j in self._joins}) for e in entities
        ]
