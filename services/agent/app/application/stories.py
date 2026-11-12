"""Writing a chapter from words: find the story, read the margin notes, and put it all on one card the person approves.

Used by the stories tool pack (a line on Telegram, a voice note, a chat) and by the app's writing view, so both go the same way: the prose
is kept exactly as written, the notes are what the margin-notes job read from it and code checked, and approving the card writes the
chapter, its twists, its tasks and calls and its hunches in one step (the practice service's /chapters/write).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from app.application.aijobs import JobRunner
from app.application.ports import DataGateway
from app.domain import proposals as p
from app.domain.aijobs import JobResult
from app.domain.story_jobs import FIND_STORY, MARGIN_NOTES, empty_notes, story_named

Offer = Callable[[str, str, list[str], list[dict[str, Any]]], Awaitable[str]]
Runners = Callable[[str], JobRunner | None]
SOURCES = ("app", "telegram", "voice", "mail", "news", "hearing")


def here_of(story: dict[str, Any]) -> dict[str, Any] | None:
    return next((c for c in story.get("checkpoints") or [] if c.get("status", "ahead") == "ahead"), None)


def cast_of(chapters: list[dict[str, Any]]) -> list[str]:
    """The people named in a story's chapters, most recent first, once each."""
    seen: dict[str, None] = {}
    for c in reversed(chapters):
        for person in c.get("people") or []:
            if person.get("name"):
                seen.setdefault(person["name"], None)
    return list(seen)


def brief(story: dict[str, Any], chapters: list[dict[str, Any]]) -> dict[str, Any]:
    """The story as a job sees it: small, with ids to choose from."""
    here = here_of(story)
    return {
        "id": story["id"], "title": story.get("title", ""), "ending": story.get("ending", ""), "here": here["id"] if here else None,
        "checkpoints": [{"id": c["id"], "title": c.get("title", ""), "status": c.get("status", "ahead")} for c in story.get("checkpoints") or []],
        "people": cast_of(chapters),
    }


@dataclass
class Found:
    status: Literal["found", "ask", "none"]
    story: dict[str, Any] | None = None
    choices: list[dict[str, Any]] = field(default_factory=list)
    by: str = "code"


async def find_story(message: str, stories: list[dict[str, Any]], runner: JobRunner | None, key: str | None = None) -> Found:
    """The story a message (or a given id or title) is about. Code first: an id, or a title the message names. Only then the model."""
    live = [s for s in stories if s.get("state", "writing") != "ended"] or stories
    if key:
        k = key.strip().lower()
        hit = next((s for s in stories if s.get("id") == key), None) or next((s for s in stories if str(s.get("title", "")).strip().lower() == k), None)
        if hit:
            return Found("found", hit)
        partial = [s for s in stories if k in str(s.get("title", "")).lower()]
        if len(partial) == 1:
            return Found("found", partial[0])
    if not live:
        return Found("none")
    named = story_named(f"{key}: {message}" if key else message, live)
    if named:
        return Found("found", next(s for s in live if s["id"] == named))
    if runner is None:
        return Found("ask", choices=live[:5]) if len(live) <= 5 else Found("none")
    result = await runner.run(FIND_STORY, {"message": message, "stories": [
        {"id": s["id"], "title": s.get("title", ""), "ending": s.get("ending", ""),
         "checkpoints": [c.get("title", "") for c in s.get("checkpoints") or []]} for s in live]})
    by_id = {s["id"]: s for s in live}
    if result.output.get("story") in by_id:
        return Found("found", by_id[result.output["story"]], by="model")
    unsure = [by_id[i] for i in result.output.get("unsure_between") or [] if i in by_id]
    return Found("ask", choices=unsure, by="model") if unsure else Found("none", by="model")


async def read_notes(runner: JobRunner | None, story: dict[str, Any], chapters: list[dict[str, Any]], prose: str, today: str, day: str | None) -> JobResult:
    if runner is None:
        return JobResult(empty_notes(), False, 0, [], ["no model is chosen for margin notes"])
    return await runner.run(MARGIN_NOTES, {"story": brief(story, chapters), "prose": prose, "today": today, "date": day or today})


