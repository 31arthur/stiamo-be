import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.ports import Delivery, DeliveryLog
from app.infrastructure.db.models import DeliveryModel

#: How long a record is kept.
KEEP = timedelta(days=30)


class SqlDeliveryLog(DeliveryLog):
    """Its own sessions, so a record is kept even when the request that sent the message fails."""

    def __init__(self, session_factory: Callable[[], AsyncSession]) -> None:
        self._factory = session_factory

    async def add(self, user_id: str, kind: str, channel: str, target: str, ok: bool, error: str = "", summary: str = "", workspace: str | None = None) -> None:
        async with self._factory() as s:
            now = datetime.now(UTC)
            s.add(DeliveryModel(id=str(uuid.uuid4()), user_id=user_id, kind=kind[:12], channel=channel[:12], target=target[:160], ok=ok, error=error[:600], summary=summary[:300], workspace=workspace, created_at=now))
            await s.execute(delete(DeliveryModel).where(DeliveryModel.user_id == user_id, DeliveryModel.created_at < now - KEEP))
            await s.commit()

    async def recent(self, user_id: str, limit: int = 50, failed_only: bool = False) -> list[Delivery]:
        async with self._factory() as s:
            q = select(DeliveryModel).where(DeliveryModel.user_id == user_id)
            if failed_only:
                q = q.where(DeliveryModel.ok.is_(False))
            rows = (await s.execute(q.order_by(DeliveryModel.created_at.desc()).limit(limit))).scalars()
            return [Delivery(r.id, r.user_id, r.kind, r.channel, r.target, r.ok, r.error, r.summary, r.workspace, r.created_at) for r in rows]

    async def counts(self, user_id: str, since: datetime) -> dict[str, int]:
        async with self._factory() as s:
            rows = (await s.execute(select(DeliveryModel.ok, func.count()).where(DeliveryModel.user_id == user_id, DeliveryModel.created_at >= since).group_by(DeliveryModel.ok))).all()
        got = {bool(ok): n for ok, n in rows}
        return {"sent": got.get(True, 0), "failed": got.get(False, 0)}
