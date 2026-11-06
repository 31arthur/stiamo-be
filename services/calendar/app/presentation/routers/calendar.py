from datetime import date
from typing import Annotated, Any
from urllib.parse import urlencode

import jwt
from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from stiamo_common.errors import ValidationFailed

from app.application.contacts import STATE_TYPE as CONTACTS_STATE
from app.presentation.deps import Calendar, CurrentPrincipal, Session, Token, build_contacts, build_service

router = APIRouter(prefix="/v1/calendar", tags=["calendar"])


class StartIn(BaseModel):
    workspace: str
    #: Which Google account to offer first.
    email: Annotated[str | None, Field(max_length=320)] = None


class SourceIn(BaseModel):
    enabled: bool | None = None
    #: Send this workspace's meetings and hearings here.
    export: bool | None = None
    color: Annotated[str | None, Field(max_length=9)] = None


class SyncIn(BaseModel):
    #: The person's own time zone, as the browser knows it (Asia/Kolkata).
    timezone: Annotated[str | None, Field(max_length=64)] = None
    account_id: str | None = None


@router.get("/status", summary="What is connected: accounts, their calendars, and what is shown or sent")
async def status(me: CurrentPrincipal, cal: Calendar) -> dict[str, Any]:
    return await cal.status(me.user_id)


@router.post("/oauth/start", summary="Where to send the person to allow Stiamo to use a Google calendar")
async def start(body: StartIn, me: CurrentPrincipal, cal: Calendar) -> dict[str, str]:
    return cal.start(me.user_id, body.workspace, body.email)


@router.get("/oauth/callback", include_in_schema=False, summary="Google sends the person back here")
async def callback(request: Request, session: Session, code: str | None = None, state: str | None = None, error: str | None = None) -> RedirectResponse:
    """Public: the browser arrives here from Google, not from the app, so what proves who it is is the signed `state`."""
    app_url = request.app.state.settings.app_url.rstrip("/")
    try:  # which connection this was is in the state; the service checks its signature
        kind = jwt.decode(state or "", options={"verify_signature": False}).get("typ")
    except jwt.PyJWTError:
        kind = None
    contacts = kind == CONTACTS_STATE

    def back(workspace: str, **q: str) -> RedirectResponse:
        if contacts:
            q = {k.replace("calendar_", "contacts_"): v for k, v in q.items()}
        return RedirectResponse(f"{app_url}/{workspace}/{'contacts' if contacts else 'calendar'}?{urlencode(q)}", status_code=303)

    if error or not code or not state:
        return back("sb", calendar_error="Google did not allow it." if error == "access_denied" else "Connecting the calendar did not finish.")
    cal = build_contacts(request.app, session) if contacts else build_service(request.app, session)
    try:
        workspace, email = await cal.complete(code, state)
    except ValidationFailed as exc:
        return back("sb", calendar_error=getattr(exc, "message", None) or str(exc))
    return back(workspace, calendar_connected=email)


@router.put("/sources/{source_id}", status_code=204, summary="Show or hide a calendar, send meetings and hearings to it, or change its colour")
async def set_source(source_id: str, body: SourceIn, me: CurrentPrincipal, cal: Calendar) -> Response:
    await cal.set_source(me.user_id, source_id, enabled=body.enabled, export=body.export, color=body.color)
    return Response(status_code=204)


@router.delete("/accounts/{account_id}", status_code=204, summary="Disconnect a Google account")
async def disconnect(account_id: str, me: CurrentPrincipal, cal: Calendar) -> Response:
    await cal.disconnect(me.user_id, account_id)
    return Response(status_code=204)


@router.get("/events", summary="The events of every shown calendar, on the person's own clock, optionally of one workspace")
async def events(me: CurrentPrincipal, cal: Calendar, start: Annotated[date, Query(alias="from")], to: date, workspace: str | None = None) -> dict[str, Any]:
    return await cal.events(me.user_id, start, to, workspace)


@router.post("/sync", summary="Read the calendars now, and send meetings and hearings to Google")
async def sync(body: SyncIn, me: CurrentPrincipal, cal: Calendar, token: Token) -> dict[str, Any]:
    return await cal.sync(me.user_id, token=token, account_id=body.account_id, timezone=body.timezone)
