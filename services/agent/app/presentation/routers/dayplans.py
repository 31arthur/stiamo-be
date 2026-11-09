from typing import Annotated, Any

from fastapi import APIRouter, Request
from pydantic import Field
from stiamo_common.models import DomainModel

from app.application.dayplans import DayPlanService
from app.presentation import deps

router = APIRouter(prefix="/v1/agent/day-plans", tags=["day plans"])


def _service(request: Request, session: deps.Session) -> DayPlanService:
    return deps.build_day_plans(request.app, session)


Plans = Annotated[DayPlanService, deps.Depends(_service)]


class SettingsBody(DomainModel):
    enabled: bool
    #: Hours and minutes, like 21:00.
    time: Annotated[str, Field(min_length=4, max_length=5)]
    timezone: Annotated[str, Field(min_length=1, max_length=64)]
    #: Also send the plan to the connected Telegram and WhatsApp.
    channels: bool = True
    #: Which workspaces plan their own day each evening (each to its own bot). Left out: as before. Empty: one plan across everything.
    workspaces: Annotated[list[str] | None, Field(max_length=8)] = None


class GenerateBody(DomainModel):
    #: The day to plan (YYYY-MM-DD); tomorrow when left out.
    day: Annotated[str | None, Field(max_length=10)] = None
    #: Replace a plan that was already approved.
    force: bool = False
    #: Plan just this workspace's day; left out, one plan across everything.
    workspace: Annotated[str, Field(max_length=16)] = ""


@router.get("", summary="The evening plan's settings and the plans made for the latest days")
async def list_plans(plans: Plans, me: deps.Me, request: Request, workspace: str | None = None) -> dict[str, Any]:
    out = await plans.list(me, workspace=workspace)
    return {**out, "schedule": request.app.state.daily.status(me.user_id)}


@router.put("/settings", summary="Switch the evening plan on or off, and say when and where it goes")
async def set_settings(body: SettingsBody, plans: Plans, me: deps.Me, request: Request) -> dict[str, Any]:
    out = await plans.set_settings(me, body.enabled, body.time, body.timezone, body.channels, body.workspaces)
    deps.sync_evening(request.app, me, out)
    return {"settings": out, "schedule": request.app.state.daily.status(me.user_id)}


@router.post("/generate", summary="Plan a day now (tomorrow by default): ask the daily planner and keep what it proposes")
async def generate(body: GenerateBody, plans: Plans, me: deps.Me, token: deps.Token) -> dict[str, Any]:
    return await plans.generate(me, token, day=body.day, trigger="manual", force=body.force, workspace=body.workspace)


@router.post("/{plan_id}/apply", summary="Approve the proposed schedule: put each block on the planner")
async def apply(plan_id: str, plans: Plans, me: deps.Me, token: deps.Token) -> dict[str, Any]:
    return await plans.apply(me, token, plan_id)


@router.post("/{plan_id}/dismiss", summary="Not this plan")
async def dismiss(plan_id: str, plans: Plans, me: deps.Me) -> dict[str, Any]:
    return await plans.dismiss(me, plan_id)
