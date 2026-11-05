"""A day, as the planner reads it: what is already fixed, what is free, and whether a proposed schedule fits. Pure: no network, no framework.

Times are minutes after midnight, the way the app stores them.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

#: The working day the planner fits things into: it starts at 9 and, for most days, ends at 9 in the evening.
DAY_START = 9 * 60
DAY_END = 21 * 60
#: Earliest and latest a block may be proposed, for the odd early or late job.
EARLIEST = 6 * 60
LATEST = 22 * 60
#: How long a hearing or meeting with no length is taken to take.
DEFAULT_HEARING = 60
DEFAULT_MEETING = 30
MIN_WINDOW = 15


def clock(minutes: int | None) -> str:
    return "—" if minutes is None else f"{minutes // 60:02d}:{minutes % 60:02d}"


def tomorrow(today: str) -> str:
    return (date.fromisoformat(today) + timedelta(days=1)).isoformat()


def is_open(record: dict[str, Any]) -> bool:
    return (record.get("outcome") or {}).get("status", "open") == "open"


def hearing_block(h: dict[str, Any]) -> tuple[int, int] | None:
    start = h.get("time")
    return None if start is None else (int(start), int(start) + int(h.get("duration") or DEFAULT_HEARING))


def meeting_block(m: dict[str, Any]) -> tuple[int, int] | None:
    slot = m.get("slot") or {}
    if slot.get("start") is None:
        return None
    return int(slot["start"]), int(slot["start"]) + int(slot.get("duration") or DEFAULT_MEETING)


def hearings_on(day: str, hearings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted((h for h in hearings if h.get("date") == day and is_open(h)), key=lambda h: h.get("time") if h.get("time") is not None else 24 * 60)


def meetings_on(day: str, meetings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted((m for m in meetings if (m.get("slot") or {}).get("date") == day and is_open(m)), key=lambda m: (m.get("slot") or {}).get("start", 24 * 60))


def tasks_on(day: str, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Open tasks planned for the day (a time block) or due on it."""
    return [t for t in tasks if is_open(t) and ((t.get("slot") or {}).get("date") == day or t.get("dueDate") == day)]


