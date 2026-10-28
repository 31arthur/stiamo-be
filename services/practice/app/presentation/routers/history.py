from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Response, status

from app.application.use_cases.history import HistoryUseCases
from app.domain.common import DomainModel, EntityKind, EntityRef
from app.presentation import deps
from app.presentation.crud_router import dump

router = APIRouter(prefix="/v1", tags=["history"])
Service = Annotated[HistoryUseCases, Depends(deps.history_service)]


class Titles(DomainModel):
    a: str
    b: str


class LinkIn(DomainModel):
    a: EntityRef
    b: EntityRef
    titles: Titles
    note: str | None = None


class NoteIn(DomainModel):
    ref: EntityRef
    title: str
    text: str


@router.get("/activity", summary="A record's own history, newest first")
async def history(principal: deps.CurrentPrincipal, svc: Service, kind: EntityKind, id: str,
                  limit: Annotated[int, Query(ge=1, le=500)] = 200) -> dict[str, Any]:
    items = await svc.history_of(principal.user_id, EntityRef(kind=kind, id=id), limit)
    return {"items": [dump(a) for a in items]}


@router.post("/activity/notes", status_code=status.HTTP_204_NO_CONTENT, summary="Add a note to a record's thread")
async def add_note(body: NoteIn, principal: deps.CurrentPrincipal, svc: Service):
    await svc.add_note(principal.user_id, body.ref, body.title, body.text)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class EventIn(DomainModel):
    ref: EntityRef
    title: str = ""
    action: Literal["attached", "detached"]
    summary: str


@router.post("/activity/events", status_code=status.HTTP_204_NO_CONTENT,
             summary="Record that a document was attached or removed (called by the files service)")
async def add_event(body: EventIn, principal: deps.CurrentPrincipal, svc: Service):
    await svc.add_event(principal.user_id, body.ref, body.title, body.action, body.summary)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/links", summary="Links touching a record")
async def links(principal: deps.CurrentPrincipal, svc: Service, kind: EntityKind, id: str) -> dict[str, Any]:
    return {"items": [dump(item) for item in await svc.links_of(principal.user_id, EntityRef(kind=kind, id=id))]}


@router.post("/links", status_code=status.HTTP_201_CREATED, summary="Link two records")
async def link(body: LinkIn, principal: deps.CurrentPrincipal, svc: Service) -> dict[str, Any]:
    made = await svc.link(principal.user_id, body.a, body.b, (body.titles.a, body.titles.b), body.note)
    return dump(made)


@router.delete("/links/{link_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Remove a link")
async def unlink(link_id: str, principal: deps.CurrentPrincipal, svc: Service,
                 title_a: Annotated[str | None, Query(alias="titleA")] = None,
                 title_b: Annotated[str | None, Query(alias="titleB")] = None):
    titles = (title_a, title_b) if title_a and title_b else None
    await svc.unlink(principal.user_id, link_id, titles)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
