"""Action items: everything the owner has to do, attend or answer, as one kind of thing. Pure: no network, no framework.

A task, a call, a meeting, a hearing, a calendar event, an email that needs a reply: each is an *action item* with a kind, a day and time (or only a due
date), a status, a priority, and whether it is *fixed* (it cannot be moved without the owner saying so: a hearing, a meeting with someone else, an event in
a calendar that is not Stiamo's). The records still live where they always did (tasks, follow-ups, meetings and hearings in the practice service, events
in Google); this module reads them as items, and plans what changing them means. Every change is written to history (see application/actions.py).

An item's *ref* names it for good: "task:ab12", "followUp:cd34", "meeting:...", "hearing:...", "event:...", "mail:...". Chittu and the Telegram queue talk in refs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from app.domain.dayplan import DAY_END, DAY_START, DEFAULT_MEETING, busy_blocks, clock, free_windows, hearing_block, is_open
from app.domain.zones import convert

def label_day(day: str | None) -> str | None:
    """A day as it is said: Thu 26 Nov."""
    if not day:
        return None
    d = date.fromisoformat(day)
    return f"{d.strftime('%a')} {d.day} {d.strftime('%b')}"


KINDS = ("task", "call", "message", "meeting", "hearing", "event", "mail", "step", "deadline")
STATUSES = ("open", "done", "cancelled", "snoozed")
PRIORITY_RANK = {"urgent": 0, "high": 1, "normal": 2, "low": 3}
#: The practice-service table each kind of record lives in.
TABLE_OF = {"task": "tasks", "followUp": "follow-ups", "meeting": "meetings", "hearing": "hearings"}


@dataclass
class ActionItem:
    ref: str
    kind: str
    title: str
    workspace: str = "personal"
    #: The day it is on (planned, scheduled or fixed) and the time of day in minutes after midnight; None when it is only due, not placed.
    day: str | None = None
    start: int | None = None
    duration: int | None = None
    #: The day it is due by, when that is different from where it is placed.
    due: str | None = None
    status: str = "open"
    priority: str = "normal"
    #: Cannot be moved by a bulk move: a hearing, a meeting with someone, an event in a calendar that is not Stiamo's.
    fixed: bool = False
    source: str = "stiamo"
    case_ref: str | None = None
    contact: str | None = None
    notes: str | None = None
    #: What this item is made from: the table and the record, so a change can be written back.
    table: str | None = None
    record: dict[str, Any] = field(default_factory=dict)

    @property
    def when(self) -> str:
        if self.day and self.start is not None:
            return f"{self.day} {clock(self.start)}"
        return self.day or (f"due {self.due}" if self.due else "unscheduled")

    def view(self, today: str | None = None, zone: str | None = None, home: str | None = None) -> dict[str, Any]:
        """What Chittu is shown: the facts, never the raw record. Days come with a label ready to say (Thu 26 Nov), and an overdue item with how many days late
        it is, so he never has to work either out."""
        day, start = self.day, self.start
        if zone and home and zone != home and day and start is not None:  # shown on the clock the owner is on now (stored on the home clock)
            day, start = convert(day, start, home, zone)
        out = {"ref": self.ref, "kind": self.kind, "title": self.title, "workspace": self.workspace, "day": day, "dayLabel": label_day(day), "time": clock(start) if start is not None else None,
               "minutes": self.duration, "due": self.due, "dueLabel": label_day(self.due) if self.due and self.due != self.day else None, "status": self.status, "priority": self.priority, "fixed": self.fixed, "case": self.case_ref,
               "contact": self.contact, "source": self.source}
        there = self.day or self.due
        if today and there and there < today:
            out["daysLate"] = (date.fromisoformat(today) - date.fromisoformat(there)).days
        if self.kind == "mail" and self.notes:
            out["summary"] = str(self.notes)[:220]
        return {k: v for k, v in out.items() if v not in (None, "", False) or k in ("fixed",)}


# --- reading the records as items -------------------------------------------------------------------------------------------------------------------


def _status(r: dict[str, Any]) -> str:
    s = (r.get("outcome") or {}).get("status", "open")
    return s if s in STATUSES else ("done" if s in ("completed", "held", "disposed") else "open")


def from_task(r: dict[str, Any], case_ref: str | None = None) -> ActionItem:
    slot = r.get("slot") or {}
    return ActionItem(f"task:{r['id']}", "task", str(r.get("title") or "Task"), r.get("workspace", "personal"), slot.get("date"), slot.get("start"), slot.get("duration") or r.get("estimate"), r.get("dueDate"), _status(r), r.get("priority", "normal"), False, "stiamo", case_ref, r.get("contact"), r.get("notes") or None, "tasks", r)


def from_follow_up(r: dict[str, Any], case_ref: str | None = None) -> ActionItem:
    due = r.get("due") or {}
    kind = "call" if r.get("channel", "call") in ("call", "visit") else "message"
    return ActionItem(f"followUp:{r['id']}", kind, str(r.get("title") or "Follow-up"), r.get("workspace", "personal"), due.get("date"), due.get("time"), r.get("duration"), due.get("date"), _status(r), "normal", False, "stiamo", case_ref, r.get("contact"), r.get("notes") or None, "follow-ups", r)


def from_meeting(r: dict[str, Any], case_ref: str | None = None) -> ActionItem:
    slot = r.get("slot") or {}
    return ActionItem(f"meeting:{r['id']}", "meeting", str(r.get("title") or "Meeting"), r.get("workspace", "personal"), slot.get("date"), slot.get("start"), slot.get("duration") or DEFAULT_MEETING, None, _status(r), "normal", bool(r.get("counterpart") or r.get("attendees")), "stiamo", case_ref, r.get("counterpart") or r.get("contact"), r.get("purpose"), "meetings", r)


def from_hearing(r: dict[str, Any], case_ref: str | None = None) -> ActionItem:
    b = hearing_block(r)
    return ActionItem(f"hearing:{r['id']}", "hearing", str(r.get("purpose") or "Hearing"), "sb", r.get("date"), b[0] if b else None, (b[1] - b[0]) if b else None, None, _status(r), "high", True, "stiamo", case_ref, None, None, "hearings", r)


def from_event(e: dict[str, Any]) -> ActionItem:
    start = e.get("startMinute")
    end = e.get("endMinute")
    return ActionItem(f"event:{e.get('id')}", "event", str(e.get("title") or "Event"), "personal", e.get("date"), start, (end - start) if (start is not None and end is not None) else None, None, "open", "normal", True, "google", None, None, e.get("location") or None, None, e)


def from_native(r: dict[str, Any]) -> ActionItem:
    """An item Chittu keeps himself (an email that needs the owner, a thing the owner told him): see the action_items table."""
    return ActionItem(f"{r['kind']}:{r['id']}" if r["kind"] != "mail" else f"mail:{r['id']}", r["kind"], r["title"], r.get("workspace", "personal"), r.get("day"), r.get("start"), r.get("duration"), r.get("due"), r.get("status", "open"), r.get("priority", "normal"), False, r.get("source", "chittu"), r.get("case_ref"), r.get("contact"), r.get("notes"), "native", r)


# --- dates, in words ------------------------------------------------------------------------------------------------------------------------------

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december")


def resolve_day(text: str, today: str) -> str | None:
    """A day as the owner would say it, worked out in code (the model never does date arithmetic): "today", "tomorrow", "the day after", "monday",
    "next friday", "in 3 days", "next week" (the coming Monday), "25th November 2026", "25 nov", "25/11/2026", "2026-11-25". None when it can't be read,
    so Chittu asks instead of guessing."""
    t = " ".join(str(text or "").lower().replace(",", " ").split())
    if not t:
        return None
    base = date.fromisoformat(today)
    if t in ("today", "now", "this day"):
        return base.isoformat()
    if t in ("tomorrow", "tmrw", "tomorow"):
        return (base + timedelta(days=1)).isoformat()
    if t in ("day after tomorrow", "the day after tomorrow", "the day after", "day after"):
        return (base + timedelta(days=2)).isoformat()
    if t == "yesterday":
        return (base - timedelta(days=1)).isoformat()
    if m := re.fullmatch(r"in (\d{1,3}) days?", t):
        return (base + timedelta(days=int(m.group(1)))).isoformat()
    if m := re.fullmatch(r"in (\d{1,2}) weeks?", t):
        return (base + timedelta(weeks=int(m.group(1)))).isoformat()
    if t in ("next week", "the coming week"):
        return (base + timedelta(days=7 - base.weekday())).isoformat()
    if m := re.fullmatch(r"(this |next |coming |on )?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)", t):
        want = WEEKDAYS.index(m.group(2))
        ahead = (want - base.weekday()) % 7 or 7  # the same weekday as today means a week from today
        return (base + timedelta(days=ahead)).isoformat()
    if m := re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", t):
        return _ymd(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    if m := re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})", t):
        return _ymd(int(m.group(3)), int(m.group(2)), int(m.group(1)))  # day first, as in India
    if m := re.fullmatch(r"(?:the )?(\d{1,2})(?:st|nd|rd|th)?(?: of)? ([a-z]{3,9})(?: (\d{4}))?", t):
        mon = _month(m.group(2))
        if mon:
            year = int(m.group(3)) if m.group(3) else base.year
            out = _ymd(year, mon, int(m.group(1)))
            if out and not m.group(3) and out < base.isoformat():
                out = _ymd(year + 1, mon, int(m.group(1)))
            return out
    if m := re.fullmatch(r"([a-z]{3,9}) (\d{1,2})(?:st|nd|rd|th)?(?: (\d{4}))?", t):
        mon = _month(m.group(1))
        if mon:
            year = int(m.group(3)) if m.group(3) else base.year
            out = _ymd(year, mon, int(m.group(2)))
            if out and not m.group(3) and out < base.isoformat():
                out = _ymd(year + 1, mon, int(m.group(2)))
            return out
    return None


def _month(word: str) -> int | None:
    w = word[:3]
    return next((i + 1 for i, m in enumerate(MONTHS) if m[:3] == w and m.startswith(word[: len(m)])), None)


def _ymd(y: int, m: int, d: int) -> str | None:
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return None


# --- moving things ----------------------------------------------------------------------------------------------------------------------------------


@dataclass
class Move:
    ref: str
    title: str
    kind: str
    from_day: str | None
    from_start: int | None
    to_day: str
    to_start: int | None
    note: str = ""

    def view(self) -> dict[str, Any]:
        return {"ref": self.ref, "title": self.title, "kind": self.kind, "from": f"{label_day(self.from_day) or 'unplaced'}{' ' + clock(self.from_start) if self.from_start is not None else ''}", "to": f"{label_day(self.to_day)}{' ' + clock(self.to_start) if self.to_start is not None else ''}", **({"note": self.note} if self.note else {})}


@dataclass
class MovePlan:
    target: str
    moves: list[Move] = field(default_factory=list)
    #: (ref, title, why): not moved.
    stays: list[tuple[str, str, str]] = field(default_factory=list)
    #: Could not be placed on the target day for lack of room: they are moved to the day after, or left, as asked.
    overflow: list[tuple[str, str]] = field(default_factory=list)

    def view(self) -> dict[str, Any]:
        return {"target": self.target, "targetLabel": label_day(self.target), "moves": [m.view() for m in self.moves], "staying": [{"ref": r, "title": t, "why": w} for r, t, w in self.stays], "noRoom": [{"ref": r, "title": t} for r, t in self.overflow]}


def pick(items: list[ActionItem], *, day: str | None = None, until: str | None = None, kinds: list[str] | None = None, workspace: str | None = None, priority_below: str | None = None, refs: list[str] | None = None, priority_at_least: str | None = None) -> list[ActionItem]:
    """The open items a request is about. `day` (or `day` to `until`): placed or due in that range, and overdue ones when the range starts today or earlier
    is the caller's choice. `priority_below`: only items strictly less important than that ("non-urgent" is below "urgent")."""
    out = []
    for it in items:
        if it.status != "open":
            continue
        if refs is not None and it.ref not in refs:
            continue
        if kinds and it.kind not in kinds:
            continue
        if workspace and it.workspace != workspace:
            continue
        if priority_at_least and PRIORITY_RANK.get(it.priority, 2) > PRIORITY_RANK.get(priority_at_least, 2):
            continue
        if priority_below and PRIORITY_RANK.get(it.priority, 2) <= PRIORITY_RANK.get(priority_below, 2):
            continue
        if refs is None and day:
            there = it.day or it.due
            hi = until or day
            if not there or not (day <= there <= hi):
                continue
        out.append(it)
    return sorted(out, key=lambda i: (i.day or i.due or "9999", i.start if i.start is not None else 24 * 60, PRIORITY_RANK.get(i.priority, 2)))