def follow_ups_on(day: str, follow_ups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted((f for f in follow_ups if is_open(f) and (f.get("due") or {}).get("date") == day), key=lambda f: (f.get("due") or {}).get("time", 24 * 60))


def overdue_tasks(day: str, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Open, past due, and not already planned for the day or later."""
    return [t for t in tasks if is_open(t) and t.get("dueDate") and t["dueDate"] < day and not ((t.get("slot") or {}).get("date", "") >= day)]


def overdue_follow_ups(day: str, follow_ups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [f for f in follow_ups if is_open(f) and (f.get("due") or {}).get("date") and f["due"]["date"] < day]


def busy_blocks(day: str, hearings: list[dict[str, Any]], meetings: list[dict[str, Any]], tasks: list[dict[str, Any]], follow_ups: list[dict[str, Any]], *, skip: set[str] | None = None, calendar: list[dict[str, Any]] | None = None) -> list[tuple[int, int, str]]:
    """Every stretch of the day already taken: hearings, meetings, events in the person's Google calendars, and tasks or follow-ups with a time
    (except those named in `skip`). An event that runs over midnight counts on the day it starts. All-day events don't take time."""
    skip = skip or set()
    out: list[tuple[int, int, str]] = []
    for h in hearings_on(day, hearings):
        if (b := hearing_block(h)) and f"hearing:{h.get('id')}" not in skip:
            out.append((*b, f"hearing: {h.get('purpose') or 'hearing'}"))
    for m in meetings_on(day, meetings):
        if (b := meeting_block(m)) and f"meeting:{m.get('id')}" not in skip:
            out.append((*b, f"meeting: {m.get('title') or 'meeting'}"))
    for t in tasks:
        slot = t.get("slot") or {}
        if is_open(t) and slot.get("date") == day and slot.get("start") is not None and f"task:{t.get('id')}" not in skip:
            out.append((int(slot["start"]), int(slot["start"]) + int(slot.get("duration") or t.get("estimate") or 30), f"task: {t.get('title')}"))
    for f in follow_ups:
        due = f.get("due") or {}
        if is_open(f) and due.get("date") == day and due.get("time") is not None and f"followUp:{f.get('id')}" not in skip:
            out.append((int(due["time"]), int(due["time"]) + int(f.get("duration") or 15), f"follow-up: {f.get('title')}"))
    for e in calendar_on(day, calendar or []):
        start = int(e["startMinute"])
        out.append((start, max(start + 5, int(e.get("endMinute") or start + DEFAULT_MEETING)), f"calendar: {e.get('title') or 'event'}"))
    return sorted(out)


def calendar_on(day: str, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Google events with a time of day that start on the day."""
    return sorted((e for e in events if not e.get("allDay") and e.get("date") == day and e.get("startMinute") is not None), key=lambda e: e["startMinute"])


def all_day_on(day: str, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in events if e.get("allDay") and e.get("date", "") <= day <= e.get("endDate", e.get("date", ""))]


def free_windows(busy: list[tuple[int, int, str]], start: int = DAY_START, end: int = DAY_END) -> list[tuple[int, int]]:
    """The gaps between what is already fixed, inside the working day, long enough to use."""
    windows: list[tuple[int, int]] = []
    cursor = start
    for b_start, b_end, _ in sorted(busy):
        if b_start - cursor >= MIN_WINDOW:
            windows.append((cursor, min(b_start, end)))
        cursor = max(cursor, b_end)
    if end - cursor >= MIN_WINDOW:
        windows.append((cursor, end))
    return [(a, b) for a, b in windows if b - a >= MIN_WINDOW]


def check_blocks(blocks: list[dict[str, Any]], busy: list[tuple[int, int, str]]) -> list[str]:
    """Why a proposed schedule doesn't fit, in words the model can act on. Empty: it fits."""
    problems: list[str] = []
    placed: list[tuple[int, int, str]] = []
    for b in blocks:
        start, length, name = b["start"], b["duration"], b["title"]
        if length < 5 or length > 8 * 60:
            problems.append(f"“{name}”: a block must be between 5 minutes and 8 hours.")
            continue
        if start < EARLIEST or start + length > LATEST:
            problems.append(f"“{name}” ({clock(start)}–{clock(start + length)}) is outside {clock(EARLIEST)}–{clock(LATEST)}.")
            continue
        for o_start, o_end, o_name in [*busy, *placed]:
            if start < o_end and o_start < start + length:
                problems.append(f"“{name}” ({clock(start)}–{clock(start + length)}) overlaps {o_name} ({clock(o_start)}–{clock(o_end)}).")
                break
        placed.append((start, start + length, name))
    return problems


SECTION_TITLES = {"fixed": "Fixed", "prepare": "Prepare", "calls": "Calls to make", "work": "Work to do", "attention": "Needs your attention", "later": "Coming up"}


def briefing_text(card: dict[str, Any]) -> str:
    """A briefing card as plain text, for where cards can't be shown (a phone's chat)."""
    lines = [str(card.get("headline") or "").strip()]
    if card.get("summary"):
        lines.append(str(card["summary"]).strip())
    for section in card.get("sections", []):
        items = section.get("items", [])
        if not items:
            continue
        lines += ["", str(section.get("title") or SECTION_TITLES.get(section.get("kind"), "")).upper()]
        for i in items:
            when = f"{i['time']} " if i.get("time") else ""
            note = f" ({i['note']})" if i.get("note") else ""
            lines.append(f"• {when}{i.get('title') or i.get('note') or ''}{note if i.get('title') else ''}")
    return "\n".join(x for x in lines if x is not None).strip()
