"""The two AI jobs of writing a chapter: which story a message is about, and the margin notes read from the chapter. Pure.

Both are built to work on cheap models: one narrow decision each, plain-word definitions, worked examples, a day table instead of date
arithmetic, ids to choose from instead of names to invent, and every note pinned to a quote that code checks against the person's words.
Code does what code can: a story named in the message is found without asking any model at all.
"""

from __future__ import annotations

import re
from typing import Any

from app.domain.aijobs import Checked, Job, Problem, calendar, find_quote, iso_day, items, one_of, short

BEATS = ("progress", "setback", "twist", "lull", "breakthrough", "note")
CHANNELS = ("call", "message", "email", "visit")
_STOP = frozenset("the a an and or of to for in on at is my our with from by".split())
#: Words a chapter uses for its own writer. They are never a character.
NARRATOR = frozenset({"i", "me", "myself", "we", "us", "you"})


def same_name(a: str | None, b: str | None) -> bool:
    """Whether two ways of naming someone are the same name: case, "the" and punctuation don't matter ("The agent" = "agent")."""
    def norm(s: str | None) -> str:
        return " ".join(re.findall(r"[a-z0-9]+", (s or "").lower().removeprefix("the ")))
    return bool(norm(a)) and norm(a) == norm(b)

# --- which story -------------------------------------------------------------------------------------------------


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9&]+", (text or "").lower()) if w not in _STOP]


def story_named(message: str, stories: list[dict[str, Any]]) -> str | None:
    """The story a message names, found by code: "Home loan: the agent called…" or "…on the home loan…". Only a single clear match counts;
    two stories named, or none, is left to the model (or to the person)."""
    text = " ".join(_words(message))
    head = " ".join(_words(re.split(r"[:\n—–-]", message, maxsplit=1)[0])) if re.search(r"[:\n—–-]", message) else ""
    exact = [s["id"] for s in stories if (t := " ".join(_words(s.get("title", "")))) and (head == t or f" {t} " in f" {text} ")]
    if len(exact) == 1:
        return exact[0]
    return None


FIND_SYSTEM = """You decide which of a person's stories a message is about. A story is something ongoing in their life or work (a court case, a client project, a home loan). The message is usually an update on one of them.

How to decide:
1. Look for the story's title, or words from it, in the message.
2. Then look for its people, its checkpoints or what it is about.
3. If one story clearly fits, give its id.
4. If two or three could fit and you can't tell, give no story and list them in "unsure_between".
5. If none fits, give no story and an empty list.
Never give an id that isn't in the list.

Reply with one JSON object and nothing else:
{"story": "<id>" or null, "unsure_between": ["<id>", ...], "reason": "<at most 12 words>"}"""


def _render_find(i: dict[str, Any]) -> str:
    lines = []
    for s in i["stories"]:
        bits = [f"{s['id']} · {s.get('title', '')}"]
        if s.get("ending"):
            bits.append(f"ending: {s['ending']}")
        if s.get("checkpoints"):
            bits.append("checkpoints: " + ", ".join(s["checkpoints"][:8]))
        if s.get("people"):
            bits.append("people: " + ", ".join(s["people"][:8]))
        lines.append("- " + " · ".join(bits))
    return f"The person's message:\n<<<\n{i['message']}\n>>>\n\nTheir stories:\n" + "\n".join(lines)


def _check_find(out: dict[str, Any], i: dict[str, Any]) -> Checked:
    ids = {s["id"] for s in i["stories"]}
    story = out.get("story")
    dropped = []
    if story is not None and story not in ids:
        dropped.append(f"“{story}” isn't one of the stories")
        story = None
    unsure = [x for x in (out.get("unsure_between") or []) if isinstance(x, str) and x in ids and x != story][:3] if not story else []
    return Checked({"story": story, "unsure_between": unsure, "reason": short(out.get("reason"), 120) or ""}, dropped)


_FIND_EXAMPLE_STORIES = [
    {"id": "a1", "title": "Gulbarga SMPL", "ending": "Order collected, fee in", "checkpoints": ["Application filed", "Order passed"], "people": ["SMPL's MD", "Counsel, Kalaburagi"]},
    {"id": "a2", "title": "Home loan", "ending": "Disbursed before March", "checkpoints": ["Documents in", "Sanction letter"], "people": ["Loan agent"]},
    {"id": "a3", "title": "Car loan", "ending": "Approved by June", "checkpoints": ["Quote", "Approval"], "people": ["Bank RM"]},
]

