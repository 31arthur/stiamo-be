"""SQLAlchemy implementations of the repository ports."""

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.ports import ActivityRepository, LinkRepository, Repository, Stores
from app.domain.common import EntityRef, to_dt
from app.domain.directory import Client, Counsel, Project
from app.domain.history import Activity, Link
from app.infrastructure.db import models
from app.infrastructure.db.mappers import ACTIVITY, FOLLOW_UP, LINK, MEETING, TASK, Mapper, flat_mapper

_CHUNK = 200


class SqlRepository(Repository):
    def __init__(self, session: AsyncSession, mapper: Mapper) -> None:
        self._s, self._m = session, mapper
        self._table = mapper.model

    async def list(self, user_id, *, after=None, limit=None, updated_after=None):
        M = self._table
        q = select(M).where(M.user_id == user_id)
        if after is not None:
            q = q.where(M.id > after)
        if updated_after is not None:
            q = q.where(M.updated_at > to_dt(updated_after))
        q = q.order_by(M.id)
        if limit is not None:
            q = q.limit(limit)
        return [self._m.from_row(r) for r in (await self._s.execute(q)).scalars()]

    async def get(self, user_id, id):
        row = await self._s.get(self._table, (user_id, id))
        return self._m.from_row(row) if row else None

    async def save(self, entity):
        await self._upsert([entity])
        return entity

    async def save_many(self, entities):
        for i in range(0, len(entities), _CHUNK):
            await self._upsert(entities[i : i + _CHUNK])

    async def remove(self, user_id, id):
        M = self._table
        await self._s.execute(delete(M).where(M.user_id == user_id, M.id == id))
        self._s.expire_all()

    async def _upsert(self, entities) -> None:
        if not entities:
            return
        insert = pg_insert if self._s.bind.dialect.name == "postgresql" else sqlite_insert
        stmt = insert(self._table).values([self._m.to_row(e) for e in entities])
        keys = {c.key for c in self._table.__table__.primary_key.columns}
        updatable = {c.key: stmt.excluded[c.key] for c in self._table.__table__.columns if c.key not in keys | {"created_at"}}
        await self._s.execute(stmt.on_conflict_do_update(index_elements=sorted(keys), set_=updatable))
        self._s.expire_all()


class SqlLinkRepository(SqlRepository, LinkRepository):
    async def touching(self, user_id: str, ref: EntityRef) -> list[Link]:
        M = models.LinkModel
        q = select(M).where(
            M.user_id == user_id,
            or_(and_(M.a_kind == ref.kind, M.a_id == ref.id), and_(M.b_kind == ref.kind, M.b_id == ref.id)),
        ).order_by(M.id)
        return [self._m.from_row(r) for r in (await self._s.execute(q)).scalars()]


class SqlActivityRepository(SqlRepository, ActivityRepository):
    async def for_refs(self, user_id: str, refs: list[EntityRef], limit: int) -> list[Activity]:
        if not refs:
            return []
        M = models.ActivityModel
        q = (
            select(M)
            .where(M.user_id == user_id, or_(*[and_(M.ref_kind == r.kind, M.ref_id == r.id) for r in refs]))
            .order_by(M.at.desc(), M.id.desc())
            .limit(limit)
        )
        return [self._m.from_row(r) for r in (await self._s.execute(q)).scalars()]


def build_stores(s: AsyncSession) -> Stores:
    return Stores(
        counsel=SqlRepository(s, flat_mapper(models.CounselModel, Counsel)),
        client=SqlRepository(s, flat_mapper(models.ClientModel, Client)),
        project=SqlRepository(s, flat_mapper(models.ProjectModel, Project)),
        task=SqlRepository(s, TASK),
        follow_up=SqlRepository(s, FOLLOW_UP),
        meeting=SqlRepository(s, MEETING),
        link=SqlLinkRepository(s, LINK),
        activity=SqlActivityRepository(s, ACTIVITY),
    )
