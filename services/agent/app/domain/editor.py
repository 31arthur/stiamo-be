"""The story editor's judgement, as plain code: how quiet a story is against its own pace, which step of the ladder is due, which question
to ask, and which story most deserves the next hour. Pure: no model, no network, the same answer every time whatever model is running.

The ladder, in multiples of a story's pace (brisk 7 days, steady 14, slow 30) since its last chapter:

  1× a nudge with the story so far and one question     2× three ways the next chapter could start (each books a step)
  3× a harder question: what went wrong, in a month?     4× is this still the ending you want?

The clock pauses while a story is waiting on someone until a date, and starts again on that date. A resting or ended story never asks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal

PACE_DAYS: dict[str, int] = {"brisk": 7, "steady": 14, "slow": 30}
State = Literal["moving", "waiting", "quiet", "resting", "ended"]
Rung = Literal[0, 1, 2, 3, 4]
#: How far into the future a deadline makes a story "soon".
SOON_DAYS = 7


def _d(s: str | None) -> date | None:
    try:
        return date.fromisoformat(str(s)[:10]) if s else None
    except ValueError:
        return None


@dataclass(frozen=True)
class Momentum:
    state: State
    #: Days since the story last moved (its newest chapter, its start, or the end of a wait).
    quiet_days: int
    pace_days: int
    #: 0 when on pace; 1–4 the ladder step it has reached.
    rung: int
    since: str | None


def momentum(story: dict[str, Any], chapters: list[dict[str, Any]], today: str) -> Momentum:
    pace = PACE_DAYS.get(story.get("pacing") or "steady", 14)
    state = story.get("state") or "writing"
    if state in ("resting", "ended"):
        return Momentum(state, 0, pace, 0, None)  # type: ignore[arg-type]
    now = _d(today) or date.today()
    marks = [d for d in (_d(c.get("date")) for c in chapters) if d] + [d for d in [_d(story.get("createdAt"))] if d]
    since = max(marks) if marks else now
    waiting = story.get("waitingOn") or {}
    until = _d(waiting.get("until")) if waiting else None
    if waiting and (until is None or until > now):
        # Waiting on someone: no clock while the wait is open-ended or not due. (An open-ended wait still counts once it's very long.)
        if until is not None or (now - since).days < 4 * pace:
            return Momentum("waiting", (now - since).days, pace, 0, since.isoformat())
    if until is not None and until <= now:
        since = max(since, until)
    quiet = max(0, (now - since).days)
    rung = min(4, quiet // pace)
    return Momentum("quiet" if rung else "moving", quiet, pace, rung, since.isoformat())


def step_due(r: dict[str, Any]) -> str | None:
    return (r.get("due") or {}).get("date") if isinstance(r.get("due"), dict) else r.get("dueDate")


def push_score(story: dict[str, Any], m: Momentum, open_steps: list[dict[str, Any]], today: str) -> float:
    """chance × stakes × ready × soon × quiet: what a story is worth, whether a step can be taken now, whether a date is close, and whether
    it is slipping. A resting or ended story scores nothing; one with no chance written counts as even odds."""
    if m.state in ("resting", "ended"):
        return 0.0
    chance = (story.get("chance") if isinstance(story.get("chance"), int) else 50) / 100
    stakes = story.get("stakes") if isinstance(story.get("stakes"), int) else 3
    ready = 0.2 if m.state == "waiting" else 1.0
    now = _d(today) or date.today()
    horizon = now + timedelta(days=SOON_DAYS)
    dates = [_d(c.get("targetDate")) for c in story.get("checkpoints") or [] if c.get("status", "ahead") == "ahead"] + [_d(step_due(s)) for s in open_steps]
    soon = 1.5 if any(d and now <= d <= horizon for d in dates) else 1.0
    slipping = 1.3 if m.rung >= 1 else 1.0
    return round(chance * stakes * ready * soon * slipping, 3)


def why(story: dict[str, Any], m: Momentum, open_steps: list[dict[str, Any]]) -> str:
    """The score in words, for the planner's "push this" and the writers' room."""
    bits = [(f"{story['chance']}% chance" if isinstance(story.get("chance"), int) else "chance not set") + f" · stakes {story.get('stakes', 3)}"]
    if m.state == "waiting":
        bits.append("waiting")
    elif open_steps:
        bits.append(f"{len(open_steps)} step{'s' if len(open_steps) != 1 else ''} ready")
    if m.rung:
        bits.append(f"quiet {m.quiet_days} days ({m.rung}× its pace)")
    return " · ".join(bits)


# --- which step of the ladder, and when -----------------------------------------------------------------------------------------


def rung_to_send(m: Momentum, memory: dict[str, Any], today: str) -> int:
    """The ladder step to send now, or 0. Each step is sent once as the quiet passes it; a new chapter (rung back to 0) starts the ladder
    again; "not now" snoozes a story for its own pace."""
    if m.rung == 0:
        return 0
    snoozed = _d(memory.get("snoozed"))
    if snoozed and snoozed > (_d(today) or date.today()):
        return 0
    if int(memory.get("rung") or 0) >= m.rung and memory.get("since") == m.since:
        return 0
    return m.rung


def remember(memory: dict[str, Any], m: Momentum, rung: int, today: str) -> dict[str, Any]:
    return {**memory, "rung": rung, "since": m.since, "on": today}


def reset_if_moved(memory: dict[str, Any], m: Momentum) -> dict[str, Any]:
    """A story that moved (a new chapter, a wait ended) starts its ladder again."""
    return {} if memory and memory.get("since") != m.since and m.rung == 0 else memory


# --- the question library ---------------------------------------------------------------------------------------------------------


def first_sentence(text: str, limit: int = 160) -> str:
    t = " ".join((text or "").split())
    m = re.match(r"(.+?[.!?])(\s|$)", t)
    s = m.group(1) if m else t
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def here_of(story: dict[str, Any]) -> dict[str, Any] | None:
    return next((c for c in story.get("checkpoints") or [] if c.get("status", "ahead") == "ahead"), None)


def previously(story: dict[str, Any], chapters: list[dict[str, Any]]) -> str:
    """"Previously…": the latest chapter's day and its first line, where the story stands, and what it waits on. Written by code from the person's
    own words, so it never says anything that isn't there."""
    last = max(chapters, key=lambda c: (c.get("date") or "", c.get("number") or 0)) if chapters else None
    parts = []
    if last:
        d = _d(last.get("date"))
        when = f"{d.day} {d.strftime('%b')}" if d else str(last.get("date"))
        parts.append(f"{when}: “{last.get('title') or first_sentence(last.get('prose') or '', 80)}”")
    here = here_of(story)
    if here:
        parts.append(f"now at “{here.get('title')}”")
    if story.get("waitingOn"):
        parts.append(f"waiting on {story['waitingOn'].get('on')}")
    return " · ".join(parts) if parts else "nothing written yet"


