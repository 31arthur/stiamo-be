"""Writing a chapter in the app, and choosing the model each AI job runs on."""

from typing import Any

from fastapi import APIRouter, Request
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


# --- the story editor -------------------------------------------------------------------------------------------


def _editor(request: Request, session: deps.Session):
    return deps.build_services(request.app, session)[1].editor_actions


class EditorSettingsBody(BaseModel):
    enabled: bool = True
    roomDay: str = "sun"


class RunBody(BaseModel):
    workspace: str | None = None


@router.get("/editor", summary="The story editor: its settings, and every story's pace, quiet and push score")
async def editor(request: Request, session: deps.Session, me: deps.Me, token: deps.Token) -> dict[str, Any]:
    e = _editor(request, session)
    return {"settings": await e.settings(me), "stories": await e.overview(me, token)}


@router.put("/editor/settings", summary="Switch the story editor on or off, and choose the writers' room day")
async def editor_settings(body: EditorSettingsBody, request: Request, session: deps.Session, me: deps.Me) -> dict[str, Any]:
    return await _editor(request, session).set_settings(me, body.enabled, body.roomDay)


@router.post("/editor/run", summary="Run the story editor now (at most one message per workspace per day still holds)")
async def editor_run(body: RunBody, request: Request, session: deps.Session, me: deps.Me, token: deps.Token) -> dict[str, Any]:
    return {"sent": await _editor(request, session).run_now(me, token, body.workspace)}
