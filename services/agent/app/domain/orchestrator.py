"""The assistant that picks the right agent: how a message is routed, and how the conversation is remembered across the agents it uses. Pure.

The assistant doesn't answer by itself. For each message it chooses one of the team's agents, hands it the message together with what the
conversation has established so far (a short transcript and the case or day it has been about), and returns that agent's answer. Because
every agent gets the same account of the conversation, the person is talking to one thing, whichever agent happens to answer.
"""

from __future__ import annotations

import re
from typing import Any

ORCHESTRATOR_ID = "assistant"
ORCHESTRATOR_NAME = "Assistant"
ORCHESTRATOR_DESCRIPTION = "Ask anything. It chooses the right agent for the job and keeps the thread of the conversation."

#: How many exchanges are told to the agent in full; older ones are only listed in one line each.
RECENT_TURNS = 6
DIGEST_TURNS = 20
CLIP_QUESTION = 400
CLIP_ANSWER = 700
CLIP_DIGEST = 90
#: A message this short is taken to carry on from the last answer.
FOLLOW_UP_WORDS = 4
CONTINUERS = ("and ", "also ", "then ", "what about", "how about", "yes", "no", "ok", "okay", "sure", "do it", "go ahead", "those", "that", "it ", "them", "move ", "change ", "make it", "instead", "why", "which one")

_CASE_REF = re.compile(r"\b([A-Z]{2,5}-\d{4}-\d{3})\b")
_STOP = frozenset("the a an and or of to for in on at is are was be it this that with my me i you your what who when how can do does about from by as".split())

#: What each agent is known for, in the words people use. An agent not listed here is still matched on its own description and suggestions.
KEYWORDS: dict[str, tuple[str, ...]] = {
    "daily-planner": ("plan", "schedule", "tomorrow", "today", "day", "free", "busy", "agenda", "arrange", "reschedule", "task", "add a task", "move", "morning", "afternoon", "evening"),
    "hearing-prep": ("prepare", "prep", "hearing", "last order", "carry", "bring", "documents", "bench", "court", "arguments", "brief"),
    "follow-up-chaser": ("call", "ring", "phone", "follow-up", "followup", "follow up", "chase", "callback", "call back", "message them", "who should i call", "no answer", "promised"),
    "deadline-scout": ("due", "deadline", "this week", "coming up", "attention", "inbox", "mail", "email", "upcoming", "missed", "overdue", "clash"),
    "cases": ("case", "cases", "find", "search", "document", "documents", "drive", "letter", "reference", "petition", "party", "client"),
}


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9\-]+", text.lower()) if w not in _STOP and len(w) > 2}


def is_follow_up(message: str) -> bool:
    """Whether a message goes on from the last answer rather than starting something new: short, or opening with a word that continues."""
    m = message.strip().lower()
    return len(m.split()) <= FOLLOW_UP_WORDS or m.startswith(CONTINUERS)


def route_by_rules(message: str, candidates: list[dict[str, Any]], last: str | None) -> tuple[str, str]:
    """Picks an agent without a model: by the words the message shares with each agent, falling back to the one that answered last, then to the
    first. Returns the agent and a short reason. Used when the decision model is off or unreachable."""
    if not candidates:
        raise ValueError("There is no agent to choose from.")
    ids = [c["id"] for c in candidates]
    if last in ids and is_follow_up(message):
        return last, "carrying on from the last answer"
    text = message.lower()
    mine = words(message)
    scored: list[tuple[float, str]] = []
    for c in candidates:
        listed = sum(1 for k in KEYWORDS.get(c["id"], ()) if k in text)
        own = len(mine & words(f"{c.get('description', '')} {' '.join(c.get('suggestions', []))}")) * 0.5
        scored.append((listed + own, c["id"]))
    best = max(s for s, _ in scored)
    if best > 0:
        top = [i for s, i in scored if s == best]
        pick = last if last in top else top[0]
        return pick, "matched the words of the request"
    return (last if last in ids else ids[0]), "nothing in particular, so the usual one"


