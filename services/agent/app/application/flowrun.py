"""Running a workflow (see domain/flow.py): items pass from node to node, each node leaves a trace, and the run ends with an answer.

The executor works like a queue of "these items arrived at this input of this node". A node runs, gives items on each of its outputs, and each
output's items are queued for the nodes it is connected to. A node that gets no items doesn't run (that is how a branch ends); a Merge waits for
both its inputs; a Loop over items hands out batches and collects what comes back. Every node run is kept in the run's trace with the items
that went in and came out, so an execution can be opened node by node.

Data nodes read through the same workspace-limited gateway the agent's tools use, so a workflow can only see what its run is about.
"""

from __future__ import annotations

import html
import json
import logging
import time
import uuid
from collections import deque
from datetime import UTC, date, datetime, timedelta
from typing import Any

from stiamo_common.errors import ValidationFailed

from app.application.runner import AgentRun, Step, run_agent
from app.domain.dayplan import is_open
from app.domain.flow import MAX_RUNS, TRIGGERS, Flow, FNode, default_flow, validate
from app.domain.flow_expr import ExpressionError, resolve
from app.domain.llm import AiError, Message
from app.domain.prompt import build_package

log = logging.getLogger(__name__)

SAMPLE = 5
CLIP = 1200
Item = dict[str, Any]


def total_usage(runs: list[AgentRun]) -> dict[str, Any]:
    """What the model calls of a run used, added up: tokens, and the cost when the provider reported one."""
    out: dict[str, Any] = {"inputTokens": 0, "outputTokens": 0}
    for r in runs:
        for k, v in (r.usage or {}).items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out[k] = out.get(k, 0) + v
    return out


def flow_of(stored: dict[str, Any], agent_id: str) -> Flow:
    """The agent's own workflow if it has one that can run, else the default. A stored one that no longer validates never breaks the agent."""
    raw = (stored or {}).get(agent_id)
    if raw:
        try:
            f = Flow.from_json(raw)
            if not validate(f):
                return f
            log.warning("the stored workflow of %s can't run; using the default", agent_id)
        except ValueError:
            log.warning("the stored workflow of %s can't be read; using the default", agent_id)
    return default_flow()


def _clip(v: Any) -> Any:
    text = json.dumps(v, default=str, ensure_ascii=False)
    return v if len(text) <= CLIP else text[: CLIP - 1] + "…"


def _items(rows: list[dict[str, Any]]) -> list[Item]:
    return [{"json": dict(r)} for r in rows]


def _first(inputs: list[Item]) -> dict[str, Any]:
    return inputs[0]["json"] if inputs else {}


# --- conditions ---------------------------------------------------------------------------------------------------------------


def _test(op: str, left: Any, right: Any) -> bool:
    text, want = str(left if left is not None else "").lower(), str(right if right is not None else "").lower()
    if op == "contains":
        return want in text if not isinstance(left, list) else right in left
    if op == "not_contains":
        return want not in text
    if op == "equals":
        return text == want
    if op == "not_equals":
        return text != want
    if op == "starts_with":
        return text.startswith(want)
    if op == "empty":
        return left in (None, "", [], {}) or text.strip() in ("", "0", "false")
    if op == "not_empty":
        return not _test("empty", left, right)
    if op == "matches":
        import re

        try:
            return re.search(str(right)[:200], str(left or ""), re.IGNORECASE) is not None
        except re.error:
            return False
    if op in ("gt", "lt"):
        try:
            return float(left) > float(right) if op == "gt" else float(left) < float(right)
        except (TypeError, ValueError):
            return False
    return False


