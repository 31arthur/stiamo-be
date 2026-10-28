from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.application.use_cases.views import AgendaUseCases, Block, RelationUseCases
from app.domain.common import DomainModel, EntityKind, EntityRef, ISODate, Minutes
from app.domain.relations import Neighbor, ThreadNode
from app.presentation import deps
from app.presentation.crud_router import dump

router = APIRouter(tags=["relations", "agenda"])
Relations = Annotated[RelationUseCases, Depends(deps.relation_service)]
Agenda = Annotated[AgendaUseCases, Depends(deps.agenda_service)]


def _neighbor(n: Neighbor) -> dict[str, Any]:
    out = {"ref": dump(n.ref), "kind": n.kind, "direction": n.direction}
    if n.link_id:
        out["linkId"] = n.link_id
    return out


def _node(n: ThreadNode) -> dict[str, Any]:
    out: dict[str, Any] = {"ref": dump(n.ref), "children": [_node(c) for c in n.children]}
    if n.kind:
        out["kind"] = n.kind
    return out


@router.get("/v1/relations/thread", summary="Everything connected to a record: what it belongs to, what grew out of it")
async def thread(principal: deps.CurrentPrincipal, svc: Relations, kind: EntityKind, id: str) -> dict[str, Any]:
    t = await svc.thread(principal.user_id, EntityRef(kind=kind, id=id))
    return {
        "ancestors": [_neighbor(n) for n in t.ancestors], "tree": _node(t.tree),
        "peers": [_neighbor(n) for n in t.peers], "members": [dump(m) for m in t.members],
    }


@router.get("/v1/relations/thread/history", summary="The merged history of a record's whole thread, newest first")
async def thread_history(principal: deps.CurrentPrincipal, svc: Relations, kind: EntityKind, id: str,
                         limit: int = 300) -> dict[str, Any]:
    items = await svc.thread_history(principal.user_id, EntityRef(kind=kind, id=id), min(max(limit, 1), 1000))
    return {"items": [dump(a) for a in items]}


@router.get("/v1/agenda", summary="Every dated task, meeting, follow-up and hearing between two days")
async def agenda(principal: deps.CurrentPrincipal, svc: Agenda, start: ISODate, end: ISODate) -> dict[str, Any]:
    entries = await svc.range(principal.user_id, start, end)
    items = []
    for e in entries:
        item: dict[str, Any] = {
            "key": e.key, "ref": dump(e.ref), "kind": e.kind, "title": e.title, "date": e.date, "duration": e.duration,
            "workspace": e.workspace, "outcome": dump(e.outcome), "pinned": e.pinned, "movable": e.movable,
        }
        if e.subtitle:
            item["subtitle"] = e.subtitle
        if e.start is not None:
            item["slot"] = {"start": e.start, "duration": e.slot_duration or e.duration}
        if e.meeting_kind:
            item["meetingKind"] = e.meeting_kind
        if e.priority:
            item["priority"] = e.priority
        items.append(item)
    return {"items": items}


class BlockBody(DomainModel):
    start: Minutes
    duration: int


class ScheduleBody(DomainModel):
    ref: EntityRef
    date: ISODate
    #: Null takes the entry off the timeline, keeping it on the day.
    block: BlockBody | None = None


@router.put("/v1/agenda/schedule", summary="Place an entry at a time on a day, or take it off the timeline")
async def schedule(body: ScheduleBody, principal: deps.CurrentPrincipal, svc: Agenda) -> dict[str, Any]:
    block = Block(body.block.start, body.block.duration) if body.block else None
    kind, before = await svc.schedule(principal.user_id, body.ref, body.date, block)
    return {"before": {"kind": kind, "entity": dump(before)}}
