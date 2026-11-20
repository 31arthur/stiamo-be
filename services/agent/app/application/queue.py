"""Chittu on Telegram: the day's things, one at a time.

In the morning (06:00) he says good morning and how many things need the owner, and asks whether to start. On *Start* he brings the first one, with buttons that
fit what it is; each time one is settled he brings the next, and says how far along they are. After ten he asks whether to go on. At night (22:00) he says
good night with what got done, what moved and what is still open. The state of all of it is kept (the action_queues table), so a button pressed an hour later,
or twice, does the right thing, and every step is in the history of the item it was about.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.application.actions import ZONE, ActionService
from app.application.services import Who
from app.domain.actionitems import EMOJI, ActionItem, queue_order, resolve_day
from app.domain.chittu import SITTING, good_morning, good_night
from app.domain.dayplan import clock, day_label
from app.infrastructure.db.actions import SqlActionRepository

log = logging.getLogger(__name__)

#: Sends a message (text, buttons) to the owner's phone.
Send = Callable[[Who, str, list[list[dict[str, str]]] | None], Awaitable[int]]
FIXED_KINDS = ("hearing", "meeting", "event")


def _tag(queue_id: str) -> str:
    return queue_id.replace("-", "")[:8]


def _buttons(kind: str, fixed: bool, tag: str, index: int, at_limit: bool = False) -> list[list[dict[str, str]]]:
    d = lambda action: f"aq:{action}:{tag}:{index}"  # noqa: E731  (64 bytes is Telegram's limit)
    if at_limit:
        return [[{"text": "▶️ Keep going", "callback_data": d("g")}, {"text": "⏹ Enough for now", "callback_data": d("x")}]]
    if fixed or kind in FIXED_KINDS:
        return [[{"text": "✔️ Noted", "callback_data": d("d")}, {"text": "⏭ Next", "callback_data": d("k")}], [{"text": "⏹ Stop", "callback_data": d("x")}]]
    if kind == "mail":
        return [[{"text": "✅ Handled", "callback_data": d("d")}, {"text": "😴 Tomorrow", "callback_data": d("t")}], [{"text": "⏭ Skip", "callback_data": d("k")}, {"text": "⏹ Stop", "callback_data": d("x")}]]
    return [[{"text": "✅ Done", "callback_data": d("d")}, {"text": "🔁 Tomorrow", "callback_data": d("t")}], [{"text": "⏭ Skip", "callback_data": d("k")}, {"text": "⏹ Stop", "callback_data": d("x")}]]


def item_text(it: ActionItem, position: int, total: int, today: str) -> str:
    """One item, as Chittu says it: where we are, what it is, when, and why it matters. Short."""
    when = None
    if it.day and it.start is not None:
        when = ("Today " if it.day == today else f"{day_label(it.day)} ") + clock(it.start)
    elif (it.day or it.due or "") and (it.day or it.due) < today:  # type: ignore[operator]
        when = f"Overdue since {day_label(it.day or it.due)}"  # type: ignore[arg-type]
    elif it.due:
        when = f"Due {'today' if it.due == today else day_label(it.due)}"
    bits = [b for b in (when, it.priority if it.priority in ("urgent", "high") else None, it.case_ref, it.contact) if b]
    lines = [f"{position}/{total} · {EMOJI.get(it.kind, '•')} {it.title}"]
    if bits:
        lines.append(" · ".join(str(b) for b in bits))
    if it.kind == "mail" and it.notes:
        lines.append(str(it.notes)[:200])
    return "\n".join(lines)


class QueueService:
    def __init__(self, repo: SqlActionRepository, actions: ActionService, send: Send, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._repo, self._actions, self._send, self._now = repo, actions, send, now

    def _today(self) -> str:
        return self._now().astimezone(ZoneInfo(ZONE)).date().isoformat()

    @staticmethod
    def _owner(who: Who) -> str:
        return (who.name.split() or ["there"])[0].title()

    async def _items(self, who: Who, token: str) -> list[ActionItem]:
        return queue_order((await self._actions.load(who, token)).items, self._today())

    # --- the day opens and closes ----------------------------------------------------------------------------------------------------------

    async def morning(self, who: Who, token: str) -> dict[str, Any]:
        """Good morning: how many things need the owner today, and whether to start. Nothing is brought until they say so."""
        today = self._today()
        old = await self._repo.queue(who.user_id, statuses=["waiting", "active", "paused"])
        if old and old["day"] == today and old["status"] in ("waiting", "active"):
            return {"queueId": old["id"], "again": True}  # this morning's hello already went (a restart, a second trigger)
        if old:
            old["status"], old["updatedAt"] = "done", self._now()
            await self._repo.save_queue(who.user_id, old)
        items = await self._items(who, token)
        entries = [{"ref": i.ref, "state": "pending"} for i in items]
        row = {"id": str(uuid.uuid4()), "day": today, "status": "waiting" if entries else "done", "entries": entries, "cursor": 0, "shown": {"sitting": 0}, "createdAt": self._now(), "updatedAt": self._now()}
        await self._repo.save_queue(who.user_id, row)
        first = items[0].title if items else None
        buttons = [[{"text": "▶️ Start", "callback_data": f"aq:s:{_tag(row['id'])}:0"}, {"text": "⏰ Later", "callback_data": f"aq:l:{_tag(row['id'])}:0"}]] if entries else None
        await self._send(who, good_morning(self._owner(who), today, len(entries), first), buttons)
        return {"queueId": row["id"], "count": len(entries)}

    async def begin(self, who: Who, token: str, *, day: str | None = None, until: str | None = None, kinds: list[str] | None = None, workspace: str | None = None, priority_below: str | None = None,
                    priority_at_least: str | None = None, overdue_only: bool = False, label: str = "") -> dict[str, Any]:
        """Whatever the owner asked to see (today, a day, a week, only calls, only what is overdue): never a list, always the same one-at-a-time walk as the
        morning, numbered 1/10. A short line says how many, then the first item comes with its buttons, and each one settled brings the next. Replaces a list that
        was waiting or paused."""
        today = self._today()
        old = await self._repo.queue(who.user_id, statuses=["waiting", "active", "paused"])
        if old:
            old["status"], old["updatedAt"] = "done", self._now()
            await self._repo.save_queue(who.user_id, old)
        loaded = await self._actions.load(who, token)
        from app.domain.actionitems import pick

        base = pick(loaded.items, kinds=kinds, workspace=workspace, priority_below=priority_below, priority_at_least=priority_at_least)
        through = until or day or today
        if overdue_only:
            items = queue_order([i for i in base if (i.day or i.due) and (i.day or i.due) < today], today)  # type: ignore[operator]
        else:
            items = queue_order(base, today, through)
            if day and day > today:  # a day or a week ahead: not what was left over from before
                items = [i for i in items if (i.day or i.due or "") >= day]
        when = through
        if not items:
            return {"started": False, "count": 0}
        row = {"id": str(uuid.uuid4()), "day": today, "status": "active", "entries": [{"ref": i.ref, "state": "pending"} for i in items], "cursor": 0, "shown": {"sitting": 0}, "createdAt": self._now(), "updatedAt": self._now()}
        await self._repo.save_queue(who.user_id, row)
        many = "one thing" if len(items) == 1 else f"{len(items)} things"
        scope = label or ("your day" if when == today and not kinds and not priority_below and not priority_at_least else day_label(when) if when != today else "today")
        await self._send(who, f"Here's {scope}, {self._owner(who)}: {many}. One at a time.", None)
        text, buttons = await self._show(who, token, row)
        await self._send(who, text, buttons)
        return {"started": True, "count": len(items), "first": items[0].title}

    async def remind(self, who: Who, token: str) -> dict[str, Any]:
        """If the owner has not answered the good morning, ask once more, gently. Never a third time."""
        q = await self._repo.queue(who.user_id, statuses=["waiting"])
        if q is None or q["day"] != self._today() or (q.get("shown") or {}).get("reminded"):
            return {"reminded": False}
        left = sum(1 for e in q["entries"] if e["state"] == "pending")
        if not left:
            return {"reminded": False}
        q["shown"] = {**(q.get("shown") or {}), "reminded": True}
        await self._save(who, q)
        many = "one thing" if left == 1 else f"{left} things"
        await self._send(who, f"Still here whenever you're ready: {many}.", [[{"text": "▶️ Start", "callback_data": f"aq:s:{_tag(q['id'])}:0"}, {"text": "⏰ Later", "callback_data": f"aq:l:{_tag(q['id'])}:0"}]])
        return {"reminded": True}

    async def night(self, who: Who, token: str) -> dict[str, Any]:
        """Good night: what got done today, what moved, what is still open, and what tomorrow opens with. Closes the queue."""
        today = self._today()
        since = datetime.combine(date.fromisoformat(today), datetime.min.time(), tzinfo=ZoneInfo(ZONE)).astimezone(UTC)
        events = await self._repo.history(who.user_id, None, 500, since)
        done = len({e["ref"] for e in events if e["event"] == "completed"})
        moved = len({e["ref"] for e in events if e["event"] in ("moved", "snoozed")})
        loaded = await self._actions.load(who, token)
        left = len(queue_order(loaded.items, today))
        tomorrow = resolve_day("tomorrow", today)
        nxt = sorted((i for i in loaded.items if i.status == "open" and i.day == tomorrow), key=lambda i: (i.start if i.start is not None else 24 * 60))
        q = await self._repo.queue(who.user_id, statuses=["waiting", "active", "paused"])
        if q:
            q["status"], q["updatedAt"] = "done", self._now()
            await self._repo.save_queue(who.user_id, q)
        await self._send(who, good_night(self._owner(who), done, moved, left, f"{nxt[0].title}" + (f" at {clock(nxt[0].start)}" if nxt[0].start is not None else "") if nxt else None), None)
        return {"done": done, "moved": moved, "left": left}

    # --- one at a time ---------------------------------------------------------------------------------------------------------------------

    async def _show(self, who: Who, token: str, q: dict[str, Any], ack: str = "") -> tuple[str, list[list[dict[str, str]]] | None]:
        """The next pending item as a message with its buttons, or the closing words when none is left. Saves where the queue has got to."""
        today = self._today()
        loaded = await self._actions.load(who, token)
        total = len(q["entries"])
        sitting = int((q.get("shown") or {}).get("sitting", 0))
        for i, e in enumerate(q["entries"]):
            if e["state"] != "pending":
                continue
            it = loaded.get(e["ref"])
            if it is None or it.status != "open":  # settled somewhere else since the morning
                e["state"] = "gone"
                continue
            if sitting >= SITTING:
                q["entries"][i]["state"] = "pending"
                await self._save(who, q)
                left = sum(1 for x in q["entries"] if x["state"] == "pending")
                return (f"{ack}\n\nThat's {sitting} so far. {left} more to go. Keep going?".strip(), _buttons("task", False, _tag(q["id"]), i, at_limit=True))
            e["state"], q["cursor"] = "shown", i
            q["shown"] = {**(q.get("shown") or {}), "sitting": sitting + 1, "index": i}
            await self._save(who, q)
            await self._actions.note(who, e["ref"], "queued", "chittu", it.title, "brought to the owner on Telegram")
            return (f"{ack}\n\n{item_text(it, sum(1 for x in q['entries'][: i + 1] if x['state'] != 'gone'), sum(1 for x in q['entries'] if x['state'] != 'gone'), today)}".strip(), _buttons(it.kind, it.fixed, _tag(q["id"]), i))
        q["status"] = "done"
        await self._save(who, q)
        counts = {s: sum(1 for x in q["entries"] if x["state"] == s) for s in ("done", "moved", "skipped")}
        bits = [f"{n} {w}" for w, n in (("done", counts["done"]), ("moved", counts["moved"]), ("skipped", counts["skipped"])) if n]
        return (f"{ack}\n\n✅ That's everything for now" + (f" ({', '.join(bits)})." if bits else ".")).strip(), None

    async def _save(self, who: Who, q: dict[str, Any]) -> None:
        q["updatedAt"] = self._now()
        await self._repo.save_queue(who.user_id, q)

    async def press(self, who: Who, token: str, action: str, rest: str) -> str | tuple[str, list[list[dict[str, str]]] | None]:
        """A button under one of Chittu's messages: start, later, done, tomorrow, skip, stop, keep going. Returns what to say next."""
        tag, _, idx = rest.partition(":")
        q = await self._repo.queue(who.user_id, statuses=["waiting", "active", "paused"])
        if q is None or _tag(q["id"]) != tag:
            return "That was from an earlier list. Ask me what's on today and I'll start again."
        index = int(idx) if idx.isdigit() else 0
        if action == "s":
            q["status"] = "active"
            q["shown"] = {"sitting": 0}
            return await self._show(who, token, q)
        if action == "l":
            q["status"] = "paused"
            await self._save(who, q)
            return "Okay, I'll be here. Say start when you're ready.", [[{"text": "▶️ Start", "callback_data": f"aq:s:{tag}:0"}]]
        if action == "x":
            q["status"] = "done"
            await self._save(who, q)
            return "Stopping here. Anything left stays on your list. I'm around whenever you want to go on."
        if action == "g":
            q["status"], q["shown"] = "active", {"sitting": 0}
            return await self._show(who, token, q)
        if not 0 <= index < len(q["entries"]):
            return "I can't find that one."
        e = q["entries"][index]
        if e["state"] != "shown":
            return "That one is already settled."  # an old button, or a second tap
        ref, ack = e["ref"], ""
        loaded_title = ref
        try:
            if action == "d":
                if ref.split(":")[0] in FIXED_KINDS:
                    await self._actions.note(who, ref, "resolved", "owner", ref, "noted on Telegram")
                    ack = "✔️ Noted."
                else:
                    out = await self._actions.command(who, token, ref, "complete", actor="owner", reason="done on Telegram")
                    ack = f"✅ {out['item']}: done."
                e["state"] = "done"
            elif action == "t":
                out = await self._actions.command(who, token, ref, "snooze" if ref.startswith("mail:") else "move", to="tomorrow", actor="owner", reason="moved to tomorrow on Telegram")
                ack = f"🔁 {out['item']}: tomorrow."
                e["state"] = "moved"
            elif action == "k":
                await self._actions.note(who, ref, "skipped", "owner", loaded_title, "skipped on Telegram")
                ack = "⏭ Skipped."
                e["state"] = "skipped"
            else:
                return "I don't know that button."
        except Exception as exc:  # noqa: BLE001  a button must always answer, and the item stays to try again
            log.warning("could not settle %s from Telegram: %s", ref, exc)
            e["state"] = "pending"
            ack = f"⚠️ {getattr(exc, 'message', None) or 'That did not work.'} Skipping it for now."
            e["state"] = "skipped"
        return await self._show(who, token, q, ack)
