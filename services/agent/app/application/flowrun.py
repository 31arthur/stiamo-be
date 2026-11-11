"""Running a workflow (see domain/flow.py): items pass from node to node, each node leaves a trace, and the run ends with an answer.

The executor works like a queue of "these items arrived at this input of this node". A node runs, gives items on each of its outputs, and each
output's items are queued for the nodes it is connected to. A node that gets no items doesn't run (that is how a branch ends); a Merge waits for
both its inputs; a Loop over items hands out batches and collects what comes back. Every node run is kept in the run's trace with the items
that went in and came out, so an execution can be opened node by node.

Data nodes read through the same workspace-limited gateway the agent's tools use, so a workflow can only see what its run is about.
"""

from __future__ import annotations

import html
from contextvars import ContextVar
import json
import logging
import re
import time
import uuid
from collections import deque
from datetime import UTC, date, datetime, timedelta
from typing import Any

from stiamo_common.errors import ValidationFailed

from app.application.runner import AgentRun, Step, run_agent
from app.domain.dayplan import is_open
from app.domain.flow import MAX_RUNS, TRIGGERS, Flow, FNode, default_flow, entry_order, validate
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


# --- live progress: which node a run is on, so the editor can light it up while the run is still going ---------------------------------------
# Kept in memory, per (user, id the editor made up): it only matters for the minute a run takes, and a restart simply ends it.

_PROGRESS: dict[tuple[str, str], dict[str, Any]] = {}
_PROGRESS_KEEP = 40
_PROGRESS_SECONDS = 600
current_progress: ContextVar[tuple[str, str] | None] = ContextVar("current_progress", default=None)


def progress_begin(user: str, pid: str) -> tuple[str, str]:
    now = time.monotonic()
    for k in [k for k, v in _PROGRESS.items() if now - v["t"] > _PROGRESS_SECONDS]:
        del _PROGRESS[k]
    while len(_PROGRESS) >= _PROGRESS_KEEP:
        del _PROGRESS[next(iter(_PROGRESS))]
    _PROGRESS[(user, pid)] = {"t": now, "nodes": {}, "done": False}
    return (user, pid)


def progress_note(key: tuple[str, str] | None, node: str, state: str, ms: int | None = None, outputs: list[int] | None = None) -> None:
    entry = _PROGRESS.get(key) if key else None
    if entry is None:
        return
    prev = entry["nodes"].get(node, {})
    entry["nodes"][node] = {"state": state, "runs": prev.get("runs", 0) + (1 if state == "running" else 0), **({"ms": ms} if ms is not None else {}), **({"outputs": outputs} if outputs is not None else {})}


def progress_done(key: tuple[str, str] | None) -> None:
    if key and key in _PROGRESS:
        _PROGRESS[key]["done"] = True


def progress_get(user: str, pid: str) -> dict[str, Any]:
    """What a run has done so far: each node's state (running, ok, error) and whether the run is over. Nothing yet: an empty answer."""
    entry = _PROGRESS.get((user, pid))
    return {"nodes": dict(entry["nodes"]), "done": entry["done"]} if entry else {"nodes": {}, "done": False}


class Run:
    """Everything one execution of a workflow shares: the data it may read, what earlier nodes produced, the answer so far."""

    def __init__(self, svc: Any, who: Any, token: str, agent: Any, prep: Any, past: list[Message], channel: str, trigger: str, channel_id: str | None, message: str, asked: str) -> None:
        self.svc, self.who, self.token, self.agent, self.prep, self.past, self.channel, self.trigger, self.channel_id = svc, who, token, agent, prep, past, channel, trigger, channel_id
        self.message, self.asked = message, asked
        self.source = ""  # where the message came from beyond the channel: "orchestrator" when the master assistant handed it over
        self.vars: dict[str, Any] = {}
        self.results: dict[str, list[list[Item]]] = {}
        self.names: dict[str, str] = {}
        self.runs: list[AgentRun] = []
        self.final: str | None = None
        self.used_history = False
        self.record: Any = None
        self.trace: list[dict[str, Any]] = []
        #: Items "Only new items" let through, to be remembered once the run has gone well.
        self.to_remember: list[tuple[str, str]] = []

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
    return [[{"json": {"message": run.message, "asked": run.asked, "workspace": run.workspace, "source": run.source or run.trigger}}]]


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


