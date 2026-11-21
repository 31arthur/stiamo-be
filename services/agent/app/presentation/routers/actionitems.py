from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from stiamo_common.errors import NotFound

from app.application.actions import ActionService
from app.presentation import deps

router = APIRouter(prefix="/v1/agent/action-items", tags=["action-items"])


def _service(request: Request, session: deps.Session) -> ActionService:
    return deps.build_services(request.app, session)[0].actions


Actions = deps.Annotated[ActionService, deps.Depends(_service)]


class Act(BaseModel):
    action: str = Field(pattern="^(complete|cancel|reopen|move|snooze)$")
    to: str | None = Field(default=None, max_length=40)
    reason: str = Field(default="", max_length=300)


@router.get("", summary="Every open action item across all workspaces, in one shape (tasks, calls, meetings, hearings, events, mail that needs you)")
async def items(actions: Actions, me: deps.Me, token: deps.Token, workspace: str | None = None, kind: str | None = None, limit: int = 300) -> dict[str, Any]:
    found = await actions.find(me, token, workspace=workspace, kinds=[kind] if kind else None, include_overdue=False)
    today = actions.today()
    rows = [i.view(today, actions.zone, actions.home) for i in found if i.status in ("open", "snoozed")]
    rows.sort(key=lambda r: (r.get("day") or r.get("due") or "9999-12-31", r.get("time") or "99:99"))
    return {"today": today, "total": len(rows), "items": rows[: max(1, min(limit, 500))]}


@router.post("/{ref}/act", summary="Done, cancel, reopen, move or snooze one item (undoable)")
async def act(ref: str, body: Act, actions: Actions, me: deps.Me, token: deps.Token) -> dict[str, Any]:
    return await actions.command(me, token, ref, body.action, to=body.to, actor="owner", reason=body.reason)


@router.get("/history", summary="What changed lately, newest first")
async def history(actions: Actions, me: deps.Me, limit: int = 40) -> dict[str, Any]:
    return {"events": await actions.history(me, limit=max(1, min(limit, 200)))}


@router.post("/changes/{changeset_id}/undo", summary="Put a change back")
async def undo(changeset_id: str, actions: Actions, me: deps.Me, token: deps.Token) -> dict[str, Any]:
    out = await actions.undo_changeset(me, token, changeset_id, actor="owner")
    if not out:
        raise NotFound("That change is not there any more.")
    return out