class Run:
    """Everything one execution of a workflow shares: the data it may read, what earlier nodes produced, the answer so far."""

    def __init__(self, svc: Any, who: Any, token: str, agent: Any, prep: Any, past: list[Message], channel: str, trigger: str, channel_id: str | None, message: str, asked: str) -> None:
        self.svc, self.who, self.token, self.agent, self.prep, self.past, self.channel, self.trigger, self.channel_id = svc, who, token, agent, prep, past, channel, trigger, channel_id
        self.message, self.asked = message, asked
        self.vars: dict[str, Any] = {}
        self.results: dict[str, list[list[Item]]] = {}
        self.names: dict[str, str] = {}
        self.runs: list[AgentRun] = []
        self.final: str | None = None
        self.used_history = False
        self.record: Any = None
        self.trace: list[dict[str, Any]] = []

    @property
    def workspace(self) -> str:
        return self.prep.scope

    def scope(self, inputs: list[Item], item: Item | None = None) -> dict[str, Any]:
        nodes: dict[str, Any] = {}
        for nid, outs in self.results.items():
            flat = [i["json"] for i in (outs[0] if outs else [])]
            view = {"items": flat, "first": flat[0] if flat else None, "count": len(flat)}
            nodes[nid] = view
            if self.names.get(nid):
                nodes[self.names[nid]] = view
        return {"$json": (item or (inputs[0] if inputs else {"json": {}}))["json"], "$input": [i["json"] for i in inputs], "$node": nodes, "$workspace": self.workspace, "$vars": self.vars, "$now": datetime.now(UTC).isoformat(timespec="seconds"), "$today": datetime.now(UTC).date().isoformat()}


# --- the nodes ----------------------------------------------------------------------------------------------------------------


async def _conditions(run: Run, n: FNode, item: Item, inputs: list[Item], key: str = "conditions") -> list[bool]:
    sc = run.scope(inputs, item)
    return [_test(c.get("op", ""), resolve(c.get("left"), sc), resolve(c.get("right"), sc)) for c in (n.parameters.get(key) or [])]


async def node_trigger(run: Run, n: FNode, inputs: list[Item]) -> list[list[Item]]:
    if n.type == "trigger.schedule":
        return [[{"json": {"message": run.message, "asked": run.asked, "workspace": run.workspace, "day": (datetime.now(UTC).date() + timedelta(days=1)).isoformat()}}]]
    return [[{"json": {"message": run.message, "asked": run.asked, "workspace": run.workspace}}]]


async def node_if(run: Run, n: FNode, inputs: list[Item]) -> list[list[Item]]:
    true, false = [], []
    any_mode = n.parameters.get("combine") == "any"
    for it in inputs:
        checks = await _conditions(run, n, it, inputs)
        ok = any(checks) if any_mode else all(checks)
        (true if ok else false).append(it)
    return [true, false]


async def node_filter(run: Run, n: FNode, inputs: list[Item]) -> list[list[Item]]:
    keep = []
    for it in inputs:
        checks = await _conditions(run, n, it, inputs)
        if (any(checks) if n.parameters.get("combine") == "any" else all(checks)):
            keep.append(it)
    return [keep]


async def node_switch(run: Run, n: FNode, inputs: list[Item]) -> list[list[Item]]:
    rules = n.parameters.get("rules") or []
    outs: list[list[Item]] = [[] for _ in range(len(rules) + 1)]
    for it in inputs:
        sc = run.scope(inputs, it)
        hit = next((i for i, c in enumerate(rules) if _test(c.get("op", ""), resolve(c.get("left"), sc), resolve(c.get("right"), sc))), len(rules))
        outs[hit].append(it)
    return outs


async def node_set(run: Run, n: FNode, inputs: list[Item]) -> list[list[Item]]:
    out = []
    for it in inputs:
        sc = run.scope(inputs, it)
        base = dict(it["json"]) if n.parameters.get("keepOthers", True) else {}
        for f in n.parameters.get("fields") or []:
            if f.get("name"):
                base[str(f["name"])] = resolve(f.get("value"), sc)
                if n.parameters.get("asVariable"):
                    run.vars[str(f["name"])] = base[str(f["name"])]
        out.append({"json": base})
    return [out]


async def node_sort(run: Run, n: FNode, inputs: list[Item]) -> list[list[Item]]:
    field_ = str(n.parameters.get("field") or "")
    return [sorted(inputs, key=lambda i: (str(i["json"].get(field_, "")) if not isinstance(i["json"].get(field_), (int, float)) else float(i["json"][field_]), ), reverse=n.parameters.get("order") == "desc")] if field_ else [inputs]


async def node_limit(run: Run, n: FNode, inputs: list[Item]) -> list[list[Item]]:
    return [inputs[: max(0, int(n.parameters.get("max") or 10))]]


async def node_split(run: Run, n: FNode, inputs: list[Item]) -> list[list[Item]]:
    f = str(n.parameters.get("field") or "")
    out: list[Item] = []
    for it in inputs:
        v = it["json"].get(f)
        out += [{"json": x if isinstance(x, dict) else {f: x}} for x in v] if isinstance(v, list) else []
    return [out]


