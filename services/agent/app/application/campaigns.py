"""Campaigns: one message, link or picture for many contacts.

The owner writes the words once (with {name} for each person's first name) and adds a link; the link's own sharing tags are read so the owner can see how it will look
in WhatsApp. Contacts are added to the campaign. Each one gets a button that opens their own WhatsApp chat with their message already written: the owner presses send,
then marks it sent. Nothing is sent by Stiamo. Their link can be their own tracked link (a short address on this server that shows the same preview and then goes on to
the page), so opening it is counted; replies are marked by the owner.
"""

from __future__ import annotations

import secrets
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from stiamo_common.errors import NotFound, ValidationFailed

from app.application.rapport import best_phone, contact_key
from app.application.services import Who
from app.domain.zones import wa_link
from app.infrastructure.db.campaigns import SqlCampaignRepository

STATES = ("pending", "sent", "replied", "declined", "skipped")
MAX_MEMBERS = 2000


def first_name(name: str) -> str:
    return (name.split() or [""])[0]


def render_message(message: str, url: str, name: str) -> str:
    """The words for one person: {name} becomes their first name, and the link goes where {link} is, or at the end."""
    text = message.replace("{name}", first_name(name)).strip()
    if "{link}" in text:
        return text.replace("{link}", url).strip()
    return f"{text}\n{url}".strip() if url else text


