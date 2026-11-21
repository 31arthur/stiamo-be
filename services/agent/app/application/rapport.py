"""Rapport plans: keeping business contacts warm, within limits (see domain/rapport.py for the rules).

A new contact in Google contacts (any workspace) first needs its tags and where they are, filled in on the web (the owner is told what is missing); then it gets a plan:
a tier from its profile (B if not set), a zone from where they are (or their phone's country), and a list of touches. When a touch falls due the message is written by
the model at that moment, from the tags and the touch's purpose, and handed to the owner as an action item and a Telegram message with an "Open WhatsApp" button: it
opens the contact's own chat with the text already written, and the owner only presses send. Nothing is sent by Stiamo itself. Everything is in the history, and the
owner can pause, exclude, or say they replied, at any time.
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
from app.domain.rapport_advisor import ADVISOR_SYSTEM, adjust_for_stage, parse_advice
from app.domain.rapport import DAILY_CAP, missing_details, DEFAULT_TIER, PURPOSES, after_reply, after_sent, after_skip, due_now, may_send, schedule, slot_for, summary
from app.domain.zones import HOME, label, phone_e164, wa_link, zone_from_location, zone_from_phone
from app.infrastructure.db.rapport import SqlRapportRepository

log = logging.getLogger(__name__)

Send = Callable[..., Awaitable[int]]

DRAFT_SYSTEM = """You write one WhatsApp message from {owner} to a business contact. {owner_line} Write it so a busy professional is glad to get it.

Rules:
- At most 60 words. Plain words. At most one emoji, and only if it fits.
- Start with their name. One purpose only (given below). One easy next step, or none.
- Never a bare "how are you". Never flattery. Never a claim, number or fact that is not in the context. No links unless given.
- Match their world: formal and brief for officials; collegial for unions and civil society; efficient for clients.
- Write in the language set for them (default English). {sign_off}
- If the context has a persona or notes, they are what the owner knows about this person: write from them, in the style they say, and never repeat what they say was already said.
- If the stage is not "new", the message is not an introduction: refer to what happened before.
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


def zone_of(contact: dict[str, Any]) -> str:
    """Where they are: the zone the owner set, else the one their address means, else their phone's country, else home."""
    return contact.get("timezone") or zone_from_location(contact.get("location")) or zone_from_phone(best_phone(contact)) or HOME