FIND_STORY = Job(
    name="find_story",
    version="1",
    purpose="Works out which of your stories a message is about, when the message doesn't name it.",
    system=FIND_SYSTEM,
    examples=(
        ({"message": "The registry wants a notice in a Bangalore paper before the next date.", "stories": _FIND_EXAMPLE_STORIES},
         {"story": "a1", "unsure_between": [], "reason": "registry and notice fit the court matter"}),
        ({"message": "The bank called, they want my salary slips again.", "stories": _FIND_EXAMPLE_STORIES},
         {"story": None, "unsure_between": ["a2", "a3"], "reason": "both loans involve the bank"}),
        ({"message": "Bought a new phone today.", "stories": _FIND_EXAMPLE_STORIES},
         {"story": None, "unsure_between": [], "reason": "no story is about a phone"}),
    ),
    render=_render_find,
    check=_check_find,
    fallback=lambda i: {"story": None, "unsure_between": [], "reason": ""},
)

# --- margin notes ------------------------------------------------------------------------------------------------


NOTES_SYSTEM = """You read one chapter of a story a person is writing about their own life or work, and write the notes an editor puts in the margin. You never rewrite, judge or add to the chapter. You only point at what it already says.

Rules:
1. Every note has a "quote": words copied exactly from the chapter, character for character, at least three words. If you can't quote it, leave the note out.
2. Only note what the chapter says or plainly means. Never invent a task, a person, a date or an event.
3. Leave a list empty when the chapter has nothing for it. Empty lists are a good answer.
4. Dates: only when the chapter names a day. Find that day in the calendar given and copy its date. Never count days yourself. No day named: null.
5. Use only the checkpoint ids given.

The fields:
- beat: how the chapter moves the story. One of: "progress" (a step forward), "setback" (something went wrong or was delayed), "twist" (something unexpected that changes the plan), "lull" (waiting; nothing moved), "breakthrough" (a big step forward), "note" (none of these).
- checkpoints: checkpoints from the arc that this chapter says were reached ("reached"), or are no longer needed ("skipped"). Not ones that are only hoped for.
- twists: NEW plot points the story must now pass through, not in the arc yet. Title: 2 to 6 words, written as an outcome ("Get old Form 16"), not as a task. "before": the id of the checkpoint it must come before, or null to put it next.
- steps: things the person says they will do or must do. "kind": "task" for work they do themselves; "followUp" for reaching someone (a call, a message, an email or a visit; say which in "channel"). Title starts with a verb. "contact": who to reach, as the chapter names them, or null.
- people: each person who appears, other than the writer, named as the chapter names them ("the agent", "Ravi"), with what they did in a few words ("scene"). Use the name from "People already in the story" when the chapter clearly means one of them.
- hunches: what the person expects or suspects will happen ("something tells me…", "I bet…", "they'll probably…"). Not plans.
- cliffhanger: the open question the chapter ends on, written as a question, with its quote. Otherwise null.
- waiting_on: what the story is now waiting for (a person or thing), and "until" if a day is named, with its quote. Otherwise null.
- title: a title for the chapter in 2 to 5 words, in the spirit of the person's writing.

Reply with one JSON object and nothing else, in exactly this shape:
{"beat": "...", "checkpoints": [{"id": "...", "status": "reached", "quote": "..."}], "twists": [{"title": "...", "before": null, "quote": "..."}], "steps": [{"kind": "task", "title": "...", "due": null, "channel": null, "contact": null, "quote": "..."}], "people": [{"name": "...", "scene": "...", "quote": "..."}], "hunches": [{"text": "...", "quote": "..."}], "cliffhanger": null, "waiting_on": null, "title": "..."}"""


def _render_notes(i: dict[str, Any]) -> str:
    s = i["story"]
    here = s.get("here")
    arc = "\n".join(f"- {c['id']} {c['title']} [{c.get('status', 'ahead')}]" + (" ← where the story is now" if c["id"] == here else "")
                    for c in s.get("checkpoints", [])) or "(no checkpoints yet)"
    people = ", ".join(s.get("people", [])) or "(none yet)"
    return (
        f"Story: {s.get('title', '')}\n"
        f"The ending they are writing towards: {s.get('ending') or '(not written)'}\n"
        f"The arc, in order:\n{arc}\n"
        f"People already in the story: {people}\n\n"
        f"Calendar:\n{calendar(i['today'])}\n\n"
        f"The chapter, written {i.get('date') or i['today']}:\n<<<\n{i['prose']}\n>>>"
    )


