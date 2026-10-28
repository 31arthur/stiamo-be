"""How records connect: structural relations the system creates, plus hand-made links."""

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Literal

from app.domain.cases import Hearing, LegalCase, hearing_case_ids
from app.domain.common import EntityRef
from app.domain.directory import Client, Counsel, Project
from app.domain.goals import Goal
from app.domain.history import Link
from app.domain.plans import ActionPlan
from app.domain.work import FollowUp, Meeting, Task

RelationKind = Literal[
    "step", "about", "spawned", "hearing", "next_hearing", "plan", "prep", "goal", "counsel", "project", "linked"
]
Direction = Literal["parent", "child", "peer"]

#: Records that other records are "about": a case, a client, a project.
_ABOUT_KINDS = {"case", "client", "project"}
_MAX_ANCESTOR_DEPTH = 4

Key = tuple[str, str]


def key_of(ref: EntityRef) -> Key:
    return (ref.kind, ref.id)


def ref_of(key: Key) -> EntityRef:
    return EntityRef(kind=key[0], id=key[1])  # type: ignore[arg-type]


@dataclass(frozen=True)
class Edge:
    parent: Key
    child: Key
    kind: RelationKind
    #: Set on hand-made links so they can be removed.
    link_id: str | None = None


@dataclass
class GraphSources:
    tasks: list[Task] = field(default_factory=list)
    meetings: list[Meeting] = field(default_factory=list)
    follow_ups: list[FollowUp] = field(default_factory=list)
    cases: list[LegalCase] = field(default_factory=list)
    hearings: list[Hearing] = field(default_factory=list)
    plans: list[ActionPlan] = field(default_factory=list)
    goals: list[Goal] = field(default_factory=list)
    counsel: list[Counsel] = field(default_factory=list)
    clients: list[Client] = field(default_factory=list)
    projects: list[Project] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)


@dataclass
class Neighbor:
    ref: EntityRef
    kind: RelationKind
    direction: Direction
    link_id: str | None = None


@dataclass
class ThreadNode:
    ref: EntityRef
    kind: RelationKind | None = None
    children: list["ThreadNode"] = field(default_factory=list)


@dataclass
class Thread:
    #: Chain(s) of records above the root, nearest first.
    ancestors: list[Neighbor]
    #: Everything that grew out of the root, as a tree.
    tree: ThreadNode
    peers: list[Neighbor]
    #: Every record in the thread, root included.
    members: list[EntityRef]


