"""Where action items, their history, change sets and the queue are kept."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models import ActionChangesetModel, ActionEventModel, ActionItemModel, ActionQueueModel


def _item(r: ActionItemModel) -> dict[str, Any]:
    return {"id": r.id, "kind": r.kind, "title": r.title, "workspace": r.workspace, "day": r.day, "start": r.start, "duration": r.duration, "due": r.due, "status": r.status, "priority": r.priority,
            "source": r.source, "links": dict(r.links or {}), "notes": r.notes, "case_ref": (r.links or {}).get("case"), "contact": (r.links or {}).get("contact")}


def _event(r: ActionEventModel) -> dict[str, Any]:
    return {"id": r.id, "ref": r.item_ref, "event": r.event, "actor": r.actor, "title": r.title, "before": r.before, "after": r.after, "reason": r.reason, "changesetId": r.changeset_id,
            "at": r.created_at.isoformat(timespec="seconds").replace("+00:00", "Z") if r.created_at else None}


def _changeset(r: ActionChangesetModel) -> dict[str, Any]:
    return {"id": r.id, "title": r.title, "status": r.status, "ops": list(r.ops or []), "summary": dict(r.summary or {}), "actor": r.actor, "channelId": r.channel_id, "createdAt": r.created_at, "decidedAt": r.decided_at}


def _queue(r: ActionQueueModel) -> dict[str, Any]:
    return {"id": r.id, "day": r.day, "status": r.status, "entries": list(r.entries or []), "cursor": r.cursor, "shown": dict(r.shown or {}), "createdAt": r.created_at, "updatedAt": r.updated_at}


class SqlActionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    # --- the items Chittu keeps himself -----------------------------------------------------------------------------------------------------

    async def items(self, user_id: str, statuses: list[str] | None = None) -> list[dict[str, Any]]:
        q = select(ActionItemModel).where(ActionItemModel.user_id == user_id)
        if statuses:
            q = q.where(ActionItemModel.status.in_(statuses))
        return [_item(r) for r in (await self._s.execute(q.order_by(ActionItemModel.created_at))).scalars()]

    async def item(self, user_id: str, item_id: str) -> dict[str, Any] | None:
        r = await self._s.get(ActionItemModel, item_id)
        return _item(r) if r and r.user_id == user_id else None

    async def save_item(self, user_id: str, row: dict[str, Any], now: datetime) -> None:
        r = await self._s.get(ActionItemModel, row["id"])
        links = dict(row.get("links") or {})
        for k in ("case_ref", "contact"):
            if row.get(k):
                links[k.replace("_ref", "")] = row[k]
        fields = {k: row.get(k) for k in ("kind", "title", "workspace", "day", "start", "duration", "due", "status", "priority", "source", "notes")}
        if r is None:
            self._s.add(ActionItemModel(id=row["id"], user_id=user_id, links=links, created_at=now, updated_at=now, **{**{"workspace": "personal", "status": "open", "priority": "normal", "source": "chittu"}, **{k: v for k, v in fields.items() if v is not None}}))
        else:
            for k, v in fields.items():
                if k in row:
                    setattr(r, k, v)
            r.links, r.updated_at = links, now
        await self._s.flush()

    # --- history ------------------------------------------------------------------------------------------------------------------------------

    async def add_event(self, user_id: str, event_id: str, ref: str, event: str, actor: str, title: str, before: Any, after: Any, reason: str, changeset_id: str | None, run_id: str | None, now: datetime) -> None:
        self._s.add(ActionEventModel(id=event_id, user_id=user_id, item_ref=ref, event=event, actor=actor, title=title[:300], before=before, after=after, reason=reason[:400], changeset_id=changeset_id, run_id=run_id, created_at=now))
        await self._s.flush()

    async def history(self, user_id: str, ref: str | None = None, limit: int = 50, since: datetime | None = None) -> list[dict[str, Any]]:
        q = select(ActionEventModel).where(ActionEventModel.user_id == user_id)
        if ref:
            q = q.where(ActionEventModel.item_ref == ref)
        if since:
            q = q.where(ActionEventModel.created_at >= since)
        return [_event(r) for r in (await self._s.execute(q.order_by(desc(ActionEventModel.created_at)).limit(limit))).scalars()]

    # --- change sets --------------------------------------------------------------------------------------------------------------------------

    async def add_changeset(self, user_id: str, row: dict[str, Any]) -> None:
        self._s.add(ActionChangesetModel(id=row["id"], user_id=user_id, title=row["title"][:300], status=row["status"], ops=row["ops"], summary=row.get("summary") or {}, actor=row.get("actor", "chittu"), channel_id=row.get("channelId"), created_at=row["createdAt"]))
        await self._s.flush()

    async def changeset(self, user_id: str, changeset_id: str) -> dict[str, Any] | None:
        r = await self._s.get(ActionChangesetModel, changeset_id)
        return _changeset(r) if r and r.user_id == user_id else None

    async def save_changeset(self, row: dict[str, Any]) -> None:
        r = await self._s.get(ActionChangesetModel, row["id"])
        if r is not None:
            r.status, r.ops, r.summary, r.decided_at = row["status"], row["ops"], row.get("summary") or {}, row.get("decidedAt")
            await self._s.flush()

    async def pending_changesets(self, user_id: str, channel_id: str | None = None) -> list[dict[str, Any]]:
        q = select(ActionChangesetModel).where(ActionChangesetModel.user_id == user_id, ActionChangesetModel.status == "pending")
        if channel_id:
            q = q.where(ActionChangesetModel.channel_id == channel_id)
        return [_changeset(r) for r in (await self._s.execute(q.order_by(desc(ActionChangesetModel.created_at)))).scalars()]

    async def last_applied(self, user_id: str) -> dict[str, Any] | None:
        r = (await self._s.execute(select(ActionChangesetModel).where(ActionChangesetModel.user_id == user_id, ActionChangesetModel.status == "applied").order_by(desc(ActionChangesetModel.decided_at)).limit(1))).scalars().first()
        return _changeset(r) if r else None

    # --- the queue ---------------------------------------------------------------------------------------------------------------------------

    async def queue(self, user_id: str, queue_id: str | None = None, statuses: list[str] | None = None) -> dict[str, Any] | None:
        if queue_id:
            r = await self._s.get(ActionQueueModel, queue_id)
            return _queue(r) if r and r.user_id == user_id else None
        q = select(ActionQueueModel).where(ActionQueueModel.user_id == user_id)
        if statuses:
            q = q.where(ActionQueueModel.status.in_(statuses))
        r = (await self._s.execute(q.order_by(desc(ActionQueueModel.created_at)).limit(1))).scalars().first()
        return _queue(r) if r else None

    async def save_queue(self, user_id: str, row: dict[str, Any]) -> None:
        r = await self._s.get(ActionQueueModel, row["id"])
        if r is None:
            self._s.add(ActionQueueModel(id=row["id"], user_id=user_id, day=row["day"], status=row["status"], entries=row["entries"], cursor=row["cursor"], shown=row.get("shown") or {}, created_at=row["createdAt"], updated_at=row["updatedAt"]))
        else:
            r.status, r.entries, r.cursor, r.shown, r.updated_at = row["status"], row["entries"], row["cursor"], row.get("shown") or {}, row["updatedAt"]
        await self._s.flush()
