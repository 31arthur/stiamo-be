from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request

from app.application.usage import UsageService
from app.infrastructure.db.workflows import SqlWorkflowRepository
from app.presentation import deps

router = APIRouter(prefix="/v1/agent/usage", tags=["usage"])


def _service(request: Request, session: deps.Session) -> UsageService:
    return UsageService(request.app.state.runs, request.app.state.registry, SqlWorkflowRepository(session))


Usage = Annotated[UsageService, deps.Depends(_service)]
Days = Annotated[int | None, Query(ge=1, le=3650, description="Only the last this many days. Leave out for everything.")]


@router.get("/summary", summary="What the agents have used: totals, by agent, by model, day by day")
async def summary(usage: Usage, me: deps.Me, days: Days = 30, agent: str | None = None) -> dict[str, Any]:
    return await usage.summary(me, days, agent)


@router.get("/transactions", summary="Every run and what it used, newest first")
async def transactions(usage: Usage, me: deps.Me, days: Days = None, agent: str | None = None, before: datetime | None = None, limit: Annotated[int, Query(ge=1, le=200)] = 50) -> dict[str, Any]:
    return await usage.transactions(me, days, agent, before, limit)