async def node_aggregate(run: Run, n: FNode, inputs: list[Item]) -> list[list[Item]]:
    f = str(n.parameters.get("field") or "")
    return [[{"json": {(f or "items"): [i["json"].get(f) if f else i["json"] for i in inputs], "count": len(inputs)}}]]


async def node_noop(run: Run, n: FNode, inputs: list[Item]) -> list[list[Item]]:
    return [inputs]


async def node_stop(run: Run, n: FNode, inputs: list[Item]) -> list[list[Item]]:
    raise ValidationFailed(str(resolve(n.parameters.get("message") or "The workflow was stopped.", run.scope(inputs))))


async def _data(run: Run) -> Any:
    return run.prep.data


async def node_mail(run: Run, n: FNode, inputs: list[Item]) -> list[list[Item]]:
    p = resolve(n.parameters, run.scope(inputs))
    data = run.prep.data
    since = datetime.now(UTC).date() - timedelta(days=int(p.get("days") or 3))
    word = str(p.get("query") or "").lower()
    out: list[dict[str, Any]] = []
    try:
        for acc in await data.mail_accounts():
            got = await data.mail_uids(str(acc["id"]), str(p.get("folder") or "INBOX"), since, None)
            uids = list(got.get("uids", []))[: max(1, min(int(p.get("limit") or 20), 100))]
            for m in await data.mail_summaries(str(acc["id"]), str(p.get("folder") or "INBOX"), uids) if uids else []:
                row = {"id": f"{acc['id']}:{m.get('uid')}", "account": acc["id"], "workspace": acc.get("workspace"), "uid": m.get("uid"), "subject": m.get("subject"), "from": (m.get("sender") or {}).get("name") or (m.get("sender") or {}).get("address"), "fromAddress": (m.get("sender") or {}).get("address"), "date": m.get("date"), "snippet": m.get("snippet")}
                if not word or word in json.dumps(row, default=str).lower():
                    out.append(row)
    except AiError as exc:
        raise ValidationFailed(str(exc)) from exc
    return [_items(out[: int(p.get("limit") or 20)])]


async def node_mail_body(run: Run, n: FNode, inputs: list[Item]) -> list[list[Item]]:
    out = []
    for it in inputs[:10]:
        sc = run.scope(inputs, it)
        acc, uid = resolve(n.parameters.get("account", "{{ $json.account }}"), sc), resolve(n.parameters.get("uid", "{{ $json.uid }}"), sc)
        try:
            m = await run.prep.data.mail_message(str(acc), str(n.parameters.get("folder") or "INBOX"), int(uid))
        except (AiError, ValueError, TypeError) as exc:
            raise ValidationFailed(str(getattr(exc, "message", None) or exc)) from exc
        out.append({"json": {**it["json"], "text": m.get("text", ""), "subject": m.get("subject", it["json"].get("subject"))}})
    return [out]


async def node_contacts(run: Run, n: FNode, inputs: list[Item]) -> list[list[Item]]:
    p = resolve(n.parameters, run.scope(inputs))
    people = await run.prep.data.contacts(str(p.get("query") or ""), int(p.get("limit") or 20))
    return [_items([{"name": c.get("name"), "phone": ((c.get("phones") or [{}])[0] or {}).get("value"), "email": ((c.get("emails") or [{}])[0] or {}).get("value"), "organisation": c.get("organisation"), "workspaces": c.get("workspaces")} for c in people])]


def _day(v: Any) -> str:
    return str(v or "")[:10]


