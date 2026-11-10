"""The agenda pack: what a day holds (hearings, meetings, calls, tasks), how much of it is free, what is coming, and a schedule proposal."""

from datetime import date, timedelta
from typing import Any

from app.application.runner import Tool, ToolOutput
from app.application.toolpacks import PackContext, ToolPack
from app.application.toolpacks.common import limit, text
from app.domain.dayplan import (
    DAY_END,
    DAY_START,
    SECTION_TITLES,
    all_day_on,
    busy_blocks,
    calendar_on,
    call_only_blocks,
    check_blocks,
    clock,
    follow_ups_on,
    free_windows,
    hearings_on,
    is_open,
    meetings_on,
    overdue_follow_ups,
    overdue_tasks,
    tasks_on,
    tomorrow,
)
from app.domain.llm import ToolSpec

OBJ = "object"
PRIORITY = {"urgent": 0, "high": 1, "normal": 2, "low": 3}
MAX_AHEAD = 30


def _day(a: dict[str, Any], default: str) -> str:
    raw = text(a.get("date"))
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError:
        return default


def _compact(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v not in (None, "", [], {})}


def _span(start: int | None, length: int | None) -> str | None:
    return None if start is None else f"{clock(start)}–{clock(start + (length or 0))}" if length else clock(start)


def _present(typ: str, r: dict[str, Any], day: str, case_ref, cases: dict[str, Any], plans: dict[str, Any]) -> dict[str, Any]:
    """One thing as the card shows it, from the record itself, so what is shown is never what a model remembered."""
    base: dict[str, Any] = {"type": typ, "id": r.get("id")}
    if typ == "hearing":
        c = cases.get(r.get("caseId") or "") or {}
        return {**base, "title": r.get("purpose") or "Hearing", "time": _span(r.get("time"), r.get("duration")), "caseRef": c.get("reference"), "place": r.get("forum"), "caseId": r.get("caseId")}
    if typ == "meeting":
        slot = r.get("slot") or {}
        return {**base, "title": r.get("title"), "time": _span(slot.get("start"), slot.get("duration") or 30), "place": r.get("location") or r.get("counterpart"), "caseRef": case_ref(r)}
    if typ == "task":
        slot = r.get("slot") or {}
        return {**base, "title": r.get("title"), "time": _span(slot.get("start"), slot.get("duration")) if slot.get("date") == day else None, "priority": r.get("priority"), "due": r.get("dueDate"), "minutes": r.get("estimate"), "caseRef": case_ref(r)}
    if typ == "followUp":
        due, tries = r.get("due") or {}, r.get("attempts") or []
        return {**base, "title": r.get("title"), "time": _span(due.get("time"), r.get("duration")) if due.get("date") == day else None, "channel": r.get("channel"), "contact": r.get("contact"), "due": due.get("date"), "minutes": r.get("duration"), "tries": len(tries) or None, "caseRef": case_ref(r)}
    return {**base, "title": r.get("title"), "time": _span(r.get("startMinute"), (r.get("endMinute") or 0) - (r.get("startMinute") or 0)) if not r.get("allDay") else "All day", "place": r.get("location") or r.get("calendar"), "link": r.get("link")}