def due_hunch(chapters: list[dict[str, Any]], pace: int, today: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """The oldest hunch whose moment has come (its own date, or a pace after it was planted) and that hasn't been settled."""
    now = _d(today) or date.today()
    for c in sorted(chapters, key=lambda c: c.get("date") or ""):
        for h in c.get("hunches") or []:
            if h.get("cameTrue") is not None:
                continue
            when = _d(h.get("checkOn")) or ((_d(c.get("date")) or now) + timedelta(days=pace))
            if when <= now:
                return c, h
    return None


def oldest_step(open_steps: list[dict[str, Any]]) -> dict[str, Any] | None:
    return min(open_steps, key=lambda s: (step_due(s) or "9999", s.get("createdAt") or "")) if open_steps else None


#: Rung 3: the pre-mortem. A fixed set of answers, so a tap is enough and no model is needed to read it.
PREMORTEM = (("they", "They didn't deliver"), ("me", "I didn't push"), ("no", "The answer was no"), ("else", "Something else"))
#: Rung 4: the ending.
ENDING = (("keep", "Keep it"), ("rest", "Rest this story"), ("end", "The End"))


def fallback_openings(story: dict[str, Any], cast: list[str], today: str) -> list[dict[str, Any]]:
    """Three ways forward that need no model: ask the last person in it, list what the next checkpoint needs, set a day to look again."""
    tomorrow = ((_d(today) or date.today()) + timedelta(days=1)).isoformat()
    later = ((_d(today) or date.today()) + timedelta(days=PACE_DAYS.get(story.get("pacing") or "steady", 14) // 2 or 1)).isoformat()
    here = here_of(story)
    out = []
    if cast:
        out.append({"opening": f"You ask {cast[0][:1].lower() + cast[0][1:] if cast[0].lower().startswith('the ') else cast[0]} where things stand.", "step": {"kind": "followUp", "title": f"Ask {cast[0]} where things stand", "due": tomorrow, "contact": cast[0], "channel": "call"}})
    if here:
        out.append({"opening": f"You write down what “{here.get('title')}” still needs.", "step": {"kind": "task", "title": f"List what “{here.get('title')}” needs", "due": tomorrow}})
    out.append({"opening": "You set a day to come back to it with fresh eyes.", "step": {"kind": "task", "title": f"Look at “{story.get('title')}” again", "due": later}})
    if len(out) < 3:
        out.insert(0, {"opening": "You take the one step you've been putting off.", "step": {"kind": "task", "title": f"Next step on “{story.get('title')}”", "due": tomorrow}})
    return out[:3]
