"""The stories pack: reading the person's stories, and turning what they tell you into a chapter they approve.

Nothing here writes. A chapter becomes a card (the prose as written, plus the margin notes read from it); approving it writes everything.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.application.runner import Tool, ToolOutput
from app.application.stories import cast_in, draft_chapter, here_of
from app.application.toolpacks import PackContext, ToolPack
from app.application.toolpacks.common import text
from app.domain import proposals as p
from app.domain.llm import ToolSpec
from app.domain.story_jobs import same_name

S = {"type": "string"}
PACINGS = ("brisk", "steady", "slow")
STANCES = ("for_us", "neutral", "wary", "against")
SWAYS = ("decides", "shapes", "informs")


def _digits(phone: str) -> str:
    d = "".join(ch for ch in phone if ch.isdigit())
    return d[-10:]  # the last ten digits: enough to tell two numbers apart, whatever prefix each was written with


def _contact_phones(c: dict[str, Any]) -> list[str]:
    return [p.get("value") if isinstance(p, dict) else str(p) for p in c.get("phones") or [] if (p.get("value") if isinstance(p, dict) else p)]


def _contact_emails(c: dict[str, Any]) -> list[str]:
    return [e.get("value") if isinstance(e, dict) else str(e) for e in c.get("emails") or [] if (e.get("value") if isinstance(e, dict) else e)]


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

        async def find_story(key: str) -> dict[str, Any] | None:
            k = key.strip().lower()
            all_ = await stories()
            return next((x for x in all_ if x["id"] == key), None) or next((x for x in all_ if k and k in str(x.get("title", "")).lower()), None)

        async def story_cast(a: dict[str, Any]) -> ToolOutput:
            s = await find_story(text(a.get("story")))
            if s is None:
                return ToolOutput({"error": "No story matches that. Use list_stories."})
            cast, characters = await cast_in(data, s["id"])
            by_id = {c["id"]: c for c in characters}
            return ToolOutput({"story": s.get("title"), "cast": [
                {"id": m["characterId"], "name": m["name"], "part": m["part"], "calledHere": m["knownAs"], "phones": by_id[m["characterId"]].get("phones") or []}
                for m in cast]})

        async def read_character(a: dict[str, Any]) -> ToolOutput:
            key = text(a.get("character"))
            characters = await data.records("characters")
            parts = await data.records("parts")
            c = next((x for x in characters if x["id"] == key), None)
            if c is None:
                hits = [x for x in characters if same_name(key, x.get("name")) or any(same_name(key, k) for p in parts if p.get("characterId") == x["id"] for k in p.get("knownAs") or [])]
                if len(hits) > 1:
                    return ToolOutput({"ask": [{"id": h["id"], "name": h.get("name"), "organisation": h.get("organisation")} for h in hits],
                                       "note": "More than one person fits. Ask which one."})
                c = hits[0] if hits else None
            if c is None:
                return ToolOutput({"error": "Nobody in the cast is called that. Use story_cast, or add them with add_to_cast."})
            titles = {s["id"]: s.get("title", "") for s in await stories()}
            mine = [p for p in parts if p.get("characterId") == c["id"]]
            scenes = sorted((x for x in await data.records("scenes") if x.get("characterId") == c["id"]), key=lambda x: x.get("date", ""), reverse=True)
            return ToolOutput({
                "id": c["id"], "name": c.get("name"), "organisation": c.get("organisation"), "title": c.get("title"),
                "phones": c.get("phones") or [], "emails": c.get("emails") or [],
                "parts": [{"story": titles.get(p.get("storyId"), ""), "part": p.get("part") or "", "calledHere": p.get("knownAs") or [],
                           "stance": p.get("stance"), "sway": p.get("sway")} for p in mine],
                "scenes": [{"date": x.get("date"), "story": titles.get(x.get("storyId"), ""), "what": x.get("text"), "quote": x.get("quote")} for x in scenes[:10]],
            })

        async def add_to_cast(a: dict[str, Any]) -> ToolOutput:
            if ctx.propose is None:
                return ToolOutput({"error": "The cast can only be changed in a chat."})
            name = p.words(a.get("name"), "The name", required=True)
            body: dict[str, Any] = {"name": name, "id": str(uuid.uuid4())}
            lines: list[str] = []
            phones = [x for x in [text(a.get("phone"))] if x]
            if (look := text(a.get("from_contact"))):
                found = await data.contacts(look, 10)
                exact = [x for x in found if same_name(look, x.get("name"))] or found
                if len(exact) != 1:
                    return ToolOutput({"error": f"{'No' if not exact else 'More than one'} Google contact matches “{look}”.",
                                       "contacts": [x.get("name") for x in exact[:5]]})
                g = exact[0]
                phones += _contact_phones(g)
                body.update(emails=_contact_emails(g), organisation=g.get("organisation") or None, title=g.get("title") or None, googleContactId=g.get("id"))
                lines.append(f"From your Google contact “{g.get('name')}”")
            if phones:
                body["phones"] = phones
            if (org := p.words(a.get("organisation"), "The organisation")) is not None:
                body["organisation"] = org
            story = None
            if text(a.get("story")):
                story = await find_story(text(a.get("story")))
                if story is None:
                    raise p.Bad("No story matches that. Use list_stories.")
                body["storyId"] = story["id"]
                if (part := p.words(a.get("part"), "The part", most=200)) is not None:
                    body["part"] = part
                if (st := p.pick(a.get("stance"), STANCES, "The stance")) is not None:
                    body["stance"] = st
                if (sw := p.pick(a.get("sway"), SWAYS, "The sway")) is not None:
                    body["sway"] = sw
            # Found before made: someone this workspace already knows by that number is reused, so nobody is entered twice.
            wanted = {_digits(x) for x in phones if len(_digits(x)) >= 6}
            known = next((c for c in await data.records("characters") if wanted & {_digits(x) for x in c.get("phones") or []}), None) if wanted else None
            if known is not None:
                body["characterId"] = known["id"]
                lines.insert(0, f"Already in your cast as {known.get('name')} (same number)")
            if phones:
                lines.append("Phone: " + ", ".join(phones))
            if body.get("organisation"):
                lines.append(f"Organisation: {body['organisation']}")
            if story is not None:
                lines.append(f"Part in “{story.get('title')}”: {body.get('part') or 'not said'}" + (f" · {body['stance'].replace('_', ' ')}" if body.get("stance") else ""))
            title = f"Cast: {known.get('name') if known else name}"
            pid = await ctx.propose("cast.join", title, lines or ["Joins your cast"], [{"cast": body}])
            card = {"kind": "proposal", "id": pid, "action": "cast.join", "title": title, "lines": lines or ["Joins your cast"]}
            return ToolOutput({"proposed": True, "id": pid, "note": "Nothing is saved until the person approves. Say so in a sentence."}, [card])

        return [
            Tool(ToolSpec("story_cast", "The people in one story: who each is, the part they play there, what they're called in its chapters, their numbers.",
                          {**_props(story=S), "required": ["story"]}), lambda a: f"Read the cast of {text(a.get('story'))}", story_cast),
            Tool(ToolSpec("read_character", "One person in the cast: their numbers and organisation, the part they play in each story, and their latest scenes.",
                          {**_props(character={**S, "description": "Their id, name, or what a story calls them."}), "required": ["character"]}),
                 lambda a: f"Read {text(a.get('character'))}", read_character),
            Tool(ToolSpec("add_to_cast",
                          "Propose bringing someone into the cast, optionally with the part they play in a story. Give a phone number, or `from_contact` "
                          "(a Google contact's name) to take their numbers and organisation from there. Someone already known by that number is reused.",
                          {**_props(name=S, phone=S, organisation=S, from_contact=S, story=S, part=S,
                                    stance={"type": "string", "enum": list(STANCES)}, sway={"type": "string", "enum": list(SWAYS)}), "required": ["name"]}),
                 lambda a: f"Proposed {text(a.get('name'))} for the cast", safe(add_to_cast)),
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