async def node_telegram(run: Run, n: FNode, inputs: list[Item]) -> list[list[Item]]:
    """Sends with one named Telegram bot (the node's credential), to all of its linked chats or to one. A bot limited to a workspace is only used by
    a workflow running in that workspace, so nothing about another workspace can reach it."""
    p = n.parameters
    credential, chat = str(p.get("credential") or "").strip(), str(p.get("chat") or "").strip()
    if not credential:
        raise ValidationFailed("Choose the Telegram bot to send with.")
    if run.svc.send_telegram is None:
        raise ValidationFailed("Telegram isn't available here.")
    out: list[Item] = []
    for it in (inputs or [{"json": {}}])[: 1 if p.get("once", True) else 10]:
        text = str(resolve(p.get("text") or "{{ $json.text }}", run.scope(inputs, it))).strip()
        if not text:
            raise ValidationFailed("There is nothing to send: the text is empty.")
        sent = await run.svc.send_telegram(run.who, run.workspace, credential, chat or None, text)
        out.append({"json": {"sent": sent, "text": text, "chat": chat or "all linked chats"}})
    return [out]


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


_EMAIL = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")


async def node_mail_draft(run: Run, n: FNode, inputs: list[Item]) -> list[list[Item]]:
    """Writes an email and keeps it as a proposal. Nothing is sent: approving the proposal (in Needs me, or YES on the phone) sends it, from this
    workspace's mailbox."""
    p = resolve(n.parameters, run.scope(inputs))
    to = [a.strip() for a in re.split(r"[,;]", str(p.get("to") or "")) if a.strip()]
    if not to or any(not _EMAIL.match(a) for a in to):
        raise ValidationFailed(f"“{', '.join(to) or 'nobody'}” isn't an email address to write to.")
    boxes = await run.prep.data.mail_accounts()
    if not boxes:
        raise ValidationFailed("This workspace has no mailbox to send from.")
    box = next((b for b in boxes if str(b.get("id")) == str(p.get("account") or "")), boxes[0])
    subject, text = str(p.get("subject") or "").strip()[:200], str(p.get("text") or "").strip()
    if not text:
        raise ValidationFailed("The email has no text.")
    propose = run.svc._proposer(run.who, run.agent.id, run.channel_id)
    if propose is None:
        raise ValidationFailed("Proposals aren't kept here, so a draft can't be made.")
    pid = await propose("mail.send", f"Email to {', '.join(to)}: {subject or '(no subject)'}", [f"From {box.get('address') or box.get('label') or 'your mailbox'}", f"Subject: {subject}", text[:400]], [{"mail": {"account": str(box["id"]), "message": {"to": to, "subject": subject, "text": text}}}])
    if p.get("tell", True) and run.svc.notify is not None:
        await run.svc.notify(run.who, run.workspace, f"✉️ Draft ready for {', '.join(to)}: “{subject or text[:40]}”. Approve it in Needs me to send it.")
    return [[{"json": {"proposal": pid, "to": to, "subject": subject, "text": text}}]]


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
    key = prep.settings.keys.get(sel["providerId"])
    if not key:
        raise ValidationFailed(f"“{n.name or n.id}” uses {sel['providerId']}, which has no key connected. Add one under Credentials, or pick another model.")
    client = svc._llm(sel["providerId"], key)
    hist = run.past if (not run.used_history and p.get("history", True)) else []
    run.used_history = True
    out = await run_agent(client=client, model=sel["modelId"], thinking=sel.get("thinking", "off"), system=pkg.system, user=pkg.user, tools=tools, history=hist, max_steps=int(p.get("maxSteps") or svc._max_steps))
    run.runs.append(out)
    return [[{"json": {"text": out.answer, "cards": len(out.found), "tools": [s.call.name for s in out.steps], "model": f"{sel['providerId']}/{sel['modelId']}"}}]]


# --- sorting mail: the older kind of agent's judging, as nodes ------------------------------------------------------------------------------

MAX_SORT = 50


