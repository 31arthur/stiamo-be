"""The interviewer: which person to ask about, on which lens, and how. Plain code, the same whatever model is running.

priority = unsure × matters × now × lens weight
  unsure       1 − confidence on that lens (1 when there's no evidence or it's in conflict)
  matters      the highest stakes of their stories (1–5, as a fifth) × how much they sway it (decides 1 · shapes .7 · informs .4 · not said .5)
  now          ×2 when you're in touch with them within two days, or just were
  lens weight  under pressure and wants count 1.5: they're what breaking news depends on

Eight ways of asking, each a known interviewing technique, picked by the moment:
  a prediction to settle → debrief it · just in touch → debrief · a conflict → check · someone new → character sheet ·
  wants unknown and high stakes → why, and why again · scenes but no answers yet → the moment · a story gone quiet → what would they do ·
  once a week, two people in like parts → compare two · otherwise → quick tap (about a specific scene when there is one)

Free-text answers (the moment, why, compare) are written to the assistant starting with the person's name, and become scenes or a lens.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from app.domain.lenses import CODEBOOK, LENSES, REACTION_WORDS, REACTIONS, label_names, phrase

SWAY = {"decides": 1.0, "shapes": 0.7, "informs": 0.4}
LENS_WEIGHT = {"under_pressure": 1.5, "wants": 1.5}
UNSURE = {"high": 0.1, "medium": 0.4, "low": 0.7}
#: Questions a day per workspace (on top of the editor's one), one per person per day; "skip" rests a person for a week.
PER_DAY = 2
REST_DAYS = 7

#: How each lens is asked as a quick tap.
TAP = {
    "talks": "When {name} has something to tell you, they…",
    "decides": "In “{story}”, {name}…",
    "wants": "What does {name} want most in “{story}”?",
    "under_pressure": "When the news is bad, {name}…",
    "trust": "In “{story}”, {name} is…",
    "moved_by": "What gets {name} to act?",
}


def _d(s: Any) -> date | None:
    try:
        return date.fromisoformat(str(s)[:10]) if s else None
    except ValueError:
        return None


@dataclass
class Person:
    """Everything the interviewer weighs about one person, as seen from one workspace."""

    character: dict[str, Any]
    #: (part, story) for each of their stories in this workspace.
    parts: list[tuple[dict[str, Any], dict[str, Any]]]
    evidence: list[dict[str, Any]] = field(default_factory=list)
    scenes: list[dict[str, Any]] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    #: Stories of theirs that have gone quiet (ids).
    quiet: set[str] = field(default_factory=set)


@dataclass
class Question:
    person: Person
    method: str
    lens: str
    story: dict[str, Any] | None
    text: str
    options: list[tuple[str, str]]  # (label, button text)
    specific: bool = False
    scene: dict[str, Any] | None = None
    prediction: dict[str, Any] | None = None
    other: dict[str, Any] | None = None
    priority: float = 0.0


def matters(p: Person) -> float:
    best = 0.0
    for part, story in p.parts:
        stakes = story.get("stakes") if isinstance(story.get("stakes"), int) else 3
        best = max(best, stakes / 5 * SWAY.get(part.get("sway") or "", 0.5))
    return best or 0.3


def in_touch(p: Person, today: str) -> str | None:
    """The day they were reached or a call with them was done, within the last two days."""
    now = _d(today) or date.today()
    days = []
    for f in p.calls:
        days += [a.get("date") for a in f.get("attempts") or [] if a.get("result") == "reached"]
        if (f.get("outcome") or {}).get("status") == "done":
            days.append(((f.get("outcome") or {}).get("recordedAt") or "")[:10])
    recent = [d for d in days if (_d(d) and 0 <= (now - _d(d)).days <= 2)]
    return max(recent) if recent else None


def unsure(profile: dict[str, Any] | None, lens: str) -> float:
    v = ((profile or {}).get("lenses") or {}).get(lens)
    if not v or v.get("conflict"):
        return 1.0
    return UNSURE.get(v.get("confidence") or "low", 0.7)


def main_story(p: Person) -> dict[str, Any] | None:
    return max((s for _, s in p.parts), key=lambda s: s.get("stakes") if isinstance(s.get("stakes"), int) else 3, default=None)


def _options(lens: str) -> list[tuple[str, str]]:
    return [(label, phrase(lens, label)) for label in label_names(lens)]


def choose(p: Person, today: str, memory: dict[str, Any], week_compare_done: bool, others: list[Person]) -> Question | None:
    """The one question worth asking about this person now, or None."""
    c = p.character
    name, prof = c.get("name") or "them", c.get("profile") or {}
    story = main_story(p)
    if story is None:
        return None
    mem = memory.get(c["id"]) or {}
    if (_d(mem.get("rest")) or date.min) > (_d(today) or date.today()) or mem.get("on") == today:
        return None
    title = story.get("title", "")
    touch = in_touch(p, today)
    now = 2.0 if touch else 1.0
    base = matters(p) * now

    pending = [e for e in p.evidence if e.get("source") == "prediction" and e.get("verdict") == "pending" and (e.get("date") or "") < today]
    if pending and touch:
        e = max(pending, key=lambda x: x.get("date") or "")
        return Question(p, "debrief_prediction", "under_pressure", next((s for _, s in p.parts if s["id"] == e.get("storyId")), story),
                        f"You were going to tell {name}: “{e.get('about')}”. How did they take it?",
                        [(r, REACTION_WORDS[r]) for r in REACTIONS], True, prediction=e, priority=base * 2)
    if touch:
        return Question(p, "debrief", "trust", story, f"You were in touch with {name} on {touch}. In “{title}”, they're…", _options("trust"), True, priority=base * 1.5)
    conflicts = [k for k in prof.get("conflicts") or [] if k in LENSES]
    if conflicts:
        lens = conflicts[0]
        a, b = prof["lenses"][lens]["conflict"][:2]
        return Question(p, "check", lens, story, f"About {name}, I have both “{phrase(lens, a)}” and “{phrase(lens, b)}”. Which is closer now?",
                        [(a, phrase(lens, a)), (b, phrase(lens, b)), ("depends", "It depends")], True, priority=base * 1.4)
    answers = [e for e in p.evidence if e.get("source") == "answer"]
    if not p.evidence and not mem.get("sheet"):
        return Question(p, "sheet", "wants", story, f"🎭 {name} is new in “{title}”. What do they want most here?", _options("wants"), priority=base * 1.3)
    if "wants" not in (prof.get("lenses") or {}) and isinstance(story.get("stakes"), int) and story["stakes"] >= 4 and not mem.get("why"):
        return Question(p, "why", "wants", story, f"What does {name} want out of “{title}”? Tell me in a line, starting with “{name}:”, and I'll ask why once more.", [], priority=base * 1.2)
    if len(p.scenes) >= 2 and not answers and not mem.get("moment"):
        return Question(p, "moment", "", story, f"Tell me about the moment {name} surprised you most: what happened, what they did, what came of it. Start with “{name}:”.", [], priority=base * 1.1)
    if story["id"] in p.quiet and not any(e.get("source") == "prediction" and e.get("predictedBy") == "you" and e.get("verdict") == "pending" for e in p.evidence) \
            and (_d(mem.get("what_would")) or date.min) < (_d(today) or date.today()) - timedelta(days=30):
        return Question(p, "what_would", "under_pressure", story, f"A guess, to check later: if you brought {name} bad news about “{title}”, they'd…",
                        [(r, REACTION_WORDS[r]) for r in REACTIONS], True, priority=base * 1.05)
    if not week_compare_done:
        like = next((o for o in others if o.character["id"] != c["id"] and any(
            (pa.get("part") or "").strip().lower() and (pa.get("part") or "").strip().lower() == (pb.get("part") or "").strip().lower()
            for pa, _ in p.parts for pb, _ in o.parts)), None)
        if like:
            other = like.character.get("name")
            return Question(p, "compare", "", story, f"How is {name} different from {other}? Answer in a line, starting with “compare {name} and {other}:”.", [],
                            other=like.character, priority=base)
    lens = max(LENSES, key=lambda k: unsure(prof, k) * LENS_WEIGHT.get(k, 1.0))
    scene = max(p.scenes, key=lambda s: s.get("date") or "", default=None)
    lead = f"Thinking of when {name} {scene['text']}: " if scene and scene.get("text") else ""
    text = lead + TAP[lens].format(name=name, story=title)
    return Question(p, "quick_tap", lens, story, text, _options(lens), bool(lead), scene=scene, priority=base * unsure(prof, lens) * LENS_WEIGHT.get(lens, 1.0))


def plan(people: list[Person], today: str, memory: dict[str, Any], week_compare_done: bool, budget: int = PER_DAY) -> list[Question]:
    """Today's questions for one workspace: the most useful ones, one per person, within the budget."""
    qs = [q for q in (choose(p, today, memory, week_compare_done, people) for p in people) if q]
    qs.sort(key=lambda q: q.priority, reverse=True)
    out: list[Question] = []
    compare_used = week_compare_done
    for q in qs:
        if q.method == "compare":
            if compare_used:
                continue
            compare_used = True
        out.append(q)
        if len(out) >= budget:
            break
    return out


