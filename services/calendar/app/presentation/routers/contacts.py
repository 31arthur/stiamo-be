from typing import Annotated, Any

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, Field

from app.presentation.deps import Contacts, CurrentPrincipal

router = APIRouter(prefix="/v1/calendar/contacts", tags=["contacts"])


class StartIn(BaseModel):
    workspace: str
    #: Which Google account to offer first.
    email: Annotated[str | None, Field(max_length=320)] = None


class LinkIn(BaseModel):
    workspace: str


class SyncIn(BaseModel):
    account_id: str | None = None


@router.get("/status", summary="The Google accounts linked for contacts, each with its workspace and how many contacts it has")
async def status(me: CurrentPrincipal, svc: Contacts) -> dict[str, Any]:
    return await svc.status(me.user_id)


@router.get("", summary="The contacts of one workspace, or of every workspace together, by name")
async def contacts(me: CurrentPrincipal, svc: Contacts, workspace: str | None = None, q: Annotated[str, Query(max_length=100)] = "", limit: Annotated[int, Query(ge=1, le=500)] = 100, offset: Annotated[int, Query(ge=0)] = 0) -> dict[str, Any]:
    return await svc.contacts(me.user_id, workspace, q, limit, offset)


@router.post("/oauth/start", summary="Where to send the person to allow Stiamo to read a Google account's contacts")
async def start(body: StartIn, me: CurrentPrincipal, svc: Contacts) -> dict[str, str]:
    return svc.start(me.user_id, body.workspace, body.email)


@router.post("/accounts/{account_id}/link", summary="The same Google account in another workspace too (no new sign-in)")
async def link(account_id: str, body: LinkIn, me: CurrentPrincipal, svc: Contacts) -> dict[str, Any]:
    return await svc.link(me.user_id, account_id, body.workspace)


@router.post("/sync", summary="Read the contacts again, for one account or all of them")
async def sync(body: SyncIn, me: CurrentPrincipal, svc: Contacts) -> dict[str, Any]:
    return await svc.sync(me.user_id, body.account_id)


@router.delete("/accounts/{account_id}", status_code=204, summary="Stop using this account for this workspace's contacts")
async def disconnect(account_id: str, me: CurrentPrincipal, svc: Contacts) -> Response:
    await svc.disconnect(me.user_id, account_id)
    return Response(status_code=204)
