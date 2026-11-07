from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from stiamo_common.models import DomainModel

from app.application.insights import InsightService
from app.presentation import deps

router = APIRouter(prefix="/v1/agent/insights", tags=["insights"])


def _service(request: Request, session: deps.Session) -> InsightService:
    return deps.build_insights(request.app, session)


Insights = deps.Annotated[InsightService, deps.Depends(_service)]


class DigestBody(DomainModel):
    #: A workspace, or "all".
    workspace: str = "all"
    days: int = 7
    #: Write it again even if it was written today.
    force: bool = False


@router.get("", summary="What mail, news and your own work add up to over the last 7, 30 or 90 days, for one workspace or all")
async def insights(svc: Insights, me: deps.Me, token: deps.Token, workspace: str = "all", days: Annotated[int, Query()] = 7) -> dict[str, Any]:
    return await svc.compute(me, token, None if workspace == "all" else workspace, days)


@router.post("/digest", summary="A short written reading of those numbers by your own model, kept for the day")
async def digest(body: DigestBody, svc: Insights, me: deps.Me, token: deps.Token) -> dict[str, Any]:
    return await svc.digest(me, token, None if body.workspace == "all" else body.workspace, body.days, body.force)
