from typing import Annotated, Any

from fastapi import APIRouter, Request, Response, status

from app.application.availability import AvailabilityService
from app.infrastructure.db.repositories import SqlSettingsRepository
from app.presentation import deps

router = APIRouter(prefix="/v1/agent/availability", tags=["availability"])


def _service(request: Request, session: deps.Session) -> AvailabilityService:
    s = request.app.state
    return AvailabilityService(SqlSettingsRepository(session, s.box), s.data_factory, None)


Availability = Annotated[AvailabilityService, deps.Depends(_service)]


@router.get("", summary="What was said about time inside events, remembered by event title")
async def rules(svc: Availability, me: deps.Me) -> dict[str, Any]:
    return {"rules": await svc.rules(me)}


@router.delete("/rules/{title}", status_code=status.HTTP_204_NO_CONTENT, summary="Forget an answer: the next event with that title is asked about again")
async def forget(title: str, svc: Availability, me: deps.Me) -> Response:
    await svc.forget(me, title)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