def queue_order(items: list[ActionItem], today: str, through: str | None = None) -> list[ActionItem]:
    """What Chittu brings to the owner today, in the order he brings it: first the things at a fixed time (a hearing, a meeting, an event), then urgent items,
    then what is overdue (oldest first), then what is for today by priority. Only open items on, due on, or overdue before today; done ones never come."""
    mine = []
    for it in items:
        if it.status != "open":
            continue
        there = it.day or it.due
        if not there or there > (through or today):
            continue
        mine.append(it)

    def key(it: ActionItem) -> tuple[Any, ...]:
        if through and through > today:  # a range ahead: by day, then the usual order within a day
            return (it.day or it.due or "", 0 if it.priority == "urgent" else 1, it.start if it.start is not None else 24 * 60, PRIORITY_RANK.get(it.priority, 2))
        timed_fixed = it.fixed and it.day == today and it.start is not None
        overdue = (it.day or it.due or today) < today
        return (0 if timed_fixed else 1, it.start if timed_fixed else 0, 0 if it.priority == "urgent" else 1, 0 if overdue else 1, (it.day or it.due or today) if overdue else "", PRIORITY_RANK.get(it.priority, 2), it.start if it.start is not None else 24 * 60)

    return sorted(mine, key=key)


EMOJI = {"task": "📝", "call": "📞", "message": "💬", "meeting": "🤝", "hearing": "⚖️", "event": "📅", "mail": "✉️", "step": "🪜", "deadline": "⏰"}


