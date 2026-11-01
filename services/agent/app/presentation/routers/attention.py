from typing import Annotated, Any

from fastapi import APIRouter, Request
from pydantic import Field
from stiamo_common.models import DomainModel

from app.application.attention import AttentionService
from app.infrastructure.db.repositories import SqlSettingsRepository
from app.infrastructure.db.workflows import SqlWorkflowRepository
from app.presentation import deps

router = APIRouter(prefix="/v1/agent/attention", tags=["attention"])


def _service(request: Request, session: deps.Session) -> AttentionService:
    s = request.app.state
    return AttentionService(SqlWorkflowRepository(session), SqlSettingsRepository(session, s.box), s.data_factory)


Attention = Annotated[AttentionService, deps.Depends(_service)]


class ActBody(DomainModel):
    agent_id: Annotated[str, Field(max_length=64)]
    item_id: Annotated[str, Field(max_length=200)]
    action: str
    #: For approve_event: the event as the person left it (they may have changed it).
    event: dict[str, Any] | None = None


@router.get("", summary="What the agents found that needs the owner: issues, important mail, events to approve")
async def items(attention: Attention, me: deps.Me, workspace: str | None = None) -> dict[str, Any]:
    return await attention.items(me, workspace)


@router.post("/act", summary="Done, not important, or approve (or skip) the calendar event")
async def act(body: ActBody, attention: Attention, me: deps.Me, token: deps.Token) -> dict[str, Any]:
    return await attention.act(me, token, body.agent_id, body.item_id, body.action, body.event)