def _mail_item(i: int, j: dict[str, Any]) -> Any:
    """An item of the workflow as something to judge: its title and text (a mail's sender, subject and start), and who it is from."""
    from app.domain.workflows import MAX_ITEM_CHARS
    from app.domain.workflows import Item as Judged

    address = str(j.get("fromAddress") or "").strip().lower()
    name = str(j.get("from") or "").strip()
    who = f"{name} <{address}>" if name and address and name != address else (address or name)
    title = str(j.get("subject") or j.get("title") or j.get("name") or "(no subject)")[:120]
    text = str(j.get("text") or "") or f"From: {who}\nSubject: {title}\n\n{j.get('snippet') or ''}"
    return Judged(f"i{i}", title, text[:MAX_ITEM_CHARS], j.get("date"), {"from": address})


def _sorter(run: Run) -> Any:
    if run.svc.sorter is None:
        raise ValidationFailed("Sorting isn't available here.")
    return run.svc.sorter()


def _model_of(p: dict[str, Any]) -> dict[str, Any] | None:
    m = p.get("model")
    return m if isinstance(m, dict) and m.get("modelId") else None


async def node_classify(run: Run, n: FNode, inputs: list[Item]) -> list[list[Item]]:
    """Gives each item one of the labels (or "not sure"): the same judging, thresholds and models as an agent of the older kind. One output for each
    label, and the last for what it isn't sure about or couldn't read."""
    p = resolve({k: v for k, v in n.parameters.items() if k not in ("labels", "prompt")}, run.scope(inputs))
    labels = [{"name": str(x.get("name") or "").strip(), "description": str(x.get("description") or "").strip(), **({"threshold": float(x["threshold"])} if x.get("threshold") not in (None, "") else {})} for x in (n.parameters.get("labels") or []) if isinstance(x, dict)]
    lanes: list[list[Item]] = [[] for _ in range(len(labels) + 1)]
    chosen = inputs[:MAX_SORT]
    if not chosen:
        return lanes
    outcomes = await _sorter(run).sort_items(
        run.who, run.prep.settings, run.agent.id, [_mail_item(i, it["json"]) for i, it in enumerate(chosen)],
        prompt=str(n.parameters.get("prompt") or ""), labels=labels, decider="jev" if p.get("decider") == "jev" else "model", decision_model=str(p.get("decisionModel") or "") or None,
        batch_size=max(1, min(int(p.get("batchSize") or 1), 50)), default_threshold=float(p.get("threshold") if p.get("threshold") not in (None, "") else 0.7), selection=_model_of(p),
    )
    if outcomes and all(o.status == "failed" for o in outcomes):
        raise ValidationFailed(f"Nothing could be judged: {outcomes[0].error}")
    names = [x["name"] for x in labels]
    by = {o.item.id: o for o in outcomes}
    for i, it in enumerate(chosen):
        o = by.get(f"i{i}")
        base = dict(it["json"])
        if o is None or o.status == "failed" or o.label is None or o.status == "needs_review":
            lanes[-1].append({"json": {**base, "label": o.label if o else None, "confidence": round(o.confidence, 3) if o else 0.0, "reason": (o.error or o.reason) if o else "No answer.", "sure": False}})
        else:
            lanes[names.index(o.label)].append({"json": {**base, "label": o.label, "confidence": round(o.confidence, 3), "reason": o.reason, "sure": True}})
    return lanes


async def node_safe_senders(run: Run, n: FNode, inputs: list[Item]) -> list[list[Item]]:
    """Keeps mail from people you trust out of the judging: the first output carries on, the second is what was kept and why. Your correspondents
    are the addresses found in the mailbox's Sent folder."""
    from app.domain.workflows import Rules, protected_by

    p = n.parameters
    rules = Rules(
        protected_domains=[str(x).strip().lower().lstrip("@").removeprefix("*.") for x in (p.get("domains") or []) if str(x).strip()],
        protected_phrases=[str(x).strip().lower() for x in (p.get("phrases") or []) if str(x).strip()],
        protected_addresses=[str(x).strip().lower() for x in (p.get("addresses") or []) if "@" in str(x)],
        protect_correspondents=p.get("correspondents", True) is not False,
    )
    known: dict[str, set[str]] = {}
    go, kept = [], []
    for k, it in enumerate(inputs):
        j = it["json"]
        account = str(j.get("account") or "")
        if rules.protect_correspondents and account and account not in known:
            try:
                known[account] = {a.lower() for a in await run.prep.data.mail_correspondents(account)}
            except AiError:
                known[account] = set()
        why = protected_by(_mail_item(k, j), rules, known.get(account, set()))
        (kept if why else go).append({"json": {**j, **({"kept": why} if why else {})}})
    return [go, kept]


