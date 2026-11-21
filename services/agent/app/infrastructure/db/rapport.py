"""Where rapport plans are kept."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models import RapportPlanModel


def _plan(r: RapportPlanModel) -> dict[str, Any]:
    return {"id": r.id, "contactKey": r.contact_key, "contactId": r.contact_id, "name": r.name, "workspaces": list(r.workspaces or []), "phone": r.phone, "zone": r.zone, "about": dict(r.about or {}), "tier": r.tier,
            "status": r.status, "autoSend": bool(r.auto_send), **dict(r.plan or {}), "createdAt": r.created_at, "updatedAt": r.updated_at}


PLAN_KEYS = ("persona", "notes", "suggestions", "missing", "touches", "unanswered", "pausedUntil", "pauseReason", "repliedOn", "source", "lastMessage")


class SqlRapportRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def all(self, user_id: str, statuses: list[str] | None = None) -> list[dict[str, Any]]:
        q = select(RapportPlanModel).where(RapportPlanModel.user_id == user_id)
        if statuses:
            q = q.where(RapportPlanModel.status.in_(statuses))
        return [_plan(r) for r in (await self._s.execute(q.order_by(RapportPlanModel.created_at))).scalars()]

    async def by_key(self, user_id: str, key: str) -> dict[str, Any] | None:
        r = (await self._s.execute(select(RapportPlanModel).where(RapportPlanModel.user_id == user_id, RapportPlanModel.contact_key == key))).scalars().first()
        return _plan(r) if r else None

    async def get(self, user_id: str, plan_id: str) -> dict[str, Any] | None:
        r = await self._s.get(RapportPlanModel, plan_id)
        return _plan(r) if r and r.user_id == user_id else None

    async def by_prefix(self, user_id: str, prefix: str) -> dict[str, Any] | None:
        r = (await self._s.execute(select(RapportPlanModel).where(RapportPlanModel.user_id == user_id, RapportPlanModel.id.like(f"{prefix.replace('%', '')}%")))).scalars().first()
        return _plan(r) if r else None

    async def save(self, user_id: str, p: dict[str, Any], now: datetime) -> None:
        r = await self._s.get(RapportPlanModel, p["id"])
        body = {k: p[k] for k in PLAN_KEYS if k in p}
        if r is None:
            self._s.add(RapportPlanModel(id=p["id"], user_id=user_id, contact_key=p["contactKey"], contact_id=p["contactId"], name=p["name"][:200], workspaces=p.get("workspaces", []), phone=p.get("phone", ""), zone=p.get("zone", ""),
                                         about=p.get("about", {}), tier=p.get("tier", "B"), status=p.get("status", "active"), auto_send=p.get("autoSend", True), plan=body, created_at=now, updated_at=now))
        else:
            r.name, r.workspaces, r.phone, r.zone, r.about, r.tier, r.status, r.auto_send, r.plan, r.updated_at = p["name"][:200], p.get("workspaces", []), p.get("phone", ""), p.get("zone", ""), p.get("about", {}), p.get("tier", "B"), p.get("status", "active"), p.get("autoSend", True), body, now
        await self._s.flush()
