"""Gauging a person: the evidence about them, and the profile code makes of it.

The model never decides what someone is like. Small jobs label one scene at a time against a written codebook (agent service), and the
person answers short questions; each label is a piece of **evidence** here, with where it came from. A **profile** is plain arithmetic
over the evidence: weighed by how good it is, faded with time (half-life six months, so people may change), with a confidence, and with
conflicts shown rather than averaged away. The **track record** is counted from the call log and never labelled.

Evidence keeps its story's workspace (its quote is that workspace's); the profile is labels and counts only, so it can travel with the
person into every workspace they are part of.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any, Literal

from app.domain.common import Entity, ISODate, Workspace
from app.domain.work import FollowUp

#: The lenses a scene or an answer can be labelled on. A custom lens (from "compare two") is `custom:<lens id>`.
LENSES = ("talks", "decides", "wants", "under_pressure", "trust", "moved_by")
#: Lenses where people are usually two things at once; their profile keeps the top two.
MULTI = {"wants", "moved_by"}
Source = Literal["scene", "answer", "prediction"]
Strength = Literal["clear", "weak"]
Method = Literal["scene", "quick_tap", "debrief", "moment", "what_would", "why", "compare", "check", "sheet", "you"]
PredictedBy = Literal["you", "model"]
Verdict = Literal["pending", "right", "wrong"]

HALF_LIFE_DAYS = 182
#: How much each kind of evidence counts. A prediction counts nothing until reality arrives (then the real reaction is evidence).
WEIGHTS = {"scene_clear": 1.0, "scene_weak": 0.5, "answer_specific": 0.9, "answer_general": 0.6, "check": 1.0, "prediction": 0.0}


class Evidence(Entity):
    character_id: str
    #: The story it is about, if any. Its workspace is the story's (or, without a story, the character's home).
    story_id: str | None = None
    workspace: Workspace = "personal"
    lens: str
    label: str
    source: Source = "scene"
    method: Method = "scene"
    strength: Strength = "clear"
    #: An answer about one particular moment (counts more) rather than a general view.
    specific: bool = False
    date: ISODate
    quote: str | None = None
    scene_id: str | None = None
    #: For predictions: who made it, and how it turned out.
    predicted_by: PredictedBy | None = None
    verdict: Verdict | None = None
    #: The question or news a prediction was about.
    about: str | None = None


class Lens(Entity):
    """A lens of the person's own, from comparing two people: "overpromises ↔ underpromises". Labels on it are "a", "b" or "middle"."""

    name: str
    pole_a: str
    pole_b: str


def kind_of(e: Evidence) -> str:
    if e.source == "prediction":
        return "prediction"
    if e.method == "check":
        return "check"
    if e.source == "scene":
        return "scene_clear" if e.strength == "clear" else "scene_weak"
    return "answer_specific" if e.specific else "answer_general"


def weight(e: Evidence, today: str) -> float:
    base = WEIGHTS[kind_of(e)]
    try:
        age = max(0, (date.fromisoformat(today) - date.fromisoformat(e.date)).days)
    except ValueError:
        age = 0
    return base * 0.5 ** (age / HALF_LIFE_DAYS)


def confidence(total: float, share: float) -> str:
    if total >= 3 and share >= 0.7:
        return "high"
    if total >= 1.5 and share >= 0.55:
        return "medium"
    return "low"


def lens_view(items: list[Evidence], today: str, multi: bool = False) -> dict[str, Any] | None:
    """One lens of a profile: the leading label(s), how sure, and a conflict when two labels each hold 30%+ of enough evidence."""
    by: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    for e in items:
        w = weight(e, today)
        if w <= 0:
            continue
        by[e.label] += w
        counts[e.label] += 1
    total = sum(by.values())
    if total <= 0:
        return None
    ranked = sorted(by.items(), key=lambda kv: kv[1], reverse=True)
    top, top_w = ranked[0]
    share = top_w / total
    view: dict[str, Any] = {"label": top, "share": round(share, 2), "weight": round(total, 2), "confidence": confidence(total, share),
                            "evidence": sum(counts.values()), "labels": {k: round(v, 2) for k, v in ranked}}
    if multi and len(ranked) > 1 and ranked[1][1] / total >= 0.25:
        view["also"] = ranked[1][0]
    strong = [k for k, v in ranked if v / total >= 0.3]
    if total >= 2.5 and len(strong) >= 2:
        view["conflict"] = strong[:2]
        view["confidence"] = "low"
    return view