async def node_read(run: Run, n: FNode, inputs: list[Item]) -> list[list[Item]]:
    """Opens each email in full and says what it is about, how urgent it is, what to do, and for the event route the date it names."""
    p = n.parameters
    route = str(p.get("route") or "attention")
    route = route if route in ("attention", "event", "alert") else "attention"
    out: list[Item] = []
    for it in inputs[:10]:
        j = it["json"]
        email = _mail_item(0, j).text
        folder = str(p.get("folder") or j.get("folder") or "INBOX")
        full: dict[str, Any] = {}
        if j.get("account") and j.get("uid") is not None:
            try:  # the list only has the start of an email: a date or a deadline may be further down
                full = await run.prep.data.mail_message(str(j["account"]), folder, int(j["uid"]))
                sender = full.get("sender") or {}
                email = f"From: {sender.get('name', '')} <{sender.get('address', '')}>\nSubject: {full.get('subject', '')}\n\n{str(full.get('text') or '')[:4000]}"
            except (AiError, ValueError, TypeError):
                pass
        try:
            parsed, _, _ = await _sorter(run).read_one(run.who, run.prep.settings, run.agent.id, prompt=str(p.get("prompt") or ""), route=route, email=email, selection=_model_of(p))
        except AiError as exc:
            raise ValidationFailed(str(getattr(exc, "message", None) or exc)) from exc
        sender = full.get("sender") or {}
        read = parsed or {"summary": str(j.get("subject") or "")[:200], "priority": "normal", "action": None, "due": None, "event": None, "note": "It couldn't be summarised."}
        out.append({"json": {**j, **read, "folder": folder, "senderName": sender.get("name") or j.get("from") or "", "senderAddress": sender.get("address") or j.get("fromAddress") or "", "messageId": full.get("messageId") or "", "subject": full.get("subject") or j.get("subject")}})
    return [out]


async def node_trash(run: Run, n: FNode, inputs: list[Item]) -> list[list[Item]]:
    """Moves emails to Trash, never deleting them. It asks first (one proposal for all of them, to approve in Needs me or with YES) unless the
    node is set to move at once, and even then a workflow run by hand only proposes."""
    p = n.parameters
    rows = [it["json"] for it in inputs if it["json"].get("account") and it["json"].get("uid") is not None]
    if not rows:
        return [[{"json": {"moved": 0, "proposal": None}}]]
    folder = str(p.get("folder") or "INBOX")
    now = bool(p.get("auto")) and run.trigger != "manual"
    by_account: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_account.setdefault(str(r["account"]), []).append(r)
    if now:
        moved = 0
        for account, mine in by_account.items():
            try:
                moved += await run.prep.data.mail_trash(account, folder, [int(r["uid"]) for r in mine])
            except AiError as exc:
                raise ValidationFailed(str(getattr(exc, "message", None) or exc)) from exc
        return [[{"json": {"moved": moved or len(rows), "proposal": None}}]]
    propose = run.svc._proposer(run.who, run.agent.id, run.channel_id)
    if propose is None:
        raise ValidationFailed("Proposals aren't kept here, so this can't ask first.")
    titles = [f"{str(r.get('subject') or '(no subject)')[:70]} · {r.get('from') or ''}" for r in rows]
    pid = await propose("mail.trash", f"Move {len(rows)} email{'s' if len(rows) != 1 else ''} to Trash", titles[:12] + ([f"…and {len(rows) - 12} more"] if len(rows) > 12 else []), [{"trash": {"account": a, "folder": folder, "uids": [int(r["uid"]) for r in mine]}} for a, mine in by_account.items()])
    if p.get("tell", True) and run.svc.notify is not None:
        await run.svc.notify(run.who, run.workspace, f"🗑️ {len(rows)} email{'s' if len(rows) != 1 else ''} to move to Trash. Approve it in Needs me, or say YES.")
    return [[{"json": {"moved": 0, "proposal": pid, "count": len(rows)}}]]


