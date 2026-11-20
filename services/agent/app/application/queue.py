"""Chittu's worklist on Telegram: the owner's things, one at a time, numbered (1/10), kept until a new request replaces it.

*Making* a list is a request: the morning, a question ("what's overdue?"), or a list a model gave as JSON (set_queue). It happens once. *Walking* it is code: a button,
a word the owner types (next, done, tomorrow, stop, continue, how many left, undo), or an item settled somewhere else moves it on, with no model call in between. The
list is stored (action_queues), so a pause, a night, a restart or a week changes nothing: "continue" picks up where it stopped, and the morning carries what is left
over and adds what is new. Only a new request replaces it (or adds to it, when the request says to). Every step is in the history of the item it was about.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.application.actions import ZONE, ActionService
from app.application.services import Who
from app.domain.actionitems import EMOJI, ActionItem, pick, queue_order, resolve_day
from app.domain.chittu import SITTING, good_morning, good_night
from app.domain.dayplan import check_blocks, clock, day_label
from app.domain.worklist import DONE, GONE, MOVED, PENDING, SETTLED, SHOWN, SKIPPED, Spec, classify_words, counts, entry, merge, next_index, position
from app.infrastructure.db.actions import SqlActionRepository

log = logging.getLogger(__name__)

#: Sends a message (text, buttons) to the owner's phone.
Send = Callable[..., Awaitable[int]]  # (who, text, buttons, channel_id=None): to the bot named, else to the general bot
Reply = "str | tuple[str, list[list[dict[str, str]]] | None]"
FIXED_KINDS = ("hearing", "meeting", "event")
OPEN = ["waiting", "active", "paused"]


def _tag(queue_id: str) -> str:
    return queue_id.replace("-", "")[:8]


def _buttons(kind: str, fixed: bool, tag: str, index: int, *, at_limit: bool = False, undo: int | None = None) -> list[list[dict[str, str]]]:
    d = lambda action, i=index: f"aq:{action}:{tag}:{i}"  # noqa: E731  (64 bytes is Telegram's limit)
    if kind == "review":  # looking at what a change would do: nothing here changes an item
        return [[{"text": "⏭ Next", "callback_data": d("k")}, {"text": "⏹ Stop", "callback_data": d("x")}]]
    if at_limit:
        return [[{"text": "▶️ Keep going", "callback_data": d("g")}, {"text": "⏹ Enough for now", "callback_data": d("x")}]]
    if fixed or kind in FIXED_KINDS:
        rows = [[{"text": "✔️ Noted", "callback_data": d("d")}, {"text": "⏭ Next", "callback_data": d("k")}], [{"text": "⏹ Stop", "callback_data": d("x")}]]
    elif kind == "mail":
        rows = [[{"text": "✅ Handled", "callback_data": d("d")}, {"text": "😴 Tomorrow", "callback_data": d("t")}], [{"text": "⏭ Skip", "callback_data": d("k")}, {"text": "⏹ Stop", "callback_data": d("x")}]]
    else:
        rows = [[{"text": "✅ Done", "callback_data": d("d")}, {"text": "🔁 Tomorrow", "callback_data": d("t")}], [{"text": "⏭ Skip", "callback_data": d("k")}, {"text": "⏹ Stop", "callback_data": d("x")}]]
    if undo is not None:
        rows.append([{"text": "↩️ Undo the last one", "callback_data": d("u", undo)}])
    return rows


def _plan_buttons(tag: str, index: int) -> list[list[dict[str, str]]]:
    d = lambda action: f"aq:{action}:{tag}:{index}"  # noqa: E731
    return [[{"text": "✅ Keep", "callback_data": d("d")}, {"text": "✖️ Drop", "callback_data": d("k")}], [{"text": "⏪ 30 min earlier", "callback_data": d("m")}, {"text": "30 min later ⏩", "callback_data": d("p")}], [{"text": "⏹ Stop", "callback_data": d("x")}]]


def item_text(it: ActionItem, position_: int, total: int, today: str, note: str | None = None) -> str:
    """One item, as Chittu says it: where we are (3/10), what it is, when, and why it is on the list. Short."""
    when = None
    if it.day and it.start is not None:
        when = ("Today " if it.day == today else f"{day_label(it.day)} ") + clock(it.start)
    elif (it.day or it.due or "") and (it.day or it.due) < today:  # type: ignore[operator]
        when = f"Overdue since {day_label(it.day or it.due)}"  # type: ignore[arg-type]
    elif it.due:
        when = f"Due {'today' if it.due == today else day_label(it.due)}"
    bits = [b for b in (when, it.priority if it.priority in ("urgent", "high") else None, it.case_ref, it.contact) if b]
    lines = [f"{position_}/{total} · {EMOJI.get(it.kind, '•')} {it.title}"]
    if bits:
        lines.append(" · ".join(str(b) for b in bits))
    if it.kind == "mail" and it.notes:
        lines.append(str(it.notes)[:200])
    if note:
        lines.append(f"💬 {note}")
    return "\n".join(lines)


class QueueService:
    def __init__(self, repo: SqlActionRepository, actions: ActionService, send: Send, now: Callable[[], datetime] = lambda: datetime.now(UTC), names: Callable[[Who], Awaitable[str]] | None = None) -> None:
        self._repo, self._actions, self._send, self._now, self._names = repo, actions, send, now, names

    def _today(self) -> str:
        return self._now().astimezone(ZoneInfo(ZONE)).date().isoformat()

    async def _owner(self, who: Who) -> str:
        """What Chittu calls the owner: the name they set for him, else the one their email gives."""
        name = (await self._names(who)) if self._names is not None else ""
        return ((name or who.name).split() or ["there"])[0].title()

    async def current(self, who: Who) -> dict[str, Any] | None:
        """The list that is in force: the latest that is waiting, being walked or paused. It stays so until a new request replaces it or every entry is settled."""
        return await self._repo.queue(who.user_id, statuses=OPEN)

    async def _save(self, who: Who, q: dict[str, Any]) -> None:
        q["updatedAt"] = self._now()
        await self._repo.save_queue(who.user_id, q)

    async def _retire(self, who: Who, q: dict[str, Any], status: str = "replaced") -> int:
        left = counts(q["entries"])["left"]
        q["status"], q["shown"] = status, {**(q.get("shown") or {}), "leftWhenClosed": left}
        await self._save(who, q)
        return left

    # --- making a list: a request, once -------------------------------------------------------------------------------------------------------

    async def set(self, who: Who, token: str, spec: Spec, source: str = "ai", channel_id: str | None = None) -> dict[str, Any]:
        """Makes the list a request asked for. `replace` puts the old list aside (its place is kept in the database) and starts this one; `append` adds to the end of the
        list in force, `next` right after the item in front of the owner. Sends the opening line and, for a new or woken list, the first item."""
        if not spec.entries:
            return {"started": False, "count": 0, "dropped": spec.dropped}
        old = await self.current(who)
        today = self._today()
        if spec.mode != "replace" and old is not None:
            before = counts(old["entries"])["left"]
            old["entries"] = merge(old["entries"], spec.entries, spec.mode, old["cursor"])
            added = counts(old["entries"])["left"] - before
            was_paused = old["status"] != "active"
            old["status"], old["shown"] = "active", {**(old.get("shown") or {}), "sitting": 0}
            await self._save(who, old)
            has_front = any(e["state"] == SHOWN for e in old["entries"])
            if has_front and not was_paused:
                await self._send(who, f"Added {added} to your list: {counts(old['entries'])['left']} left.", None, channel_id)
                return {"started": True, "mode": spec.mode, "count": counts(old["entries"])["left"], "added": added, "dropped": spec.dropped}
            await self._send(who, f"Added {added} to your list.", None, channel_id)
            text, buttons = await self._show(who, token, old)
            await self._send(who, text, buttons, channel_id)
            return {"started": True, "mode": spec.mode, "count": counts(old["entries"])["left"], "added": added, "dropped": spec.dropped}
        set_aside = await self._retire(who, old) if old is not None else 0
        row = {"id": str(uuid.uuid4()), "day": today, "status": "active", "entries": spec.entries, "cursor": -1, "shown": {"sitting": 0}, "title": spec.title, "source": source, "createdAt": self._now(), "updatedAt": self._now()}
        await self._repo.save_queue(who.user_id, row)
        n = len(spec.entries)
        many = "one thing" if n == 1 else f"{n} things"
        aside = f" (I've put the earlier list aside: {set_aside} were left.)" if set_aside else ""
        await self._send(who, f"{spec.title}: {many}. One at a time.{aside}", None, channel_id)
        text, buttons = await self._show(who, token, row)
        await self._send(who, text, buttons, channel_id)
        return {"started": True, "mode": "replace", "count": n, "first": spec.entries[0]["ref"], "dropped": spec.dropped, "putAside": set_aside}

    async def set_from_json(self, who: Who, token: str, raw: Any, channel_id: str | None = None) -> dict[str, Any]:
        """A list a model gave as JSON: {title, mode, items: [{ref, note}]}. Every ref is checked against the real open items; then the code walks it."""
        loaded = await self._actions.load(who, token)
        spec = parse_spec_for(raw, loaded)
        return await self.set(who, token, spec, "ai", channel_id)

    async def begin(self, who: Who, token: str, *, day: str | None = None, until: str | None = None, kinds: list[str] | None = None, workspace: str | None = None, priority_below: str | None = None,
                    priority_at_least: str | None = None, overdue_only: bool = False, or_overdue: bool = False, changeset_id: str | None = None, label: str = "", channel_id: str | None = None) -> dict[str, Any]:
        """A question the owner asked (today, a day or a week, only calls, only what is overdue): the code picks and orders the items by the usual rules and makes the list."""
        today = self._today()
        if changeset_id:  # "which ones?": the items of a change that was previewed, to look at one by one (nothing is changed by looking)
            cs = await self._repo.changeset(who.user_id, changeset_id)
            if cs is None or not cs["ops"]:
                return {"started": False, "count": 0}
            rows = [{**entry(o["ref"], f"→ {o.get('to') or ''}"[:140]), "review": True} for o in cs["ops"]]
            out = await self.set(who, token, Spec((label or "What I'd change")[:60], "replace", rows), "ask", channel_id)
            return {**out, "first": cs["ops"][0].get("title")}
        loaded = await self._actions.load(who, token)
        base = pick(loaded.items, kinds=kinds, workspace=workspace, priority_below=priority_below, priority_at_least=priority_at_least)
        through = until or day or today
        if overdue_only:
            items = queue_order([i for i in base if (i.day or i.due) and (i.day or i.due) < today], today)  # type: ignore[operator]
        else:
            items = queue_order(base, today, through)
            if day and day > today:  # a day or a week ahead: not what was left over from before
                items = [i for i in items if (i.day or i.due or "") >= day]
            if or_overdue:  # "overdue, and also urgent": the filters are AND, this adds what is late (of the kinds and workspace asked) to the end
                late = queue_order([i for i in pick(loaded.items, kinds=kinds, workspace=workspace) if (i.day or i.due) and (i.day or i.due) < today], today)  # type: ignore[operator]
                items += [i for i in late if i.ref not in {x.ref for x in items}]
        if not items:
            return {"started": False, "count": 0}
        scope = (label[:1].upper() + label[1:]) if label else ("Your day" if through == today and not kinds and not priority_below and not priority_at_least else day_label(through) if through != today else "Today")
        out = await self.set(who, token, Spec(scope[:60] or "Your list", "replace", [entry(i.ref) for i in items]), "ask", channel_id)
        return {**out, "first": items[0].title}

    # --- the day opens, nudges and closes ---------------------------------------------------------------------------------------------------

    async def morning(self, who: Who, token: str) -> dict[str, Any]:
        """Good morning: how many things need the owner (and how many are still on the list from before), and whether to start. A list left over from yesterday is
        kept, checked against what is real now, and what is new today is added to its end; nothing is brought until the owner says start."""
        today = self._today()
        old = await self.current(who)
        if old and old["day"] == today and old["status"] in ("waiting", "active"):
            return {"queueId": old["id"], "again": True}  # this morning's hello already went (a restart, a second trigger)
        loaded = await self._actions.load(who, token)
        todays = queue_order(loaded.items, today)
        carried = 0
        if old is not None and counts(old["entries"])["left"] > 0:
            self._revalidate(old, loaded)
            carried = counts(old["entries"])["left"]
            fresh = [entry(i.ref) for i in todays]
            old["entries"] = merge(old["entries"], fresh, "append")
            old["status"], old["day"], old["cursor"] = "waiting", today, -1
            old["shown"] = {"sitting": 0}
            row = old
            await self._save(who, row)
        else:
            if old is not None:
                await self._retire(who, old, "done")
            entries = [entry(i.ref) for i in todays]
            row = {"id": str(uuid.uuid4()), "day": today, "status": "waiting" if entries else "done", "entries": entries, "cursor": -1, "shown": {"sitting": 0}, "title": "Your day", "source": "morning", "createdAt": self._now(), "updatedAt": self._now()}
            await self._repo.save_queue(who.user_id, row)
        total = counts(row["entries"])["left"]
        first = next((loaded.get(e["ref"]).title for e in row["entries"] if e["state"] == PENDING and loaded.get(e["ref"])), None)  # type: ignore[union-attr]
        buttons = [[{"text": "▶️ Start", "callback_data": f"aq:s:{_tag(row['id'])}:0"}, {"text": "⏰ Later", "callback_data": f"aq:l:{_tag(row['id'])}:0"}]] if total else None
        await self._send(who, good_morning(await self._owner(who), today, total, first, carried), buttons)
        return {"queueId": row["id"], "count": total, "carried": carried}

    async def remind(self, who: Who, token: str) -> dict[str, Any]:
        """If the owner has not answered the good morning, ask once more, gently. Never a third time."""
        q = await self._repo.queue(who.user_id, statuses=["waiting"])
        if q is None or q["day"] != self._today() or (q.get("shown") or {}).get("reminded"):
            return {"reminded": False}
        left = counts(q["entries"])["left"]
        if not left:
            return {"reminded": False}
        q["shown"] = {**(q.get("shown") or {}), "reminded": True}
        await self._save(who, q)
        many = "one thing" if left == 1 else f"{left} things"
        await self._send(who, f"Still here whenever you're ready: {many}.", [[{"text": "▶️ Start", "callback_data": f"aq:s:{_tag(q['id'])}:0"}, {"text": "⏰ Later", "callback_data": f"aq:l:{_tag(q['id'])}:0"}]])
        return {"reminded": True}

    async def night(self, who: Who, token: str) -> dict[str, Any]:
        """Good night: what got done today, what moved, what is still open, and what tomorrow opens with. The list is not closed: it is paused where it is, and
        the morning will pick it up."""
        from app.domain.dayplan import clock as hhmm

        today = self._today()
        since = datetime.combine(date.fromisoformat(today), datetime.min.time(), tzinfo=ZoneInfo(ZONE)).astimezone(UTC)
        events = await self._repo.history(who.user_id, None, 500, since)
        done = len({e["ref"] for e in events if e["event"] == "completed"})
        moved = len({e["ref"] for e in events if e["event"] in ("moved", "snoozed")})
        loaded = await self._actions.load(who, token)
        left = len(queue_order(loaded.items, today))
        tomorrow = resolve_day("tomorrow", today)
        nxt = sorted((i for i in loaded.items if i.status == "open" and i.day == tomorrow), key=lambda i: (i.start if i.start is not None else 24 * 60))
        q = await self.current(who)
        kept = 0
        if q is not None:
            self._revalidate(q, loaded)
            kept = counts(q["entries"])["left"]
            q["status"] = "paused" if kept else "done"
            q["shown"] = {**(q.get("shown") or {}), "sitting": 0}
            await self._save(who, q)
        await self._send(who, good_night(await self._owner(who), done, moved, left, f"{nxt[0].title}" + (f" at {hhmm(nxt[0].start)}" if nxt[0].start is not None else "") if nxt else None, kept), None)
        return {"done": done, "moved": moved, "left": left, "kept": kept}

    # --- walking the list ------------------------------------------------------------------------------------------------------------------

    @staticmethod
    def _revalidate(q: dict[str, Any], loaded: Any) -> None:
        """Entries whose item was settled somewhere else, or deleted, since the list was made are gone: the list never shows what is not real."""
        for e in q["entries"]:
            if e["state"] in (PENDING, SHOWN):
                it = loaded.get(e["ref"])
                if it is None or it.status != "open":
                    e["state"] = GONE

    async def _show(self, who: Who, token: str, q: dict[str, Any], ack: str = "") -> tuple[str, list[list[dict[str, str]]] | None]:
        """The item in front of the owner (the one already shown, else the next pending), with its buttons; or the closing words when none is left."""
        today = self._today()
        loaded = await self._actions.load(who, token)
        self._revalidate(q, loaded)
        sitting = int((q.get("shown") or {}).get("sitting", 0))
        front = next((i for i, e in enumerate(q["entries"]) if e["state"] == SHOWN), None)
        index = front if front is not None else next_index(q["entries"], int(q.get("cursor", -1)))
        if index is None and q.get("source") == "plan":
            return await self._finish_plan(who, token, q, ack)
        if index is None:
            left_counts = counts(q["entries"])
            q["status"] = "done"
            await self._save(who, q)
            bits = [f"{n} {w}" for w, n in (("done", left_counts[DONE]), ("moved", left_counts[MOVED]), ("skipped", left_counts[SKIPPED]), ("snoozed", left_counts["snoozed"])) if n]
            return (f"{ack}\n\n✅ That's everything on the list" + (f" ({', '.join(bits)})." if bits else ".")).strip(), None
        e = q["entries"][index]
        it = loaded.get(e["ref"])
        assert it is not None
        if front is None and sitting >= SITTING:
            await self._save(who, q)
            left = counts(q["entries"])["left"]
            return (f"{ack}\n\nThat's {sitting} so far. {left} more to go. Keep going?".strip(), _buttons("task", False, _tag(q["id"]), index, at_limit=True))
        if front is None:
            e["state"], q["cursor"] = SHOWN, index
            q["shown"] = {**(q.get("shown") or {}), "sitting": sitting + 1}
            await self._actions.note(who, e["ref"], "queued", "chittu", it.title, "brought to the owner on Telegram")
        q["status"] = "active"
        last = (q.get("shown") or {}).get("last")
        undo = last if isinstance(last, int) and 0 <= last < len(q["entries"]) and q["entries"][last]["state"] in (DONE, MOVED, SKIPPED) and q["entries"][last].get("cs") and last != index else None
        await self._save(who, q)
        pos, total = position(q["entries"], index)
        if e.get("plan"):  # a block of a plan being looked at: when it would be, and what the owner can do about it
            p = e["plan"]
            body = f"{pos}/{total} · {EMOJI.get(it.kind, '•')} {it.title}\n🕐 {clock(p['start'])}–{clock(p['start'] + p['duration'])}" + (f" · {it.contact}" if it.contact else "") + (f"\n💬 {e['note']}" if e.get("note") else "")
            return f"{ack}\n\n{body}".strip(), _plan_buttons(_tag(q["id"]), index)
        return (f"{ack}\n\n{item_text(it, pos, total, today, e.get('note'))}".strip(), _buttons("review" if e.get("review") else it.kind, it.fixed, _tag(q["id"]), index, undo=undo))

    async def _settle(self, who: Who, token: str, q: dict[str, Any], index: int, action: str) -> str:
        """Does what a button or a word says to the item in front of the owner and returns what to say. A failure leaves the item skipped, never stuck."""
        e = q["entries"][index]
        ref = e["ref"]
        try:
            if e.get("plan"):
                ack = await self._settle_block(who, token, q, e, action)
            elif e.get("review"):  # only looked at: skipping is all there is
                ack, e["state"] = "⏭ Next.", SKIPPED
            elif action == "d":
                if ref.split(":")[0] in FIXED_KINDS:
                    await self._actions.note(who, ref, "resolved", "owner", ref, "noted on Telegram")
                    ack = "✔️ Noted."
                else:
                    out = await self._actions.command(who, token, ref, "complete", actor="owner", reason="done on Telegram")
                    e["cs"], ack = out.get("changesetId"), f"✅ {out['item']}: done."
                e["state"] = DONE
            elif action == "t":
                out = await self._actions.command(who, token, ref, "snooze" if ref.startswith("mail:") else "move", to="tomorrow", actor="owner", reason="moved to tomorrow on Telegram")
                e["cs"], ack, e["state"] = out.get("changesetId"), f"🔁 {out['item']}: tomorrow.", MOVED
            else:
                await self._actions.note(who, ref, "skipped", "owner", ref, "skipped on Telegram")
                ack, e["state"] = "⏭ Skipped.", SKIPPED
        except Exception as exc:  # noqa: BLE001  a button must always answer, and the list must move on
            log.warning("could not settle %s from Telegram: %s", ref, exc)
            ack, e["state"] = f"⚠️ {getattr(exc, 'message', None) or 'That did not work.'} Skipping it for now.", SKIPPED
        q["shown"] = {**(q.get("shown") or {}), "last": index}
        return ack

    async def _undo(self, who: Who, token: str, q: dict[str, Any], index: int) -> tuple[str, list[list[dict[str, str]]] | None]:
        """Puts back what was done to an entry (the item itself, from the change set) and makes the entry the next to show again."""
        e = q["entries"][index]
        ack = "↩️ Put back."
        if e.get("cs"):
            try:
                out = await self._actions.undo_changeset(who, token, e["cs"], actor="owner")
                ack = f"↩️ Put back: {out['title']}."
            except Exception as exc:  # noqa: BLE001
                return f"⚠️ {getattr(exc, 'message', None) or 'I could not undo that.'}", None
        e["state"], e["cs"] = PENDING, None
        # (anything shown now goes back to waiting so the undone entry comes first)
        for other in q["entries"]:
            if other["state"] == SHOWN:
                other["state"] = PENDING
        q["cursor"] = index - 1
        q["shown"] = {**(q.get("shown") or {}), "last": None}
        return await self._show(who, token, q, ack)

    async def press(self, who: Who, token: str, action: str, rest: str) -> Any:
        """A button under one of Chittu's messages: start, later, done, tomorrow, skip, stop, keep going, undo. Returns what to say next."""
        tag, _, idx = rest.partition(":")
        q = await self.current(who)
        if q is None or _tag(q["id"]) != tag:
            return "That was from an earlier list, and I've moved on. Ask me what's on and I'll start again."
        index = int(idx) if idx.isdigit() else 0
        if action in ("s", "g"):
            q["status"], q["shown"] = "active", {**(q.get("shown") or {}), "sitting": 0}
            return await self._show(who, token, q)
        if action == "l":
            q["status"] = "paused"
            await self._save(who, q)
            return "Okay, I'll be here. Say start when you're ready.", [[{"text": "▶️ Start", "callback_data": f"aq:s:{tag}:0"}]]
        if action == "x":
            return await self._stop(who, q, tag)
        if not 0 <= index < len(q["entries"]):
            return "I can't find that one."
        if action == "u":
            return await self._undo(who, token, q, index)
        e = q["entries"][index]
        if e["state"] != SHOWN:
            return "That one is already settled."  # an old button, or a second tap
        if action not in ("d", "t", "k", "m", "p"):
            return "I don't know that button."
        if action in ("m", "p") and not e.get("plan"):
            return "That one can't be moved here."
        ack = await self._settle(who, token, q, index, action)
        return await self._show(who, token, q, ack)

    async def _stop(self, who: Who, q: dict[str, Any], tag: str) -> Any:
        q["status"] = "paused"
        await self._save(who, q)
        left = counts(q["entries"])["left"]
        return f"Stopping here. {left} still on your list; it stays until you ask for something else. Say continue whenever you like.", [[{"text": "▶️ Continue", "callback_data": f"aq:s:{tag}:0"}]]

    # --- a plan, looked at one block at a time ----------------------------------------------------------------------------------------------

    async def review_schedule(self, who: Who, token: str, day: str, blocks: list[dict[str, Any]], *, title: str = "", channel_id: str | None = None) -> dict[str, Any]:
        """A proposed plan for a day: not shown as a list but walked, block by block (1/5 · 08:00–09:30 Draft the rejoinder), each kept, dropped or nudged 30 minutes
        by a button. After the last, the kept ones become one change to approve."""
        loaded = await self._actions.load(who, token)
        rows = []
        for b in blocks:
            ref = f"{b['kind']}:{b['id']}"
            it = loaded.get(ref)
            if it is None or it.status != "open":
                continue
            rows.append({**entry(ref), "plan": {"day": day, "start": int(b["start"]), "duration": int(b.get("duration") or it.duration or 30)}})
        if not rows:
            return {"started": False, "count": 0}
        out = await self.set(who, token, Spec((title or f"Plan for {day_label(day)}")[:60], "replace", rows), "plan", channel_id)
        return {**out, "day": day}

    async def _settle_block(self, who: Who, token: str, q: dict[str, Any], e: dict[str, Any], action: str) -> str:
        """Keep, drop or move one block of a plan being reviewed. A move is checked against what is fixed that day and the other blocks, and refused in words if it clashes."""
        p = e["plan"]
        span = lambda start: f"{clock(start)}–{clock(start + p['duration'])}"  # noqa: E731
        if action == "d":
            e["state"] = DONE
            return f"✅ Kept {span(p['start'])}."
        if action == "k":
            e["state"] = SKIPPED
            return "✖️ Dropped from the plan."
        new = p["start"] + (-30 if action == "m" else 30)
        loaded = await self._actions.load(who, token)
        mine = {x["ref"] for x in q["entries"] if x.get("plan")}
        busy = self._actions.busy_around(loaded, p["day"], mine)
        others = [(x["plan"]["start"], x["plan"]["start"] + x["plan"]["duration"], (loaded.get(x["ref"]).title if loaded.get(x["ref"]) else x["ref"])) for x in q["entries"] if x.get("plan") and x is not e and x["state"] != SKIPPED]  # type: ignore[union-attr]
        problems = check_blocks([{"title": loaded.get(e["ref"]).title if loaded.get(e["ref"]) else e["ref"], "start": new, "duration": p["duration"], "kind": e["ref"].split(":")[0]}], busy + others)  # type: ignore[union-attr]
        if problems:
            return f"⚠️ {problems[0]} It stays at {span(p['start'])}."
        p["start"] = new
        return f"🕐 Moved to {span(new)}."

    async def _finish_plan(self, who: Who, token: str, q: dict[str, Any], ack: str) -> tuple[str, list[list[dict[str, str]]] | None]:
        """The last block has been looked at: what was kept becomes one change, to approve with one tap (and undo with another)."""
        kept = [e for e in q["entries"] if e["state"] == DONE and e.get("plan")]
        q["status"] = "done"
        await self._save(who, q)
        if not kept:
            return f"{ack}\n\nNothing was kept, so there is nothing to book.".strip(), None
        day = kept[0]["plan"]["day"]
        out = await self._actions.schedule_changeset(who, token, day, [{"kind": e["ref"].split(":")[0], "id": e["ref"].split(":", 1)[1], "start": e["plan"]["start"], "duration": e["plan"]["duration"]} for e in kept])
        if not out["changesetId"]:
            return f"{ack}\n\nThose are no longer open, so there is nothing to book.".strip(), None
        dropped = sum(1 for e in q["entries"] if e["state"] == SKIPPED)
        text = f"That's all of them. Book the {out['count']} you kept on {day_label(day)}?" + (f" ({dropped} dropped.)" if dropped else "")
        return f"{ack}\n\n{text}".strip(), [[{"text": "✅ Book them", "callback_data": f"ac:a:{out['changesetId']}"}, {"text": "✖️ Not now", "callback_data": f"ac:x:{out['changesetId']}"}]]

    # --- buttons under a previewed change ---------------------------------------------------------------------------------------------------

    async def change_press(self, who: Who, token: str, action: str, change_id: str, channel_id: str | None = None) -> Any:
        """Apply, drop, look at, or undo a change that was previewed (a move, a clear-out, a plan). Applying twice does nothing; an old or replaced one says so."""
        cs = await self._repo.changeset(who.user_id, change_id)
        if cs is None:
            return "I can't find that change any more."
        if action == "w":  # look at what it would do, one by one; the preview's buttons stay, so it can still be applied afterwards
            out = await self.begin(who, token, changeset_id=change_id, channel_id=channel_id)
            return "" if out.get("started") else "There is nothing in that change."
        if action == "a":
            out = await self._actions.apply_changeset(who, token, change_id, actor="owner")
            if out.get("again"):
                return "That was already done."
            gone = f" ({len(out['gone'])} no longer there.)" if out.get("gone") else ""
            return f"✅ {cs['title']}: done.{gone}", [[{"text": "↩️ Undo", "callback_data": f"ac:u:{change_id}"}]]
        if action == "x":
            await self._actions.drop_changeset(who, change_id)
            return "👍 Left as it was. Nothing changed."
        if action == "u":
            out = await self._actions.undo_changeset(who, token, change_id, actor="owner")
            return f"↩️ Put back: {out['title']}."
        return "I don't know that button."

    # --- words instead of buttons: no model ---------------------------------------------------------------------------------------------

    async def command(self, who: Who, token: str, text: str) -> Any:
        """A plain word from the owner while a list is in force (next, done, tomorrow, stop, continue, how many left, undo): handled here, without a model.
        None when it is not one, or there is no list: the message then goes to Chittu as usual."""
        word = classify_words(text)
        if word is None:
            return None
        q = await self.current(who)
        if q is None:
            return None
        tag = _tag(q["id"])
        front = next((i for i, e in enumerate(q["entries"]) if e["state"] == SHOWN), None)
        if word == "left":
            c = counts(q["entries"])
            if c["left"] == 0:
                return "Everything on your list is settled."
            return f"{c['left']} left of {c['total']} on {q.get('title') or 'your list'}: {c[DONE]} done, {c[MOVED]} moved, {c[SKIPPED]} skipped.", [[{"text": "▶️ Continue", "callback_data": f"aq:s:{tag}:0"}]]
        if word == "resume":
            if counts(q["entries"])["left"] == 0:
                return None
            q["status"], q["shown"] = "active", {**(q.get("shown") or {}), "sitting": 0}
            return await self._show(who, token, q)
        if word == "stop":
            return await self._stop(who, q, tag)
        if word == "undo":
            last = (q.get("shown") or {}).get("last")
            if isinstance(last, int) and 0 <= last < len(q["entries"]) and q["entries"][last]["state"] in (DONE, MOVED, SKIPPED):
                return await self._undo(who, token, q, last)
            return None
        if front is None or q["status"] != "active":
            return None  # nothing is in front of the owner (or the list is paused): "done" means something else, and Chittu can work out what
        if word == "tomorrow" and q["entries"][front].get("plan"):
            return None  # a plan's block is kept or dropped, not moved to another day
        ack = await self._settle(who, token, q, front, {"skip": "k", "done": "d", "tomorrow": "t"}[word])
        return await self._show(who, token, q, ack)

    # --- things that arrive while a list is in force ---------------------------------------------------------------------------------------

    async def add(self, who: Who, ref: str, where: str = "append") -> bool:
        """A new item that needs the owner (a mail judged "now") joins the list in force, at its end, so it is not lost behind the rest. Nothing is sent: the alert
        for it goes out on its own. False when there is no list."""
        q = await self.current(who)
        if q is None:
            return False
        q["entries"] = merge(q["entries"], [entry(ref, "new")], where, q["cursor"])
        if q["status"] == "done":
            q["status"] = "paused"
        await self._save(who, q)
        return True

    async def view(self, who: Who, token: str) -> dict[str, Any]:
        """The list in force, for the app: what it is, where it came from, where it is, and what is next."""
        q = await self.current(who)
        if q is None:
            return {"queue": None}
        loaded = await self._actions.load(who, token)
        self._revalidate(q, loaded)
        c = counts(q["entries"])
        rows = [{"ref": e["ref"], "state": e["state"], "title": (loaded.get(e["ref"]).title if loaded.get(e["ref"]) else None), "note": e.get("note")} for e in q["entries"]]  # type: ignore[union-attr]
        return {"queue": {"id": q["id"], "title": q.get("title") or "", "source": q.get("source") or "ask", "status": q["status"], "counts": c, "entries": rows, "createdAt": q["createdAt"].isoformat() if q.get("createdAt") else None}}


def parse_spec_for(raw: Any, loaded: Any) -> Spec:
    from app.domain.worklist import parse_spec

    return parse_spec(raw, {i.ref: i for i in loaded.items})
