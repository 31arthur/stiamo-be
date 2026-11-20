from typing import Any

from fastapi import APIRouter, Request

from app.application.queue import QueueService
from app.presentation import deps

router = APIRouter(prefix="/v1/agent/queue", tags=["chittu"])


def _service(request: Request, session: deps.Session) -> QueueService:
    return deps.build_services(request.app, session)[1].queue_actions


Queue = deps.Annotated[QueueService, deps.Depends(_service)]


@router.get("", summary="The list Chittu is walking the owner through: what it is, where it came from, where it is, and what is left")
async def view(queue: Queue, me: deps.Me, token: deps.Token) -> dict[str, Any]:
    return await queue.view(me, token)