def jev_question(candidates: list[dict[str, Any]], last: str | None) -> dict[str, Any]:
    """The question put to the decision model: which agent suits the message, given what was just discussed."""
    hint = f" If the message carries on from what was just discussed, prefer “{last}”, the agent that answered last." if last else ""
    return {
        "agent": {
            "type": "choice",
            "instructions": "Choose the agent best suited to handle the person's latest message, given the conversation so far. Each option is an agent and what it is for." + hint,
            "criteria": {c["id"]: f"{c['name']}: {c.get('description', '')}" for c in candidates},
        }
    }


def jev_state(message: str, preface: str) -> str:
    return (f"{preface}\n\n" if preface else "") + f"The person's latest message:\n{message.strip()}"


def _clip(text: str, n: int) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


def gist_of(found: list[dict[str, Any]]) -> str:
    """What the cards in an answer said, in a line, so the next agent knows what was shown even though it can't see the cards."""
    bits: list[str] = []
    for f in found:
        k = f.get("kind")
        if k == "briefing":
            titles = [i.get("title") for sec in f.get("sections", []) for i in sec.get("items", []) if i.get("title")]
            bits.append(f"Laid out {f.get('date')}: {f.get('headline', '')}" + (f" Items: {'; '.join(titles[:8])}." if titles else ""))
        elif k == "schedule":
            bits.append(f"Proposed times for {f.get('date')}: " + "; ".join(f"{b.get('from')} {b.get('title')}" for b in f.get("blocks", [])[:8]))
        elif k == "proposal":
            bits.append(f"Proposed: {f.get('title')}")
        elif k == "case":
            bits.append(f"Found case {f.get('reference')} {f.get('title', '')}".strip())
        elif k == "document":
            bits.append(f"Found document {f.get('name')}")
    return _clip(" ".join(bits), 500)


def update_focus(focus: dict[str, Any], message: str, found: list[dict[str, Any]]) -> dict[str, Any]:
    """What the conversation has been about, kept current: the case (named in the message, or the one the last answer found) and the day."""
    out = dict(focus)
    ref = _CASE_REF.search(message)
    cases = [f for f in found if f.get("kind") == "case"]
    if ref:
        out["case"] = {"ref": ref.group(1), "title": next((c.get("title", "") for c in cases if c.get("reference") == ref.group(1)), (out.get("case") or {}).get("title", ""))}
    elif len(cases) == 1:
        out["case"] = {"ref": cases[0].get("reference", ""), "title": cases[0].get("title", "")}
    day = next((f.get("date") for f in reversed(found) if f.get("kind") in ("briefing", "schedule") and f.get("date")), None)
    if day:
        out["date"] = day
    return out


def update_digest(digest: list[str], turn: dict[str, Any]) -> list[str]:
    """One line per exchange, newest last, for what is too old to repeat in full."""
    line = f"{_clip(turn.get('question', ''), CLIP_DIGEST)} → {turn.get('agentName') or 'assistant'}"
    return [*digest, line][-DIGEST_TURNS:]


def build_preface(turns: list[dict[str, Any]], context: dict[str, Any]) -> str:
    """What every agent is told before the message: who and what this conversation has been about, and the latest exchanges in the person's
    words and the agents'. Plain text, so any agent can read it, whichever agents answered before."""
    parts: list[str] = []
    focus = context.get("focus") or {}
    about = []
    if focus.get("case", {}).get("ref"):
        c = focus["case"]
        about.append(f"the case {c['ref']}" + (f" ({c['title']})" if c.get("title") else ""))
    if focus.get("date"):
        about.append(f"the day {focus['date']}")
    if about:
        parts.append("This conversation has been about " + " and ".join(about) + ". Unless the person says otherwise, a request that doesn't name another one means this.")
    recent = turns[-RECENT_TURNS:]
    older = (context.get("digest") or [])[: max(0, len(turns) - RECENT_TURNS)][-8:]
    if older:
        parts.append("Earlier in the conversation:\n" + "\n".join(f"- {d}" for d in older))
    if recent:
        lines = []
        for t in recent:
            lines.append(f"Person: {_clip(t.get('question', ''), CLIP_QUESTION)}")
            said = _clip(t.get("answer", ""), CLIP_ANSWER)
            if t.get("gist"):
                said = f"{said} [{t['gist']}]".strip()
            lines.append(f"{t.get('agentName') or 'Assistant'}: {said}")
        parts.append("The latest exchanges:\n" + "\n".join(lines))
    return "\n\n".join(parts)