class AgendaPack(ToolPack):
    name = "agenda"
    variables = {
        "tomorrow": "Tomorrow's date.",
        "day_ends": "When the working day usually ends (21:00).",
    }

    async def context(self, ctx: PackContext) -> dict[str, Any]:
        return {"tomorrow": tomorrow(ctx.today), "day_ends": clock(DAY_END)}

    async def tools(self, ctx: PackContext) -> list[Tool]:
        data, today = ctx.data, ctx.today
        cases = {c["id"]: c for c in await data.cases()}
        hearings, meetings = await data.hearings(), await data.records("meetings")
        tasks, follow_ups = await data.records("tasks"), await data.records("follow-ups")
        plans = {p["id"]: p for p in await data.records("plans")}
        # What is in the person's Google calendars: busy time the plan must work around, though Stiamo can't move it.
        calendar = await data.calendar_events(today, (date.fromisoformat(today) + timedelta(days=60)).isoformat())

        def case_ref(r: dict[str, Any]) -> str | None:
            rel = r.get("related") or r.get("subject") or {}
            c = cases.get(r.get("caseId") or (rel.get("id") if rel.get("kind") == "case" else None))
            if c is None and r.get("planId"):
                c = next((cases[i] for i in (plans.get(r["planId"]) or {}).get("caseIds", []) if i in cases), None)
            return c.get("reference") if c else None

        def hearing_card(h: dict[str, Any]) -> dict[str, Any]:
            c = cases.get(h.get("caseId") or "") or {}
            return _compact({"id": h["id"], "time": clock(h.get("time")) if h.get("time") is not None else "time not set", "forum": h.get("forum"), "purpose": h.get("purpose"), "case": c.get("reference"), "title": c.get("title"), "alsoCases": [cases[i].get("reference") for i in h.get("alsoCaseIds") or [] if i in cases]})

        def meeting_card(m: dict[str, Any]) -> dict[str, Any]:
            slot = m.get("slot") or {}
            return _compact({"id": m["id"], "time": f"{clock(slot.get('start'))}–{clock((slot.get('start') or 0) + (slot.get('duration') or 30))}", "title": m.get("title"), "with": m.get("counterpart"), "where": m.get("location") or m.get("mode"), "case": case_ref(m)})

        def follow_up_card(f: dict[str, Any]) -> dict[str, Any]:
            attempts = f.get("attempts") or []
            due = f.get("due") or {}
            last = attempts[-1] if attempts else {}
            return _compact({
                "id": f["id"], "title": f.get("title"), "channel": f.get("channel"), "contact": f.get("contact"), "due": due.get("date"), "at": clock(due["time"]) if due.get("time") is not None else None,
                "minutes": f.get("duration"), "tries": len(attempts), "lastTry": _compact({"date": last.get("date"), "at": clock(last.get("minute")) if last.get("minute") is not None else None, "result": last.get("result"), "promisedFor": last.get("promisedFor")}), "notes": f.get("notes"), "case": case_ref(f),
            })

        def task_card(t: dict[str, Any]) -> dict[str, Any]:
            slot = t.get("slot") or {}
            return _compact({"id": t["id"], "title": t.get("title"), "priority": t.get("priority"), "due": t.get("dueDate"), "minutes": t.get("estimate"), "planned": f"{slot.get('date')} {clock(slot.get('start'))}" if slot.get("date") else None, "case": case_ref(t), "plan": (plans.get(t.get("planId") or "") or {}).get("title")})

        def ranked(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            return sorted(rows, key=lambda t: (PRIORITY.get(t.get("priority", "normal"), 2), t.get("dueDate") or "9999", t.get("order") or 0))

        async def day_agenda(a: dict[str, Any]) -> ToolOutput:
            day = _day(a, tomorrow(today))
            busy = busy_blocks(day, hearings, meetings, tasks, follow_ups, calendar=calendar)
            free = free_windows(busy)
            out = {
                "date": day,
                "hearings": [hearing_card(h) for h in hearings_on(day, hearings)],
                "meetings": [meeting_card(m) for m in meetings_on(day, meetings)],
                "calendarEvents": [{"id": e.get("id"), "time": f"{clock(e['startMinute'])}–{clock(e.get('endMinute'))}", "title": e.get("title"), "calendar": e.get("calendar"), "where": e.get("location") or None} for e in calendar_on(day, calendar)],
                "allDayEvents": [e.get("title") for e in all_day_on(day, calendar)],
                "followUps": [follow_up_card(f) for f in follow_ups_on(day, follow_ups)],
                "tasks": [task_card(t) for t in ranked(tasks_on(day, tasks))],
                "overdueTasks": [task_card(t) for t in ranked(overdue_tasks(day, tasks))][:15],
                "overdueFollowUps": [follow_up_card(f) for f in overdue_follow_ups(day, follow_ups)][:15],
                "freeWindows": [f"{clock(s)}–{clock(e)}" for s, e in free],
                "freeMinutes": sum(e - s for s, e in free),
                # Inside these the person can take calls and follow-ups (but not do heavy work): put calls there.
                "callWindows": [{"from": clock(s), "to": clock(e), "during": n, "minutes": e - s} for s, e, n in call_only_blocks(day, meetings, calendar)],
            }
            return ToolOutput(out)

        async def free_time(a: dict[str, Any]) -> ToolOutput:
            day = _day(a, tomorrow(today))
            free = free_windows(busy_blocks(day, hearings, meetings, tasks, follow_ups, calendar=calendar))
            return ToolOutput({"date": day, "workingDay": f"{clock(DAY_START)}–{clock(DAY_END)}", "freeWindows": [{"from": clock(s), "to": clock(e), "minutes": e - s} for s, e in free], "callOnlyWindows": [{"from": clock(s), "to": clock(e), "during": n} for s, e, n in call_only_blocks(day, meetings, calendar)]})

        async def upcoming(a: dict[str, Any]) -> ToolOutput:
            span = min(MAX_AHEAD, limit(a.get("days"), 7, MAX_AHEAD))
            start = date.fromisoformat(_day(a, today))
            days = []
            for i in range(span + 1):
                day = (start + timedelta(days=i)).isoformat()
                row = {
                    "date": day,
                    "hearings": [f"{clock(h.get('time')) if h.get('time') is not None else 'time not set'} {h.get('purpose') or 'hearing'} ({(cases.get(h.get('caseId') or '') or {}).get('reference', '?')})" for h in hearings_on(day, hearings)],
                    "meetings": [m.get("title") for m in meetings_on(day, meetings)],
                    "calendar": [e.get("title") for e in (*all_day_on(day, calendar), *calendar_on(day, calendar))],
                    "tasksDue": [t.get("title") for t in tasks if is_open(t) and t.get("dueDate") == day],
                    "followUpsDue": [f.get("title") for f in follow_ups_on(day, follow_ups)],
                    "plansDue": [p.get("title") for p in plans.values() if p.get("status") == "active" and p.get("dueDate") == day],
                }
                if any(row[k] for k in row if k != "date"):
                    days.append(row)
            return ToolOutput({"from": start.isoformat(), "days": span, "busyDays": days})

        async def propose_schedule(a: dict[str, Any]) -> ToolOutput:
            day = _day(a, tomorrow(today))
            raw = a.get("blocks") if isinstance(a.get("blocks"), list) else []
            by_task = {t["id"]: t for t in tasks if is_open(t)}
            by_follow_up = {f["id"]: f for f in follow_ups if is_open(f)}
            blocks: list[dict[str, Any]] = []
            problems: list[str] = []
            for b in raw[:40]:
                if not isinstance(b, dict):
                    continue
                kind, ident = text(b.get("kind")), text(b.get("id"))
                record = (by_task if kind == "task" else by_follow_up if kind == "followUp" else {}).get(ident)
                start, length = b.get("start"), b.get("duration")
                if record is None:
                    problems.append(f"There is no open {'task' if kind == 'task' else 'follow-up' if kind == 'followUp' else kind or 'item'} with id “{ident}”. Use kind task or followUp and an id from day_agenda.")
                    continue
                if isinstance(start, bool) or not isinstance(start, (int, float)):
                    problems.append(f"“{record.get('title')}” needs a start time in minutes after midnight (9:30 is 570).")
                    continue
                default = int(record.get("estimate") or record.get("duration") or 30)
                length = int(length) if isinstance(length, (int, float)) and not isinstance(length, bool) else default
                blocks.append({"kind": kind, "id": ident, "title": record.get("title", ""), "start": int(start), "duration": length})
            skip = {f"{b['kind']}:{b['id']}" for b in blocks}
            problems += check_blocks(blocks, busy_blocks(day, hearings, meetings, tasks, follow_ups, skip=skip, calendar=calendar), call_only_blocks(day, meetings, calendar))
            if problems:
                return ToolOutput({"ok": False, "problems": problems, "hint": "Fix these and call propose_schedule again."})
            if not blocks:
                return ToolOutput({"ok": False, "problems": ["No blocks were given."]})
            ordered = sorted(blocks, key=lambda b: b["start"])
            shown = [{**b, "from": clock(b["start"]), "to": clock(b["start"] + b["duration"])} for b in ordered]
            card: dict[str, Any] = {"kind": "schedule", "id": f"schedule:{day}", "date": day, "blocks": shown}
            if ctx.propose is not None and ctx.interactive:
                # In a chat the schedule can be approved on the spot (or with YES on a phone): approving puts a time on each.
                ops = [
                    {"table": "tasks", "id": b["id"], "create": False, "patch": {"slot": {"date": day, "start": b["start"], "duration": b["duration"]}}} if b["kind"] == "task"
                    else {"table": "follow-ups", "id": b["id"], "create": False, "patch": {"due": {"date": day, "time": b["start"]}, "duration": b["duration"]}}
                    for b in ordered
                ]
                card["proposalId"] = await ctx.propose("schedule.apply", f"Put {len(ordered)} {'item' if len(ordered) == 1 else 'items'} on the planner for {day}", [f"{b['from']}–{b['to']} {b['title']}" for b in shown], ops)
            return ToolOutput({"ok": True, "date": day, "blocks": len(ordered), "note": "Shown to the person as a schedule they can approve. Nothing is booked until they do."}, [card])

        async def present_plan(a: dict[str, Any]) -> ToolOutput:
            day = _day(a, tomorrow(today))
            raw = a.get("sections") if isinstance(a.get("sections"), list) else []
            if not raw:
                return ToolOutput({"ok": False, "problems": ["Give at least one section, each with items."]})
            by = {"hearing": {h["id"]: h for h in hearings}, "meeting": {m["id"]: m for m in meetings}, "task": {t["id"]: t for t in tasks}, "followUp": {f["id"]: f for f in follow_ups}, "calendar": {e["id"]: e for e in calendar if e.get("id")}}
            problems: list[str] = []
            sections: list[dict[str, Any]] = []
            for s in raw[:8]:
                if not isinstance(s, dict):
                    continue
                kind = text(s.get("kind")) if text(s.get("kind")) in SECTION_TITLES else "work"
                items: list[dict[str, Any]] = []
                for it in (s.get("items") if isinstance(s.get("items"), list) else [])[:25]:
                    if not isinstance(it, dict):
                        continue
                    typ, ident, note = text(it.get("type")), text(it.get("id")), text(it.get("text"))[:240] or None
                    if typ == "note":
                        if note:
                            items.append({"type": "note", "note": note})
                        continue
                    record = by.get(typ, {}).get(ident)
                    if record is None:
                        problems.append(f"There is no {typ or 'item'} with id “{ident}”. Use type hearing, meeting, task, followUp or calendar with an id from day_agenda (or type note with text only).")
                        continue
                    items.append(_compact({**_present(typ, record, day, case_ref, cases, plans), "note": note}))
                if items:
                    sections.append({"kind": kind, "title": text(s.get("title"))[:60] or SECTION_TITLES[kind], "items": items})
            if problems:
                return ToolOutput({"ok": False, "problems": problems[:6], "hint": "Fix these and call present_plan again."})
            if not sections:
                return ToolOutput({"ok": False, "problems": ["Nothing to show: every section was empty."]})
            free = free_windows(busy_blocks(day, hearings, meetings, tasks, follow_ups, calendar=calendar))
            card = {"kind": "briefing", "id": f"briefing:{day}", "date": day, "headline": text(a.get("headline"))[:160], "summary": text(a.get("summary"))[:400] or None, "sections": sections, "free": [{"from": clock(s), "to": clock(e), "minutes": e - s} for s, e in free]}
            return ToolOutput({"ok": True, "note": "The person sees this as a card with each task and call ready to complete or move. Do not repeat its items in your answer: write one or two sentences at most."}, [_compact(card)])

        item_schema = {"type": OBJ, "properties": {"type": {"type": "string", "enum": ["hearing", "meeting", "task", "followUp", "calendar", "note"]}, "id": {"type": "string", "description": "The id from day_agenda. Not for a note."}, "text": {"type": "string", "description": "Why it matters or what to do, a short phrase. For a note, the note itself."}}, "required": ["type"]}
        section_schema = {"type": OBJ, "properties": {"kind": {"type": "string", "enum": list(SECTION_TITLES)}, "title": {"type": "string", "description": "Optional heading; a sensible one is used by default."}, "items": {"type": "array", "items": item_schema}}, "required": ["kind", "items"]}
        block_schema = {"type": OBJ, "properties": {"kind": {"type": "string", "enum": ["task", "followUp"]}, "id": {"type": "string"}, "start": {"type": "integer", "description": "Minutes after midnight (9:30 is 570)."}, "duration": {"type": "integer", "description": "Minutes (the item's own estimate if left out)."}}, "required": ["kind", "id", "start"]}
        return [
            Tool(
                ToolSpec("day_agenda", "Everything on one day: hearings, meetings, calls and messages to make, tasks planned or due, what is overdue, and the free windows left. Date defaults to tomorrow.", {"type": OBJ, "properties": {"date": {"type": "string", "description": "YYYY-MM-DD."}}}),
                lambda a: f"Read the agenda for {_day(a, tomorrow(today))}",
                day_agenda,
            ),
            Tool(
                ToolSpec("free_time", "The free windows in a day's working hours after everything already fixed (hearings, meetings, and items with a time).", {"type": OBJ, "properties": {"date": {"type": "string", "description": "YYYY-MM-DD, default tomorrow."}}}),
                lambda a: f"Looked for free time on {_day(a, tomorrow(today))}",
                free_time,
            ),
            Tool(
                ToolSpec("upcoming", "What is coming up day by day: hearings, meetings, tasks and follow-ups falling due, and action plans due. Only days with something are listed.", {"type": OBJ, "properties": {"days": {"type": "integer", "description": "How many days ahead (default 7, at most 30)."}, "date": {"type": "string", "description": "Start date, default today."}}}),
                lambda a: f"Looked {limit(a.get('days'), 7, MAX_AHEAD)} days ahead",
                upcoming,
            ),
            Tool(
                ToolSpec(
                    "present_plan",
                    "Show the day to the person as a card instead of writing it out: sections (fixed, prepare, calls, work, attention, later), each a list of items that point at real hearings, meetings, tasks, follow-ups and calendar events by id. The app fills in their titles and times, and the person can complete or move each one right there. After it, write at most two short sentences.",
                    {"type": OBJ, "properties": {"date": {"type": "string", "description": "YYYY-MM-DD, default tomorrow."}, "headline": {"type": "string", "description": "One line that sums the day up."}, "summary": {"type": "string", "description": "Optional: a sentence or two of context."}, "sections": {"type": "array", "items": section_schema}}, "required": ["headline", "sections"]},
                ),
                lambda a: f"Laid out {_day(a, tomorrow(today))}",
                present_plan,
            ),
            Tool(
                ToolSpec(
                    "propose_schedule",
                    "Propose time blocks for open tasks and follow-ups on a day. It checks the blocks against what is already fixed and against each other, and tells you what is wrong. When it passes the person sees the schedule and can approve it; nothing is booked by this call.",
                    {"type": OBJ, "properties": {"date": {"type": "string", "description": "YYYY-MM-DD, default tomorrow."}, "blocks": {"type": "array", "items": block_schema}}, "required": ["blocks"]},
                ),
                lambda a: f"Proposed a schedule for {_day(a, tomorrow(today))}",
                propose_schedule,
            ),
        ]
