"""The rapport pack: contacts, and keeping in touch with them well.

A new contact in Google contacts (any workspace) gets a get-to-know plan on its own. Chittu can show recent contacts (one at a time on Telegram), start a plan for
a contact the owner already knows, pause or leave one out, and say how the plans are going. Tags and the profile of a contact are the owner's and are edited on the web.
"""

from typing import Any

from app.application.runner import Tool, ToolOutput
from app.application.toolpacks import PackContext, ToolPack
from app.application.toolpacks.cards import card
from app.application.toolpacks.common import limit, text
from app.domain.llm import ToolSpec
from app.domain.rapport import summary

OBJ = "object"
S = {"type": "string"}


def _row(c: dict[str, Any]) -> dict[str, Any]:
    return {"id": c.get("id"), "name": c.get("name"), "organisation": c.get("organisation") or None, "title": c.get("title") or None, "workspaces": c.get("workspaces"), "tags": c.get("tags") or None,
            "sector": (c.get("profile") or {}).get("sector"), "tier": (c.get("profile") or {}).get("tier"), "addedOn": (c.get("firstSeenAt") or "")[:10] or None, "new": c.get("isNew") or None,
            "noPlan": c.get("rapportExcluded") or None}


class RapportPack(ToolPack):
    name = "rapport"

    async def tools(self, ctx: PackContext) -> list[Tool]:
        svc, who, token = ctx.rapport, ctx.who, ctx.token
        if svc is None or who is None:
            return []
        data = ctx.data

        async def find_contacts(a: dict[str, Any]) -> ToolOutput:
            days = a.get("recent_days")
            rows = await data.contacts_recent(limit(days, 14, 365)) if days else await data.contacts(text(a.get("query")), limit(a.get("max"), 10, 30))
            if days and text(a.get("query")):
                rows = [r for r in rows if text(a.get("query")).lower() in str(r.get("name", "")).lower()]
            return ToolOutput({"count": len(rows), "contacts": [_row(r) for r in rows[: limit(a.get("max"), 10, 30)]]})

        async def walk_contacts(a: dict[str, Any]) -> ToolOutput:
            rows = await data.contacts_recent(limit(a.get("recent_days"), 14, 365))
            if not rows:
                return ToolOutput({"started": False, "count": 0, "next": "No contact was added in that time. Say so in one line."})
            out = await ctx.queue.review_contacts(who, token, rows, channel_id=ctx.channel_id, title=text(a.get("label"))[:40] or "New contacts")
            return ToolOutput({**out, "next": "The first contact has ALREADY been sent with buttons (Start plan, Tier A, No plan, Next). The app walks the rest. Reply with one short line only."})

        async def start_rapport(a: dict[str, Any]) -> ToolOutput:
            c = await svc.find(who, token, text(a.get("contact")))
            if c is None:
                return ToolOutput({"error": f"I can't find a contact called “{text(a.get('contact'))}”. Ask for the name as it is in Google contacts."})
            plan = await svc.start(who, token, c, tier=text(a.get("tier")).upper() or None, source="manual")
            nxt = summary(plan)["next"]
            return ToolOutput({"name": plan["name"], "tier": plan["tier"], "zone": plan.get("zone"), "phone": bool(plan.get("phone")), "firstMessage": nxt, "note": "The plan is on. Each message comes to the owner inside 10:00–15:00 IST and their working hours, with a button that opens the contact's WhatsApp chat with the text ready. Tags and where they are go in on the web."})

        async def change_rapport(a: dict[str, Any]) -> ToolOutput:
            c = await svc.find(who, token, text(a.get("contact")))
            if c is None:
                return ToolOutput({"error": f"I can't find a contact called “{text(a.get('contact'))}”."})
            from app.application.rapport import contact_key

            plan = await svc._repo.by_key(who.user_id, contact_key(c))
            if plan is None:
                return ToolOutput({"error": f"{c.get('name')} has no plan. Use start_rapport."})
            action = {"pause": "p", "replied": "r", "exclude": "x"}.get(text(a.get("action")))
            if text(a.get("action")) == "resume":
                plan.pop("pausedUntil", None)
                plan["status"] = "active"
                await svc._repo.save(who.user_id, plan, svc._now())
                return ToolOutput({"name": plan["name"], "status": "active"})
            if action is None:
                return ToolOutput({"error": "The action is pause, resume, replied or exclude."})
            said = await svc.press(who, token, action, f"{plan['id'][:8]}:0")
            return ToolOutput({"name": plan["name"], "done": said if isinstance(said, str) else said[0]})

        async def tell_about_contact(a: dict[str, Any]) -> ToolOutput:
            from stiamo_common.errors import ValidationFailed

            try:
                out = await svc.learn(who, token, text(a.get("contact")), text(a.get("details")), text(a.get("phone")))
            except ValidationFailed as exc:
                return ToolOutput({"error": str(exc)})
            first = (out["suggestions"] or [None])[0]
            if first and ctx.queue is not None and ctx.channel == "Telegram" and first.get("whatsapp"):
                await ctx.queue._send(who, f"✉️ {out['name']} · {first['purpose'].replace('_', ' ')}\n\n{first['message']}", [[{"text": "💬 Open WhatsApp", "url": first["whatsapp"]}]], ctx.channel_id)
            return ToolOutput({"name": out["name"], "planCreated": out["created"], "inGoogleContacts": out["inGoogle"], "stage": out["persona"].get("stage"), "persona": out["persona"].get("summary"), "next": out["persona"].get("next"), "tier": out["tier"],
                               "tagsAdded": (out.get("contactUpdated") or {}).get("tags"), "placeSet": (out.get("contactUpdated") or {}).get("location"), "introductionsChangedToFollowUps": out["introductionsChanged"], "advice": out["reply"],
                               "messageSent": bool(first and ctx.channel == "Telegram"),
                               "say": "Tell the owner in two or three short sentences what you understood (the stage), what you set up, and the advice. If a message was sent with an Open WhatsApp button, say it is ready; do not repeat it."})

        async def rapport_status(a: dict[str, Any]) -> ToolOutput:
            return ToolOutput(await svc.status(who))

        tools = [
            Tool(
                ToolSpec("find_contacts", card(
                    "Looks up the owner's Google contacts (every workspace): name, organisation, tags, sector, tier, when it was added, whether it is new, and whether a rapport plan is switched off for it.",
                    use=["The owner asks about a contact, or who was added recently: “who did I add this week?”, “find Ramesh”, “is Chen in my contacts?”."],
                    avoid=["On Telegram never list the contacts out: for “show me the new contacts” use walk_contacts. A count or a yes/no is one sentence."],
                    args={"query": "A word in the name.", "recent_days": "Only contacts added in the last so many days (not those already there when Google was first connected).", "max": "How many (default 10)."},
                    returns="{count, contacts: [{id, name, organisation, title, workspaces, tags, sector, tier, addedOn, new, noPlan}]}.",
                    fails=["An empty list means none: say so."],
                    effect="Read only. Tags and the profile are the owner's and are edited on the web.",
                ), {"type": OBJ, "properties": {"query": S, "recent_days": {"type": "integer"}, "max": {"type": "integer"}}}),
                lambda a: "Looked at the contacts",
                find_contacts,
            ),
            Tool(
                ToolSpec("start_rapport", card(
                    "Starts a get-to-know plan for a contact: a series of messages with a reason each, spaced out (closer at first, then less often), each handed to the owner inside 10:00–15:00 IST and the contact's working hours, written at the moment from their tags, with a button that opens the contact's WhatsApp with the text ready. New contacts get a plan automatically once their tags and place are filled in on the web; use this for a contact the owner already knows, or to bring back one that was left out.",
                    use=["The owner says to start, build or attach a rapport or get-to-know plan for a named contact, or to change their tier: “start a plan for Meena, tier A”."],
                    avoid=["Do not use it for a contact that already has a live plan unless the tier changes."],
                    args={"contact": "The contact's name.", "tier": "A (key: closest), B (usual, the default) or C (light)."},
                    returns="{name, tier, zone, phone, firstMessage: {day, purpose}, note}, or {error} when no contact has that name.",
                    fails=["{error}: ask for the name as it is in Google contacts."],
                    effect="Creates a plan (and puts messages on a schedule). Nothing is sent right now unless the first one is due.",
                ), {"type": OBJ, "properties": {"contact": S, "tier": {"type": "string", "enum": ["A", "B", "C"]}}, "required": ["contact"]}),
                lambda a: f"Started a plan for {text(a.get('contact'))}",
                start_rapport,
            ),
            Tool(
                ToolSpec("change_rapport", card(
                    "Pauses a contact's plan for 60 days, brings it back, notes that they replied, or leaves the contact out of rapport plans altogether.",
                    use=["The owner says to pause, stop, resume or exclude a contact's plan, or that a contact replied."],
                    avoid=["Not to start one: use start_rapport."],
                    args={"contact": "The contact's name.", "action": "pause, resume, replied or exclude."},
                    returns="{name, done} or {error}.",
                    fails=["{error} when the contact has no plan."],
                    effect="Changes the plan (and for exclude, marks the contact so no plan is built for it).",
                ), {"type": OBJ, "properties": {"contact": S, "action": {"type": "string", "enum": ["pause", "resume", "replied", "exclude"]}}, "required": ["contact", "action"]}),
                lambda a: f"{text(a.get('action')).capitalize()} {text(a.get('contact'))}",
                change_rapport,
            ),
            Tool(
                ToolSpec("tell_about_contact", card(
                    "The owner tells you about a person: who they are, what has already happened between them (messages sent, calls, favours), what they have in common, how the person likes to be written to. The plan, the persona, the stage, the tier, the tags and the place are worked out from it, and a ready message is offered.",
                    use=["The owner describes a contact or a conversation with someone: “Meena Rao is the programme officer at the foundation, I wrote to her on 3 Nov and she hasn't replied”, “I met Ramesh at the Doha conference, he prefers Hindi”, “add a rapport plan for Anil, +91 98xxxxxx, he is a union leader in Hyderabad”."],
                    avoid=["Do not summarise or reword their details: pass their words as they said them. Not for starting a plain plan with no details (start_rapport), and not for pausing or leaving someone out (change_rapport)."],
                    args={"contact": "The person's name, as in Google contacts if they are there.", "details": "Everything the owner said about them, in their own words, as one text.", "phone": "Their phone number with country code, only if the owner gave one and they are not in Google contacts."},
                    returns="{name, planCreated, inGoogleContacts, stage, persona, next, tier, tagsAdded, placeSet, introductionsChangedToFollowUps, advice, messageSent, say}, or {error}.",
                    fails=["{error} when no AI model is connected: say so and point to Agents, then Connections.", "{error} with no name: ask whose plan it is."],
                    effect="Creates or updates the plan and the persona for that person, adds tags and a place to their Google contact (never overwriting what is there), may change the tier, and on Telegram sends one ready message with an Open WhatsApp button.",
                ), {"type": OBJ, "properties": {"contact": S, "details": S, "phone": S}, "required": ["contact", "details"]}),
                lambda a: f"Took in what you said about {text(a.get('contact'))}",
                tell_about_contact,
            ),
            Tool(
                ToolSpec("rapport_status", card(
                    "How the rapport plans are going: how many are active, paused or left out, and the next few messages due.",
                    use=["The owner asks how their rapport or get-to-know plans are going, who is due next, or how many contacts are in plans."],
                    avoid=["On Telegram do not list every plan: say the counts and the next one or two in a sentence."],
                    args={},
                    returns="{plans, active, paused, excluded, dueSoon: [{name, day, purpose}]}.",
                    fails=[],
                    effect="Read only.",
                ), {"type": OBJ, "properties": {}}),
                lambda a: "Looked at the rapport plans",
                rapport_status,
            ),
        ]
        if ctx.queue is not None and ctx.channel == "Telegram":
            tools.append(Tool(
                ToolSpec("walk_contacts", card(
                    "Shows the contacts added recently on Telegram, one at a time and numbered (1/5): name, organisation, workspace and tags, with buttons Start plan, Tier A, No plan and Next.",
                    use=["On Telegram, the owner asks who was added recently, or to go through new contacts: “who did I add this week?”, “show me new contacts”."],
                    avoid=["Never list contacts in a message yourself: always this tool.", "For a count: one sentence from find_contacts."],
                    args={"recent_days": "How far back (default 14).", "label": "A few words for the opening line."},
                    returns="{started, count, next}. started=true means the first contact has already been sent; reply with one short line only.",
                    fails=["started=false: no contact was added in that time; say so."],
                    effect="Sends the opening line and the first contact to the owner's Telegram. Changes nothing until a button is pressed.",
                ), {"type": OBJ, "properties": {"recent_days": {"type": "integer"}, "label": S}}),
                lambda a: "Started the contacts walk",
                walk_contacts,
            ))
        return tools