def plan_move(items: list[ActionItem], target: str, busy_on_target: list[tuple[int, int, str]], *, include_fixed: bool = False, keep_times: bool = True) -> MovePlan:
    """Where each item goes on the target day. Fixed items stay (a hearing is adjourned by a court, not moved by a request; a meeting has other people in it)
    unless the owner said to include them, and items already done or cancelled are skipped. A timed item keeps its time of day when that is free,
    otherwise takes the first free window that fits; an item that is only due simply gets the new due date. What cannot fit the day is reported, not forced."""
    plan = MovePlan(target)
    busy = sorted(busy_on_target)
    for it in items:
        if it.status != "open":
            continue
        if it.kind == "hearing":
            plan.stays.append((it.ref, it.title, "a hearing is set by the court, so it is never moved by a request"))
            continue
        if it.fixed and not include_fixed:
            plan.stays.append((it.ref, it.title, "fixed: it involves other people or someone else's calendar" if it.source != "google" else "an event in a calendar that is not Stiamo's"))
            continue
        if it.day == target and it.start is None and it.due in (None, target):
            plan.stays.append((it.ref, it.title, "already on that day"))
            continue
        if it.start is None:
            plan.moves.append(Move(it.ref, it.title, it.kind, it.day, it.start, target, None, "it has no time of day: only its day changes"))
            continue
        length = it.duration or 30
        free = free_windows(busy)
        clash = next((n for a, b, n in sorted(busy) if it.start < b and a < it.start + length), None)
        if keep_times and clash is None and any(a <= it.start and it.start + length <= b for a, b in free):
            spot = it.start
        else:
            # the nearest free place to where it was (a draft that was at nine goes to eleven, not to six in the morning)
            fits = [max(a, min(it.start, b - length)) for a, b in free if b - a >= length]
            spot = min(fits, key=lambda c: (abs(c - it.start), -c)) if fits else None
        if spot is None:
            plan.overflow.append((it.ref, it.title))
            continue
        busy.append((spot, spot + length, it.title))
        busy.sort()
        plan.moves.append(Move(it.ref, it.title, it.kind, it.day, it.start, target, spot, "" if spot == it.start else (f"{clock(it.start)} clashes with {clash}, so it goes at {clock(spot)}" if clash else f"{clock(it.start)} is not free, so it goes at {clock(spot)}")))
    return plan