# --- the sketch: the profile in three lines, written by code -----------------------------------------------------------


def sketch(name: str, profile: dict[str, Any] | None) -> str:
    """What the profile says, in plain words, using only what is at least fairly sure. Never more than the evidence."""
    p = profile or {}
    lenses = p.get("lenses") or {}
    sure = {k: v for k, v in lenses.items() if v.get("confidence") in ("high", "medium") and not v.get("conflict")}
    first = [phrase(k, sure[k]["label"]) for k in ("under_pressure", "talks", "wants", "trust", "decides") if k in sure]
    if "wants" in sure and sure["wants"].get("also"):
        first[first.index(phrase("wants", sure["wants"]["label"]))] += f" and {phrase('wants', sure['wants']['also'])}"
    lines = [f"{name} " + ", ".join(first[:3]) + "." if first else f"Not much is known about {name} yet."]
    tr = p.get("trackRecord") or {}
    rates = tr.get("answerRate") or {}
    if rates:
        best = max(rates.items(), key=lambda kv: kv[1])
        reach = f"Reach them by {best[0]} (answers {round(best[1] * 100)}%)"
        if tr.get("bestHours"):
            reach += f", best {' or '.join(tr['bestHours'])}"
        lines.append(reach + ".")
    if tr.get("promises"):
        pr = tr["promises"]
        lines.append(f"Kept {pr['kept']} of {pr['of']} promised dates" + (f", about {pr['avgDaysLate']} days late otherwise" if pr.get("avgDaysLate") else "") + ".")
    if "moved_by" in sure:
        lines.append(f"Moves for: {phrase('moved_by', sure['moved_by']['label']).replace('moves for ', '').replace('moves at ', '')}.")
    if p.get("conflicts"):
        lines.append("Unsure about: " + ", ".join(CODEBOOK.get(k, {}).get("asks", k).lower().rstrip("?") for k in p["conflicts"][:2]) + ".")
    return " ".join(lines)
