"""The actions pack: Chittu's hands. Find the owner's action items, move or finish them, preview a bulk move, apply or drop it, undo, and read the history.

Every tool below carries a full description (a "tool card"): what it is for, when to use it, when not to, what each argument means, what it gives back, and what
to do when it fails. The model reads that text and nothing else about a tool, so it is written to be enough on its own.

Reads are free. Changes to one item are applied at once and can be undone. A bulk move is previewed first (a change set), and applied only when the owner has said yes.
"""

from __future__ import annotations

from typing import Any

from stiamo_common.errors import Conflict, NotFound, ValidationFailed

from app.application.runner import Tool, ToolOutput
from app.application.toolpacks import PackContext, ToolPack
from app.application.toolpacks.cards import card
from app.application.toolpacks.common import limit, text
from app.domain.actionitems import KINDS, label_day, resolve_day
from app.domain.llm import ToolSpec
from app.domain.zones import label as zone_label
from app.domain.zones import offset_label, resolve_zone

OBJ = "object"


S = {"type": "string"}


def _day_arg(note: str) -> dict[str, Any]:
    return {"type": "string", "description": note}


class ActionsPack(ToolPack):
    name = "actions"
    variables = {"day_start": "When Chittu's day opens (06:00).", "day_end": "When Chittu's day closes (22:00).", "here": "Where the owner is now and what the clock says there, and the clock their records are kept on."}

    async def context(self, ctx: PackContext) -> dict[str, Any]:
        from datetime import UTC, datetime
        from zoneinfo import ZoneInfo

        now = datetime.now(UTC).astimezone(ZoneInfo(ctx.zone))
        away = ctx.zone != ctx.home
        where = f"{zone_label(ctx.zone)} (UTC{offset_label(ctx.zone)}), where it is {now.strftime('%a %-d %b, %H:%M')}"
        return {"day_start": "06:00", "day_end": "22:00", "here": where + (f". Their records are kept on {zone_label(ctx.home)} time; every time you handle is converted by the tools." if away else ". That is also the clock their records are kept on.")}

    async def tools(self, ctx: PackContext) -> list[Tool]:
        svc, who, token = ctx.actions, ctx.who, ctx.token
        if svc is None or who is None:
            return []
        actor = ctx.actor
        scope = ctx.workspace
        today = ctx.today

        def clip(rows: list[Any], most: int) -> list[Any]:
            return rows[:most]

        async def find_items(a: dict[str, Any]) -> ToolOutput:
            try:
                day = resolve_day(text(a.get("day")), today) if text(a.get("day")) else None
                until = ("2999-12-31" if text(a.get("until")).lower() in ("open", "forever", "no limit", "any") else resolve_day(text(a.get("until")), today)) if text(a.get("until")) else None
                if (text(a.get("day")) and day is None) or (text(a.get("until")) and until is None):
                    return ToolOutput({"error": "I can't read that day. Give a date like 2026-11-25, or a day like tomorrow or next Monday."})
                kinds = [k for k in (a.get("kinds") or []) if k in KINDS] or None
                # a day that is today (or past) shows what is still open from before as well, unless asked not to: "what's on today" includes what is overdue
                overdue = a.get("include_overdue") if isinstance(a.get("include_overdue"), bool) else bool(day and day <= today)
                found, free = await svc.find(who, token, day=day, until=until, kinds=kinds, workspace=scope or (text(a.get("workspace")) or None), priority_below=text(a.get("less_important_than")) or None, include_overdue=overdue, with_free=True)
            except ValidationFailed as exc:
                return ToolOutput({"error": str(getattr(exc, "message", None) or exc)})
            word = text(a.get("query")).lower()
            if word:  # find one by a word in its title, its summary (a mail's subject and what it says), its case or its contact
                found = [i for i in found if word in " ".join(str(x or "") for x in (i.title, i.notes, i.case_ref, i.contact)).lower()]
            rows = [i.view(today, ctx.zone, ctx.home) for i in found]
            most = limit(a.get("max"), 40, 100)
            out: dict[str, Any] = {"count": len(rows), "range": {"from": day, "to": until or day}, "items": clip(rows, most), "truncated": len(rows) > most}
            if day and not until:
                out["freeTime"] = free  # what is left of the day, so "how full is it" needs no second tool
            return ToolOutput(out)

        async def item_history(a: dict[str, Any]) -> ToolOutput:
            rows = await svc.history(who, text(a.get("ref")) or None, limit(a.get("max"), 15, 100), int(a["hours"]) if isinstance(a.get("hours"), (int, float)) else None)
            return ToolOutput({"count": len(rows), "events": [{"ref": r["ref"], "what": r["event"], "by": r["actor"], "title": r["title"], "before": r["before"], "after": r["after"], "why": r["reason"], "at": r["at"]} for r in rows]})

        async def which_day(a: dict[str, Any]) -> ToolOutput:
            out = resolve_day(text(a.get("words")), today)
            return ToolOutput({"words": text(a.get("words")), "day": out, "dayLabel": label_day(out), "today": today} if out else {"error": f"I can't tell which day “{text(a.get('words'))}” is. Ask the owner."})

        async def preview_move(a: dict[str, Any]) -> ToolOutput:
            try:
                kinds = [k for k in (a.get("kinds") or []) if k in KINDS] or None
                out = await svc.preview_move(
                    who, token, to=text(a.get("to")), day=text(a.get("from_day")) or None, until=text(a.get("until")) or None, kinds=kinds, workspace=scope or (text(a.get("workspace")) or None),
                    priority_below=text(a.get("less_important_than")) or None, refs=[str(r) for r in a["refs"]] if isinstance(a.get("refs"), list) and a["refs"] else None,
                    include_overdue=bool(a.get("include_overdue")), include_fixed=bool(a.get("include_fixed")), actor=actor, why=text(a.get("why")),
                )
            except ValidationFailed as exc:
                return ToolOutput({"error": str(getattr(exc, "message", None) or exc)})
            out["next"] = "Tell the owner what would move and what stays, then ask them to confirm (on Telegram the message comes with Apply, Not this and Which ones? buttons, and a typed yes works too). Only after they say yes or press Apply, the change is applied: call apply_change with this changesetId only if they typed yes." if out.get("changesetId") else "Nothing would move. Say why (see staying) and do not call apply_change."
            return ToolOutput(out, [{"kind": "changeset", "id": out["changesetId"], "title": out["title"]}] if out.get("changesetId") else [])

        async def preview_close(a: dict[str, Any]) -> ToolOutput:
            try:
                kinds = [k for k in (a.get("kinds") or []) if k in KINDS] or None
                out = await svc.preview_close(
                    who, token, action=text(a.get("action")), day=text(a.get("from_day")) or None, until=text(a.get("until")) or None, kinds=kinds, workspace=scope or (text(a.get("workspace")) or None),
                    priority_below=text(a.get("less_important_than")) or None, refs=[str(r) for r in a["refs"]] if isinstance(a.get("refs"), list) and a["refs"] else None, include_overdue=bool(a.get("include_overdue")), actor=actor, why=text(a.get("why")),
                )
            except ValidationFailed as exc:
                return ToolOutput({"error": str(getattr(exc, "message", None) or exc)})
            out["next"] = "Tell the owner how many and which kind, then ask them to confirm (on Telegram the message comes with Apply, Not this and Which ones? buttons, and a typed yes works too). Call apply_change only if they typed yes." if out.get("changesetId") else "Nothing would change. Say why (see staying)."
            return ToolOutput(out, [{"kind": "changeset", "id": out["changesetId"], "title": out["title"]}] if out.get("changesetId") else [])

        async def apply_change(a: dict[str, Any]) -> ToolOutput:
            try:
                return ToolOutput(await svc.apply_changeset(who, token, text(a.get("changeset_id")), actor=actor))
            except (NotFound, Conflict, ValidationFailed) as exc:
                return ToolOutput({"error": str(getattr(exc, "message", None) or exc)})

        async def drop_change(a: dict[str, Any]) -> ToolOutput:
            try:
                await svc.drop_changeset(who, text(a.get("changeset_id")))
            except NotFound as exc:
                return ToolOutput({"error": str(getattr(exc, "message", None) or exc)})
            return ToolOutput({"dropped": True})

        async def change_item(a: dict[str, Any]) -> ToolOutput:
            try:
                named = text(a.get("timezone"))
                if named and resolve_zone(named) is None:
                    return ToolOutput({"error": f"I don't know which time zone “{named}” is. Give a city or a zone name like Asia/Qatar."})
                return ToolOutput(await svc.command(who, token, text(a.get("ref")), text(a.get("action")), to=text(a.get("to")) or None, start=int(a["start_minute"]) if isinstance(a.get("start_minute"), (int, float)) else None, actor=actor, reason=text(a.get("why")), note=text(a.get("note")) or None, include_fixed=bool(a.get("include_fixed")), tz=resolve_zone(named) if named else None))
            except (NotFound, Conflict, ValidationFailed) as exc:
                return ToolOutput({"error": str(getattr(exc, "message", None) or exc)})

        async def walk_through(a: dict[str, Any]) -> ToolOutput:
            try:
                when = resolve_day(text(a.get("day")), today) if text(a.get("day")) else None
                last = ("2999-12-31" if text(a.get("until")).lower() in ("open", "forever", "no limit", "any") else resolve_day(text(a.get("until")), today)) if text(a.get("until")) else None
                if (text(a.get("day")) and when is None) or (text(a.get("until")) and last is None):
                    return ToolOutput({"error": "I can't read that day. Ask the owner which day they mean."})
                kinds = [k for k in (a.get("kinds") or []) if k in KINDS] or None
                out = await ctx.queue.begin(who, token, day=when, until=last, kinds=kinds, workspace=scope or (text(a.get("workspace")) or None), priority_below=text(a.get("less_important_than")) or None, priority_at_least=text(a.get("at_least")) or None, overdue_only=bool(a.get("overdue_only")), or_overdue=bool(a.get("or_overdue")), changeset_id=text(a.get("changeset_id")) or None, label=text(a.get("label"))[:40], channel_id=ctx.channel_id)
            except (ValidationFailed, NotFound, Conflict) as exc:
                return ToolOutput({"error": str(getattr(exc, "message", None) or exc)})
            if not out.get("started"):
                return ToolOutput({"started": False, "count": 0, "next": "Nothing is waiting. Say the day is clear, in one line."})
            return ToolOutput({**out, "next": "The first item has ALREADY been sent to the owner's phone with its buttons. Reply with one short line only (for example: Say stop whenever you like). Do not list or repeat any item."})

        async def set_queue(a: dict[str, Any]) -> ToolOutput:
            try:
                out = await ctx.queue.set_from_json(who, token, {"title": a.get("title"), "mode": a.get("mode"), "items": a.get("items")}, channel_id=ctx.channel_id)
            except (ValidationFailed, NotFound, Conflict) as exc:
                return ToolOutput({"error": str(getattr(exc, "message", None) or exc)})
            if not out.get("started"):
                return ToolOutput({"started": False, "count": 0, "dropped": out.get("dropped"), "next": "Nothing valid was on the list. Look the items up with find_items and use their refs."})
            return ToolOutput({**out, "next": "The list is made and the first item has ALREADY been sent with its buttons. From here the app walks it by itself: do NOT call any tool per item. Reply with one short line only, and mention anything in dropped."})

        async def set_timezone(a: dict[str, Any]) -> ToolOutput:
            if ctx.set_zone is None:
                return ToolOutput({"error": "I can't change the zone here."})
            name = resolve_zone(text(a.get("place")))
            if name is None:
                return ToolOutput({"error": f"I don't know which time zone “{text(a.get('place'))}” is. Ask the owner for the city or country."})
            from datetime import UTC, datetime

            from app.domain.zones import today_in

            back = None
            if text(a.get("until")):
                back = resolve_day(text(a.get("until")), today_in(name, datetime.now(UTC)))
                if back is None:
                    return ToolOutput({"error": f"I can't tell which day “{text(a.get('until'))}” is. Ask the owner when they are back."})
            try:
                out = await ctx.set_zone(name, back)
            except ValidationFailed as exc:
                return ToolOutput({"error": str(getattr(exc, "message", None) or exc)})
            from zoneinfo import ZoneInfo

            now = datetime.now(UTC).astimezone(ZoneInfo(name))
            return ToolOutput({"zone": name, "label": zone_label(name), "now": now.strftime("%a %-d %b %H:%M"), "utcOffset": offset_label(name), "home": zone_label(out["home"]), "backOn": out.get("backOn"), "backOnLabel": label_day(out["backOn"]) if out.get("backOn") else None,
                               "note": "From now on, today, tomorrow and the times the owner says are on this clock; records stay on the home clock and the tools convert. Confirm in one short line."})

        async def undo_last(a: dict[str, Any]) -> ToolOutput:
            try:
                return ToolOutput(await svc.undo_changeset(who, token, text(a.get("changeset_id")) or None, actor=actor))
            except (NotFound, Conflict, ValidationFailed) as exc:
                return ToolOutput({"error": str(getattr(exc, "message", None) or exc)})

        filters = {
            "day": _day_arg("The first day, as the owner said it (“today”, “tomorrow”, “next Monday”, “25 November 2026”) or YYYY-MM-DD. Code resolves it; never work out dates yourself."),
            "until": _day_arg("The last day of a range, in the same way. Say “open” for no end (everything from the first day on). Leave out for a single day."),
            "kinds": {"type": "array", "items": {"type": "string", "enum": list(KINDS)}, "description": "Only these kinds. Leave out for every kind."},
            "workspace": {"type": "string", "enum": ["personal", "sb", "fsa", "media"], "description": "Only this workspace. Leave out for all (ignored when you already work inside one workspace)."},
            "less_important_than": {"type": "string", "enum": ["urgent", "high", "normal"], "description": "Only items strictly less important than this. “Non-urgent” is urgent (everything but urgent). “The small stuff” is high (normal and low). “Only the low ones” is normal."},
            "include_overdue": {"type": "boolean", "description": "Also items that were due before the first day and are still open. On by default when the day is today or earlier; pass false to leave them out."},
            "query": {"type": "string", "description": "Only items with this word in their title, summary, case or contact (to find “the RTI mail” or “the Ramesh call”)."},
        }
        return [
            Tool(
                ToolSpec("find_items", card(
                    "Looks up the owner's action items: every task, call, message, meeting, hearing, calendar event and email that needs them, as one list, with each item's ref, kind, day, time, priority, whether it is fixed, and its case.",
                    use=["The owner asks what is on a day, this week, or what is overdue.", "You need an item's ref before changing it.", "Before any bulk move, to see what is there.", "To find “the call to Ramesh” or “the Alur hearing”: list the day or range and match by title."],
                    avoid=["For a single known ref you just got from another tool: use it directly.", "To get free time: use free_time."],
                    args={**{k: v["description"] for k, v in filters.items()}, "max": "How many to return (default 40, at most 100)."},
                    returns="{count, range, items: [{ref, kind, title, workspace, day, dayLabel, time, minutes, due, dueLabel, status, priority, fixed, case, contact, source, daysLate?, summary?}], truncated, freeTime?}. Only open items are returned. fixed=true means it cannot be moved without the owner saying so. dayLabel and dueLabel are ready to say (Thu 26 Nov): use them, never compute a weekday. daysLate is how many days an overdue item is late. summary is what a mail is about. freeTime (for one day) lists what is free, like 12:45–15:00. For today or an earlier day, items still open from before are included (overdue) unless include_overdue is false.",
                    fails=["{error} for a day it cannot read: ask the owner which day they mean. An empty list is a real answer: say the day is clear."],
                    effect="Read only. Changes nothing.",
                ), {"type": OBJ, "properties": {**filters, "max": {"type": "integer"}}}),
                lambda a: f"Looked up items{' for ' + text(a.get('day')) if text(a.get('day')) else ''}",
                find_items,
            ),
            *([Tool(
                ToolSpec("set_queue", card(
                    "Gives the app a list of action items, as JSON, in the order you chose and with a few words on why each is there. The app then walks the owner through it one at a time, numbered (1/10), by itself: no model call per item. The list stays in force (through pauses, nights and restarts) until the owner asks for something else.",
                    use=["On Telegram, when YOU decide which items and in what order: “who should I call first?” (order the calls by judgement), “what should I do before the hearing?”, “line up my morning”, “put the RTI mail first”.", "To add something to the list in force: mode append (at the end) or next (right after the item in front of the owner)."],
                    avoid=["For a plain filter (today, overdue, calls, urgent): use walk_through, which picks and orders by itself and is cheaper.", "Never call it once per item, and never call any tool while the list is being walked: the app does that.", "Do not invent refs: take them from find_items."],
                    args={"title": "What the list is, in a few words (“Your calls”).", "mode": "replace (default: this list takes over), append, or next.", "items": "The entries in the order to show them: each {ref, note}. ref is an item's ref from find_items. note is a few words on why it is on the list (at most 140 characters), shown under the item."},
                    returns="{started, mode, count, added?, dropped: [{ref, why}], next}. Items that do not exist, are already settled, or are listed twice are dropped and named: mention them in your one line. started=true means the first item has already been sent.",
                    fails=["started=false with dropped: nothing valid was on the list; use find_items again."],
                    effect="Sends the opening line and the first item to the owner's Telegram and keeps the list. Changes no items.",
                ), {"type": OBJ, "properties": {"title": S, "mode": {"type": "string", "enum": ["replace", "append", "next"]}, "items": {"type": "array", "items": {"type": OBJ, "properties": {"ref": S, "note": S}, "required": ["ref"]}}}, "required": ["items"]}),
                lambda a: "Made the list",
                set_queue,
            )] if ctx.queue is not None and ctx.channel == "Telegram" else []),
            *([Tool(
                ToolSpec("walk_through", card(
                    "Shows the owner ANY set of their action items on Telegram, one at a time and numbered (1/10): a short line says how many there are, then the first item arrives with buttons (Done, Tomorrow, Skip, Stop), and each one they settle brings the next. This is the only way to show more than one item on Telegram.",
                    use=["On Telegram, whenever the owner's question would be answered by more than one action item: what is on today, tomorrow or this week, what is overdue, who to call, which tasks are pending, what is urgent, what is for one workspace or one case, what is next, or “start”.", "Use the filters to match the question: “who should I call?” is kinds=[call, message]; “what's overdue?” is overdue_only=true; “what's due this week?” is day=today, until=“next week”… as the owner said it; “what's urgent?” is at_least=urgent; “what matters most?” is at_least=high."],
                    avoid=["Never list or enumerate several items in a message or a card on Telegram, in any form: it is always this tool.", "For a count or a yes/no (“how many calls do I have?”, “is the hearing today?”): answer in one sentence from find_items.", "Not on the web app: show a card there with present_plan."],
                    args={"day": "The first day, in the owner's words. Default today (with what is overdue).", "until": "The last day of a range (“Friday”, “open”).", "kinds": "Only these kinds.", "workspace": "Only this workspace.", "less_important_than": "Only items below this priority.", "at_least": "Only items at least this important: urgent, or high (high and urgent).", "overdue_only": "Only items that are late.", "or_overdue": "Filters are combined with AND; this ALSO adds what is overdue (“overdue and urgent” is at_least=urgent with or_overdue=true).", "changeset_id": "Look at the items of a change you previewed, one by one (for “which ones?”): they come with where each would go, and nothing is changed by looking.", "label": "A few words for what this is, for the opening line (“your calls”, “what is overdue”)."},
                    returns="{started, count, first, next}. started=true means the first item has already been sent with its buttons; reply with one short line only. started=false means nothing matches.",
                    fails=["{error} for a day it cannot read: ask for a date."],
                    effect="Sends the opening line and the first item to the owner's Telegram and starts a list that moves on as items are settled. Changes no items.",
                ), {"type": OBJ, "properties": {"day": S, "until": S, "kinds": filters["kinds"], "workspace": filters["workspace"], "less_important_than": filters["less_important_than"], "at_least": {"type": "string", "enum": ["urgent", "high"]}, "overdue_only": {"type": "boolean"}, "or_overdue": {"type": "boolean"}, "changeset_id": S, "label": S}}),
                lambda a: "Started the list",
                walk_through,
            )] if ctx.queue is not None and ctx.channel == "Telegram" else []),
            Tool(
                ToolSpec("set_timezone", card(
                    "Tells the app where the owner is now. Their day, their today, their good morning and good night, and every time they say follow that place until they are back.",
                    use=["The owner says where they are or are going: “I'm in Qatar”, “I'm in Brazil till Monday”, “I'm back”, “I'm landing in Hyderabad Monday”. Say the place as they did.", "They give times in a place they are not in (a flight home): do NOT change the zone for that; pass timezone on the task instead."],
                    avoid=["Do not call it for a single time in another place."],
                    args={"place": "A country, a city or a zone (“Qatar”, “São Paulo”, “Asia/Qatar”). “India” or “home” puts them back.", "until": "The day they are back at home, in their words (“Monday”, “23 November”). Leave out if they did not say: it then stays until they say they are back."},
                    returns="{zone, label, now (the time there), utcOffset, home, backOn, backOnLabel}, or {error} when the place cannot be told: ask the owner for the city or country.",
                    fails=["{error} for a place or day it cannot read: ask one short question."],
                    effect="Changes the owner's settings (where they are). Records are not changed: they stay on the home clock.",
                ), {"type": OBJ, "properties": {"place": S, "until": S}, "required": ["place"]}),
                lambda a: f"Set the zone to {text(a.get('place'))}",
                set_timezone,
            ),
            Tool(
                ToolSpec("which_day", card(
                    "Turns the owner's words for a day into a calendar date, in code, using today's date and the owner's time zone.",
                    use=["The owner names a day in words and you want to confirm it before acting or before telling them: “the 25th”, “next Friday”, “in three days”."],
                    avoid=["Do not call it for a YYYY-MM-DD you already have. The other tools resolve words themselves."],
                    args={"words": "The day exactly as the owner said it."},
                    returns="{words, day (YYYY-MM-DD), dayLabel (Thu 26 Nov, ready to say), today}, or {error} if it cannot be read. Use it to get the YYYY-MM-DD that add_task, add_follow_up and the other proposing tools need when the owner says a day in words.",
                    fails=["If it cannot be read, ask the owner for a date. Never guess."],
                    effect="Read only.",
                ), {"type": OBJ, "properties": {"words": S}, "required": ["words"]}),
                lambda a: f"Worked out which day “{text(a.get('words'))}” is",
                which_day,
            ),
            Tool(
                ToolSpec("preview_move", card(
                    "Works out moving many items to another day, and keeps it as a pending change. It does not change anything. It shows what would move (with the time each would take on the new day), what would stay and why, and what would not fit.",
                    use=["The owner says to clear a day, push things to another day, or shift a range: “clear out today and move everything to tomorrow”, “move all non-urgent items to 25 November 2026”, “push this week's calls to Monday”.", "More than one item would move."],
                    avoid=["For one item: use change_item.", "To only look: use find_items.", "Never call apply_change straight after: show the owner the preview first."],
                    args={"to": "The target day, in the owner's words or YYYY-MM-DD. Required.", "from_day": "The first day to take items from (“today”).", "until": "The last day, for a range.", "refs": "Exact refs to move (use instead of from_day when the owner named items).", "kinds": "Only these kinds.", "workspace": "Only this workspace.", "less_important_than": "Only items below this priority.", "include_overdue": "Also take open items that were due before from_day.", "include_fixed": "Also move fixed items (meetings with other people). Only when the owner said so explicitly.", "why": "The owner's reason in a few words, for the history."},
                    returns="{changesetId, title, willMove, target, targetLabel, moves: [{ref, title, from, to, note}], staying: [{ref, title, why}], noRoom: [{ref, title}], warnings, notIncluded, free_on_target, next}. notIncluded lists open items in the range that your filter left where they were, with why. from and to are ready to say (Thu 26 Nov 16:00). A timed item keeps its time when it is free, otherwise takes the nearest free place and note says why. Moving an item also moves its due date to the new day when that was on or before it. warnings names urgent items being moved and says how many due dates change: mention them. Hearings and other people's events always stay. changesetId is null when nothing would move.",
                    fails=["{error} with a day it cannot read, or a day in the past: ask the owner for a date.", "If noRoom is not empty, tell the owner those items do not fit and offer the day after, or another day."],
                    effect="Saves a pending change (not applied). The owner's records are untouched until apply_change.",
                ), {"type": OBJ, "properties": {"to": S, "from_day": S, "until": S, "refs": {"type": "array", "items": S}, "kinds": filters["kinds"], "workspace": filters["workspace"], "less_important_than": filters["less_important_than"], "include_overdue": {"type": "boolean"}, "include_fixed": {"type": "boolean"}, "why": S}, "required": ["to"]}),
                lambda a: f"Worked out moving things to {text(a.get('to'))}",
                preview_move,
            ),
            Tool(
                ToolSpec("preview_close", card(
                    "Works out marking many items done, or cancelling them, and keeps it as a pending change. It does not change anything. Cancelling is how a clear-out is done: nothing is ever deleted, and it can be undone.",
                    use=["The owner says to cancel or finish a group: “cancel everything non-urgent for today”, “mark all my calls done”, “drop all the low priority tasks”, “delete my tasks” (offer cancel: you never delete)."],
                    avoid=["For one item: use change_item.", "To move things to another day: use preview_move.", "Never apply it without the owner's yes."],
                    args={"action": "complete or cancel. Required.", "from_day": "The first day to take items from.", "until": "The last day, for a range.", "refs": "Exact refs (instead of from_day).", "kinds": "Only these kinds.", "workspace": "Only this workspace.", "less_important_than": "Only items below this priority.", "include_overdue": "Also open items due before from_day.", "why": "The owner's reason in a few words."},
                    returns="{changesetId, title, willChange, items: [{ref, title}], staying, warnings, next}. Hearings, meetings and events are never cancelled or finished from here and are listed as staying. changesetId is null when nothing would change.",
                    fails=["{error} for a day it cannot read: ask for a date."],
                    effect="Saves a pending change (not applied). The owner's records are untouched until apply_change.",
                ), {"type": OBJ, "properties": {"action": {"type": "string", "enum": ["complete", "cancel"]}, "from_day": S, "until": S, "refs": {"type": "array", "items": S}, "kinds": filters["kinds"], "workspace": filters["workspace"], "less_important_than": filters["less_important_than"], "include_overdue": {"type": "boolean"}, "why": S}, "required": ["action"]}),
                lambda a: f"Worked out {text(a.get('action'))}ing a group",
                preview_close,
            ),
            Tool(
                ToolSpec("apply_change", card(
                    "Applies a pending change from preview_move or preview_close: changes every item in it, and records each change in the history.",
                    use=["Only after the owner has seen the preview and said yes, in their own words (“yes”, “go ahead”, “do it”)."],
                    avoid=["Never in the same turn as preview_move. Never because it seems sensible. Never if the owner changed what they want: preview again.", "Not for a change that is older than a day: it will refuse."],
                    args={"changeset_id": "The changesetId from preview_move or preview_close."},
                    returns="{changesetId, applied, gone, again?}. gone lists items that were deleted since the preview. again=true means it was already applied.",
                    fails=["{error} saying it was dropped, undone, stale or unknown: preview again if the owner still wants it."],
                    effect="Changes the owner's records (tasks, calls, meetings) and the history. Can be undone with undo_last.",
                ), {"type": OBJ, "properties": {"changeset_id": S}, "required": ["changeset_id"]}),
                lambda a: "Applied the change",
                apply_change,
            ),
            Tool(
                ToolSpec("drop_change", card(
                    "Throws away a pending change from preview_move.",
                    use=["The owner says no, not that, or changes their mind after seeing a preview."],
                    avoid=["Not for undoing something already applied: use undo_last."],
                    args={"changeset_id": "The changesetId from preview_move."},
                    returns="{dropped: true}, or {error}.",
                    fails=["If the id is unknown, nothing was pending: carry on."],
                    effect="Nothing in the owner's records changes.",
                ), {"type": OBJ, "properties": {"changeset_id": S}, "required": ["changeset_id"]}),
                lambda a: "Dropped the change",
                drop_change,
            ),
            Tool(
                ToolSpec("change_item", card(
                    "Does one thing to one item: mark it done, cancel it, reopen it, move it to another day (and optionally a time), or snooze it.",
                    use=["The owner says “done with the call to Ramesh”, “cancel that”, “move the rejoinder to Thursday”, “snooze this till Monday”, “put it at 4 pm”.", "Exactly one item is meant, and you have its ref from find_items."],
                    avoid=["For several items: use preview_move.", "For a hearing or a Google event: they cannot be changed here; tell the owner where to do it.", "Do not guess the item. If two items could be meant, ask which."],
                    args={"ref": "The item's ref from find_items.", "action": "complete, cancel, reopen, move or snooze.", "to": "For move and snooze: the day, in words or YYYY-MM-DD.", "start_minute": "For move: a time of day as minutes after midnight (4 pm is 960). Leave out to keep the time.", "why": "The owner's reason in a few words. Always pass it when they gave one: it is kept in the history.", "note": "A comment to keep with a completed or cancelled item.", "include_fixed": "Only when the owner said they want a fixed item (one with other people in it) moved.", "timezone": "The place or zone start_minute is on, if not where the owner is now (“Qatar”)."},
                    returns="{ok, item, ref, did, changesetId, undo}. did is ready to say (Moved to Thu 26 Nov 16:00: …). Say what was done in one short line and mention that undo is possible. A move also moves the due date when it was on or before the new day.",
                    fails=["{error} saying the item is fixed: tell the owner it involves others and ask if they really want it moved, then retry with include_fixed.", "{error} saying it cannot find the ref: call find_items again.", "{error} for an unreadable or past day: ask for a date."],
                    effect="Changes the owner's record at once and writes the history. Undo with undo_last.",
                ), {"type": OBJ, "properties": {"ref": S, "action": {"type": "string", "enum": ["complete", "cancel", "reopen", "move", "snooze"]}, "to": S, "start_minute": {"type": "integer"}, "why": S, "note": S, "include_fixed": {"type": "boolean"}, "timezone": S}, "required": ["ref", "action"]}),
                lambda a: f"{text(a.get('action')).capitalize()} {text(a.get('ref'))}",
                change_item,
            ),
            Tool(
                ToolSpec("undo_last", card(
                    "Puts back what the last applied change (or a named one) changed: every item in it returns to what it was.",
                    use=["The owner says undo, put it back, that was wrong, or I didn't mean that, right after a change."],
                    avoid=["Not for a change that was only previewed: use drop_change."],
                    args={"changeset_id": "Optional. Leave out to undo the most recent applied change."},
                    returns="{changesetId, undone, title}, or {error} if there is nothing to undo.",
                    fails=["If nothing can be undone, say so and offer to move things yourself."],
                    effect="Changes the owner's records back and writes the history.",
                ), {"type": OBJ, "properties": {"changeset_id": S}}),
                lambda a: "Undid the last change",
                undo_last,
            ),
            Tool(
                ToolSpec("item_history", card(
                    "Reads the history of action items: what was created, moved, completed, snoozed or undone, by whom (the owner, Chittu, a mail agent), what it was before and after, and why.",
                    use=["The owner asks what happened to something, when it moved, who changed it, or what you did today.", "At the end of the day, to say what got done and moved."],
                    avoid=["Not to find current items: use find_items."],
                    args={"ref": "One item's ref. Leave out for everything.", "hours": "Only the last this many hours (24 is today).", "max": "How many events (default 15, at most 100)."},
                    returns="{count, events: [{ref, what, by, title, before, after, why, at}]}, newest first.",
                    fails=["An empty list means nothing was recorded: say so."],
                    effect="Read only.",
                ), {"type": OBJ, "properties": {"ref": S, "hours": {"type": "integer"}, "max": {"type": "integer"}}}),
                lambda a: "Read the history",
                item_history,
            ),
        ]