class CampaignService:
    def __init__(self, repo: SqlCampaignRepository, fetch_preview: Callable[[str], Awaitable[dict[str, Any]]], public_url: str = "", now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._repo, self._fetch, self._public, self._now = repo, fetch_preview, public_url.rstrip("/"), now

    async def preview(self, url: str) -> dict[str, Any]:
        url = url.strip()
        if not url:
            raise ValidationFailed("Give a web address.")
        return await self._fetch(url)

    def _link(self, c: dict[str, Any], m: dict[str, Any]) -> str:
        """What goes in the message: their own tracked link when that is on and this server has a public https address, else the page's own address."""
        if c.get("url") and c.get("trackClicks") and self._public.startswith("https://"):
            return f"{self._public}/c/{m['token']}"
        return c.get("url") or c.get("imageUrl") or ""

    def _member_view(self, c: dict[str, Any], m: dict[str, Any]) -> dict[str, Any]:
        text = render_message(c.get("message", ""), self._link(c, m), m["name"])
        iso = lambda d: d.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z") if d else None  # noqa: E731
        return {"id": m["id"], "name": m["name"], "phone": bool(m["phone"]), "state": m["state"], "clicks": m["clicks"], "clickedAt": iso(m["clickedAt"]), "sentAt": iso(m["sentAt"]), "repliedAt": iso(m["repliedAt"]), "note": m["note"],
                "text": text, "whatsapp": wa_link(m["phone"], text) if m["phone"] else ""}

    def _view(self, c: dict[str, Any], counts: dict[str, int] | None = None) -> dict[str, Any]:
        iso = lambda d: d.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z") if d else None  # noqa: E731
        return {"id": c["id"], "name": c["name"], "message": c["message"], "url": c["url"], "imageUrl": c["imageUrl"], "preview": c["preview"], "trackClicks": c["trackClicks"], "status": c["status"], "createdAt": iso(c["createdAt"]),
                "tracking": bool(c["url"] and c["trackClicks"] and self._public.startswith("https://")),
                "counts": counts or {"total": 0, "pending": 0, "sent": 0, "replied": 0, "declined": 0, "skipped": 0, "clicked": 0}}

    async def list(self, who: Who) -> list[dict[str, Any]]:
        counts = await self._repo.counts(who.user_id)
        return [self._view(c, counts.get(c["id"])) for c in await self._repo.campaigns(who.user_id)]

    async def _get(self, who: Who, campaign_id: str) -> dict[str, Any]:
        c = await self._repo.campaign(who.user_id, campaign_id)
        if c is None:
            raise NotFound("That campaign isn't there.")
        return c

    async def detail(self, who: Who, campaign_id: str) -> dict[str, Any]:
        c = await self._get(who, campaign_id)
        members = await self._repo.members(who.user_id, campaign_id)
        counts = (await self._repo.counts(who.user_id)).get(campaign_id)
        return {**self._view(c, counts), "members": [self._member_view(c, m) for m in members]}

    async def save(self, who: Who, campaign_id: str | None, *, name: str, message: str, url: str, image_url: str, track_clicks: bool, status: str | None = None) -> dict[str, Any]:
        name, message, url, image_url = " ".join(name.split())[:200], message.strip()[:2000], url.strip()[:2000], image_url.strip()[:2000]
        if not name:
            raise ValidationFailed("Give the campaign a name.")
        if not (message or url or image_url):
            raise ValidationFailed("A campaign needs words, a link or a picture.")
        for u in (url, image_url):
            if u and not u.lower().startswith(("http://", "https://")):
                raise ValidationFailed("Addresses start with http:// or https://.")
        old = await self._get(who, campaign_id) if campaign_id else None
        preview = (old or {}).get("preview") or {}
        if url and (old is None or old["url"] != url or not preview):
            fetched = await self._fetch(url)
            preview = {k: v for k, v in fetched.items() if k != "error"} if not fetched.get("error") else {"error": fetched["error"]}
        elif not url:
            preview = {}
        c = {"id": campaign_id or str(uuid.uuid4()), "name": name, "message": message, "url": url, "imageUrl": image_url, "preview": preview, "trackClicks": track_clicks, "status": status or (old or {}).get("status", "active")}
        await self._repo.save_campaign(who.user_id, c, self._now())
        return await self.detail(who, c["id"])

    async def delete(self, who: Who, campaign_id: str) -> None:
        await self._get(who, campaign_id)
        await self._repo.delete_campaign(who.user_id, campaign_id)

    async def add_contacts(self, who: Who, campaign_id: str, contacts: list[dict[str, Any]]) -> dict[str, Any]:
        """Puts these contacts in the campaign (a contact already in it is left as it is). Each gets their own secret for their link."""
        await self._get(who, campaign_id)
        have = len(await self._repo.members(who.user_id, campaign_id))
        added = skipped = 0
        for c in contacts:
            if have + added >= MAX_MEMBERS:
                break
            name = " ".join(str(c.get("name") or "").split())
            if not name:
                continue
            ok = await self._repo.add_member(who.user_id, {"id": str(uuid.uuid4()), "campaignId": campaign_id, "contactKey": contact_key(c), "contactId": str(c.get("id") or ""), "name": name, "phone": best_phone(c), "token": secrets.token_urlsafe(12)})
            added, skipped = added + (1 if ok else 0), skipped + (0 if ok else 1)
        return {**await self.detail(who, campaign_id), "added": added, "alreadyIn": skipped}

    async def mark(self, who: Who, member_id: str, state: str, note: str | None = None) -> dict[str, Any]:
        if state not in STATES:
            raise ValidationFailed("The state is pending, sent, replied, declined or skipped.")
        m = await self._repo.member(who.user_id, member_id)
        if m is None:
            raise NotFound("That person isn't in the campaign.")
        now = self._now()
        fields: dict[str, Any] = {"state": state}
        if state == "sent":
            fields["sent_at"] = m["sentAt"] or now
        if state == "replied":
            fields.update(sent_at=m["sentAt"] or now, replied_at=now)
        if state == "pending":
            fields.update(sent_at=None, replied_at=None)
        if note is not None:
            fields["note"] = note.strip()[:1000]
        await self._repo.update_member(who.user_id, member_id, fields)
        return await self.detail(who, m["campaignId"])

    async def remove(self, who: Who, member_id: str) -> dict[str, Any]:
        m = await self._repo.member(who.user_id, member_id)
        if m is None:
            raise NotFound("That person isn't in the campaign.")
        await self._repo.remove_member(who.user_id, member_id)
        return await self.detail(who, m["campaignId"])

    # --- the link a person opens (no sign-in) -------------------------------------------------------------------------------------------

    async def opened(self, token: str, *, crawler: bool) -> dict[str, Any] | None:
        """Their campaign's page and preview for the link with this secret. A person opening it is counted; a crawler (WhatsApp making the preview) is not."""
        found = await self._repo.by_token(token)
        if found is None:
            return None
        m, c = found
        if not crawler:
            await self._repo.record_click(m["id"], self._now())
        return {"url": c["url"], "preview": c["preview"], "name": c["name"]}
