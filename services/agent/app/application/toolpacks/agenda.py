"""The agenda pack: what a day holds (hearings, meetings, calls, tasks), how much of it is free, what is coming, and a schedule proposal."""

from datetime import date, timedelta
from typing import Any

from app.application.runner import Tool, ToolOutput
from app.application.toolpacks import PackContext, ToolPack
from app.application.toolpacks.common import limit, text
from app.domain.dayplan import (
    DAY_END,
    DAY_START,
    busy_blocks,
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
            busy = busy_blocks(day, hearings, meetings, tasks, follow_ups)
            free = free_windows(busy)
            out = {
                "date": day,
                "hearings": [hearing_card(h) for h in hearings_on(day, hearings)],
                "meetings": [meeting_card(m) for m in meetings_on(day, meetings)],
                "followUps": [follow_up_card(f) for f in follow_ups_on(day, follow_ups)],
                "tasks": [task_card(t) for t in ranked(tasks_on(day, tasks))],
                "overdueTasks": [task_card(t) for t in ranked(overdue_tasks(day, tasks))][:15],
                "overdueFollowUps": [follow_up_card(f) for f in overdue_follow_ups(day, follow_ups)][:15],
                "freeWindows": [f"{clock(s)}–{clock(e)}" for s, e in free],
                "freeMinutes": sum(e - s for s, e in free),
            }
            return ToolOutput(out)

        async def free_time(a: dict[str, Any]) -> ToolOutput:
            day = _day(a, tomorrow(today))
            free = free_windows(busy_blocks(day, hearings, meetings, tasks, follow_ups))
            return ToolOutput({"date": day, "workingDay": f"{clock(DAY_START)}–{clock(DAY_END)}", "freeWindows": [{"from": clock(s), "to": clock(e), "minutes": e - s} for s, e in free]})

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
            problems += check_blocks(blocks, busy_blocks(day, hearings, meetings, tasks, follow_ups, skip=skip))
            if problems:
                return ToolOutput({"ok": False, "problems": problems, "hint": "Fix these and call propose_schedule again."})
            if not blocks:
                return ToolOutput({"ok": False, "problems": ["No blocks were given."]})
            ordered = sorted(blocks, key=lambda b: b["start"])
            card = {"kind": "schedule", "id": f"schedule:{day}", "date": day, "blocks": [{**b, "from": clock(b["start"]), "to": clock(b["start"] + b["duration"])} for b in ordered]}
            return ToolOutput({"ok": True, "date": day, "blocks": len(ordered), "note": "Shown to the person as a schedule they can approve. Nothing is booked until they do."}, [card])

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
                    "propose_schedule",
                    "Propose time blocks for open tasks and follow-ups on a day. It checks the blocks against what is already fixed and against each other, and tells you what is wrong. When it passes the person sees the schedule and can approve it; nothing is booked by this call.",
                    {"type": OBJ, "properties": {"date": {"type": "string", "description": "YYYY-MM-DD, default tomorrow."}, "blocks": {"type": "array", "items": block_schema}}, "required": ["blocks"]},
                ),
                lambda a: f"Proposed a schedule for {_day(a, tomorrow(today))}",
                propose_schedule,
            ),
        ]
