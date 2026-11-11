"""An agent as a graph of nodes. Pure: no network, no framework.

A graph starts at one **trigger** and follows its edges until it reaches an **output**. The nodes:

- `trigger`  starts the run with what was asked.
- `scope`    limits the data the later nodes can see to one workspace (inherits the chat's, or a fixed one).
- `prompt`   builds what the model is told: the agent's saved prompt, or one written here.
- `agent`    the model, with the tools it may call: the loop that reads, calls tools and answers.
- `branch`   chooses which edge to follow from conditions on the run so far.
- `set`      stores a value (made from a template) for later nodes.
- `loop`     goes back round (edge "again") until a condition holds or it has gone round `max` times (edge "done").
- `output`   ends the run with an answer (the last one, or a template).

Edges carry a label (`next` by default; a branch uses its cases' labels and `else`; a loop uses `again` and `done`). A cycle is allowed only
through a loop node, and a run is cut off after MAX_VISITS node visits, so a graph can't run away.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

NODE_TYPES = ("trigger", "scope", "prompt", "agent", "branch", "set", "loop", "output")
MAX_NODES = 30
MAX_AGENTS = 5
MAX_VISITS = 60
MAX_LOOP = 8
OPS = ("contains", "not_contains", "equals", "not_equals", "matches", "empty", "not_empty", "gt", "lt")
WORKSPACE_IDS = ("personal", "sb", "fsa", "media")


@dataclass
class Node:
    id: str
    type: str
    label: str = ""
    config: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "type": self.type, "label": self.label or self.type.capitalize(), "config": self.config}


@dataclass
class Edge:
    source: str
    target: str
    label: str = "next"

    def to_json(self) -> dict[str, str]:
        return {"from": self.source, "to": self.target, "label": self.label}


@dataclass
class Graph:
    nodes: list[Node]
    edges: list[Edge]

    def node(self, node_id: str) -> Node | None:
        return next((n for n in self.nodes if n.id == node_id), None)

    def out(self, node_id: str) -> list[Edge]:
        return [e for e in self.edges if e.source == node_id]

    def to_json(self) -> dict[str, Any]:
        return {"nodes": [n.to_json() for n in self.nodes], "edges": [e.to_json() for e in self.edges]}

    @staticmethod
    def from_json(raw: Any) -> Graph:
        """Reads a graph from what was sent or stored. Raises ValueError (with words for a person) when it isn't one."""
        if not isinstance(raw, dict) or not isinstance(raw.get("nodes"), list) or not isinstance(raw.get("edges"), list):
            raise ValueError("A workflow needs nodes and edges.")
        nodes, edges = [], []
        for n in raw["nodes"][: MAX_NODES + 1]:
            if not isinstance(n, dict) or not isinstance(n.get("id"), str) or n.get("type") not in NODE_TYPES:
                raise ValueError(f"A node needs an id and one of these types: {', '.join(NODE_TYPES)}.")
            nodes.append(Node(n["id"][:40], n["type"], str(n.get("label") or "")[:60], dict(n.get("config") or {})))
        for e in raw["edges"][: MAX_NODES * 4]:
            if not isinstance(e, dict) or not isinstance(e.get("from"), str) or not isinstance(e.get("to"), str):
                raise ValueError("An edge needs a from and a to.")
            edges.append(Edge(e["from"], e["to"], str(e.get("label") or "next")[:40]))
        return Graph(nodes, edges)


def default_graph() -> Graph:
    """What every agent runs unless it was changed: the prompt and tools the agent already has, read once and answered."""
    return Graph(
        [Node("trigger", "trigger", "Starts when"), Node("scope", "scope", "Workspace scope", {"mode": "inherit"}), Node("prompt", "prompt", "Prompt"), Node("agent", "agent", "Agent"), Node("output", "output", "Answer")],
        [Edge("trigger", "scope"), Edge("scope", "prompt"), Edge("prompt", "agent"), Edge("agent", "output")],
    )


# --- conditions and templates -------------------------------------------------------------------------------------------------


def lookup(state: dict[str, Any], name: str) -> Any:
    """A value of the run so far by name: input, answer, workspace, found, steps, vars.x or nodes.<id>."""
    head, _, rest = name.partition(".")
    if head in ("vars", "nodes"):
        return (state.get(head) or {}).get(rest, "")
    return state.get(head, "")


def render(template: str, state: dict[str, Any]) -> str:
    """Fills {{name}} in a template from the run so far. Anything unknown is left empty, never an error."""
    return re.sub(r"\{\{\s*([\w.\-]+)\s*\}\}", lambda m: str(lookup(state, m.group(1)) if lookup(state, m.group(1)) is not None else ""), template)


