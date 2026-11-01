from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request, Response
from pydantic import Field
from stiamo_common.models import DomainModel

from app.application.news import NewsService
from app.infrastructure.db.repositories import SqlSettingsRepository
from app.infrastructure.db.workflows import SqlWorkflowRepository
from app.presentation import deps

router = APIRouter(prefix="/v1/agent/news", tags=["news"])


def _service(request: Request, session: deps.Session) -> NewsService:
    return NewsService(SqlWorkflowRepository(session), SqlSettingsRepository(session, request.app.state.box))


News = Annotated[NewsService, deps.Depends(_service)]


class HideBody(DomainModel):
    agent_id: Annotated[str, Field(max_length=64)]
    item_id: Annotated[str, Field(max_length=200)]


@router.get("", summary="Up to 20 stories the agents found relevant, most important first (sort=importance) or latest first (sort=latest)")
async def top(news: News, me: deps.Me, workspace: str | None = None, limit: Annotated[int, Query(ge=1, le=20)] = 20, days: Annotated[int, Query(ge=1, le=60)] = 7, sort: Literal["importance", "latest"] = "importance") -> dict[str, Any]:
    return await news.top(me, workspace, limit, days, sort)


@router.post("/hide", status_code=204, summary="Take a story off the page")
async def hide(body: HideBody, news: News, me: deps.Me) -> Response:
    await news.hide(me, body.agent_id, body.item_id)
    return Response(status_code=204)