async def node_records(run: Run, n: FNode, inputs: list[Item]) -> list[list[Item]]:
    p = resolve(n.parameters, run.scope(inputs))
    data, t = run.prep.data, n.type
    today = datetime.now(UTC).date().isoformat()
    rows: list[dict[str, Any]]
    if t == "data.tasks":
        rows = [r for r in await data.records("tasks") if p.get("status", "open") == "all" or is_open(r)]
        if p.get("dueOn"):
            rows = [r for r in rows if _day(r.get("dueDate")) == _day(p["dueOn"]) or _day((r.get("slot") or {}).get("date")) == _day(p["dueOn"])]
        if p.get("overdue"):
            rows = [r for r in rows if r.get("dueDate") and _day(r["dueDate"]) < today]
    elif t == "data.follow_ups":
        rows = [r for r in await data.records("follow-ups") if p.get("status", "open") == "all" or is_open(r)]
        if p.get("dueOn"):
            rows = [r for r in rows if _day((r.get("due") or {}).get("date")) == _day(p["dueOn"])]
        if p.get("overdue"):
            rows = [r for r in rows if (r.get("due") or {}).get("date") and _day(r["due"]["date"]) < today]
    elif t == "data.meetings":
        rows = [r for r in await data.records("meetings") if p.get("status", "open") == "all" or is_open(r)]
        if p.get("on"):
            rows = [r for r in rows if _day((r.get("slot") or {}).get("date")) == _day(p["on"])]
    elif t == "data.hearings":
        rows = [r for r in await data.hearings() if p.get("status", "open") == "all" or is_open(r)]
        if p.get("on"):
            rows = [r for r in rows if _day(r.get("date")) == _day(p["on"])]
    elif t == "data.cases":
        rows = [r for r in await data.cases() if p.get("status", "active") == "all" or r.get("status") == p.get("status", "active")]
    elif t == "data.calendar":
        start = _day(p.get("from") or today)
        end = _day(p.get("to") or (date.fromisoformat(start) + timedelta(days=1)).isoformat())
        rows = await data.calendar_events(start, end)
    else:  # data.drive
        try:
            rows = await data.drive_search(str(p.get("query") or "")) if await data.drive_available() else []
        except AiError as exc:
            raise ValidationFailed(str(exc)) from exc
    return [_items(rows[: max(1, min(int(p.get("limit") or 50), 200))])]


async def node_send(run: Run, n: FNode, inputs: list[Item]) -> list[list[Item]]:
    sent = 0
    texts = []
    for it in (inputs or [{"json": {}}])[: 1 if n.parameters.get("once", True) else 10]:
        text = str(resolve(n.parameters.get("text") or "{{ $json.text }}", run.scope(inputs, it))).strip()
        if text and run.svc.notify is not None:
            sent += await run.svc.notify(run.who, run.workspace, text)
        texts.append(text)
    return [[{"json": {"sent": sent, "text": texts[0] if texts else ""}}]]


async def node_ask(run: Run, n: FNode, inputs: list[Item]) -> list[list[Item]]:
    """Asks on the phone and stops here. The question is kept with everything the rest of the workflow needs; the button pressed carries on from the
    matching output (see AgentService.resume_flow). Nothing waits in memory, so it survives a restart."""
    p = n.parameters
    sc = run.scope(inputs)
    text = str(resolve(p.get("text") or "", sc)).strip()
    options = [str(o).strip() for o in (p.get("options") or [])][:4]
    short = uuid.uuid4().hex[:8]
    state = {
        "agent": run.agent.id, "node": n.id, "options": options, "text": text, "message": run.message, "asked": run.asked, "workspace": run.workspace, "channel": run.channel_id,
        "vars": run.vars, "names": run.names, "item": _first(inputs), "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "results": {nid: [[i["json"] for i in lane] for lane in outs] for nid, outs in run.results.items()},
    }
    await run.svc.park_wait(run.who, short, state)
    sent = await run.svc.ask_phone(run.who, run.workspace, text, [[{"text": o, "callback_data": f"fw:{i}:{short}"}] for i, o in enumerate(options)]) if run.svc.ask_phone is not None else 0
    if not sent:
        await run.svc.drop_wait(run.who, short)
        raise ValidationFailed("Nothing was asked: no Telegram chat is linked to send the question to.")
    return [[] for _ in options]