def holds(cond: dict[str, Any], state: dict[str, Any]) -> bool:
    """Whether one condition is true of the run so far. An unknown operator or a bad pattern is false, never an error."""
    value = lookup(state, str(cond.get("var") or ""))
    op, want = cond.get("op"), cond.get("value", "")
    text, wanted = str(value).lower(), str(want).lower()
    if op == "contains":
        return wanted in text
    if op == "not_contains":
        return wanted not in text
    if op == "equals":
        return text == wanted
    if op == "not_equals":
        return text != wanted
    if op == "empty":
        return text.strip() in ("", "0", "false", "[]", "{}")
    if op == "not_empty":
        return text.strip() not in ("", "0", "false", "[]", "{}")
    if op == "matches":
        try:
            return re.search(str(want)[:200], str(value), re.IGNORECASE) is not None
        except re.error:
            return False
    if op in ("gt", "lt"):
        try:
            return float(value) > float(want) if op == "gt" else float(value) < float(want)
        except (TypeError, ValueError):
            return False
    return False


# --- validation ---------------------------------------------------------------------------------------------------------------


def validate(g: Graph) -> list[str]:
    """What is wrong with a graph, in words for a person. Empty: it can run."""
    problems: list[str] = []
    ids = [n.id for n in g.nodes]
    if len(g.nodes) > MAX_NODES:
        return [f"A workflow can have at most {MAX_NODES} nodes."]
    if len(set(ids)) != len(ids):
        problems.append("Two nodes have the same id.")
    by = {n.id: n for n in g.nodes}
    triggers = [n for n in g.nodes if n.type == "trigger"]
    if len(triggers) != 1:
        problems.append("A workflow needs exactly one trigger.")
    if not any(n.type == "output" for n in g.nodes):
        problems.append("A workflow needs at least one output (the answer).")
    if sum(1 for n in g.nodes if n.type == "agent") > MAX_AGENTS:
        problems.append(f"A workflow can have at most {MAX_AGENTS} agent nodes.")
    if not any(n.type == "agent" for n in g.nodes):
        problems.append("A workflow needs an agent node, which is what reads, calls tools and answers.")
    for e in g.edges:
        if e.source not in by or e.target not in by:
            problems.append(f"An edge joins a node that isn't there ({e.source} → {e.target}).")
        elif by[e.source].type == "output":
            problems.append("An output ends the run: nothing can follow it.")
        elif by[e.target].type == "trigger":
            problems.append("Nothing can lead into the trigger.")
    if problems:
        return problems
    for n in g.nodes:
        out = g.out(n.id)
        labels = [e.label for e in out]
        if n.type != "output" and not out:
            problems.append(f"“{n.label or n.id}” leads nowhere: connect it to the next node.")
        if len(labels) != len(set(labels)):
            problems.append(f"“{n.label or n.id}” has two edges with the same label.")
        if n.type == "branch":
            cases = n.config.get("cases") or []
            if not cases:
                problems.append(f"“{n.label or n.id}” needs at least one case.")
            for c in cases:
                if c.get("op") not in OPS or not c.get("label"):
                    problems.append(f"A case in “{n.label or n.id}” needs a condition and a label.")
            want = {str(c.get("label")) for c in cases} | {"else"}
            if not set(labels) <= want or "else" not in labels:
                problems.append(f"“{n.label or n.id}” needs an edge for each case ({', '.join(sorted(want - {'else'}))}) and one labelled else.")
        elif n.type == "loop":
            if set(labels) != {"again", "done"}:
                problems.append(f"“{n.label or n.id}” needs one edge labelled again and one labelled done.")
            if not 1 <= int(n.config.get("max") or 3) <= MAX_LOOP:
                problems.append(f"A loop goes round 1 to {MAX_LOOP} times.")
        elif n.type == "scope":
            if n.config.get("mode") == "fixed" and n.config.get("workspace") not in WORKSPACE_IDS:
                problems.append("A fixed scope needs one of the workspaces.")
        elif n.type == "set" and not n.config.get("name"):
            problems.append(f"“{n.label or n.id}” needs a name for the value it stores.")
    if triggers:
        reach = _reach(g, triggers[0].id)
        for n in g.nodes:
            if n.id not in reach:
                problems.append(f"“{n.label or n.id}” can't be reached from the trigger.")
        if not any(by[i].type == "output" for i in reach):
            problems.append("No path from the trigger reaches an output.")
    problems += _cycles_without_loop(g)
    return problems


def _reach(g: Graph, start: str) -> set[str]:
    seen, todo = set(), [start]
    while todo:
        cur = todo.pop()
        if cur in seen:
            continue
        seen.add(cur)
        todo += [e.target for e in g.out(cur)]
    return seen


def _cycles_without_loop(g: Graph) -> list[str]:
    """A cycle must pass through a loop node: remove the loop nodes and nothing may circle."""
    keep = {n.id for n in g.nodes if n.type != "loop"}
    state: dict[str, int] = {}

    def visit(i: str) -> bool:
        state[i] = 1
        for e in g.out(i):
            if e.target in keep:
                if state.get(e.target) == 1 or (state.get(e.target) is None and visit(e.target)):
                    return True
        state[i] = 2
        return False

    return ["The workflow goes round in a circle. Only a loop node may do that, so it can stop."] if any(state.get(i) is None and visit(i) for i in keep) else []
