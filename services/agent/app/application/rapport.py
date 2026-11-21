"""Rapport plans: keeping business contacts warm, within limits (see domain/rapport.py for the rules).

A new contact in Google contacts (any workspace) gets a plan on its own: a tier from its profile (B if not set), a zone from where it is (or its phone's country),
and a list of touches. The tags are the owner's, added on the web; the plan reads them when each message is written. When a touch falls due the message is written
by the model at that moment, from the tags and the touch's purpose, and sent on WhatsApp (auto-send). If WhatsApp will not take it (a business-initiated message
needs an approved template, or the number has no open chat), or there is no number or no WhatsApp channel, it becomes an action item and a Telegram message with an
"Open WhatsApp" button whose text is already written. Everything is in the history, and the owner can pause, exclude, or say they replied, at any time.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from stiamo_common.errors import NotFound, ValidationFailed

from app.application.actions import ActionService
from app.application.services import Who
from app.domain.llm import Message
from app.domain.rapport import DAILY_CAP, DEFAULT_TIER, PURPOSES, after_reply, after_sent, after_skip, due_now, may_send, schedule, slot_for, summary
from app.domain.zones import HOME, label, phone_e164, wa_link, zone_from_phone
from app.infrastructure.db.rapport import SqlRapportRepository

log = logging.getLogger(__name__)

Send = Callable[..., Awaitable[int]]

DRAFT_SYSTEM = """You write one WhatsApp message from {owner} to a business contact. Write it so a busy professional is glad to get it.

Rules:
- At most 60 words. Plain words. At most one emoji, and only if it fits.
- Start with their name. One purpose only (given below). One easy next step, or none.
- Never a bare "how are you". Never flattery. Never a claim, number or fact that is not in the context. No links unless given.
- Match their world: formal and brief for officials; collegial for unions and civil society; efficient for clients.
- Write in the language set for them (default English). Sign off with {owner}'s first name.
- If you do not know enough to be specific, be short and honest rather than generic.