async def node_agent(run: Run, n: FNode, inputs: list[Item]) -> list[list[Item]]:
    svc, prep, agent = run.svc, run.prep, run.agent
    p = n.parameters
    sc = run.scope(inputs)
    sel = p.get("model") or svc._selection(prep.settings, agent.id)[0]
    if not sel or not sel.get("modelId"):
        raise ValidationFailed(f"Choose a provider and a model for the {agent.name} first (AI agents → {agent.name}).")
    question = str(resolve(p.get("prompt") or "{{ $json.asked }}", sc))
    config = prep.config
    override = {k: v for k, v in (("system", p.get("system")), ("input_template", p.get("inputTemplate"))) if v}
    if override:
        config = config.model_copy(update=override)
    pkg = build_package(config, [t.spec for t in prep.tools], prep.context, question)
    tools = svc._tools_for(pkg, prep.tools)
    if isinstance(p.get("tools"), list):
        tools = [t for t in tools if t.spec.name in p["tools"]]
    rec = run.record
    if not rec.model:  # the first AI node says which model and prompt this run used
        from app.application.services import MAX_INPUT_CHARS

        rec.provider, rec.model, rec.thinking = sel["providerId"], sel["modelId"], sel.get("thinking", "off")
        rec.user_message, rec.system_prompt, rec.tools = pkg.user[: MAX_INPUT_CHARS * 2], pkg.system, [t.name for t in pkg.tools]
    client = svc._llm(sel["providerId"], prep.settings.keys[sel["providerId"]])
    hist = run.past if (not run.used_history and p.get("history", True)) else []
    run.used_history = True
    out = await run_agent(client=client, model=sel["modelId"], thinking=sel.get("thinking", "off"), system=pkg.system, user=pkg.user, tools=tools, history=hist, max_steps=int(p.get("maxSteps") or svc._max_steps))
    run.runs.append(out)
    return [[{"json": {"text": out.answer, "cards": len(out.found), "tools": [s.call.name for s in out.steps]}}]]


async def node_merge(run: Run, n: FNode, inputs: list[Item]) -> list[list[Item]]:
    return [inputs]  # (the executor has already waited for both inputs and put their items together)


async def node_respond(run: Run, n: FNode, inputs: list[Item]) -> list[list[Item]]:
    run.final = str(resolve(n.parameters.get("text") or "{{ $json.text }}", run.scope(inputs)))
    return []


NODES: dict[str, Any] = {
    "trigger.chat": node_trigger, "trigger.schedule": node_trigger, "trigger.manual": node_trigger,
    "core.if": node_if, "core.switch": node_switch, "core.filter": node_filter, "core.set": node_set, "core.sort": node_sort, "core.limit": node_limit,
    "core.split_out": node_split, "core.aggregate": node_aggregate, "core.noop": node_noop, "core.stop": node_stop,
    "data.mail": node_mail, "data.mail_body": node_mail_body, "data.contacts": node_contacts,
    "data.tasks": node_records, "data.follow_ups": node_records, "data.meetings": node_records, "data.hearings": node_records, "data.cases": node_records, "data.calendar": node_records, "data.drive": node_records,
    "core.merge": node_merge, "msg.send": node_send, "msg.ask": node_ask, "ai.agent": node_agent, "respond": node_respond,
}


# --- the executor ---------------------------------------------------------------------------------------------------------------