def patch_for(it: ActionItem, to_day: str, to_start: int | None) -> dict[str, Any]:
    """The change to write to the record to move this item (only the fields that change)."""
    if it.kind == "task":
        slot = dict(it.record.get("slot") or {})
        patch: dict[str, Any] = {}
        if slot or to_start is not None:
            slot.update({"date": to_day, **({"start": to_start} if to_start is not None else {}), "duration": slot.get("duration") or it.duration or 30})
            patch["slot"] = slot
        if it.due and it.due <= to_day:
            patch["dueDate"] = to_day
        return patch or {"dueDate": to_day}
    if it.kind in ("call", "message"):
        due = dict(it.record.get("due") or {})
        due["date"] = to_day
        if to_start is not None:
            due["time"] = to_start
        return {"due": due}
    if it.kind == "meeting":
        slot = dict(it.record.get("slot") or {})
        slot.update({"date": to_day, **({"start": to_start} if to_start is not None else {})})
        return {"slot": slot}
    if it.kind == "native" or it.table == "native":
        return {"day": to_day, **({"start": to_start} if to_start is not None else {})}
    return {}


def busy_for(day: str, hearings: list[dict[str, Any]], meetings: list[dict[str, Any]], tasks: list[dict[str, Any]], follow_ups: list[dict[str, Any]], calendar: list[dict[str, Any]], skip: set[str]) -> list[tuple[int, int, str]]:
    return busy_blocks(day, hearings, meetings, tasks, follow_ups, skip=skip, calendar=calendar)


__all__ = ["ActionItem", "Move", "MovePlan", "plan_move", "pick", "resolve_day", "patch_for", "from_task", "from_follow_up", "from_meeting", "from_hearing", "from_event", "from_native", "DAY_START", "DAY_END", "is_open"]