Answer with JSON only: {{"message": "<the message>"}}"""


def _first(name: str) -> str:
    return (name.split() or [""])[0]


def contact_key(c: dict[str, Any]) -> str:
    emails = [e.get("value") for e in c.get("emails") or [] if e.get("value")]
    phones = [phone_e164(p.get("value")) for p in c.get("phones") or [] if p.get("value")]
    return (emails[0].lower() if emails else (phones[0] if phones and phones[0] else str(c.get("id"))))[:200]


def best_phone(c: dict[str, Any]) -> str:
    rows = [p for p in c.get("phones") or [] if phone_e164(p.get("value"))]
    rows.sort(key=lambda p: 0 if "mobile" in str(p.get("label") or "").lower() else 1)
    return phone_e164(rows[0]["value"]) or "" if rows else ""


class RapportService:
    def __init__(self, repo: SqlRapportRepository, actions: ActionService, data_factory: Callable[[str], Any], send_whatsapp: Callable[[Who, str, str], Awaitable[None]], notify: Send,
                 chat_model: Callable[[Who], Awaitable[Any]] | None = None, names: Callable[[Who], Awaitable[str]] | None = None, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._repo, self._actions, self._data, self._wa, self._notify, self._model, self._names, self._now = repo, actions, data_factory, send_whatsapp, notify, chat_model, names, now

    def _today(self) -> str:
        return self._now().astimezone(ZoneInfo(HOME)).date().isoformat()

    async def _owner(self, who: Who) -> str:
        name = (await self._names(who)) if self._names is not None else ""
        return _first(name or who.name).title() or "me"

    async def _taken(self, who: Who) -> dict[str, int]:
        out: dict[str, int] = {}
        for p in await self._repo.all(who.user_id, ["active"]):
            for t in p.get("touches", []):
                if t["state"] in ("planned", "queued"):
                    out[t["day"]] = out.get(t["day"], 0) + 1
        return out

    # --- starting a plan ---------------------------------------------------------------------------------------------------------------------

    async def start(self, who: Who, token: str, contact: dict[str, Any], *, tier: str | None = None, source: str = "manual", announce: bool = False) -> dict[str, Any]:
        """A plan for this contact. A contact that already has one gets it back (changed to the tier given, and woken if it was paused or excluded)."""
        key = contact_key(contact)
        profile = contact.get("profile") or {}
        chosen = (tier or profile.get("tier") or DEFAULT_TIER).upper()
        chosen = chosen if chosen in ("A", "B", "C") else DEFAULT_TIER
        phone = best_phone(contact)
        zone = contact.get("timezone") or zone_from_phone(phone) or HOME
        about = {"organisation": contact.get("organisation") or "", "title": contact.get("title") or "", "tags": contact.get("tags") or [], "profile": profile, "notes": str(contact.get("notes") or "")[:400]}
        today = self._today()
        existing = await self._repo.by_key(who.user_id, key)
        if existing is not None and existing["status"] in ("active", "paused") and tier is None:
            return existing
        plan = {"id": existing["id"] if existing else str(uuid.uuid4()), "contactKey": key, "contactId": str(contact.get("id")), "name": contact.get("name") or key, "workspaces": contact.get("workspaces") or [], "phone": phone, "zone": zone,
                "about": about, "tier": chosen, "status": "active", "autoSend": True, "touches": schedule(chosen, today, zone, await self._taken(who)), "unanswered": 0, "source": source}
        await self._repo.save(who.user_id, plan, self._now())
        await self._actions.note(who, f"rapport:{plan['id']}", "created", "chittu", plan["name"], f"rapport plan, tier {chosen}, {source}")
        if announce:
            first = plan["touches"][0] if plan["touches"] else None
            when = f" The first message goes {self._when(first)}." if first else ""
            tagged = " Add tags on the web so the messages fit them." if not about["tags"] and not profile.get("sector") else ""
            ws = ", ".join(w.upper() if w != "personal" else "Personal" for w in plan["workspaces"])
            await self._notify(who, f"🌱 New contact: {plan['name']}" + (f" · {about['organisation']}" if about["organisation"] else "") + (f" ({ws})" if ws else "") + f".\nI've started a get-to-know plan, tier {chosen}.{when}{tagged}",
                               [[{"text": "⭐ Tier A", "callback_data": f"rp:t:{plan['id'][:8]}:A"}, {"text": "Tier C", "callback_data": f"rp:t:{plan['id'][:8]}:C"}], [{"text": "🚫 No plan for them", "callback_data": f"rp:x:{plan['id'][:8]}:0"}]])
        return plan

    @staticmethod
    def _when(t: dict[str, Any]) -> str:
        d = date.fromisoformat(t["day"])
        return f"on {d.strftime('%a')} {d.day} {d.strftime('%b')} at {t['at'] // 60:02d}:{t['at'] % 60:02d}"

    async def scan(self, who: Who, token: str, days: int = 14) -> list[dict[str, Any]]:
        """Every contact that appeared recently (in any workspace) and has no plan yet gets one, and the owner is told. Contacts marked "no plan" are left alone."""
        made = []
        for c in await self._data(token).contacts_recent(days):
            if c.get("rapportExcluded") or await self._repo.by_key(who.user_id, contact_key(c)) is not None:
                continue
            made.append(await self.start(who, token, c, source="auto", announce=True))
        return made

    # --- writing and sending -------------------------------------------------------------------------------------------------------------

    async def draft(self, who: Who, plan: dict[str, Any], touch: dict[str, Any], redo: bool = False) -> str:
        owner = await self._owner(who)
        about, prof = plan.get("about") or {}, (plan.get("about") or {}).get("profile") or {}
        fallback = f"Hello {_first(plan['name'])}, this is {owner}. " + {
            "intro": "I wanted to say it was good to be connected, and that I'm glad to help with anything in your work.", "value": "I came across something I thought might be useful to you; shall I send it over?",
            "ask": "What is the main thing on your plate in the coming weeks? I'd like to see where I can help.", "connect": "I know someone whose work fits yours. May I introduce you?",
            "invite": "Could we have a short call this week? Tell me what suits you.", }.get(touch["purpose"], "A short note to stay in touch, and to say I'm here if I can help.")
        model = await self._model(who) if self._model is not None else None
        if model is None:
            return fallback
        client, name, thinking = model
        context = {
            "contact": plan["name"], "organisation": about.get("organisation"), "title": about.get("title"), "tags": about.get("tags"), "profile": prof, "notes": about.get("notes"),
            "their local time zone": label(plan.get("zone") or HOME), "purpose": f"{touch['purpose']}: {PURPOSES.get(touch['purpose'], '')}",
            "already sent": [{"purpose": t["purpose"], "on": t.get("sentOn")} for t in plan.get("touches", []) if t["state"] == "sent"][-4:], "their last reply on": plan.get("repliedOn"), "the last message I sent": plan.get("lastMessage"),
            "instruction": "Write a different wording from the last message." if redo else "",
        }
        try:
            reply = await client.chat(model=name, messages=[Message("system", DRAFT_SYSTEM.format(owner=owner)), Message("user", json.dumps({k: v for k, v in context.items() if v not in (None, "", [], {})}, ensure_ascii=False))], tools=[], thinking=thinking)
            text = json.loads(reply.text[reply.text.index("{"): reply.text.rindex("}") + 1])["message"]
            return str(text).strip()[:900] or fallback
        except Exception:  # noqa: BLE001  a message that cannot be written well is written plainly, not skipped
            log.info("could not write a rapport message with the model")
            return fallback

    async def tick(self, who: Who, token: str) -> dict[str, int]:
        """Everything due now: sends what may go (inside the caps), and asks the owner about what WhatsApp will not take. Run every few minutes."""
        out = {"sent": 0, "queued": 0, "waiting": 0}
        await self._reconcile(who)
        today = self._today()
        plans = await self._repo.all(who.user_id, ["active"])
        sent_today = sum(1 for p in plans for t in p.get("touches", []) if t.get("sentOn") == today)
        for plan in plans:
            touch = due_now(plan, self._now())
            if touch is None:
                continue
            last30 = sum(1 for t in plan["touches"] if t["state"] == "sent" and t.get("sentOn") and (date.fromisoformat(today) - date.fromisoformat(t["sentOn"])).days < 30)
            why = may_send(plan, last30, sent_today)
            if why:
                self._postpone(plan, touch, today)
                await self._repo.save(who.user_id, plan, self._now())
                out["waiting"] += 1
                continue
            text = await self.draft(who, plan, touch)
            if await self._deliver(who, token, plan, touch, text):
                out["sent"] += 1
                sent_today += 1
            else:
                out["queued"] += 1
        return out

    @staticmethod
    def _postpone(plan: dict[str, Any], touch: dict[str, Any], today: str) -> None:
        day = date.fromisoformat(today) + timedelta(days=1)
        for _ in range(30):
            at = slot_for(day.isoformat(), plan.get("zone"))
            if at is not None:
                touch["day"], touch["at"], touch["outside"] = day.isoformat(), at[0], at[1]
                return
            day += timedelta(days=1)

    async def _deliver(self, who: Who, token: str, plan: dict[str, Any], touch: dict[str, Any], text: str) -> bool:
        """Sends on WhatsApp; true when it went. Otherwise the owner gets it to send themselves, with the text written and a button that opens WhatsApp."""
        today = self._today()
        reason = ""
        if plan.get("autoSend", True) and plan.get("phone"):
            try:
                await self._wa(who, plan["phone"], text)
                plan.update(after_sent(plan, touch["n"], today))
                plan["lastMessage"] = text
                await self._repo.save(who.user_id, plan, self._now())
                await self._actions.note(who, f"rapport:{plan['id']}", "sent", "chittu", plan["name"], f"{touch['purpose']}: {text[:300]}")
                await self._notify(who, f"💬 Sent to {plan['name']} ({touch['purpose']}):\n{text}", [[{"text": "🗣 They replied", "callback_data": f"rp:r:{plan['id'][:8]}:{touch['n']}"}, {"text": "⏸ Pause", "callback_data": f"rp:p:{plan['id'][:8]}:{touch['n']}"}]])
                return True
            except Exception as exc:  # noqa: BLE001
                reason = str(getattr(exc, "message", None) or exc)[:160]
        elif not plan.get("phone"):
            reason = "there is no phone number for them"
        touch["state"] = "queued"
        link = wa_link(plan["phone"], text) if plan.get("phone") else ""
        item = await self._actions.create(who, kind="message", title=f"Message {plan['name']}: {touch['purpose']}", day=self._today(), priority="normal", workspace=(plan.get("workspaces") or ["personal"])[0], source="rapport",
                                          links={"rapport": {"plan": plan["id"], "touch": touch["n"], "draft": text, "wa": link}, "contact": plan["name"]}, notes=text, actor="chittu", reason="a rapport message for you to send")
        touch["item"] = f"message:{item['id']}"
        await self._repo.save(who.user_id, plan, self._now())
        why = f" (WhatsApp wouldn't send it by itself: {reason})" if reason else ""
        rows = ([[{"text": "💬 Open WhatsApp", "url": link}]] if link else []) + [[{"text": "✅ Sent", "callback_data": f"rp:s:{plan['id'][:8]}:{touch['n']}"}, {"text": "🗣 They replied", "callback_data": f"rp:r:{plan['id'][:8]}:{touch['n']}"}],
                                                                                  [{"text": "✏️ Reword", "callback_data": f"rp:w:{plan['id'][:8]}:{touch['n']}"}, {"text": "⏭ Skip", "callback_data": f"rp:k:{plan['id'][:8]}:{touch['n']}"}, {"text": "⏸ Pause", "callback_data": f"rp:p:{plan['id'][:8]}:{touch['n']}"}]]
        await self._notify(who, f"✉️ {plan['name']} · {touch['purpose']}{why}\n\n{text}", rows)
        return False

    async def _reconcile(self, who: Who) -> None:
        """A touch waiting on the owner whose action item was settled in the list: done means it was sent, cancelled means it was skipped."""
        for plan in await self._repo.all(who.user_id, ["active"]):
            changed = False
            for t in plan.get("touches", []):
                if t["state"] != "queued" or not t.get("item"):
                    continue
                row = await self._actions._repo.item(who.user_id, t["item"].split(":", 1)[1])
                if row and row["status"] == "done":
                    plan.update(after_sent(plan, t["n"], self._today()))
                    changed = True
                elif row and row["status"] == "cancelled":
                    plan.update(after_skip(plan, t["n"]))
                    changed = True
            if changed:
                await self._repo.save(who.user_id, plan, self._now())

    # --- what the owner does -------------------------------------------------------------------------------------------------------------

    async def press(self, who: Who, token: str, action: str, rest: str) -> Any:
        """A button on a rapport message: sent, replied, reword, skip, pause, no plan, tier. Returns what to say."""
        prefix, _, arg = rest.partition(":")
        plan = await self._repo.by_prefix(who.user_id, prefix)
        if plan is None:
            return "I can't find that plan any more."
        today = self._today()
        n = int(arg) if arg.isdigit() else 0
        touch = next((t for t in plan.get("touches", []) if t["n"] == n), None)
        if action == "t":
            plan["tier"] = arg if arg in ("A", "B", "C") else plan["tier"]
            plan["touches"] = [t for t in plan["touches"] if t["state"] in ("sent", "skipped")] + [{**t, "n": t["n"] + 100} for t in schedule(plan["tier"], today, plan.get("zone"), await self._taken(who))]
            await self._repo.save(who.user_id, plan, self._now())
            return f"⭐ {plan['name']}: tier {plan['tier']}. Next message {self._when(next(t for t in plan['touches'] if t['state'] == 'planned'))}."
        if action == "x":
            plan["status"] = "excluded"
            await self._repo.save(who.user_id, plan, self._now())
            await self._data(token).contact_update(plan["contactId"], {"rapportExcluded": True})
            await self._actions.note(who, f"rapport:{plan['id']}", "cancelled", "owner", plan["name"], "no rapport plan for them")
            return f"🚫 No plan for {plan['name']}. Say start a plan for them any time to bring it back."
        if action == "p":
            plan["pausedUntil"] = (date.fromisoformat(today) + timedelta(days=60)).isoformat()
            plan["pauseReason"] = "paused by the owner"
            await self._repo.save(who.user_id, plan, self._now())
            return f"⏸ {plan['name']} is paused for 60 days."
        if action == "r":
            plan.update(after_reply(plan, today))
            if touch is not None and touch["state"] == "queued":
                plan.update(after_sent(plan, n, today))
                plan.update(after_reply(plan, today))
            await self._repo.save(who.user_id, plan, self._now())
            await self._actions.note(who, f"rapport:{plan['id']}", "resolved", "owner", plan["name"], "they replied")
            return f"🗣 Good: {plan['name']} replied. I'll keep the rhythm close for a while."
        if touch is None:
            return "That message is no longer current."
        if action == "s":
            plan.update(after_sent(plan, n, today))
            await self._repo.save(who.user_id, plan, self._now())
            await self._complete_item(who, token, touch)
            return f"✅ Noted: sent to {plan['name']}."
        if action == "k":
            plan.update(after_skip(plan, n))
            await self._repo.save(who.user_id, plan, self._now())
            await self._complete_item(who, token, touch, cancel=True)
            return "⏭ Skipped. The next one stays as planned."
        if action == "w":
            text = await self.draft(who, plan, touch, redo=True)
            link = wa_link(plan["phone"], text) if plan.get("phone") else ""
            rows = ([[{"text": "💬 Open WhatsApp", "url": link}]] if link else []) + [[{"text": "✅ Sent", "callback_data": f"rp:s:{plan['id'][:8]}:{n}"}, {"text": "✏️ Reword", "callback_data": f"rp:w:{plan['id'][:8]}:{n}"}, {"text": "⏭ Skip", "callback_data": f"rp:k:{plan['id'][:8]}:{n}"}]]
            return f"✉️ {plan['name']} · {touch['purpose']}\n\n{text}", rows
        return "I don't know that button."

    async def _complete_item(self, who: Who, token: str, touch: dict[str, Any], cancel: bool = False) -> None:
        if touch.get("item"):
            try:
                await self._actions.command(who, token, touch["item"], "cancel" if cancel else "complete", actor="owner", reason="rapport message")
            except Exception:  # noqa: BLE001  the item may already be settled
                pass

    async def on_inbound(self, who: Who, sender: str, body: str) -> bool:
        """A message on the WhatsApp number from someone with a plan: they replied. The plan stops writing until the owner has taken it from there, and the owner is told."""
        number = phone_e164(sender.removeprefix("whatsapp:"))
        for plan in await self._repo.all(who.user_id, ["active", "paused"]):
            if number and plan.get("phone") == number:
                plan.update(after_reply(plan, self._today()))
                await self._repo.save(who.user_id, plan, self._now())
                await self._actions.note(who, f"rapport:{plan['id']}", "resolved", "system", plan["name"], f"they replied: {body[:200]}")
                await self._notify(who, f"🗣 {plan['name']} replied:\n{body[:600]}")
                return True
        return False

    async def find(self, who: Who, token: str, query: str) -> dict[str, Any] | None:
        """A contact by a word in their name (their rapport plan if they have one is `plan`)."""
        rows = await self._data(token).contacts(query.strip(), 5)
        return rows[0] if rows else None

    async def status(self, who: Who) -> dict[str, Any]:
        plans = await self._repo.all(who.user_id)
        live = [p for p in plans if p["status"] == "active"]
        return {"plans": len(plans), "active": len(live), "paused": sum(1 for p in plans if p["status"] == "paused" or p.get("pausedUntil")), "excluded": sum(1 for p in plans if p["status"] == "excluded"),
                "dueSoon": [{"name": p["name"], **(summary(p)["next"] or {})} for p in sorted(live, key=lambda p: (summary(p)["next"] or {"day": "9999"})["day"])[:5]]}
