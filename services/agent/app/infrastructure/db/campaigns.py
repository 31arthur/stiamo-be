"""Where campaigns and their members are kept."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models import CampaignMemberModel, CampaignModel


def _campaign(r: CampaignModel) -> dict[str, Any]:
    return {"id": r.id, "name": r.name, "message": r.message, "url": r.url, "imageUrl": r.image_url, "preview": dict(r.preview or {}), "trackClicks": bool(r.track_clicks), "status": r.status, "createdAt": r.created_at, "updatedAt": r.updated_at}


def _member(r: CampaignMemberModel) -> dict[str, Any]:
    return {"id": r.id, "campaignId": r.campaign_id, "contactKey": r.contact_key, "contactId": r.contact_id, "name": r.name, "phone": r.phone, "state": r.state, "token": r.token, "sentAt": r.sent_at, "repliedAt": r.replied_at,
            "clickedAt": r.clicked_at, "clicks": r.clicks, "note": r.note}


class SqlCampaignRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def campaigns(self, user_id: str) -> list[dict[str, Any]]:
        rows = await self._s.scalars(select(CampaignModel).where(CampaignModel.user_id == user_id).order_by(CampaignModel.created_at.desc()))
        return [_campaign(r) for r in rows]

    async def campaign(self, user_id: str, campaign_id: str) -> dict[str, Any] | None:
        r = await self._s.get(CampaignModel, campaign_id)
        return _campaign(r) if r and r.user_id == user_id else None

    async def save_campaign(self, user_id: str, c: dict[str, Any], now: datetime) -> None:
        r = await self._s.get(CampaignModel, c["id"])
        fields = dict(name=c["name"][:200], message=c.get("message", ""), url=c.get("url", ""), image_url=c.get("imageUrl", ""), preview=c.get("preview") or {}, track_clicks=c.get("trackClicks", True), status=c.get("status", "active"))
        if r is None:
            self._s.add(CampaignModel(id=c["id"], user_id=user_id, created_at=now, updated_at=now, **fields))
        elif r.user_id == user_id:
            for k, v in fields.items():
                setattr(r, k, v)
            r.updated_at = now
        await self._s.flush()

    async def delete_campaign(self, user_id: str, campaign_id: str) -> None:
        await self._s.execute(delete(CampaignMemberModel).where(CampaignMemberModel.campaign_id == campaign_id, CampaignMemberModel.user_id == user_id))
        await self._s.execute(delete(CampaignModel).where(CampaignModel.id == campaign_id, CampaignModel.user_id == user_id))

    async def members(self, user_id: str, campaign_id: str) -> list[dict[str, Any]]:
        rows = await self._s.scalars(select(CampaignMemberModel).where(CampaignMemberModel.campaign_id == campaign_id, CampaignMemberModel.user_id == user_id).order_by(func.lower(CampaignMemberModel.name)))
        return [_member(r) for r in rows]

    async def counts(self, user_id: str) -> dict[str, dict[str, int]]:
        """Per campaign: how many members in each state, and how many opened the link."""
        out: dict[str, dict[str, int]] = {}
        for r in await self._s.scalars(select(CampaignMemberModel).where(CampaignMemberModel.user_id == user_id)):
            c = out.setdefault(r.campaign_id, {"total": 0, "pending": 0, "sent": 0, "replied": 0, "declined": 0, "skipped": 0, "clicked": 0})
            c["total"] += 1
            c[r.state] = c.get(r.state, 0) + 1
            c["clicked"] += 1 if r.clicks else 0
        return out

    async def add_member(self, user_id: str, m: dict[str, Any]) -> bool:
        """False when this person is already in the campaign."""
        have = await self._s.scalar(select(CampaignMemberModel.id).where(CampaignMemberModel.campaign_id == m["campaignId"], CampaignMemberModel.contact_key == m["contactKey"]))
        if have:
            return False
        self._s.add(CampaignMemberModel(id=m["id"], campaign_id=m["campaignId"], user_id=user_id, contact_key=m["contactKey"], contact_id=m.get("contactId", ""), name=m["name"][:200], phone=m.get("phone", ""), state="pending", token=m["token"]))
        await self._s.flush()
        return True

    async def member(self, user_id: str, member_id: str) -> dict[str, Any] | None:
        r = await self._s.get(CampaignMemberModel, member_id)
        return _member(r) if r and r.user_id == user_id else None

    async def update_member(self, user_id: str, member_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        r = await self._s.get(CampaignMemberModel, member_id)
        if r is None or r.user_id != user_id:
            return None
        for k, v in fields.items():
            setattr(r, k, v)
        await self._s.flush()
        return _member(r)

    async def remove_member(self, user_id: str, member_id: str) -> None:
        await self._s.execute(delete(CampaignMemberModel).where(CampaignMemberModel.id == member_id, CampaignMemberModel.user_id == user_id))

    async def by_token(self, token: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
        """The member whose own link this is, and their campaign (no sign-in: the secret is the proof)."""
        r = await self._s.scalar(select(CampaignMemberModel).where(CampaignMemberModel.token == token))
        if r is None:
            return None
        c = await self._s.get(CampaignModel, r.campaign_id)
        return (_member(r), _campaign(c)) if c else None

    async def record_click(self, member_id: str, now: datetime) -> None:
        r = await self._s.get(CampaignMemberModel, member_id)
        if r is not None:
            r.clicks = (r.clicks or 0) + 1
            r.clicked_at = r.clicked_at or now
            await self._s.flush()