class RapportService:
    def __init__(self, repo: SqlRapportRepository, actions: ActionService, data_factory: Callable[[str], Any], notify: Send,
                 chat_model: Callable[[Who], Awaitable[Any]] | None = None, names: Callable[[Who], Awaitable[str]] | None = None, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        #: The owner's full name (a function of who), and Chittu's list (application/queue.py): set by the app.
        self.full_name: Callable[[Who], Awaitable[str]] | None = None
        self.queue: Any = None
        self._repo, self._actions, self._data, self._notify, self._model, self._names, self._now = repo, actions, data_factory, notify, chat_model, names, now

    def _today(self) -> str:
        return self._now().astimezone(ZoneInfo(HOME)).date().isoformat()

    async def _owner(self, who: Who) -> str:
        """What the owner is called (the name they set, e.g. Bhargav); empty when none was set, so no name is guessed from their email."""
        return " ".join(((await self._names(who)) if self._names is not None else "").split())

    async def _full_name(self, who: Who) -> str:
        return " ".join(((await self.full_name(who)) if self.full_name is not None else "").split())

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
        zone = zone_of(contact)
        about = {"organisation": contact.get("organisation") or "", "title": contact.get("title") or "", "tags": contact.get("tags") or [], "profile": profile, "notes": str(contact.get("notes") or "")[:400]}
        today = self._today()
        existing = await self._repo.by_key(who.user_id, key)
        if existing is not None and existing["status"] in ("active", "paused") and tier is None:
            return existing
        plan = {"id": existing["id"] if existing else str(uuid.uuid4()), "contactKey": key, "contactId": str(contact.get("id")), "name": contact.get("name") or key, "workspaces": contact.get("workspaces") or [], "phone": phone, "zone": zone,
                "about": about, "tier": chosen, "status": "active", "autoSend": False, "touches": schedule(chosen, today, zone, await self._taken(who)), "unanswered": 0, "source": source}
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
        """Every contact that appeared recently (in any workspace) and has no plan yet: when its tags and place are filled in, it gets a plan and the owner is told; when
        they are not, the owner is asked for them once, and the plan starts the moment they are there. Contacts marked "no plan" are left alone."""
        made = []
        for c in await self._data(token).contacts_recent(days):
            if c.get("rapportExcluded") or await self._repo.by_key(who.user_id, contact_key(c)) is not None:
                continue
            gaps = missing_details(c)
            made.append(await (self._hold(who, c, gaps) if gaps else self.start(who, token, c, source="auto", announce=True)))
        made += await self._release(who, token)
        await self.rezone(who, token)
        return made

    async def _hold(self, who: Who, contact: dict[str, Any], gaps: list[str]) -> dict[str, Any]:
        """A new contact whose details are not in yet: no plan, and the owner is asked for what is missing, once."""
        key = contact_key(contact)
        plan = {"id": str(uuid.uuid4()), "contactKey": key, "contactId": str(contact.get("id")), "name": contact.get("name") or key, "workspaces": contact.get("workspaces") or [], "phone": best_phone(contact), "zone": zone_of(contact),
                "about": {}, "tier": DEFAULT_TIER, "status": "needs_info", "autoSend": False, "touches": [], "unanswered": 0, "source": "auto", "missing": gaps}
        await self._repo.save(who.user_id, plan, self._now())
        await self._actions.note(who, f"rapport:{plan['id']}", "created", "chittu", plan["name"], f"waiting for {' and '.join(gaps)}")
        await self._notify(who, f"🌱 New contact: {plan['name']}" + (f" · {contact.get('organisation')}" if contact.get("organisation") else "") + f".\nBefore I start a get-to-know plan I need their {' and '.join(gaps)}: add {'it' if len(gaps) == 1 else 'them'} on the web (Contacts, then {plan['name']}). The plan starts by itself once they are there.",
                           [[{"text": "🚫 No plan for them", "callback_data": f"rp:x:{plan['id'][:8]}:0"}]])
        return plan

    async def _release(self, who: Who, token: str) -> list[dict[str, Any]]:
        """Contacts that were waiting for their details and now have them get their plan."""
        started = []
        for plan in await self._repo.all(who.user_id, ["needs_info"]):
            found = next((c for c in await self._data(token).contacts(plan["name"], 5) if str(c.get("id")) == plan.get("contactId")), None)
            if found is None:
                continue
            if found.get("rapportExcluded"):
                plan["status"] = "excluded"
                await self._repo.save(who.user_id, plan, self._now())
            elif not missing_details(found):
                started.append(await self.start(who, token, found, source="auto", announce=True))
            else:
                plan["missing"] = missing_details(found)
                await self._repo.save(who.user_id, plan, self._now())
        return started

    async def rezone(self, who: Who, token: str) -> int:
        """A contact whose place was added or changed (here or in Google) is written to at their new local time: the messages still to come are fitted again."""
        moved = 0
        for plan in await self._repo.all(who.user_id, ["active", "paused"]):
            found = next((c for c in await self._data(token).contacts(plan["name"], 5) if str(c.get("id")) == plan.get("contactId")), None)
            if found is None or not (found.get("timezone") or found.get("location")):
                continue  # nothing said where they are: the phone's country stays what it was
            zone = zone_of(found)
            if zone == plan.get("zone"):
                continue
            plan["zone"] = zone
            for t in plan["touches"]:  # the same purposes, on the same days where they work, at their new local hours
                if t["state"] != "planned":
                    continue
                day = date.fromisoformat(t["day"])
                for _ in range(14):
                    at = slot_for(day.isoformat(), zone)
                    if at is not None:
                        t["day"], t["at"], t["outside"] = day.isoformat(), at[0], at[1]
                        break
                    day += timedelta(days=1)
            await self._repo.save(who.user_id, plan, self._now())
            moved += 1
        return moved

    # --- writing and sending -------------------------------------------------------------------------------------------------------------

    @staticmethod
    def _owner_line(owner: str, full: str) -> str:
        if not owner:
            return "Their name is not known: do not invent one."
        return f"Everyone calls them {owner}" + (f"; their full name is {full}." if full and full != owner else ".")

    @staticmethod
    def _sign_off(owner: str, full: str) -> str:
        if not owner:
            return "Do not sign with a name."
        return f"Sign off as {owner}." + (f" In the first message to someone, introduce them by the full name once ({full})." if full and full != owner else "")

    async def draft(self, who: Who, plan: dict[str, Any], touch: dict[str, Any], redo: bool = False) -> str:
        owner, full = await self._owner(who), await self._full_name(who)
        about, prof = plan.get("about") or {}, (plan.get("about") or {}).get("profile") or {}
        who_am_i = f"this is {full or owner}. " if (owner or full) and touch["purpose"] == "intro" else (f"this is {owner}. " if owner else "")
        fallback = f"Hello {_first(plan['name'])}, {who_am_i}" + {
            "intro": "I wanted to say it was good to be connected, and that I'm glad to help with anything in your work.", "value": "I came across something I thought might be useful to you; shall I send it over?",
            "ask": "What is the main thing on your plate in the coming weeks? I'd like to see where I can help.", "connect": "I know someone whose work fits yours. May I introduce you?",
            "invite": "Could we have a short call this week? Tell me what suits you.", }.get(touch["purpose"], "A short note to stay in touch, and to say I'm here if I can help.")
        model = await self._model(who) if self._model is not None else None
        if model is None:
            return fallback
        client, name, thinking = model
        context = {
            "contact": plan["name"], "organisation": about.get("organisation"), "title": about.get("title"), "tags": about.get("tags"), "profile": prof, "notes": about.get("notes"),
            "persona": plan.get("persona"), "what the owner has told me about them (newest last)": [n["text"] for n in (plan.get("notes") or []) if n.get("by") == "owner"][-6:],
            "their local time zone": label(plan.get("zone") or HOME), "purpose": f"{touch['purpose']}: {PURPOSES.get(touch['purpose'], '')}",
            "already sent": [{"purpose": t["purpose"], "on": t.get("sentOn")} for t in plan.get("touches", []) if t["state"] == "sent"][-4:], "their last reply on": plan.get("repliedOn"), "the last message I sent": plan.get("lastMessage"),
            "instruction": "Write a different wording from the last message." if redo else "",
        }
        try:
            reply = await client.chat(model=name, messages=[Message("system", DRAFT_SYSTEM.format(owner=owner or "the owner", owner_line=self._owner_line(owner, full), sign_off=self._sign_off(owner, full))), Message("user", json.dumps({k: v for k, v in context.items() if v not in (None, "", [], {})}, ensure_ascii=False))], tools=[], thinking=thinking)
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
        refs: list[str] = []
        sent_today = sum(1 for p in plans for t in p.get("touches", []) if today in (t.get("sentOn"), t.get("queuedOn")))
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
            ref = await self._deliver(who, token, plan, touch, text)
            if ref:
                refs.append(ref)
            out["queued"] += 1
            sent_today += 1
        await self._bring(who, token, refs)
        return out

    async def _bring(self, who: Who, token: str, refs: list[str]) -> None:
        """The messages that came due go into Chittu's list like any other action item, one at a time and numbered, and first: a rapport message is class 1, ahead of the rest."""
        if not refs or self.queue is None:
            return
        from app.domain.worklist import Spec, entry

        spec = Spec("Rapport messages", "next" if await self.queue.current(who) is not None else "replace", [entry(r, "to send on WhatsApp") for r in refs])
        await self.queue.set(who, token, spec, source="rapport")

    @staticmethod
    def _postpone(plan: dict[str, Any], touch: dict[str, Any], today: str) -> None:
        day = date.fromisoformat(today) + timedelta(days=1)
        for _ in range(30):
            at = slot_for(day.isoformat(), plan.get("zone"))
            if at is not None:
                touch["day"], touch["at"], touch["outside"] = day.isoformat(), at[0], at[1]
                return
            day += timedelta(days=1)

    async def _deliver(self, who: Who, token: str, plan: dict[str, Any], touch: dict[str, Any], text: str) -> str:
        """Makes the message an action item (the text, and a link that opens the contact's own WhatsApp chat with it written) and returns its ref. It is not sent anywhere
        on its own: it joins Chittu's list (see _bring), and the owner presses send in WhatsApp, then Done."""
        touch["state"] = "queued"
        touch["queuedOn"] = self._today()
        link = wa_link(plan["phone"], text) if plan.get("phone") else ""
        item = await self._actions.create(who, kind="message", title=f"Message {plan['name']}: {touch['purpose']}", day=self._today(), priority="urgent", workspace=(plan.get("workspaces") or ["personal"])[0], source="rapport",
                                          links={"rapport": {"plan": plan["id"], "touch": touch["n"], "draft": text, "wa": link}, "contact": plan["name"]}, notes=text, actor="chittu", reason="a rapport message for you to send")
        touch["item"] = f"message:{item['id']}"
        await self._repo.save(who.user_id, plan, self._now())
        return touch["item"]

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

    async def detail(self, who: Who, plan_id: str) -> dict[str, Any]:
        """One plan for the adviser on the web: who, how it stands, the persona, the conversation so far and the last suggestions (each with its WhatsApp link)."""
        plan = await self._repo.get(who.user_id, plan_id)
        if plan is None:
            raise NotFound("That plan isn't there.")
        return self._detail(plan)

    def _detail(self, plan: dict[str, Any]) -> dict[str, Any]:
        sugg = [{**x, "whatsapp": wa_link(plan["phone"], x["message"]) if plan.get("phone") else ""} for x in plan.get("suggestions") or []]
        return {"id": plan["id"], "name": plan["name"], "workspaces": plan.get("workspaces") or [], "zone": plan.get("zone"), "phone": bool(plan.get("phone")), "status": plan["status"], **summary(plan),
                "persona": plan.get("persona") or {}, "notes": plan.get("notes") or [], "suggestions": sugg, "about": {k: v for k, v in (plan.get("about") or {}).items() if k in ("organisation", "title", "tags")}}

    async def advise(self, who: Who, plan_id: str, message: str) -> dict[str, Any]:
        """The owner tells the adviser what happened (messages already sent, calls, things in common) or asks what to do. It answers, adjusts the persona of the contact
        (so every later message is written from it) and, when the relationship is already going, turns the planned introduction into a follow-up."""
        plan = await self._repo.get(who.user_id, plan_id)
        if plan is None:
            raise NotFound("That plan isn't there.")
        text = " ".join(message.split()) if len(message) < 200 else message.strip()
        if not text:
            raise ValidationFailed("Write what happened, or what you want to know.")
        model = await self._model(who) if self._model is not None else None
        if model is None:
            raise ValidationFailed("Connect an AI model first (Agents, then Connections), so the adviser can answer.")
        client, name, thinking = model
        owner, full = await self._owner(who), await self._full_name(who)
        about = plan.get("about") or {}
        touches = plan.get("touches") or []
        facts = {
            "today": self._today(), "the owner is called": owner or None, "the owner's full name": full or None,
            "contact": {"name": plan["name"], "organisation": about.get("organisation"), "title": about.get("title"), "tags": about.get("tags"), "profile": about.get("profile"), "notes from Google": about.get("notes"), "their zone": plan.get("zone")},
            "plan": {"tier": plan.get("tier"), "status": plan.get("status"), "sent": [{"purpose": t["purpose"], "on": t.get("sentOn")} for t in touches if t["state"] == "sent"][-8:],
                     "next planned": next(({"purpose": t["purpose"], "day": t["day"]} for t in touches if t["state"] == "planned"), None), "unanswered": plan.get("unanswered", 0), "they last replied on": plan.get("repliedOn"), "last message sent": plan.get("lastMessage")},
            "persona so far": plan.get("persona") or None,
            "notes so far, oldest first": [{"by": n["by"], "at": n["at"][:10], "text": n["text"][:600]} for n in (plan.get("notes") or [])[-20:]],
        }
        shown = json.dumps({k: v for k, v in facts.items() if v not in (None, "", [], {})}, ensure_ascii=False)
        user = "<item>" + shown + "</item>" + chr(10) + chr(10) + "<owner>" + text[:4000] + "</owner>"
        try:
            reply = await client.chat(model=name, messages=[Message("system", ADVISOR_SYSTEM), Message("user", user)], tools=[], thinking=thinking)
            advice = parse_advice(reply.text, plan.get("persona"))
        except ValueError as exc:
            raise ValidationFailed(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            log.info("the rapport adviser could not answer")
            raise ValidationFailed("The AI model could not answer just now. Try again in a moment.") from exc
        now = self._now().isoformat(timespec="seconds").replace("+00:00", "Z")
        plan["notes"] = ([*(plan.get("notes") or []), {"at": now, "by": "owner", "text": text[:4000]}, {"at": now, "by": "ai", "text": advice["reply"]}])[-40:]
        plan["persona"], plan["suggestions"] = advice["persona"], advice["suggestions"]
        changed = adjust_for_stage(plan, advice["stage"])
        await self._repo.save(who.user_id, plan, self._now())
        await self._actions.note(who, f"rapport:{plan['id']}", "updated", "owner", plan["name"], f"told the adviser: {text[:160]}")
        return {**self._detail(plan), "reply": advice["reply"], "suggestedTier": advice["tier"] if advice["tier"] != plan.get("tier") else None, "introductionsChanged": changed}

    async def status(self, who: Who) -> dict[str, Any]:
        plans = await self._repo.all(who.user_id)
        live = [p for p in plans if p["status"] == "active"]
        return {"plans": len(plans), "active": len(live), "paused": sum(1 for p in plans if p["status"] == "paused" or p.get("pausedUntil")), "excluded": sum(1 for p in plans if p["status"] == "excluded"), "needsInfo": sum(1 for p in plans if p["status"] == "needs_info"),
                "dueSoon": [{"name": p["name"], **(summary(p)["next"] or {})} for p in sorted(live, key=lambda p: (summary(p)["next"] or {"day": "9999"})["day"])[:5]]}