# --- the track record --------------------------------------------------------------------------------------------


def track_record(calls: list[FollowUp]) -> dict[str, Any] | None:
    """From every chase of this person: how often each way of reaching them works, the hours they answer, and whether their promised
    dates hold. Counted, never labelled."""
    attempts = [(f, a) for f in calls for a in f.attempts or []]
    if not attempts:
        return None
    per: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    hours: dict[int, int] = defaultdict(int)
    for f, a in attempts:
        ch = a.channel or f.channel
        per[ch][1] += 1
        if a.result == "reached":
            per[ch][0] += 1
            hours[a.minute // 60] += 1
    promises, kept, late = 0, 0, []
    for f in calls:
        tries = sorted(f.attempts or [], key=lambda a: (a.date, a.minute))
        for i, a in enumerate(tries):
            if not a.promised_for:
                continue
            promises += 1
            done = next((b.date for b in tries[i + 1:] if b.result == "reached"), None)
            if done is None and f.outcome.status == "done" and f.outcome.recorded_at:
                done = f.outcome.recorded_at[:10]
            if done and done <= a.promised_for:
                kept += 1
            elif done:
                late.append((date.fromisoformat(done) - date.fromisoformat(a.promised_for)).days)
    best = sorted(hours.items(), key=lambda kv: (-kv[1], kv[0]))[:2]
    out: dict[str, Any] = {
        "tries": len(attempts),
        "answerRate": {ch: round(r / n, 2) for ch, (r, n) in per.items() if n},
        "bestHours": [f"{h:02d}:00–{h + 1:02d}:00" for h, _ in sorted(best)],
    }
    if promises:
        out["promises"] = {"kept": kept, "of": promises, **({"avgDaysLate": round(sum(late) / len(late), 1)} if late else {})}
    return out


# --- the profile -------------------------------------------------------------------------------------------------


def calibration(evidence: list[Evidence]) -> dict[str, Any]:
    """How the reads of this person have turned out: the person's own and the model's."""
    out = {}
    for who in ("you", "model"):
        settled = [e for e in evidence if e.source == "prediction" and e.predicted_by == who and e.verdict in ("right", "wrong")]
        if settled:
            out[who] = {"right": sum(1 for e in settled if e.verdict == "right"), "of": len(settled)}
    return out


def profile_of(evidence: list[Evidence], calls: list[FollowUp], lenses: list[Lens], today: str) -> dict[str, Any]:
    by_lens: dict[str, list[Evidence]] = defaultdict(list)
    for e in evidence:
        if e.source != "prediction":
            by_lens[e.lens].append(e)
    views: dict[str, Any] = {}
    for lens in LENSES:
        v = lens_view(by_lens.get(lens, []), today, lens in MULTI)
        if v:
            views[lens] = v
    custom = []
    for lens in lenses:
        v = lens_view(by_lens.get(f"custom:{lens.id}", []), today)
        if v:
            custom.append({"id": lens.id, "name": lens.name, "poleA": lens.pole_a, "poleB": lens.pole_b, **v})
    out: dict[str, Any] = {"lenses": views, "asOf": today}
    if custom:
        out["custom"] = custom
    if (tr := track_record(calls)) is not None:
        out["trackRecord"] = tr
    if (cal := calibration(evidence)):
        out["reads"] = cal
    out["conflicts"] = [k for k, v in views.items() if v.get("conflict")]
    return out