class FlowRunner:
    def __init__(self, svc: Any) -> None:
        self._svc = svc

    async def run(self, who: Any, token: str, agent: Any, message: str, past: list[Message], preface: str, channel: str, *, trigger: str, channel_id: str | None, prepared: Any, entry: str | None = None, resume: dict[str, Any] | None = None, choice: int = 0) -> AgentRun:
        svc = self._svc
        flow = flow_of(prepared.settings.flows, agent.id)
        asked = f"{preface}\n\nThe person's latest message:\n{message}" if preface else message
        run = Run(svc, who, token, agent, prepared, past, channel, trigger, channel_id, message, asked)
        run.names = {n.id: n.name for n in flow.nodes}
        from app.application.ports import RunRecord
        from app.application.services import MAX_INPUT_CHARS

        run.record = RunRecord(id=str(uuid.uuid4()), user_id=who.user_id, agent_id=agent.id, trigger=trigger, status="ok", started_at=datetime.now(UTC), input=message[:MAX_INPUT_CHARS], channel=channel_id, agent_name=agent.name, workspace=prepared.scope)
        if resume:  # carrying on from an Ask me node: what the earlier nodes produced is restored, and the answer picks the output
            run.vars = dict(resume.get("vars") or {})
            run.names = {**run.names, **(resume.get("names") or {})}
            run.results = {nid: [[{"json": j} for j in lane] for lane in lanes] for nid, lanes in (resume.get("results") or {}).items()}
        start = flow.node(entry) if entry else next((n for n in flow.nodes if n.type in TRIGGERS and (trigger == "schedule") == (n.type == "trigger.schedule")), None) or next(n for n in flow.nodes if n.type in TRIGGERS)
        queue: deque[tuple[str, int, list[Item]]] = deque([(start.id, 0, [])])
        if resume:
            queue.clear()
            answer = {"json": {**(resume.get("item") or {}), "answer": resume["options"][choice], "question": resume["text"], "choice": choice}}
            for t in flow.targets(resume["node"], choice):
                queue.append((t["node"], t["index"], [answer]))
        waiting: dict[str, dict[int, list[Item]]] = {}
        loops: dict[str, dict[str, Any]] = {}
        began, runs, failure = time.monotonic(), 0, None
        try:
            while queue or loops:
                if not queue:  # a loop whose last batch never came back is finished with what it has
                    lid, st = next(iter(loops.items()))
                    del loops[lid]
                    for t in flow.targets(lid, 1):
                        queue.append((t["node"], t["index"], st["acc"]))
                    continue
                nid, index, items = queue.popleft()
                node = flow.node(nid)
                if node is None:
                    continue
                if node.type == "core.merge":
                    got = waiting.setdefault(nid, {})
                    got[index] = items
                    if len(got) < 2:
                        continue
                    items = [i for k in sorted(got) for i in got[k]]
                    del waiting[nid]
                runs += 1
                if runs > MAX_RUNS:
                    raise ValidationFailed("This workflow ran too many steps and was stopped.")
                t0 = time.monotonic()
                row: dict[str, Any] = {"node": node.id, "name": node.name or node.type, "type": node.type, "state": "ok", "itemsIn": len(items), "in": _clip([i["json"] for i in items[:SAMPLE]])}
                try:
                    if node.disabled or node.type == "core.note":
                        outs = [items]
                    elif node.type == "core.loop":
                        size = max(1, int(node.parameters.get("batchSize") or 1))
                        st = loops.get(nid)
                        if st is None:
                            st = loops[nid] = {"rest": items[size:], "acc": []}
                            outs = [items[:size], []]
                        else:
                            st["acc"] += items
                            if st["rest"]:
                                outs, st["rest"] = [st["rest"][:size], []], st["rest"][size:]
                            else:
                                outs = [[], st["acc"]]
                                del loops[nid]
                    else:
                        outs = await NODES[node.type](run, node, items)
                    run.results[node.id] = outs
                    row["outputs"] = [len(o) for o in outs]
                    row["out"] = _clip([[i["json"] for i in o[:SAMPLE]] for o in outs])
                except ExpressionError as exc:
                    row["state"], row["error"] = "error", str(exc)
                    raise ValidationFailed(f"“{node.name or node.id}”: {exc}") from exc
                except Exception as exc:
                    row["state"], row["error"] = "error", str(getattr(exc, "message", None) or exc)[:300]
                    raise
                finally:
                    row["ms"] = int((time.monotonic() - t0) * 1000)
                    run.trace.append(row)
                for i, produced in enumerate(outs):
                    for t in flow.targets(node.id, i):
                        target = flow.node(t["node"])
                        # a node with nothing arriving doesn't run, unless it is set to (so "found nobody" can still be answered)
                        if produced or (target is not None and target.parameters.get("runWhenEmpty")):
                            queue.append((t["node"], t["index"], produced))
        except AiError as exc:
            failure = exc
            if run.record is not None:
                run.record.status, run.record.error = "error", str(exc)
            raise ValidationFailed(str(exc)) from exc
        except ValidationFailed as exc:
            failure = exc
            if run.record is not None:
                run.record.status, run.record.error = "error", str(getattr(exc, "message", None) or exc)
            raise
        except Exception as exc:
            failure = exc
            if run.record is not None:
                run.record.status, run.record.error = "error", "Something went wrong inside the agent."
            log.exception("agent %s failed", agent.id)
            raise
        finally:
            if run.record is not None:
                run.record.duration_ms = int((time.monotonic() - began) * 1000)
                run.record.trace = run.trace
                if failure is None:
                    run.record.steps = [s.to_log() for r in run.runs for s in r.steps]
                    run.record.output = run.final if run.final is not None else (run.runs[-1].answer if run.runs else "")
                    run.record.found_count = sum(len(r.found) for r in run.runs)
                    run.record.usage = total_usage(run.runs)
                await svc._keep(run.record)
        if not run.runs:
            return AgentRun(run.final or "", [], [], [], {"inputTokens": 0, "outputTokens": 0})
        steps: list[Step] = [s for r in run.runs for s in r.steps]
        return AgentRun(run.final if run.final is not None else run.runs[-1].answer, steps, [f for r in run.runs for f in r.found], run.runs[-1].messages, total_usage(run.runs))


def escape(text: str) -> str:
    return html.escape(text)
