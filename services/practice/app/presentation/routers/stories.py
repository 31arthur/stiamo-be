"""Stories, chapters and arc templates, plus writing a chapter in one step and reading a story's timeline."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.application.use_cases.stories import StoryWriting, WriteChapter
from app.domain.stories import ArcTemplate, Chapter, Story, arc_progress
from app.presentation import deps
from app.presentation.crud_router import crud_router, dump

stories = crud_router(prefix="/v1/stories", tag="stories", model=Story, service_dep=deps.story_service)
chapters = crud_router(prefix="/v1/chapters", tag="stories", model=Chapter, service_dep=deps.chapter_service)
arc_templates = crud_router(prefix="/v1/arc-templates", tag="stories", model=ArcTemplate, service_dep=deps.arc_template_service)
extra = APIRouter(tags=["stories"])

Writing = Annotated[StoryWriting, Depends(deps.story_writing)]


@extra.post("/v1/stories/{story_id}/chapters/write",
            summary="Write a chapter: the prose as written, and what it moves, adds and leaves, in one step")
async def write_chapter(story_id: str, body: WriteChapter, principal: deps.CurrentPrincipal, svc: Writing) -> dict[str, Any]:
    done = await svc.write(principal.user_id, story_id, body)
    return {
        "chapter": dump(done.chapter), "story": dump(done.story),
        "tasks": [dump(t) for t in done.tasks], "followUps": [dump(f) for f in done.follow_ups],
    }


@extra.get("/v1/stories/{story_id}/chapters", summary="A story's chapters, in the order written")
async def story_chapters(story_id: str, principal: deps.CurrentPrincipal, svc: Writing) -> dict[str, Any]:
    await svc.stories.require(principal.user_id, story_id, "Story")
    return {"items": [dump(c) for c in await svc.stories.chapters_of(principal.user_id, story_id)]}


@extra.get("/v1/stories/{story_id}/timeline",
           summary="The story so far: chapters and the recorded hearings of its cases, where it stands, and its open work")
async def story_timeline(story_id: str, principal: deps.CurrentPrincipal, svc: Writing) -> dict[str, Any]:
    t = await svc.timeline(principal.user_id, story_id)
    return {
        "story": dump(t.story),
        "here": dump(t.here) if t.here else None,
        "progress": arc_progress(t.story),
        "entries": [{"kind": e.kind, "date": e.date, e.kind: dump(e.chapter if e.chapter else e.hearing)} for e in t.entries],
        "openTasks": [dump(x) for x in t.open_tasks],
        "openFollowUps": [dump(x) for x in t.open_follow_ups],
        "cliffhangers": [{"chapterId": c.id, "number": c.number, "question": q.question} for c, q in t.cliffhangers],
        "hunches": [{"chapterId": c.id, "number": c.number, **dump(h)} for c, h in t.hunches],
        "cast": [{"character": dump(c), "part": dump(p)} for p, c in t.cast],
    }
