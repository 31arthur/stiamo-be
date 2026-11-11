"""A workflow as n8n draws one: nodes on a canvas, joined output to input, passing lists of items between them. Pure: no network, no framework.

An **item** is `{"json": {...}}`. A node takes the items that arrive on its input and gives items on each of its outputs (an IF has two: true and
false; a Switch has one per rule and a fallback). Connections are stored per node, per output index, as a list of targets, so one output may
feed several nodes. A node whose input gets nothing doesn't run: that is how a branch ends.

Node types are written for this app, not copied from anywhere: triggers, flow control (IF, Switch, Merge, Loop Over Items, Filter, Sort, Limit,
Split Out, Aggregate, Edit Fields), this app's own data (mail, contacts, tasks, follow-ups, meetings, hearings, cases, calendar, drive), sending
a message, the AI agent, and the answer. See application/flownodes.py for what each one does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

MAX_NODES = 60
MAX_AGENTS = 8
MAX_RUNS = 400
OPS = ("contains", "not_contains", "equals", "not_equals", "matches", "empty", "not_empty", "gt", "lt", "starts_with")

#: type -> (group, label, what it does). The order is the order of the node palette.
CATALOG: dict[str, tuple[str, str, str]] = {
    "trigger.chat": ("Triggers", "When a message comes in", "Starts the workflow with what was asked, from the app's chat, Telegram or WhatsApp."),
    "trigger.telegram": ("Triggers", "On a Telegram message", "Starts the workflow when a message arrives on a Telegram bot linked to this agent. Without one, the chat trigger answers Telegram too."),
    "trigger.orchestrator": ("Triggers", "When called by the orchestrator", "Starts the workflow when the master assistant hands it a message. Without one, the chat trigger answers it."),
    "trigger.schedule": ("Triggers", "On a schedule", "Starts the workflow at a set time of day (the evening plan uses this)."),
    "trigger.manual": ("Triggers", "Run it by hand", "Starts the workflow when you press Run."),
    "core.if": ("Flow", "If", "Sends each item to “true” or “false” by a condition."),
    "core.switch": ("Flow", "Switch", "Sends each item to the first rule it matches, or to the fallback."),
    "core.merge": ("Flow", "Merge", "Waits for both inputs and puts their items together."),
    "core.loop": ("Flow", "Loop over items", "Works through the items in batches: “loop” gives a batch, “done” gives everything at the end."),
    "core.filter": ("Flow", "Filter", "Keeps the items that match."),
    "core.sort": ("Flow", "Sort", "Orders the items by a field."),
    "core.limit": ("Flow", "Limit", "Keeps the first items."),
    "core.split_out": ("Flow", "Split out", "Turns a list inside an item into one item each."),
    "core.aggregate": ("Flow", "Aggregate", "Puts all the items into one."),
    "core.set": ("Flow", "Edit fields", "Sets fields on each item, from expressions."),
    "core.noop": ("Flow", "Do nothing", "Passes items on unchanged (a place to attach a note)."),
    "core.stop": ("Flow", "Stop and fail", "Ends the run with an error you write."),
    "core.note": ("Flow", "Sticky note", "A note on the canvas. It does nothing."),
    "data.mail": ("Mail", "Read mail", "Recent messages from this workspace's mailboxes."),
    "data.mail_body": ("Mail", "Read a message", "The full text of one message."),
    "data.contacts": ("Contacts", "Find contacts", "People from this workspace's Google contacts."),
    "data.tasks": ("Work", "Get tasks", "This workspace's tasks."),
    "data.follow_ups": ("Work", "Get follow-ups", "This workspace's calls and messages to make."),
    "data.meetings": ("Work", "Get meetings", "This workspace's meetings."),
    "data.hearings": ("Work", "Get hearings", "Hearings (S&B)."),
    "data.cases": ("Work", "Get cases", "Cases (S&B)."),
    "data.calendar": ("Work", "Get calendar events", "Events from this workspace's Google calendars."),
    "data.drive": ("Work", "Search the Drive", "Documents in the Drive (S&B)."),
    "msg.send": ("Messages", "Send a message", "Sends text to your phone: the master bot on Telegram."),
    "msg.telegram": ("Messages", "Telegram", "Sends a message with one of your Telegram bots: pick the bot (its credential) and, if you like, one chat. Cannot send for another workspace than the bot's."),
    "msg.mail": ("Mail", "Draft an email", "Writes an email for you to approve. Nothing is sent until you approve it (in Needs me, or by saying YES)."),
    "msg.ask": ("Messages", "Ask me", "Asks you on your phone with buttons and waits. The button you press decides which output the workflow carries on from."),
    "ai.agent": ("AI", "AI agent", "A model that reads, calls the tools you give it and answers."),
    "respond": ("Output", "Answer", "Ends the run with the answer to give."),
}
TRIGGERS = ("trigger.chat", "trigger.telegram", "trigger.orchestrator", "trigger.schedule", "trigger.manual")


def entry_order(trigger: str, source: str = "") -> list[str]:
    """Which trigger types a run starts from, best first: where the message came from decides, and a workflow with only some of them still runs
    (from the first that is there, a schedule trigger last)."""
    first = {"orchestrator": ["trigger.orchestrator"], "telegram": ["trigger.telegram"], "schedule": ["trigger.schedule"], "manual": ["trigger.manual"]}
    wanted = first.get(source) or first.get(trigger) or []
    return [*wanted, "trigger.chat", "trigger.manual", "trigger.telegram", "trigger.orchestrator", "trigger.schedule"] if trigger != "schedule" else ["trigger.schedule", "trigger.chat", "trigger.manual", "trigger.telegram", "trigger.orchestrator"]


@dataclass
class FNode:
    id: str
    type: str
    name: str = ""
    position: tuple[float, float] = (0.0, 0.0)
    parameters: dict[str, Any] = field(default_factory=dict)
    disabled: bool = False
    #: Items to use in place of what this node would give, when a workflow is run by hand (to try the rest of it with fixed data). None: not pinned.
    pinned: list[dict[str, Any]] | None = None

    def outputs(self) -> int:
        p = self.parameters
        if self.type == "core.if" or self.type == "core.loop":
            return 2
        if self.type == "core.switch":
            return len(p.get("rules") or []) + 1
        if self.type == "msg.ask":
            return max(2, len(p.get("options") or []))
        if self.type in ("core.stop", "respond", "core.note"):
            return 0
        return 1

    def inputs(self) -> int:
        return 2 if self.type == "core.merge" else (0 if self.type in TRIGGERS or self.type == "core.note" else 1)

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "type": self.type, "name": self.name or CATALOG[self.type][1], "position": list(self.position), "parameters": self.parameters, **({"disabled": True} if self.disabled else {}), **({"pinData": self.pinned} if self.pinned is not None else {})}


@dataclass
class Flow:
    nodes: list[FNode]
    #: node id -> one list per output of lists of {node, index} targets
    connections: dict[str, list[list[dict[str, Any]]]]

    def node(self, node_id: str) -> FNode | None:
        return next((n for n in self.nodes if n.id == node_id), None)

    def targets(self, node_id: str, output: int) -> list[dict[str, Any]]:
        outs = self.connections.get(node_id) or []
        return list(outs[output]) if output < len(outs) and outs[output] else []

    def to_json(self) -> dict[str, Any]:
        return {"nodes": [n.to_json() for n in self.nodes], "connections": {k: {"main": v} for k, v in self.connections.items()}}

    @staticmethod
    def from_json(raw: Any) -> Flow:
        """Reads a workflow from what was sent or stored. Raises ValueError, in words for a person, when it isn't one."""
        if not isinstance(raw, dict) or not isinstance(raw.get("nodes"), list) or not isinstance(raw.get("connections", {}), dict):
            raise ValueError("A workflow needs nodes and connections.")
        nodes: list[FNode] = []
        for n in raw["nodes"][: MAX_NODES + 1]:
            if not isinstance(n, dict) or not isinstance(n.get("id"), str) or n.get("type") not in CATALOG:
                raise ValueError("Every node needs an id and a known type.")
            pos = n.get("position") or [0, 0]
            pin = n.get("pinData")
            if pin is not None and (not isinstance(pin, list) or len(pin) > 50 or any(not isinstance(i, dict) for i in pin) or len(str(pin)) > 100_000):
                raise ValueError("Pinned data is a list of up to 50 items, each an object, and not too long.")
            nodes.append(FNode(n["id"][:40], n["type"], str(n.get("name") or "")[:60], (float(pos[0]), float(pos[1])), dict(n.get("parameters") or {}), bool(n.get("disabled")), pin))
        conns: dict[str, list[list[dict[str, Any]]]] = {}
        for src, v in (raw.get("connections") or {}).items():
            main = (v or {}).get("main") if isinstance(v, dict) else None
            if not isinstance(main, list):
                raise ValueError(f"The connections of “{src}” can't be read.")
            conns[str(src)] = [[{"node": str(t["node"]), "index": int(t.get("index", 0))} for t in (out or []) if isinstance(t, dict) and "node" in t] for out in main]
        return Flow(nodes, conns)