async def node_alert(run: Run, n: FNode, inputs: list[Item]) -> list[list[Item]]:
    """Pings the phone about each email (up to five), with what it says and a few replies to choose from. An email is only ever raised once."""
    if run.svc.raise_alert is None:
        raise ValidationFailed("Alerts aren't available here.")
    out: list[Item] = []
    for it in inputs[:5]:
        j = it["json"]
        if not j.get("account") or j.get("uid") is None:
            continue
        try:
            raised = await run.svc.raise_alert(
                run.who, run.token, workflow_id=run.agent.id, account_id=str(j["account"]), folder=str(j.get("folder") or "INBOX"), uid=int(j["uid"]), item_id=str(j.get("id") or f"{j['account']}:{j['uid']}"),
                subject=str(j.get("subject") or ""), sender_name=str(j.get("senderName") or ""), sender_address=str(j.get("senderAddress") or j.get("fromAddress") or ""), message_id=str(j.get("messageId") or ""),
                analysis={"summary": j.get("summary"), "priority": j.get("priority") or "high", "action": j.get("action"), "due": j.get("due"), "replies": j.get("replies")}, call=bool(n.parameters.get("call")),
            )
        except (AiError, ValueError, TypeError) as exc:
            raise ValidationFailed(str(getattr(exc, "message", None) or exc)) from exc
        out.append({"json": {**j, "alerted": raised is not None}})
    return [out]


async def node_event(run: Run, n: FNode, inputs: list[Item]) -> list[list[Item]]:
    """Puts the event an email names in front of you to approve. Only an event that has a title and a date is proposed; nothing reaches the calendar
    until you approve it."""
    from app.domain.workflows import clean_event, event_to_meeting

    propose = run.svc._proposer(run.who, run.agent.id, run.channel_id)
    if propose is None:
        raise ValidationFailed("Proposals aren't kept here, so an event can't be proposed.")
    out: list[Item] = []
    for it in inputs[:10]:
        j = it["json"]
        event = clean_event(j.get("event"))
        if event is None:
            continue
        sender = f"{j.get('senderName') or ''} <{j.get('senderAddress') or ''}>".strip()
        record = event_to_meeting(event, record_id=str(uuid.uuid4()), workspace=str(n.parameters.get("workspace") or run.workspace or "personal"), summary=str(j.get("summary") or ""), subject=str(j.get("subject") or ""), sender=sender)
        pid = await propose("event.create", f"Add to the calendar: {event['title']}", [f"{event['date']} {event.get('start') or ''}".strip(), f"From the email “{j.get('subject') or ''}”"], [{"meeting": record}])
        out.append({"json": {**j, "proposal": pid}})
    if out and n.parameters.get("tell", True) and run.svc.notify is not None:
        await run.svc.notify(run.who, run.workspace, f"📅 {len(out)} event{'s' if len(out) != 1 else ''} found in your mail. Approve in Needs me to add {'them' if len(out) != 1 else 'it'} to the calendar.")
    return [out]



async def node_only_new(run: Run, n: FNode, inputs: list[Item]) -> list[list[Item]]:
    """Lets through the items this agent has not handled before. What passes is remembered once the whole run has gone well, so a run that fails
    leaves them to be tried again. A run by hand lets everything through and remembers nothing: trying a workflow never uses it up."""
    if run.trigger == "manual" or run.svc.ledger is None:
        return [inputs]
    seen = await run.svc.ledger().seen(run.agent.id)
    fresh: list[Item] = []
    here: set[str] = set()
    for it in inputs:
        key = str(it["json"].get("id") or "")
        if not key:
            fresh.append(it)
        elif key not in seen and key not in here:
            here.add(key)
            fresh.append(it)
            run.to_remember.append((key, str(it["json"].get("subject") or it["json"].get("title") or "")[:200]))
    return [fresh]


