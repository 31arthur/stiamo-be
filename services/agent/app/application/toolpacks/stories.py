"""The stories pack: reading the person's stories, and turning what they tell you into a chapter they approve.

Nothing here writes. A chapter becomes a card (the prose as written, plus the margin notes read from it); approving it writes everything.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.application.runner import Tool, ToolOutput
from app.application.stories import draft_chapter, here_of
from app.application.toolpacks import PackContext, ToolPack
from app.application.toolpacks.common import text
from app.domain import proposals as p
from app.domain.llm import ToolSpec

S = {"type": "string"}
PACINGS = ("brisk", "steady", "slow")


def _props(**fields: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": fields}


def _summary(s: dict[str, Any]) -> dict[str, Any]:
    here = here_of(s)
    cps = s.get("checkpoints") or []
    out = {"id": s["id"], "title": s.get("title", ""), "workspace": s.get("workspace"), "state": s.get("state", "writing"),
           "ending": s.get("ending", ""), "where": here.get("title") if here else ("every checkpoint passed" if cps else "no checkpoints yet"),
           "passed": f"{sum(1 for c in cps if c.get('status') != 'ahead')} of {len(cps)}", "stakes": s.get("stakes"), "chance": s.get("chance")}
    if s.get("waitingOn"):
        out["waitingOn"] = s["waitingOn"]
    return out


class StoriesPack(ToolPack):
    name = "stories"

    async def tools(self, ctx: PackContext) -> list[Tool]:
        data = ctx.data
        runners = ctx.jobs or (lambda name: None)

        async def stories() -> list[dict[str, Any]]:
            return await data.records("stories")

        def safe(run):
            async def wrapped(a: dict[str, Any]) -> ToolOutput:
                try:
                    return await run(a)
                except p.Bad as exc:
                    return ToolOutput({"error": str(exc)})

            return wrapped

        async def list_stories(a: dict[str, Any]) -> ToolOutput:
            items = [_summary(s) for s in await stories() if a.get("include_ended") or s.get("state", "writing") != "ended"]
            return ToolOutput({"stories": items} if items else {"stories": [], "note": "No stories yet. One can be started with add_story."})

        async def read_story(a: dict[str, Any]) -> ToolOutput:
            key = text(a.get("story")).lower()
            all_ = await stories()
            s = next((x for x in all_ if x["id"] == a.get("story")), None) or next((x for x in all_ if key and key in str(x.get("title", "")).lower()), None)
            if s is None:
                return ToolOutput({"error": "No story matches that. Use list_stories."})
            chapters = await data.chapters(s["id"])
            recent = [{"number": c.get("number"), "date": c.get("date"), "title": c.get("title", ""), "beat": c.get("beat"),
                       "prose": (c.get("prose") or "")[:600]} for c in chapters[-3:]]
            return ToolOutput({**_summary(s), "checkpoints": [{"title": c.get("title"), "status": c.get("status", "ahead")} for c in s.get("checkpoints") or []],
                               "bible": s.get("bible") or [], "chapters": len(chapters), "latest": recent})

        async def write_chapter(a: dict[str, Any]) -> ToolOutput:
            if ctx.propose is None:
                return ToolOutput({"error": "Chapters can only be proposed in a chat."})
            source = text(a.get("source")) or "app"
            out = await draft_chapter(data, runners, ctx.propose, prose=text(a.get("text")), today=ctx.today, story_key=text(a.get("story")) or None,
                                      title=text(a.get("title")) or None, day=p.day(a.get("date"), "The date"), source=source)
            if out.get("ask"):
                return ToolOutput({"ask": out["ask"], "note": "It could be any of these. Ask the person which story it is (by title), then call write_chapter again with story set."})
            if out.get("none"):
                return ToolOutput({"none": True, "stories": out["stories"], "note": "No story fits. Ask whether it belongs to one of these, or offer to start a new one with add_story."})
            card = {"kind": "proposal", "id": out["id"], "action": "chapter.write", "title": out["title"], "lines": out["lines"]}
            return ToolOutput({"proposed": True, "id": out["id"], "story": out["story"], "notes": out["lines"],
                               "note": "The person sees the chapter with its margin notes and an Approve button. Nothing is saved until they approve; say so in a sentence, do not repeat the notes, and never rewrite their words."}, [card])

        async def add_story(a: dict[str, Any]) -> ToolOutput:
            if ctx.propose is None:
                return ToolOutput({"error": "Stories can only be proposed in a chat."})
            title = p.words(a.get("title"), "The title", required=True)
            ws = ctx.workspace or p.pick(a.get("workspace"), p.WORKSPACES, "The workspace") or "personal"
            raw = a.get("checkpoints") or []
            if not isinstance(raw, list):
                raise p.Bad("Checkpoints are a list of short titles, in order.")
            cps = [{"id": str(uuid.uuid4()), "title": t, "status": "ahead", "addedBy": "you"} for t in (p.words(x, "A checkpoint", most=120) for x in raw[:20]) if t]
            stakes = a.get("stakes")
            if stakes not in (None, "") and not (isinstance(stakes, int) and 1 <= stakes <= 5):
                raise p.Bad("Stakes run from 1 to 5.")
            chance = a.get("chance")
            if chance not in (None, "") and not (isinstance(chance, int) and 0 <= chance <= 100):
                raise p.Bad("The chance is a number from 0 to 100.")
            sid = str(uuid.uuid4())
            record = {"id": sid, "title": title, "workspace": ws, "ending": p.long_text(a.get("ending"), "The ending") or "",
                      "stakes": stakes or 3, "pacing": p.pick(a.get("pacing"), PACINGS, "The pacing") or "steady", "state": "writing",
                      "checkpoints": cps, "bible": [], "links": []}
            if chance not in (None, ""):
                record["chance"] = chance
            lines = [f"Ending: {record['ending']}" if record["ending"] else "No ending written yet", f"Stakes {record['stakes']} · {record['pacing']} pace"]
            lines += [f"Checkpoint {i + 1}: {c['title']}" for i, c in enumerate(cps)] or ["No checkpoints yet: they can be written later, or added as twists"]
            pid = await ctx.propose("story.create", f"New story: {title}", lines, [{"table": "stories", "id": sid, "create": True, "patch": record}])
            card = {"kind": "proposal", "id": pid, "action": "story.create", "title": f"New story: {title}", "lines": lines}
            return ToolOutput({"proposed": True, "id": pid, "note": "Nothing is saved until the person approves. Say so."}, [card])

        return [
            Tool(ToolSpec("list_stories", "The person's stories (ongoing cases, projects, errands they write about): each with where it is, its ending, stakes and chance.",
                          _props(include_ended={"type": "boolean"})), lambda a: "Read the stories", list_stories),
            Tool(ToolSpec("read_story", "One story: its arc of checkpoints, its facts and its latest chapters.", _props(story=S)),
                 lambda a: f"Read the story {text(a.get('story'))}", read_story),
            Tool(ToolSpec("write_chapter",
                          "When the person tells you what happened in one of their stories (an update on a case, a project, a loan…), propose it as a chapter. "
                          "Pass their words in `text` exactly as they wrote them: never rewrite, shorten or correct them. Margin notes (checkpoints, twists, "
                          "tasks, calls, people, hunches) are read from it for them to approve.",
                          {**_props(text={**S, "description": "The person's own words, exactly."}, story={**S, "description": "The story's id or title, if known."},
                                    title=S, date={**S, "description": "YYYY-MM-DD, if it happened on another day."},
                                    source={"type": "string", "enum": ["app", "telegram", "voice", "mail", "news"]}), "required": ["text"]}),
                 lambda a: "Read the chapter's margin notes", safe(write_chapter)),
            Tool(ToolSpec("add_story", "Propose starting a new story: its title, the ending the person is writing towards, and its checkpoints in order (their words).",
                          {**_props(title=S, ending=S, checkpoints={"type": "array", "items": S}, stakes={"type": "integer"}, chance={"type": "integer"},
                                    pacing={"type": "string", "enum": list(PACINGS)}, workspace={"type": "string", "enum": list(p.WORKSPACES)}), "required": ["title"]}),
                 lambda a: f"Proposed a story: {text(a.get('title'))}", safe(add_story)),
        ]