def validate(f: Flow) -> list[str]:
    """What would stop a workflow from running, in words for a person. Empty: it can run."""
    problems: list[str] = []
    if len(f.nodes) > MAX_NODES:
        return [f"A workflow can have at most {MAX_NODES} nodes."]
    ids = [n.id for n in f.nodes]
    if len(set(ids)) != len(ids):
        problems.append("Two nodes have the same id.")
    by = {n.id: n for n in f.nodes}
    live = [n for n in f.nodes if n.type != "core.note"]
    if not any(n.type in TRIGGERS for n in live):
        problems.append("A workflow needs a trigger to start from.")
    if not any(n.type in ("ai.agent", "respond", "msg.send", "msg.telegram") for n in live):
        problems.append("A workflow needs an AI agent, an Answer or a Send a message node, so it does something with what it finds.")
    if sum(1 for n in f.nodes if n.type == "ai.agent") > MAX_AGENTS:
        problems.append(f"A workflow can have at most {MAX_AGENTS} AI agent nodes.")
    for src, outs in f.connections.items():
        if src not in by:
            problems.append(f"A connection starts at a node that isn't there ({src}).")
            continue
        if len(outs) > max(by[src].outputs(), 1) and any(outs[by[src].outputs():]):
            problems.append(f"“{by[src].name or src}” has no output {len(outs)}.")
        for targets in outs:
            for t in targets:
                tn = by.get(t["node"])
                if tn is None:
                    problems.append(f"“{by[src].name or src}” is joined to a node that isn't there ({t['node']}).")
                elif tn.type in TRIGGERS:
                    problems.append("Nothing can lead into a trigger.")
                elif t["index"] >= max(tn.inputs(), 1):
                    problems.append(f"“{tn.name or tn.id}” has no input {t['index'] + 1}.")
    if problems:
        return problems
    for n in live:
        t = n.type
        p = n.parameters
        if t == "core.if" and not p.get("conditions"):
            problems.append(f"“{n.name or n.id}” needs at least one condition.")
        if t == "core.switch" and not p.get("rules"):
            problems.append(f"“{n.name or n.id}” needs at least one rule.")
        for c in (p.get("conditions") or []) + (p.get("rules") or []):
            if isinstance(c, dict) and c.get("op") not in OPS:
                problems.append(f"A condition in “{n.name or n.id}” needs a comparison.")
        if t == "msg.ask":
            opts = p.get("options") or []
            if not 2 <= len(opts) <= 4 or any(not str(o).strip() for o in opts):
                problems.append(f"“{n.name or n.id}” needs two to four answers to offer, each with a name.")
            if not str(p.get("text") or "").strip():
                problems.append(f"“{n.name or n.id}” needs the question to ask.")
        if t == "msg.telegram":
            if not str(p.get("credential") or "").strip():
                problems.append(f"“{n.name or n.id}” needs a Telegram bot to send with (its credential).")
            if not str(p.get("text") or "").strip():
                problems.append(f"“{n.name or n.id}” needs the text to send.")
        if t == "msg.mail" and not str(p.get("to") or "").strip():
            problems.append(f"“{n.name or n.id}” needs who to write to.")
        if t == "core.set" and not p.get("fields"):
            problems.append(f"“{n.name or n.id}” needs at least one field to set.")
        if t == "core.merge" and sum(1 for outs in f.connections.values() for o in outs for x in o if x["node"] == n.id) < 2:
            problems.append(f"“{n.name or n.id}” needs two inputs connected.")
        if n.type not in TRIGGERS and n.type != "core.stop" and not _fed(f, n.id):
            problems.append(f"“{n.name or n.id}” has nothing leading into it.")
    reach = _reach(f)
    for n in live:
        if n.id not in reach:
            problems.append(f"“{n.name or n.id}” can't be reached from a trigger.")
    problems += _cycles(f)
    return problems


