"""The changes pack: proposing to add or change tasks, calls, hearings, cases and action plans.

Nothing here writes. Each tool checks what it was given, and hands the person a card saying exactly what would be saved. Only when they
approve (in the app, or by replying yes on their phone) is anything written. See application/proposals.py.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from app.application.runner import Tool, ToolOutput
from app.application.toolpacks import PackContext, ToolPack
from app.application.toolpacks.common import text
from app.domain import proposals as p
from app.domain.llm import ToolSpec

OBJ = "object"
S = {"type": "string"}


def _props(**fields: dict[str, Any]) -> dict[str, Any]:
    return {"type": OBJ, "properties": fields}


def _described(kind: dict[str, Any], note: str) -> dict[str, Any]:
    return {**kind, "description": note}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class ChangesPack(ToolPack):
    name = "changes"

    async def tools(self, ctx: PackContext) -> list[Tool]:
        if ctx.propose is None:
            return []
        data, offer = ctx.data, ctx.propose
        cases = await data.cases()
        hearings = await data.hearings()
        tasks, follow_ups = await data.records("tasks"), await data.records("follow-ups")

        def case_of(key: Any, name: str = "case") -> dict[str, Any] | None:
            k = text(key)
            if not k:
                return None
            found = p.find_by(cases, k, "reference", "courtNumber")
            if found is None:
                named = [c for c in cases if k.lower() in str(c.get("title") or "").lower()]
                found = named[0] if len(named) == 1 else None
            if found is None:
                raise p.Bad(f"No {name} matches “{k}”. Use search_cases for its id or reference number.")
            return found

        def outcome_for(status: str | None) -> dict[str, Any] | None:
            return None if status is None else {"status": status, **({"recordedAt": _now()} if status != "open" else {})}

        async def propose(action: str, title: str, lines: list[str], ops: list[dict[str, Any]]) -> ToolOutput:
            pid = await offer(action, title, lines, ops)
            card = {"kind": "proposal", "id": pid, "action": action, "title": title, "lines": lines}
            return ToolOutput({"proposed": True, "id": pid, "what": title, "note": "The person now sees this with an Approve button. Nothing is saved until they approve; say so, and do not say it was added."}, [card])

        def safe(run):
            async def wrapped(a: dict[str, Any]) -> ToolOutput:
                try:
                    return await run(a)
                except p.Bad as exc:
                    return ToolOutput({"error": str(exc)})

            return wrapped

        # --- tasks -----------------------------------------------------------------------------------------

        def task_fields(a: dict[str, Any], *, creating: bool) -> tuple[dict[str, Any], list[str]]:
            f: dict[str, Any] = {}
            shown: list[str] = []
            if (title := p.words(a.get("title"), "The title", required=creating)) is not None:
                f["title"] = title
            if (due := p.day(a.get("due"), "The due date")) is not None:
                f["dueDate"] = due
                shown.append(f"Due {p.day_label(due)}")
            if (pri := p.pick(a.get("priority"), p.PRIORITIES, "The priority")) is not None:
                f["priority"] = pri
                shown.append(f"{pri.capitalize()} priority")
            if a.get("minutes") not in (None, ""):
                f["estimate"] = p.length(a.get("minutes"), "The estimate", 30)
                shown.append(f"{f['estimate']} min")
            if (notes := p.long_text(a.get("notes"), "The notes")) is not None:
                f["notes"] = notes
                shown.append(notes if len(notes) <= 120 else notes[:117] + "…")
            if (c := case_of(a.get("case"))) is not None:
                f["related"] = {"kind": "case", "id": c["id"]}
                shown.append(f"On {c.get('reference')} {c.get('title', '')}".strip())
            if (ws := p.pick(a.get("workspace"), p.WORKSPACES, "The workspace")) is not None:
                f["workspace"] = ws
            date, start = p.day(a.get("date"), "The planned day"), p.minutes(a.get("start"), "The start time")
            if date or start is not None:
                if not (date and start is not None):
                    raise p.Bad("To place it on the planner give both date and start.")
                f["slot"] = {"date": date, "start": start, "duration": f.get("estimate") or int(a.get("minutes") or 30)}
                shown.append(f"On the planner {p.day_label(date)} at {p.clock(start)}")
            return f, shown

        async def add_task(a: dict[str, Any]) -> ToolOutput:
            f, shown = task_fields(a, creating=True)
            f.setdefault("workspace", "sb" if f.get("related") else "personal")
            tid = str(uuid.uuid4())
            record = {"id": tid, "notes": "", "priority": "normal", "estimate": 30, "outcome": {"status": "open"}, **f}
            return await propose("task.create", f"New task: {f['title']}", shown, [{"table": "tasks", "id": tid, "create": True, "patch": record}])

        async def change_task(a: dict[str, Any]) -> ToolOutput:
            current = p.find_by(tasks, text(a.get("id")))
            if current is None:
                raise p.Bad("No task has that id. Use day_agenda to find it.")
            f, shown = task_fields(a, creating=False)
            if (status := p.pick(a.get("status"), tuple(p.STATUSES), "The status")) is not None:
                f["outcome"] = outcome_for(status)
                shown.append({"open": "Reopen it", "done": "Mark it done", "cancelled": "Cancel it"}[status])
            if a.get("clear_planned_time") is True:
                f["slot"] = None
                shown.append("Take it off the planner")
            if isinstance(f.get("slot"), dict) and a.get("minutes") in (None, ""):
                f["slot"]["duration"] = int(current.get("estimate") or 30)
            if not f:
                raise p.Bad("Nothing to change was given.")
            return await propose("task.update", f"Change task: {current.get('title')}", shown, [{"table": "tasks", "id": current["id"], "create": False, "patch": f}])

        # --- follow-ups ------------------------------------------------------------------------------------

        def follow_fields(a: dict[str, Any], *, creating: bool) -> tuple[dict[str, Any], list[str]]:
            f: dict[str, Any] = {}
            shown: list[str] = []
            if (title := p.words(a.get("title"), "The title", required=creating)) is not None:
                f["title"] = title
            if (ch := p.pick(a.get("channel"), p.CHANNELS, "The channel")) is not None:
                f["channel"] = ch
                shown.append(ch.capitalize())
            if (who := p.words(a.get("contact"), "The contact", most=200)) is not None:
                f["contact"] = who
                shown.append(f"With {who}")
            date, at = p.day(a.get("due"), "The day"), p.minutes(a.get("time"), "The time")
            if date or at is not None:
                if not date:
                    raise p.Bad("A time needs a day too: give due as well.")
                f["due"] = {"date": date, **({"time": at} if at is not None else {})}
                shown.append(f"{p.day_label(date)}" + (f" at {p.clock(at)}" if at is not None else ""))
            if a.get("minutes") not in (None, ""):
                f["duration"] = p.length(a.get("minutes"), "The length", 15)
            if (notes := p.long_text(a.get("notes"), "The notes")) is not None:
                f["notes"] = notes
            if (c := case_of(a.get("case"))) is not None:
                f["subject"] = {"kind": "case", "id": c["id"]}
                shown.append(f"About {c.get('reference')}")
            if (ws := p.pick(a.get("workspace"), p.WORKSPACES, "The workspace")) is not None:
                f["workspace"] = ws
            return f, shown

        async def add_follow_up(a: dict[str, Any]) -> ToolOutput:
            f, shown = follow_fields(a, creating=True)
            f.setdefault("workspace", "sb" if f.get("subject") else "personal")
            fid = str(uuid.uuid4())
            record = {"id": fid, "channel": "call", "duration": 15, "outcome": {"status": "open"}, **f}
            return await propose("followUp.create", f"New follow-up: {f['title']}", shown, [{"table": "follow-ups", "id": fid, "create": True, "patch": record}])

        async def change_follow_up(a: dict[str, Any]) -> ToolOutput:
            current = p.find_by(follow_ups, text(a.get("id")))
            if current is None:
                raise p.Bad("No follow-up has that id. Use day_agenda to find it.")
            f, shown = follow_fields(a, creating=False)
            if (status := p.pick(a.get("status"), tuple(p.STATUSES), "The status")) is not None:
                f["outcome"] = outcome_for(status)
                shown.append({"open": "Reopen it", "done": "Mark it done", "cancelled": "Cancel it"}[status])
            if not f:
                raise p.Bad("Nothing to change was given.")
            return await propose("followUp.update", f"Change follow-up: {current.get('title')}", shown, [{"table": "follow-ups", "id": current["id"], "create": False, "patch": f}])

        # --- hearings and cases ------------------------------------------------------------------------------

        async def add_hearing(a: dict[str, Any]) -> ToolOutput:
            c = case_of(a.get("case"))
            if c is None:
                raise p.Bad("Which case is it for? Give its reference number or id.")
            date, at = p.day(a.get("date"), "The hearing date", required=True), p.minutes(a.get("time"), "The time")
            purpose = p.words(a.get("purpose"), "The purpose", required=True, most=200)
            also = [case_of(k) for k in (a.get("also_cases") if isinstance(a.get("also_cases"), list) else [])]
            clash = next((h for h in hearings if h.get("caseId") == c["id"] and h.get("date") == date and (h.get("outcome") or {}).get("status", "open") == "open"), None)
            if clash is not None:
                raise p.Bad(f"{c.get('reference')} already has a hearing on {p.day_label(date)} (“{clash.get('purpose')}”). Change that one instead.")
            hid = str(uuid.uuid4())
            record: dict[str, Any] = {"id": hid, "caseId": c["id"], "date": date, "purpose": purpose, "duration": p.length(a.get("minutes"), "The length", 30), "outcome": {"status": "open"}}
            if at is not None:
                record["time"] = at
            if (forum := p.words(a.get("forum"), "The court or bench", most=200)) is not None:
                record["forum"] = forum
            if also:
                record["alsoCaseIds"] = [x["id"] for x in also if x]
            shown = p.lines(f"{c.get('reference')} {c.get('title', '')}".strip(), p.day_label(date) + (f" at {p.clock(at)}" if at is not None else ""), forum if forum else None, f"Also heard with {', '.join(str(x.get('reference')) for x in also if x)}" if also else None)
            return await propose("hearing.create", f"New hearing: {purpose}", shown, [{"table": "hearings", "id": hid, "create": True, "patch": record}])

        async def add_case(a: dict[str, Any]) -> ToolOutput:
            title = p.words(a.get("title"), "The case title", required=True, most=300)
            org = p.pick(a.get("organization"), p.ORGANIZATIONS, "The organization")
            if org is None:
                raise p.Bad(f"Which organization is it filed for? One of: {', '.join(p.ORGANIZATIONS)}.")
            cid = str(uuid.uuid4())
            record: dict[str, Any] = {"id": cid, "reference": "", "organization": org, "title": title, "type": p.pick(a.get("type"), p.CASE_TYPES, "The case type", "other"), "status": "active", "summary": p.long_text(a.get("summary"), "The summary") or ""}
            for key, field, label in (("client", "client", "Client"), ("opposing_party", "opposingParty", "Against"), ("court", "court", "Court"), ("court_number", "courtNumber", "Court number"), ("stage", "stage", "Stage")):
                if (v := p.words(a.get(key), label, most=300)) is not None:
                    record[field] = v
            if (filed := p.day(a.get("filed_on"), "The filing date")) is not None:
                record["filedOn"] = filed
            shown = p.lines(f"{org} · {p.CASE_TYPE_LABELS[record['type']]}", f"For {record['client']}" if record.get("client") else None, f"Against {record['opposingParty']}" if record.get("opposingParty") else None, record.get("court"), f"Filed {p.day_label(filed)}" if filed else None, "The case number is given when it is opened.")
            return await propose("case.create", f"New case: {title}", shown, [{"table": "cases", "id": cid, "create": True, "patch": record}])

        # --- action plans ------------------------------------------------------------------------------------

        async def add_action_plan(a: dict[str, Any]) -> ToolOutput:
            title = p.words(a.get("title"), "The plan's title", required=True, most=200)
            linked = [c for c in (case_of(k) for k in (a.get("cases") if isinstance(a.get("cases"), list) else [])) if c]
            raw = a.get("steps") if isinstance(a.get("steps"), list) else []
            if not raw:
                raise p.Bad("A plan needs steps: give a list, each with a title.")
            if len(raw) > p.MAX_STEPS:
                raise p.Bad(f"At most {p.MAX_STEPS} steps in one plan.")
            workspace = p.pick(a.get("workspace"), p.WORKSPACES, "The workspace", "sb" if linked else "personal")
            pid = str(uuid.uuid4())
            due = p.day(a.get("due"), "The plan's due date")
            plan: dict[str, Any] = {"id": pid, "title": title, "objective": p.long_text(a.get("objective"), "The objective") or "", "workspace": workspace, "status": "active"}
            if due:
                plan["dueDate"] = due
            if linked:
                plan["caseIds"] = [c["id"] for c in linked]
                plan["origin"] = {"kind": "case", "id": linked[0]["id"]}
            ops = [{"table": "plans", "id": pid, "create": True, "patch": plan}]
            shown = p.lines(", ".join(str(c.get("reference")) for c in linked) or None, f"Due {p.day_label(due)}" if due else None, plan["objective"] or None)
            for i, step in enumerate(raw):
                if not isinstance(step, dict):
                    raise p.Bad("Each step must have a title.")
                label = p.words(step.get("title"), f"Step {i + 1}'s title", required=True)
                step_due = p.day(step.get("due"), f"Step {i + 1}'s due date")
                sid = str(uuid.uuid4())
                if p.pick(step.get("kind"), ("task", "followUp"), f"Step {i + 1}'s kind", "task") == "followUp":
                    rec: dict[str, Any] = {"id": sid, "title": label, "channel": p.pick(step.get("channel"), p.CHANNELS, "The channel", "call"), "workspace": workspace, "duration": p.length(step.get("minutes"), "The length", 15), "planId": pid, "order": i, "outcome": {"status": "open"}}
                    if step_due:
                        rec["due"] = {"date": step_due}
                    if (who := p.words(step.get("contact"), "The contact", most=200)) is not None:
                        rec["contact"] = who
                    table, mark = "follow-ups", "call"
                else:
                    rec = {"id": sid, "title": label, "notes": "", "workspace": workspace, "priority": p.pick(step.get("priority"), p.PRIORITIES, "The priority", "normal"), "estimate": p.length(step.get("minutes"), "The estimate", 30), "planId": pid, "order": i, "outcome": {"status": "open"}}
                    if step_due:
                        rec["dueDate"] = step_due
                    table, mark = "tasks", "task"
                ops.append({"table": table, "id": sid, "create": True, "patch": rec})
                shown.append(f"{i + 1}. {label}" + (f" ({mark}, {p.day_label(step_due)})" if step_due else f" ({mark})"))
            return await propose("plan.create", f"New action plan: {title}", shown, ops)

        task_fields_schema = {
            "title": _described(S, "What the task is."),
            "due": _described(S, "Due date, YYYY-MM-DD."),
            "priority": {"type": "string", "enum": list(p.PRIORITIES)},
            "minutes": {"type": "integer", "description": "How long it takes."},
            "notes": S,
            "case": _described(S, "The case it is for: its reference number or id."),
            "workspace": {"type": "string", "enum": list(p.WORKSPACES)},
            "date": _described(S, "Put it on the planner on this day (YYYY-MM-DD); needs start too."),
            "start": _described({"type": "string"}, "Start time on the planner, HH:MM."),
        }
        follow_schema = {
            "title": _described(S, "What to do, e.g. “Call the advocate about the filing”."),
            "channel": {"type": "string", "enum": list(p.CHANNELS)},
            "contact": _described(S, "Who to reach, a name and/or number."),
            "due": _described(S, "The day, YYYY-MM-DD."),
            "time": _described(S, "The time, HH:MM (needs due)."),
            "minutes": {"type": "integer"},
            "notes": S,
            "case": _described(S, "The case it is about: reference number or id."),
            "workspace": {"type": "string", "enum": list(p.WORKSPACES)},
        }

        def tool(name: str, description: str, schema: dict[str, Any], required: list[str], label, run) -> Tool:
            return Tool(ToolSpec(name, description + " It only proposes: the person approves it before anything is saved.", {**schema, "required": required} if required else schema), label, safe(run))

        return [
            tool("add_task", "Propose a new task.", _props(**task_fields_schema), ["title"], lambda a: f"Proposed a task: {text(a.get('title'))}", add_task),
            tool("change_task", "Propose a change to a task: its title, due date, priority, length, notes, case, a time on the planner, or its status (done, cancelled, open).", _props(id=_described(S, "The task id from day_agenda."), status={"type": "string", "enum": list(p.STATUSES)}, clear_planned_time={"type": "boolean"}, **task_fields_schema), ["id"], lambda a: f"Proposed a change to task {text(a.get('id'))}", change_task),
            tool("add_follow_up", "Propose a new call, message, email or visit to make.", _props(**follow_schema), ["title"], lambda a: f"Proposed a follow-up: {text(a.get('title'))}", add_follow_up),
            tool("change_follow_up", "Propose a change to a follow-up: when it is due, who, how, notes, or its status (done, cancelled, open).", _props(id=_described(S, "The follow-up id from day_agenda."), status={"type": "string", "enum": list(p.STATUSES)}, **follow_schema), ["id"], lambda a: f"Proposed a change to follow-up {text(a.get('id'))}", change_follow_up),
            tool("add_hearing", "Propose listing a hearing for a case.", _props(case=_described(S, "The case: reference number or id."), date=_described(S, "YYYY-MM-DD."), time=_described(S, "HH:MM."), purpose=_described(S, "What it is listed for."), forum=_described(S, "Court or bench."), minutes={"type": "integer"}, also_cases={"type": "array", "items": S, "description": "Other cases heard together."}), ["case", "date", "purpose"], lambda a: f"Proposed a hearing on {text(a.get('date'))}", add_hearing),
            tool("add_case", f"Propose opening a case. The case number is generated when it is opened. Organization is one of {', '.join(p.ORGANIZATIONS)}.", _props(title=S, organization={"type": "string", "enum": list(p.ORGANIZATIONS)}, type={"type": "string", "enum": list(p.CASE_TYPES)}, client=S, opposing_party=S, court=S, court_number=S, stage=S, summary=S, filed_on=_described(S, "YYYY-MM-DD.")), ["title", "organization"], lambda a: f"Proposed a case: {text(a.get('title'))}", add_case),
            tool("add_action_plan", "Propose an action plan: an objective with ordered steps, each a task or a call, optionally for one or more cases.", _props(title=S, objective=S, due=_described(S, "YYYY-MM-DD."), cases={"type": "array", "items": S, "description": "Case reference numbers or ids."}, workspace={"type": "string", "enum": list(p.WORKSPACES)}, steps={"type": "array", "items": _props(kind={"type": "string", "enum": ["task", "followUp"]}, title=S, due=_described(S, "YYYY-MM-DD."), minutes={"type": "integer"}, priority={"type": "string", "enum": list(p.PRIORITIES)}, channel={"type": "string", "enum": list(p.CHANNELS)}, contact=S)}), ["title", "steps"], lambda a: f"Proposed an action plan: {text(a.get('title'))}", add_action_plan),
        ]
