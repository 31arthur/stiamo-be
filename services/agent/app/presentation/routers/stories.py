"""Writing a chapter in the app, and choosing the model each AI job runs on."""

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app.presentation import deps

router = APIRouter(prefix="/v1/agent", tags=["stories"])


class ChapterBody(BaseModel):
    prose: str
    #: The story's id or title. Without it the story is worked out from the words (and asked about when unclear).
    story: str | None = None
    title: str | None = None
    date: str | None = None
    source: str = "app"


@router.post("/stories/chapters/draft", summary="Read margin notes from a chapter and put it on a card to approve (nothing is saved yet)")
async def draft(body: ChapterBody, agent: deps.Agent, me: deps.Me, token: deps.Token) -> dict[str, Any]:
    return await agent.draft_chapter(me, token, prose=body.prose, story=body.story, title=body.title, day=body.date, source=body.source)


class JobModelBody(BaseModel):
    provider: str | None = None
    model: str | None = None
    thinking: str = "off"


@router.get("/jobs", summary="The AI jobs and the model each runs on")
async def jobs(agent: deps.Agent, me: deps.Me) -> dict[str, Any]:
    return {"items": await agent.job_models(me)}


@router.put("/jobs/{name}/model", summary="Put an AI job on its own model, or (no provider) back on the one chosen last")
async def set_job(name: str, body: JobModelBody, agent: deps.Agent, me: deps.Me) -> dict[str, Any]:
    await agent.set_job_model(me, name, body.provider, body.model, body.thinking)
    return {"items": await agent.job_models(me)}