def _quoted(entry: dict[str, Any], prose: str, what: str, dropped: list[str]) -> str | None:
    q = find_quote(entry.get("quote"), prose)
    if q is None:
        dropped.append(f"{what}: its quote isn't in the chapter word for word")
    return q


def _check_notes(out: dict[str, Any], i: dict[str, Any]) -> Checked:
    prose, story = i["prose"], i["story"]
    arc = {c["id"]: c for c in story.get("checkpoints", [])}
    known_titles = {" ".join(c["title"].lower().split()) for c in arc.values()}
    dropped: list[str] = []
    asked = 0  # quote-bearing notes the model gave

    beat = one_of(out.get("beat"), BEATS)
    if beat is None:
        if out.get("beat"):
            dropped.append(f"beat “{out.get('beat')}” isn't one of the beats")
        beat = "note"

    checkpoints = []
    for c in items(out.get("checkpoints"), 6):
        asked += 1
        cid, status = c.get("id"), one_of(c.get("status"), ("reached", "skipped"))
        if cid not in arc or status is None:
            dropped.append(f"checkpoint “{cid}”: not in the arc, or no clear status")
            continue
        if (q := _quoted(c, prose, f"checkpoint {arc[cid]['title']}", dropped)) is None:
            continue
        if not any(x["id"] == cid for x in checkpoints):
            checkpoints.append({"id": cid, "status": status, "quote": q})

    twists = []
    for t in items(out.get("twists"), 3):
        asked += 1
        title = short(t.get("title"), 60)
        if not title:
            continue
        if " ".join(title.lower().split()) in known_titles:
            dropped.append(f"twist “{title}”: already in the arc")
            continue
        if (q := _quoted(t, prose, f"twist {title}", dropped)) is None:
            continue
        before = t.get("before") if t.get("before") in arc else None
        twists.append({"title": title, "before": before, "quote": q})

    steps = []
    for s in items(out.get("steps"), 6):
        asked += 1
        title = short(s.get("title"), 120)
        kind = {"task": "task", "followup": "followUp"}.get(str(s.get("kind") or "task").lower().replace("_", "").replace("-", ""), "task")
        if not title:
            continue
        if (q := _quoted(s, prose, f"step {title}", dropped)) is None:
            continue
        step = {"kind": kind, "title": title, "due": iso_day(s.get("due")), "contact": short(s.get("contact"), 80), "quote": q}
        if kind == "followUp":
            step["channel"] = one_of(s.get("channel"), CHANNELS, "call")
        steps.append(step)

    people = []
    for p in items(out.get("people"), 8):
        asked += 1
        name = short(p.get("name"), 80)
        if not name or name.lower() in NARRATOR:
            continue
        if (q := _quoted(p, prose, f"person {name}", dropped)) is None:
            continue
        if not any(x["name"].lower() == name.lower() for x in people):
            people.append({"name": name, "scene": short(p.get("scene"), 120), "quote": q})

    hunches = []
    for h in items(out.get("hunches"), 4):
        asked += 1
        text = short(h.get("text"), 160)
        if not text:
            continue
        if (q := _quoted(h, prose, f"hunch {text}", dropped)) is None:
            continue
        hunches.append({"text": text, "quote": q})

    cliffhanger = None
    if isinstance(out.get("cliffhanger"), dict):
        asked += 1
        question = short(out["cliffhanger"].get("question") or out["cliffhanger"].get("text"), 160)
        if question and (q := _quoted(out["cliffhanger"], prose, "cliffhanger", dropped)) is not None:
            cliffhanger = {"question": question, "quote": q}

    waiting = None
    if isinstance(out.get("waiting_on"), dict):
        asked += 1
        on = short(out["waiting_on"].get("on"), 120)
        if on and (q := _quoted(out["waiting_on"], prose, "waiting on", dropped)) is not None:
            waiting = {"on": on, "until": iso_day(out["waiting_on"].get("until")), "quote": q}

    kept = len(checkpoints) + len(twists) + len(steps) + len(people) + len(hunches) + (cliffhanger is not None) + (waiting is not None)
    if asked >= 2 and kept == 0:
        raise Problem("None of your quotes appear in the chapter word for word. Copy each quote exactly as it is written in the chapter, "
                      "character for character, or leave that note out.")
    return Checked({
        "beat": beat, "checkpoints": checkpoints, "twists": twists, "steps": steps, "people": people, "hunches": hunches,
        "cliffhanger": cliffhanger, "waiting_on": waiting, "title": short(out.get("title"), 60),
    }, dropped)


