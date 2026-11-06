from typing import Any

from fastapi import APIRouter, Request
from stiamo_common.models import DomainModel

from app.application.mailwatch import MailWatchService
from app.presentation import deps

router = APIRouter(prefix="/v1/agent/mail-watch", tags=["mail watch"])


def _service(request: Request, session: deps.Session) -> MailWatchService:
    return deps.build_mailwatch(request.app, session)


Watch = deps.Annotated[MailWatchService, deps.Depends(_service)]


class EnabledBody(DomainModel):
    enabled: bool


@router.get("", summary="The priority mail watch of each workspace that has a mailbox (made if it isn't there yet)")
async def watches(watch: Watch, me: deps.Me, token: deps.Token) -> dict[str, Any]:
    return await watch.view(me, token)


@router.put("/{workspace}", summary="Pause or resume a workspace's watch")
async def set_enabled(workspace: str, body: EnabledBody, watch: Watch, me: deps.Me, token: deps.Token) -> dict[str, Any]:
    await watch.set_enabled(me, workspace, body.enabled)
    return await watch.view(me, token)
