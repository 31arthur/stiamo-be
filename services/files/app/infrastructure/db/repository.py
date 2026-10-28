from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from stiamo_common.models import to_dt, to_iso

from app.application.ports import AttachmentRepository
from app.domain.attachment import Attachment, DocumentLink, EntityRef
from app.infrastructure.db.models import AttachmentModel as M


def _entity(m: M) -> Attachment:
    return Attachment(
        id=m.id, user_id=m.user_id, owner=EntityRef(kind=m.owner_kind, id=m.owner_id), name=m.name, mime=m.mime,
        size=m.size, backend=m.backend, path=m.path, link=DocumentLink.model_validate(m.link) if m.link else None,
        caption=m.caption, created_at=to_iso(m.created_at), updated_at=to_iso(m.updated_at))


class SqlAttachmentRepository(AttachmentRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def list(self, user_id, *, owner, after, limit):
        q = select(M).where(M.user_id == user_id)
        if owner:
            q = q.where(M.owner_kind == owner.kind, M.owner_id == owner.id)
        if after:
            q = q.where(M.id > after)
        return [_entity(m) for m in (await self._s.execute(q.order_by(M.id).limit(limit))).scalars()]

    async def get(self, user_id, id):
        m = await self._s.get(M, (user_id, id))
        return _entity(m) if m else None

    async def save(self, a):
        insert = pg_insert if self._s.bind.dialect.name == "postgresql" else sqlite_insert
        row = {
            "user_id": a.user_id, "id": a.id, "owner_kind": a.owner.kind, "owner_id": a.owner.id, "name": a.name,
            "mime": a.mime, "size": a.size, "backend": a.backend, "path": a.path,
            "link": a.link.model_dump(by_alias=True, exclude_none=True) if a.link else None, "caption": a.caption,
            "created_at": to_dt(a.created_at), "updated_at": to_dt(a.updated_at)}
        stmt = insert(M).values(row)
        keys = {"user_id", "id", "created_at"}
        await self._s.execute(stmt.on_conflict_do_update(
            index_elements=["user_id", "id"], set_={k: stmt.excluded[k] for k in row if k not in keys}))
        self._s.expire_all()
        return a

    async def remove(self, user_id, id):
        await self._s.execute(delete(M).where(M.user_id == user_id, M.id == id))
        self._s.expire_all()

    async def of_owner(self, user_id, owner):
        return await self.list(user_id, owner=owner, after=None, limit=100_000)

    async def transfer(self, user_id, src, dst, now):
        result = await self._s.execute(
            update(M).where(M.user_id == user_id, M.owner_kind == src.kind, M.owner_id == src.id)
            .values(owner_kind=dst.kind, owner_id=dst.id, updated_at=to_dt(now)))
        self._s.expire_all()
        return result.rowcount or 0