async def node_attention(run: Run, n: FNode, inputs: list[Item]) -> list[list[Item]]:
    """Puts each email in the Attention queue with what was worked out about it. One already there (or already dealt with) is left alone, so
    running again doesn't bring back what you have settled."""
    from app.application.ports import LedgerEntry
    from app.domain.workflows import clean_event

    if run.svc.ledger is None:
        raise ValidationFailed("The Attention queue isn't available here.")
    repo = run.svc.ledger()
    out: list[Item] = []
    for it in inputs[:50]:
        j = it["json"]
        key = str(j.get("id") or "")
        if not key:
            continue
        event = clean_event(j.get("event"))
        added = await repo.entry(run.who.user_id, run.agent.id, key) is None
        if added:
            data = {"summary": j.get("summary") or j.get("subject"), "priority": j.get("priority") or "normal", "action": j.get("action"), "due": j.get("due"), "event": event, "note": j.get("note"), "folder": j.get("folder") or "INBOX", "account": j.get("account"), "uid": j.get("uid")}
            excerpt = f"From: {j.get('senderName') or j.get('from') or ''} <{j.get('senderAddress') or j.get('fromAddress') or ''}>\nSubject: {j.get('subject') or ''}\n\n{str(j.get('snippet') or '')[:600]}"
            await repo.record([LedgerEntry(str(uuid.uuid4()), run.agent.id, run.who.user_id, key, str(j.get("subject") or "(no subject)")[:200], excerpt[:1500], str(j.get("label") or "") or None, float(j.get("confidence") or 0), str(j.get("reason") or ""), "event_proposed" if event else "queued", run.record.id if run.record else "", 1, None, None, data)])
        out.append({"json": {**j, "queued": added}})
    return [out]



async def node_merge(run: Run, n: FNode, inputs: list[Item]) -> list[list[Item]]:
    return [inputs]  # (the executor has already waited for both inputs and put their items together)


async def node_respond(run: Run, n: FNode, inputs: list[Item]) -> list[list[Item]]:
    run.final = str(resolve(n.parameters.get("text") or "{{ $json.text }}", run.scope(inputs)))
    return []


NODES: dict[str, Any] = {
    "trigger.chat": node_trigger, "trigger.telegram": node_trigger, "trigger.orchestrator": node_trigger, "trigger.schedule": node_trigger, "trigger.manual": node_trigger,
    "core.if": node_if, "core.switch": node_switch, "core.filter": node_filter, "core.set": node_set, "core.sort": node_sort, "core.limit": node_limit,
    "core.split_out": node_split, "core.aggregate": node_aggregate, "core.noop": node_noop, "core.stop": node_stop,
    "data.mail": node_mail, "data.mail_body": node_mail_body, "data.contacts": node_contacts,
    "data.tasks": node_records, "data.follow_ups": node_records, "data.meetings": node_records, "data.hearings": node_records, "data.cases": node_records, "data.calendar": node_records, "data.drive": node_records,
    "core.merge": node_merge, "msg.send": node_send, "msg.ask": node_ask, "msg.telegram": node_telegram, "msg.mail": node_mail_draft, "ai.classify": node_classify, "ai.read": node_read, "core.safe_senders": node_safe_senders, "core.only_new": node_only_new, "msg.attention": node_attention, "msg.trash": node_trash, "msg.alert": node_alert, "msg.event": node_event, "ai.agent": node_agent, "respond": node_respond,
}


# --- the executor ---------------------------------------------------------------------------------------------------------------