def empty_notes(_: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"beat": "note", "checkpoints": [], "twists": [], "steps": [], "people": [], "hunches": [], "cliffhanger": None, "waiting_on": None, "title": None}


_EX_LOAN = {
    "today": "2026-11-12", "date": "2026-11-12",
    "story": {"title": "Home loan", "ending": "Disbursed before March", "here": "k2", "people": ["Loan agent"], "checkpoints": [
        {"id": "k1", "title": "Pick a lender", "status": "reached"}, {"id": "k2", "title": "Documents in", "status": "ahead"},
        {"id": "k3", "title": "Bank verification", "status": "ahead"}]},
    "prose": ("The agent called at seven, sorry as always. The bank wants Form 16 for two years, not one, and my old employer's portal "
              "locked me out in June. I'll write to HR on Monday. If they don't answer, I'll go in person on Wednesday. "
              "Something tells me they'll ask for the ITRs next."),
}
_EX_COURT = {
    "today": "2026-11-12", "date": "2026-11-12",
    "story": {"title": "Gulbarga SMPL", "ending": "Order collected, fee in", "here": "g2", "people": ["Counsel, Kalaburagi"], "checkpoints": [
        {"id": "g1", "title": "Application filed", "status": "ahead"}, {"id": "g2", "title": "Order passed", "status": "ahead"}]},
    "prose": "Filed the application this morning, finally. Now it's the court's turn. Nothing to do until the listing on Friday.",
}
_EX_LULL = {
    "today": "2026-11-12", "date": "2026-11-12",
    "story": {"title": "Client website", "ending": "Live and paid by December", "here": "w3", "people": [], "checkpoints": [
        {"id": "w3", "title": "Build", "status": "ahead"}]},
    "prose": "Quiet week. Still no content from them.",
}

MARGIN_NOTES = Job(
    name="margin_notes",
    version="2",
    purpose="Reads a chapter you wrote and notes what it moves, adds and leaves to do, each note pinned to your words.",
    system=NOTES_SYSTEM,
    examples=(
        (_EX_LOAN, {
            "beat": "setback", "checkpoints": [],
            "twists": [{"title": "Get old Form 16", "before": None, "quote": "The bank wants Form 16 for two years, not one"}],
            "steps": [
                {"kind": "task", "title": "Write to HR for the old Form 16", "due": "2026-11-16", "channel": None, "contact": "HR", "quote": "I'll write to HR on Monday"},
                {"kind": "followUp", "title": "Visit the old office if HR doesn't answer", "due": "2026-11-18", "channel": "visit", "contact": "HR", "quote": "I'll go in person on Wednesday"},
            ],
            "people": [{"name": "The agent", "scene": "called, apologetic, with more paperwork", "quote": "The agent called at seven, sorry as always"}],
            "hunches": [{"text": "The bank will ask for the ITRs next", "quote": "they'll ask for the ITRs next"}],
            "cliffhanger": None, "waiting_on": None, "title": "The Form 16 problem",
        }),
        (_EX_COURT, {
            "beat": "progress", "checkpoints": [{"id": "g1", "status": "reached", "quote": "Filed the application this morning"}],
            "twists": [], "steps": [], "people": [], "hunches": [], "cliffhanger": None,
            "waiting_on": {"on": "The listing", "until": "2026-11-13", "quote": "Nothing to do until the listing on Friday"},
            "title": "The court's turn",
        }),
        (_EX_LULL, {
            "beat": "lull", "checkpoints": [], "twists": [], "steps": [], "people": [], "hunches": [], "cliffhanger": None,
            "waiting_on": {"on": "The client's content", "until": None, "quote": "Still no content from them"}, "title": "Quiet week",
        }),
    ),
    render=_render_notes,
    check=_check_notes,
    fallback=empty_notes,
)

JOBS: dict[str, Job] = {j.name: j for j in (FIND_STORY, MARGIN_NOTES)}
