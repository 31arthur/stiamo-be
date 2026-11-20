"""Action items: one place that reads everything the owner has to do as items, changes any of it, and remembers every change.

Reading: tasks, calls and messages, meetings and hearings come from the practice service, events from Google, and what Chittu keeps himself (an email that needs
the owner) from the action_items table. Changing: every change, however small, is a *change set* of one or more operations that records what each record was and
what it becomes; applying one writes the records and the history, and undoing one puts the old values back. Nothing is ever changed without a history row.

Small changes the owner asks for (done, move this, snooze) are applied at once and can be undone; a bulk move is previewed first and applied only on a yes.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from stiamo_common.errors import Conflict, NotFound, ValidationFailed

from app.application.services import Who
from app.domain.actionitems import ActionItem, patch_for, pick, plan_move, resolve_day
from app.domain.actionitems import busy_for as busy_on
from app.domain.actionitems import from_event, from_follow_up, from_hearing, from_meeting, from_native, from_task
from app.domain.llm import AiError
from app.infrastructure.db.actions import SqlActionRepository

STALE = timedelta(days=1)
ZONE = "Asia/Kolkata"
WRITABLE = ("tasks", "follow-ups", "meetings")
OUTCOMES = {"complete": "done", "cancel": "cancelled", "reopen": "open"}


@dataclass
class Loaded:
    items: list[ActionItem] = field(default_factory=list)
    records: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    calendar: list[dict[str, Any]] = field(default_factory=list)

    def get(self, ref: str) -> ActionItem | None:
        return next((i for i in self.items if i.ref == ref), None)


def _iso(d: datetime) -> str:
    return d.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


class ActionService:
    def __init__(self, repo: SqlActionRepository, data_factory: Callable[[str], Any], now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._repo, self._data, self._now = repo, data_factory, now

    def today(self) -> str:
        return self._now().astimezone(ZoneInfo(ZONE)).date().isoformat()

    # --- reading ---------------------------------------------------------------------------------------------------------------------------

    async def load(self, who: Who, token: str, ahead: int = 60) -> Loaded:
        data = self._data(token)
        today = self.today()
        try:
            tasks, follow_ups, meetings = await data.records("tasks"), await data.records("follow-ups"), await data.records("meetings")
            hearings, cases = await data.hearings(), await data.cases()
        except AiError as exc:
            raise ValidationFailed(str(exc)) from exc
        try:
            calendar = await data.calendar_events(today, (date.fromisoformat(today) + timedelta(days=ahead)).isoformat())
        except Exception:  # noqa: BLE001  Google being away is no reason to lose the rest
            calendar = []
        refs = {c["id"]: c.get("reference") for c in cases}

        def case_of(r: dict[str, Any]) -> str | None:
            rel = r.get("related") or r.get("subject") or {}
            return refs.get(r.get("caseId") or (rel.get("id") if rel.get("kind") == "case" else None))

        items = [from_task(r, case_of(r)) for r in tasks] + [from_follow_up(r, case_of(r)) for r in follow_ups] + [from_meeting(r, case_of(r)) for r in meetings] + [from_hearing(r, refs.get(r.get("caseId"))) for r in hearings]
        items += [from_event(e) for e in calendar if e.get("startMinute") is not None or e.get("allDay")]
        items += [from_native(r) for r in await self._repo.items(who.user_id, ["open", "snoozed"])]
        return Loaded(items, {"tasks": tasks, "follow-ups": follow_ups, "meetings": meetings, "hearings": hearings}, calendar)

    async def find(self, who: Who, token: str, *, day: str | None = None, until: str | None = None, kinds: list[str] | None = None, workspace: str | None = None, priority_below: str | None = None, include_overdue: bool = False) -> list[ActionItem]:
        loaded = await self.load(who, token)
        found = pick(loaded.items, day=day, until=until, kinds=kinds, workspace=workspace, priority_below=priority_below)
        if include_overdue and day:
            seen = {i.ref for i in found}
            found += [i for i in pick(loaded.items, kinds=kinds, workspace=workspace, priority_below=priority_below) if i.ref not in seen and (i.due or i.day) and (i.due or i.day) < day]  # type: ignore[operator]
        return found

    # --- history ---------------------------------------------------------------------------------------------------------------------------

    async def _log(self, who: Who, ref: str, event: str, actor: str, title: str, before: Any, after: Any, reason: str = "", changeset_id: str | None = None, run_id: str | None = None) -> None:
        await self._repo.add_event(who.user_id, str(uuid.uuid4()), ref, event, actor, title, before, after, reason, changeset_id, run_id, self._now())

    async def note(self, who: Who, ref: str, event: str, actor: str, title: str, reason: str = "") -> None:
        """Writes a line of history for something that changed nothing in the record (an item was shown, noted or skipped)."""
        await self._log(who, ref, event, actor, title, None, None, reason)

    async def history(self, who: Who, ref: str | None = None, limit: int = 30, hours: int | None = None) -> list[dict[str, Any]]:
        since = self._now() - timedelta(hours=hours) if hours else None
        return await self._repo.history(who.user_id, ref, max(1, min(limit, 200)), since)

    # --- change sets -----------------------------------------------------------------------------------------------------------------------

    async def preview_move(self, who: Who, token: str, *, to: str, day: str | None = None, until: str | None = None, kinds: list[str] | None = None, workspace: str | None = None, priority_below: str | None = None,
                           refs: list[str] | None = None, include_overdue: bool = False, include_fixed: bool = False, actor: str = "chittu", channel_id: str | None = None, why: str = "") -> dict[str, Any]:
        """Works out a move and keeps it as a pending change set: nothing is changed until it is applied. `to` is a day in words; it is resolved here, in code."""
        today = self.today()
        target = resolve_day(to, today)
        if target is None:
            raise ValidationFailed(f"I can't tell which day “{to}” is. Give a date like 2026-11-25, or a day like tomorrow or next Monday.")
        if target < today:
            raise ValidationFailed(f"{target} is in the past. Which day should it go to?")
        source = resolve_day(day, today) if day else None
        last = resolve_day(until, today) if until else None
        if day and source is None:
            raise ValidationFailed(f"I can't tell which day “{day}” is.")
        loaded = await self.load(who, token)
        chosen = pick(loaded.items, day=source, until=last, kinds=kinds, workspace=workspace, priority_below=priority_below, refs=refs)
        if include_overdue and source:
            have = {i.ref for i in chosen}
            chosen += [i for i in pick(loaded.items, kinds=kinds, workspace=workspace, priority_below=priority_below) if i.ref not in have and (i.due or i.day) and (i.due or i.day) < source]  # type: ignore[operator]
        r = loaded.records
        busy = busy_on(target, r["hearings"], r["meetings"], r["tasks"], r["follow-ups"], loaded.calendar, {i.ref for i in chosen})
        plan = plan_move(chosen, target, busy, include_fixed=include_fixed)
        ops = []
        for m in plan.moves:
            it = loaded.get(m.ref)
            if it is None:
                continue
            patch = patch_for(it, m.to_day, m.to_start)
            table = it.table if it.table in (*WRITABLE, "native") else None
            if not patch or table is None:
                plan.stays.append((m.ref, m.title, "this kind of item can't be changed from here yet"))
                continue
            ops.append({"ref": m.ref, "table": table, "id": m.ref.split(":", 1)[1], "title": m.title, "before": {k: it.record.get(k) for k in patch}, "after": patch, "from": m.view()["from"], "to": m.view()["to"]})
        plan.moves = [m for m in plan.moves if any(o["ref"] == m.ref for o in ops)]
        what = f"{len(ops)} item{'s' if len(ops) != 1 else ''} to {target}"
        cs = {"id": str(uuid.uuid4()), "title": f"Move {what}", "status": "pending", "ops": ops, "summary": {**plan.view(), "why": why}, "actor": actor, "channelId": channel_id, "createdAt": self._now()}
        if ops:
            await self._repo.add_changeset(who.user_id, cs)
        return {"changesetId": cs["id"] if ops else None, "title": cs["title"], "willMove": len(ops), **plan.view()}

    async def apply_changeset(self, who: Who, token: str, changeset_id: str, *, actor: str = "owner") -> dict[str, Any]:
        cs = await self._repo.changeset(who.user_id, changeset_id)
        if cs is None:
            raise NotFound("No such change.")
        if cs["status"] == "applied":
            return {"changesetId": changeset_id, "applied": len(cs["ops"]), "again": True}
        if cs["status"] != "pending":
            raise Conflict("That change was dropped or undone, so it can't be applied. Ask for it again.")
        if self._now() - cs["createdAt"] > STALE:
            raise Conflict("That was previewed more than a day ago, and things may have changed. Ask for it again.")
        data = self._data(token)
        done, gone = 0, []
        try:
            for op in cs["ops"]:
                if not await self._write(who, data, op, op["after"], op.get("event") or "moved", cs, actor):
                    gone.append(op["title"])
                    continue
                done += 1
            if any(op["table"] in ("tasks", "follow-ups") for op in cs["ops"]):
                await data.calendar_sync()
        except AiError as exc:
            raise ValidationFailed(str(exc)) from exc
        cs["status"], cs["decidedAt"] = "applied", self._now()
        await self._repo.save_changeset(cs)
        return {"changesetId": changeset_id, "applied": done, "gone": gone}

    async def undo_changeset(self, who: Who, token: str, changeset_id: str | None = None, *, actor: str = "owner") -> dict[str, Any]:
        cs = await (self._repo.changeset(who.user_id, changeset_id) if changeset_id else self._repo.last_applied(who.user_id))
        if cs is None or cs["status"] != "applied":
            raise NotFound("There is nothing to undo.")
        data = self._data(token)
        done = 0
        try:
            for op in cs["ops"]:
                if await self._write(who, data, op, op["before"], "undone", cs, actor):
                    done += 1
            if any(op["table"] in ("tasks", "follow-ups") for op in cs["ops"]):
                await data.calendar_sync()
        except AiError as exc:
            raise ValidationFailed(str(exc)) from exc
        cs["status"], cs["decidedAt"] = "undone", self._now()
        await self._repo.save_changeset(cs)
        return {"changesetId": cs["id"], "undone": done, "title": cs["title"]}

    async def drop_changeset(self, who: Who, changeset_id: str) -> None:
        cs = await self._repo.changeset(who.user_id, changeset_id)
        if cs is None:
            raise NotFound("No such change.")
        if cs["status"] == "pending":
            cs["status"], cs["decidedAt"] = "dropped", self._now()
            await self._repo.save_changeset(cs)

    async def _write(self, who: Who, data: Any, op: dict[str, Any], patch: dict[str, Any], event: str, cs: dict[str, Any], actor: str) -> bool:
        """Writes one operation's fields to the record and the history. False when the record has been deleted since."""
        if op["table"] == "native":
            row = await self._repo.item(who.user_id, op["id"])
            if row is None:
                return False
            row = {**row, **{k: v for k, v in patch.items() if k in row or k in ("day", "start", "status", "due", "duration", "priority")}}
            await self._repo.save_item(who.user_id, row, self._now())
        else:
            current = next((r for r in await data.records(op["table"]) if r.get("id") == op["id"]), None)
            if current is None:
                return False
            record = {**current, **{k: v for k, v in patch.items() if v is not None}}
            for k, v in patch.items():
                if v is None:
                    record.pop(k, None)
            await data.save_record(op["table"], record)
        await self._log(who, op["ref"], event, actor, op.get("title", ""), op["before"] if event != "undone" else op["after"], patch, cs["summary"].get("why") or "", cs["id"])
        return True

    # --- small changes, applied at once and undoable ------------------------------------------------------------------------------------

    async def command(self, who: Who, token: str, ref: str, action: str, *, to: str | None = None, start: int | None = None, actor: str = "chittu", reason: str = "", note: str | None = None, include_fixed: bool = False) -> dict[str, Any]:
        """complete, cancel, reopen, move (to a day, optionally a time) or snooze (to a day) one item. Applied now; the response says how to undo."""
        loaded = await self.load(who, token)
        it = loaded.get(ref)
        if it is None:
            raise NotFound(f"I can't find an item called {ref}. Look it up first with find_items.")
        if it.table not in (*WRITABLE, "native"):
            raise ValidationFailed(f"“{it.title}” is {'an event in a calendar that is not Stiamo\'s' if it.source == 'google' else 'a hearing'}; it can't be changed from here." + (" Its outcome is recorded on the case." if it.kind == "hearing" else ""))
        today = self.today()
        if action in OUTCOMES:
            outcome = {**(it.record.get("outcome") or {}), "status": OUTCOMES[action], "recordedAt": _iso(self._now()), **({"comment": note} if note else {})}
            patch: dict[str, Any] = {"status": OUTCOMES[action]} if it.table == "native" else {"outcome": outcome}
            before = {k: it.record.get(k) for k in patch}
            title = f"{'Done' if action == 'complete' else 'Cancelled' if action == 'cancel' else 'Reopened'}: {it.title}"
        elif action in ("move", "snooze"):
            day = resolve_day(to or "", today)
            if day is None or day < today:
                raise ValidationFailed(f"I can't tell which day “{to}” is, or it is in the past. Give a date like 2026-11-25, or tomorrow, or next Monday.")
            if it.fixed and action == "move" and not include_fixed:
                raise ValidationFailed(f"“{it.title}” is fixed: it involves other people. If you really want it moved, say so, and I will move it and tell the others.")
            patch = patch_for(it, day, start if start is not None else (it.start if action == "move" else None))
            if not patch:
                raise ValidationFailed(f"“{it.title}” can't be moved from here.")
            before = {k: it.record.get(k) for k in patch}
            title = f"{'Snoozed' if action == 'snooze' else 'Moved'} to {day}: {it.title}"
        else:
            raise ValidationFailed("The action is complete, cancel, reopen, move or snooze.")
        event = {"complete": "completed", "cancel": "cancelled", "reopen": "reopened", "snooze": "snoozed", "move": "moved"}[action]
        op = {"ref": ref, "table": it.table, "id": ref.split(":", 1)[1], "title": it.title, "before": before, "after": patch, "from": it.when, "to": title, "event": event}
        cs = {"id": str(uuid.uuid4()), "title": title, "status": "pending", "ops": [op], "summary": {"why": reason}, "actor": actor, "channelId": None, "createdAt": self._now()}
        await self._repo.add_changeset(who.user_id, cs)
        out = await self.apply_changeset(who, token, cs["id"], actor=actor)
        return {"ok": True, "item": it.title, "ref": ref, "did": title, "changesetId": cs["id"], "undo": "Say undo to put it back." if out.get("applied") else "", **({"note": "It was deleted meanwhile."} if out.get("gone") else {})}

    # --- items Chittu keeps himself ----------------------------------------------------------------------------------------------------------

    async def create(self, who: Who, *, kind: str, title: str, day: str | None = None, start: int | None = None, duration: int | None = None, due: str | None = None, priority: str = "normal",
                     workspace: str = "personal", source: str = "chittu", links: dict[str, Any] | None = None, notes: str | None = None, actor: str = "chittu", item_id: str | None = None, reason: str = "") -> dict[str, Any]:
        """A new native item (an email that needs the owner, a thing the owner told Chittu). An id given twice is the same item: nothing is doubled."""
        iid = item_id or str(uuid.uuid4())
        existing = await self._repo.item(who.user_id, iid)
        if existing is not None:
            return existing
        row = {"id": iid, "kind": kind, "title": title[:300], "workspace": workspace, "day": day, "start": start, "duration": duration, "due": due, "status": "open", "priority": priority, "source": source, "links": links or {}, "notes": notes}
        await self._repo.save_item(who.user_id, row, self._now())
        ref = f"{kind}:{iid}"
        await self._log(who, ref, "created", actor, title, None, {k: v for k, v in row.items() if v not in (None, {}, "")}, reason)
        return row

    async def record_mail(self, who: Who, *, agent_name: str, workflow_id: str, item_id: str, account: str, uid: int, folder: str, subject: str, sender: str, summary: str | None, priority: str, label: str, due: str | None, workspace: str) -> dict[str, Any]:
        """An email an agent judged to need the owner: kept as an action item, once, with where it is in the mailbox. now is high priority, digest normal."""
        iid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{who.user_id}|{workflow_id}|{item_id}"))
        return await self.create(
            who, kind="mail", title=f"{subject or '(no subject)'} · {sender}"[:300], due=due, priority="high" if (label == "now" or priority == "high") else "normal", workspace=workspace, source=agent_name,
            links={"mail": {"account": account, "uid": uid, "folder": folder, "agent": workflow_id, "item": item_id, "sender": sender, "subject": subject}, "label": label}, notes=summary, actor=agent_name, item_id=iid, reason=f"judged {label}",
        )