def _fed(f: Flow, node_id: str) -> bool:
    return any(t["node"] == node_id for outs in f.connections.values() for o in outs for t in o)


def _reach(f: Flow) -> set[str]:
    seen: set[str] = set()
    todo = [n.id for n in f.nodes if n.type in TRIGGERS]
    while todo:
        cur = todo.pop()
        if cur in seen:
            continue
        seen.add(cur)
        for o in f.connections.get(cur, []):
            todo += [t["node"] for t in o]
    return seen


def _cycles(f: Flow) -> list[str]:
    """A circle is allowed only through a Loop over items, which is what stops it."""
    keep = {n.id for n in f.nodes if n.type != "core.loop"}
    state: dict[str, int] = {}

    def visit(i: str) -> bool:
        state[i] = 1
        for o in f.connections.get(i, []):
            for t in o:
                if t["node"] in keep and (state.get(t["node"]) == 1 or (state.get(t["node"]) is None and visit(t["node"]))):
                    return True
        state[i] = 2
        return False

    bad = any(state.get(i) is None and visit(i) for i in keep)
    return ["The workflow goes round in a circle. Only a Loop over items may do that, so it can stop."] if bad else []


def default_flow(prompt: str = "{{ $json.asked }}") -> Flow:
    """What an agent runs unless it was changed: a message comes in, the AI agent (with the agent's own tools and prompt) answers, and that is the answer."""
    return Flow(
        [
            FNode("trigger", "trigger.chat", "When a message comes in", (0, 0)),
            FNode("agent", "ai.agent", "AI agent", (260, 0), {"prompt": prompt}),
            FNode("respond", "respond", "Answer", (520, 0), {"text": "{{ $json.text }}"}),
        ],
        {"trigger": [[{"node": "agent", "index": 0}]], "agent": [[{"node": "respond", "index": 0}]]},
    )