class FlowRunner:
    def __init__(self, svc: Any) -> None:
        self._svc = svc

    async def run(self, who: Any, token: str, agent: Any, message: str, past: list[Message], preface: str, channel: str, *, trigger: str, channel_id: str | None, prepared: Any, entry: str | None = None, resume: dict[str, Any] | None = None, choice: int = 0, source: str = "") -> AgentRun:
        svc = self._svc
        flow = flow_of(prepared.settings.flows, agent.id)
        asked = f"{preface}\n\nThe person's latest message:\n{message}" if preface else message
        run = Run(svc, who, token, agent, prepared, past, channel, trigger, channel_id, message, asked)
        run.names = {n.id: n.name for n in flow.nodes}
        run.source = source
        from app.application.ports import RunRecord
        from app.application.services import MAX_INPUT_CHARS

        run.record = RunRecord(id=str(uuid.uuid4()), user_id=who.user_id, agent_id=agent.id, trigger=trigger, status="ok", started_at=datetime.now(UTC), input=message[:MAX_INPUT_CHARS], channel=channel_id, agent_name=agent.name, workspace=prepared.scope)
        if resume:  # carrying on from an Ask me node: what the earlier nodes produced is restored, and the answer picks the output
            run.vars = dict(resume.get("vars") or {})
            run.names = {**run.names, **(resume.get("names") or {})}
            run.results = {nid: [[{"json": j} for j in lane] for lane in lanes] for nid, lanes in (resume.get("results") or {}).items()}
        order = entry_order(trigger, source)
        start = flow.node(entry) if entry else min((n for n in flow.nodes if n.type in TRIGGERS), key=lambda n: order.index(n.type) if n.type in order else len(order))
        queue: deque[tuple[str, int, list[Item]]] = deque([(start.id, 0, [])])
        if resume:
            queue.clear()
            answer = {"json": {**(resume.get("item") or {}), "answer": resume["options"][choice], "question": resume["text"], "choice": choice}}
            for t in flow.targets(resume["node"], choice):
                queue.append((t["node"], t["index"], [answer]))
        waiting: dict[str, dict[int, list[Item]]] = {}
        loops: dict[str, dict[str, Any]] = {}
        began, runs, failure = time.monotonic(), 0, None
        pkey = current_progress.get()
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
                progress_note(pkey, node.id, "running")
                row: dict[str, Any] = {"node": node.id, "name": node.name or node.type, "type": node.type, "state": "ok", "itemsIn": len(items), "in": _clip([i["json"] for i in items[:SAMPLE]])}
                try:
                    if node.disabled or node.type == "core.note":
                        outs = [items]
                    elif node.pinned is not None and trigger == "manual":  # a hand-run uses the data pinned on the node instead of running it
                        outs = [[{"json": dict(j)} for j in node.pinned]] + [[] for _ in range(max(node.outputs(), 1) - 1)]
                        row["pinned"] = True
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
                    progress_note(pkey, node.id, row["state"], row["ms"], row.get("outputs"))
                for i, produced in enumerate(outs):
                    for t in flow.targets(node.id, i):
                        target = flow.node(t["node"])
                        # a node with nothing arriving doesn't run, unless it is set to (so "found nobody" can still be answered)
                        if produced or (target is not None and target.parameters.get("runWhenEmpty")):
                            queue.append((t["node"], t["index"], produced))
            if run.to_remember and svc.ledger is not None:  # the run went well: what it handled is not handled again
                from app.application.ports import LedgerEntry

                repo = svc.ledger()
                known = await repo.seen(agent.id)
                await repo.record([LedgerEntry(str(uuid.uuid4()), agent.id, who.user_id, k, t, "", None, 0.0, "", "seen", run.record.id if run.record else "", 1) for k, t in run.to_remember if k not in known])
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


#: Nodes that reach beyond the workflow (a message to your phone, a draft in your approvals): running one alone needs you to say so.
SIDE_EFFECTS = ("msg.send", "msg.ask", "msg.mail", "msg.telegram", "msg.trash", "msg.alert", "msg.event", "msg.attention")


async def run_one(svc: Any, who: Any, token: str, agent: Any, prepared: Any, node_id: str, items: list[dict[str, Any]] | None, confirm: bool) -> dict[str, Any]:
    """Runs a single node of the saved workflow with the items you give (or, without any, the data pinned on the nodes leading into it) and says
    what came out, without making a run of it. The other nodes' pinned data is what `$node[...]` sees."""
    flow = flow_of(prepared.settings.flows, agent.id)
    node = flow.node(node_id)
    if node is None or node.type == "core.note":
        raise ValidationFailed("That node isn't in the saved workflow. Save first.")
    if node.type in SIDE_EFFECTS and not confirm:
        raise ValidationFailed("This node sends something outside the workflow. Run it with the confirm option if you mean it.")
    run = Run(svc, who, token, agent, prepared, [], "the web app", "manual", None, "Test message", "Test message")
    run.names = {n.id: n.name for n in flow.nodes}
    run.results = {n.id: [[{"json": dict(j)} for j in (n.pinned or [])]] for n in flow.nodes if n.pinned is not None}
    if items is not None:
        given = [{"json": dict(i)} for i in items[:50]]
    else:
        feeders = [src for src, outs in flow.connections.items() for o in outs for t in o if t["node"] == node_id]
        given = [{"json": dict(j)} for src in feeders for j in ((flow.node(src).pinned or []) if flow.node(src) else [])]
    t0 = time.monotonic()
    try:
        outs = await NODES[node.type](run, node, given) if node.type in NODES else [given]
        error = None
    except (ExpressionError, ValidationFailed, AiError) as exc:
        outs, error = [], str(getattr(exc, "message", None) or exc)
    return {"outputs": [[i["json"] for i in o[:SAMPLE * 10]] for o in outs], "itemsIn": len(given), "ms": int((time.monotonic() - t0) * 1000), **({"error": error} if error else {})}
