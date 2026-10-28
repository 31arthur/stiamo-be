from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.ports import RecordRepository
from app.domain.records import Page, Record
from app.infrastructure.db.models import RecordModel


def _entity(m: RecordModel) -> Record:
    return Record(m.user_id, m.collection, m.id, m.data, m.created_at, m.updated_at)


class SqlRecordRepository(RecordRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def list(self, user_id: str, collection: str, *, after: str | None, limit: int) -> Page:
        q = select(RecordModel).where(RecordModel.user_id == user_id, RecordModel.collection == collection)
        if after is not None:
            q = q.where(RecordModel.id > after)
        rows = (await self._s.execute(q.order_by(RecordModel.id).limit(limit + 1))).scalars().all()
        items = [_entity(m) for m in rows[:limit]]
        return Page(items, items[-1].id if len(rows) > limit else None)

    async def get(self, user_id: str, collection: str, record_id: str) -> Record | None:
        m = await self._s.get(RecordModel, (user_id, collection, record_id))
        return _entity(m) if m else None

    async def upsert_many(self, records: list[Record]) -> None:
        if not records:
            return
        insert = pg_insert if self._s.bind.dialect.name == "postgresql" else sqlite_insert
        stmt = insert(RecordModel).values([
            {"user_id": r.user_id, "collection": r.collection, "id": r.id, "data": r.data,
             "created_at": r.created_at, "updated_at": r.updated_at}
            for r in records
        ])
        stmt = stmt.on_conflict_do_update(
            index_elements=["user_id", "collection", "id"],
            set_={"data": stmt.excluded.data, "updated_at": stmt.excluded.updated_at},
        )
        await self._s.execute(stmt)

    async def delete(self, user_id: str, collection: str, record_id: str) -> None:
        await self._s.execute(delete(RecordModel).where(
            RecordModel.user_id == user_id, RecordModel.collection == collection, RecordModel.id == record_id))