def to_body(notes: dict[str, Any], prose: str, *, title: str | None, day: str | None, source: str, new_id: Callable[[], str] = lambda: str(uuid.uuid4())) -> dict[str, Any]:
    """The practice service's write-chapter request: the prose as written and the checked notes, each new record with its id fixed now so
    approving twice (or retrying after a failure) never makes anything twice."""
    body: dict[str, Any] = {
        "id": new_id(), "prose": prose, "title": (title or notes.get("title") or "").strip(), "beat": notes.get("beat", "note"),
        "source": source if source in SOURCES else "app",
        "checkpoints": [{"id": c["id"], "status": c["status"], "quote": c.get("quote")} for c in notes.get("checkpoints", [])],
        "twists": [{"id": new_id(), "title": t["title"], "before": t.get("before"), "quote": t.get("quote")} for t in notes.get("twists", [])],
        "steps": [{"id": new_id(), "kind": s["kind"], "title": s["title"], "due": s.get("due"), "contact": s.get("contact"), "quote": s.get("quote"),
                   **({"channel": s.get("channel") or "call"} if s["kind"] == "followUp" else {})} for s in notes.get("steps", [])],
        "hunches": [{"id": new_id(), "text": h["text"], "quote": h.get("quote")} for h in notes.get("hunches", [])],
        "people": [{"name": x["name"], "scene": x.get("scene"), "quote": x.get("quote")} for x in notes.get("people", [])],
    }
    if day:
        body["date"] = day
    if notes.get("cliffhanger"):
        body["cliffhanger"] = notes["cliffhanger"]["question"]
    if notes.get("waiting_on"):
        body["waitingOn"] = {"on": notes["waiting_on"]["on"], **({"until": notes["waiting_on"]["until"]} if notes["waiting_on"].get("until") else {})}
    return body


_CHANNEL = {"call": "Call", "message": "Message", "email": "Email", "visit": "Visit"}


def card_lines(story: dict[str, Any], body: dict[str, Any], result: JobResult) -> list[str]:
    """What the card says: the margin notes in plain words, then what wasn't noted and why."""
    titles = {c["id"]: c.get("title", "") for c in story.get("checkpoints") or []}
    lines: list[str] = []
    if body["beat"] != "note":
        lines.append(f"Beat: {body['beat']}")
    for c in body["checkpoints"]:
        lines.append(f"Checkpoint {'reached' if c['status'] == 'reached' else 'skipped'}: {titles.get(c['id'], c['id'])}")
    for t in body["twists"]:
        where = f", before “{titles[t['before']]}”" if t.get("before") in titles else ""
        lines.append(f"Twist: new checkpoint “{t['title']}”{where}")
    for s in body["steps"]:
        what = "Task" if s["kind"] == "task" else _CHANNEL.get(s.get("channel") or "call", "Call")
        due = f" · {p.day_label(s['due'])}" if s.get("due") else ""
        who = f" · {s['contact']}" if s.get("contact") else ""
        lines.append(f"{what}: {s['title']}{who}{due}")
    for x in body["people"]:
        lines.append(f"Cast: {x['name']}" + (f", {x['scene']}" if x.get("scene") else ""))
    for h in body["hunches"]:
        lines.append(f"Hunch: {h['text']}")
    if body.get("waitingOn"):
        w = body["waitingOn"]
        lines.append(f"Waiting on: {w['on']}" + (f" until {p.day_label(w['until'])}" if w.get("until") else ""))
    if body.get("cliffhanger"):
        lines.append(f"Cliffhanger: {body['cliffhanger']}")
    lines.append("Your words are saved exactly as you wrote them.")
    if not result.ok:
        lines.append("No margin notes this time: " + (result.problems[-1] if result.problems else "the notes couldn't be read") + ". The chapter is kept as written.")
    elif result.dropped:
        lines.append(f"Left out {len(result.dropped)} note{'s' if len(result.dropped) != 1 else ''} that couldn't be pinned to your words.")
    return lines


async def propose_chapter(offer: Offer, story: dict[str, Any], body: dict[str, Any], result: JobResult) -> tuple[str, str, list[str]]:
    title = f"Chapter for “{story.get('title', '')}”" + (f": {body['title']}" if body.get("title") else "")
    lines = card_lines(story, body, result)
    pid = await offer("chapter.write", title, lines, [{"chapter": {"story": story["id"], "body": body}}])
    return pid, title, lines


async def draft_chapter(data: DataGateway, runners: Runners, offer: Offer, *, prose: str, today: str, story_key: str | None = None,
                        title: str | None = None, day: str | None = None, source: str = "app") -> dict[str, Any]:
    """The whole path from words to a card. Returns {proposed, id, title, lines, notes, dropped} or {ask: [...]} / {none: True}."""
    prose = (prose or "").strip()
    if not prose:
        raise p.Bad("Write the chapter first.")
    stories = await data.records("stories")
    found = await find_story(prose, stories, runners(FIND_STORY.name), story_key)
    if found.status == "ask":
        return {"ask": [{"id": s["id"], "title": s.get("title", "")} for s in found.choices]}
    if found.status == "none" or found.story is None:
        return {"none": True, "stories": [{"id": s["id"], "title": s.get("title", "")} for s in stories[:20]]}
    story = found.story
    chapters = await data.chapters(story["id"])
    result = await read_notes(runners(MARGIN_NOTES.name), story, chapters, prose, today, day)
    body = to_body(result.output, prose, title=title, day=day, source=source)
    pid, card_title, lines = await propose_chapter(offer, story, body, result)
    return {"proposed": True, "id": pid, "story": {"id": story["id"], "title": story.get("title", "")}, "title": card_title, "lines": lines,
            "notes": result.output, "dropped": result.dropped, "notesOk": result.ok, "model": result.model}