class RelationGraph:
    """The connection graph over all records: structural relations plus hand-made links."""

    def __init__(self, src: GraphSources) -> None:
        self._up: dict[Key, list[Edge]] = defaultdict(list)
        self._down: dict[Key, list[Edge]] = defaultdict(list)
        self._peers: dict[Key, list[Edge]] = defaultdict(list)
        self._exists: set[Key] = set()
        for kind, items in (("task", src.tasks), ("meeting", src.meetings), ("followUp", src.follow_ups),
                            ("case", src.cases), ("hearing", src.hearings), ("plan", src.plans), ("goal", src.goals),
                            ("counsel", src.counsel), ("client", src.clients), ("project", src.projects)):
            self._exists.update((kind, e.id) for e in items)

        for t in src.tasks:
            if t.plan_id:
                self._add(("plan", t.plan_id), ("task", t.id), "step")
            # A step's plan is its parent; its `related` (the plan's origin) would only duplicate the path.
            elif t.related:
                self._add(key_of(t.related), ("task", t.id), "about" if t.related.kind in _ABOUT_KINDS else "spawned")
        for f in src.follow_ups:
            if f.plan_id:
                self._add(("plan", f.plan_id), ("followUp", f.id), "step")
            elif f.subject:
                about = f.subject.kind in _ABOUT_KINDS or f.subject.kind == "meeting"
                self._add(key_of(f.subject), ("followUp", f.id), "about" if about else "spawned")
        for m in src.meetings:
            if m.plan_id:
                self._add(("plan", m.plan_id), ("meeting", m.id), "step")
            elif m.related:
                self._add(key_of(m.related), ("meeting", m.id), "about")
            if m.prep_plan_id:
                self._add(("meeting", m.id), ("plan", m.prep_plan_id), "prep")
        for h in src.hearings:
            for cid in hearing_case_ids(h):
                self._add(("case", cid), ("hearing", h.id), "hearing")
            if h.next_hearing_id:
                self._add(("hearing", h.id), ("hearing", h.next_hearing_id), "next_hearing")
        for p in src.plans:
            origin = p.origin
            is_prep = origin and origin.kind == "meeting" and any(
                m.id == origin.id and m.prep_plan_id == p.id for m in src.meetings)
            if origin and not is_prep:
                self._add(key_of(origin), ("plan", p.id), "plan")
            # The narrowest scope is the parent: a hearing sits under its case, a task is its own thing. A plan
            # shared by several hearings or cases hangs under each of them.
            hearing_ids = p.hearing_ids or []
            for hid in hearing_ids:
                self._add(("hearing", hid), ("plan", p.id), "plan")
            if p.task_id:
                self._add(("task", p.task_id), ("plan", p.id), "plan")
            else:
                # Cases already reached through one of the plan's hearings don't need a second path.
                via_hearing = {c for h in src.hearings if h.id in hearing_ids for c in hearing_case_ids(h)}
                for cid in p.case_ids or []:
                    if cid not in via_hearing and not (origin and origin.kind == "case" and origin.id == cid):
                        self._add(("case", cid), ("plan", p.id), "plan")
        for c in src.cases:
            if c.engagement:
                self._add(("counsel", c.engagement.counsel_id), ("case", c.id), "counsel")
        for pr in src.projects:
            self._add(("client", pr.client_id), ("project", pr.id), "project")
        for g in src.goals:
            for pid in g.plan_ids:
                self._add(("goal", g.id), ("plan", pid), "goal")
        for link in src.links:
            edge = Edge(key_of(link.a), key_of(link.b), "linked", link.id)
            self._peers[key_of(link.a)].append(edge)
            self._peers[key_of(link.b)].append(edge)

    def _add(self, parent: Key, child: Key, kind: RelationKind) -> None:
        # Skip edges to records that no longer exist (deleted parents leave dangling ids behind).
        if parent not in self._exists or child not in self._exists:
            return
        edge = Edge(parent, child, kind)
        self._down[parent].append(edge)
        self._up[child].append(edge)

    def has(self, ref: EntityRef) -> bool:
        return key_of(ref) in self._exists

    def neighbors(self, ref: EntityRef) -> list[Neighbor]:
        key = key_of(ref)
        out = [Neighbor(ref_of(e.parent), e.kind, "parent") for e in self._up[key]]
        out += [Neighbor(ref_of(e.child), e.kind, "child") for e in self._down[key]]
        for e in self._peers[key]:
            other = e.child if e.parent == key else e.parent
            if other in self._exists:
                out.append(Neighbor(ref_of(other), "linked", "peer", e.link_id))
        return out

    def thread(self, root: EntityRef) -> Thread:
        """The thread around a record: what it belongs to (walking up), everything that came from it (walking
        down, as a tree), and what's been linked to it by hand."""
        root_key = key_of(root)
        seen: dict[Key, None] = {root_key: None}
        ancestors: list[Neighbor] = []
        frontier = [root_key]
        for _ in range(_MAX_ANCESTOR_DEPTH):
            if not frontier:
                break
            nxt: list[Key] = []
            for k in frontier:
                for e in self._up[k]:
                    if e.parent in seen:
                        continue
                    seen[e.parent] = None
                    ancestors.append(Neighbor(ref_of(e.parent), e.kind, "parent"))
                    # Goals sit above many plans; show them but don't climb past them.
                    if e.parent[0] != "goal":
                        nxt.append(e.parent)
            frontier = nxt

        def build(key: Key, kind: RelationKind | None = None) -> ThreadNode:
            fresh = []
            for e in self._down[key]:
                if e.child not in seen:
                    seen[e.child] = None
                    fresh.append(e)
            return ThreadNode(ref_of(key), kind, [build(e.child, e.kind) for e in fresh])

        tree = build(root_key)
        peers = [n for n in self.neighbors(root) if n.direction == "peer" and key_of(n.ref) not in seen]
        for p in peers:
            seen[key_of(p.ref)] = None
        return Thread(ancestors, tree, peers, [ref_of(k) for k in seen])
