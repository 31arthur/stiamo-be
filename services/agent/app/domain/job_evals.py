"""Scoring an AI job's answers against answers the person agreed with, so a model is switched in only when it does the job. Pure.

A test case is {"input": {...the job's input...}, "expect": {...}}. `expect` names only what matters for that case, so a case can say
"the beat is setback and there is one twist" without fixing every word. Each named field is a point; a model's agreement is the share
of points it gets across the whole set. Every quote a model gives is also checked against the text: a model that invents fails here
even when its labels happen to match.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: The share of points a model needs before a job is moved onto it.
PASS_MARK = 0.85


@dataclass
class CaseScore:
    name: str
    points: int
    got: int
    misses: list[str] = field(default_factory=list)


@dataclass
class SetScore:
    job: str
    model: str
    cases: list[CaseScore]
    fell_back: int = 0

    @property
    def agreement(self) -> float:
        total = sum(c.points for c in self.cases)
        return sum(c.got for c in self.cases) / total if total else 0.0

    @property
    def passed(self) -> bool:
        return self.agreement >= PASS_MARK


def _count(out: dict[str, Any], key: str) -> int:
    return len(out.get(key) or [])


def score_margin_notes(name: str, out: dict[str, Any], expect: dict[str, Any]) -> CaseScore:
    points, got, misses = 0, 0, []

    def check(ok: bool, what: str) -> None:
        nonlocal points, got
        points += 1
        if ok:
            got += 1
        else:
            misses.append(what)

    if "beat" in expect:
        check(out.get("beat") == expect["beat"], f"beat {out.get('beat')} ≠ {expect['beat']}")
    if "checkpoints" in expect:
        moved = {c["id"]: c["status"] for c in out.get("checkpoints") or []}
        check(moved == expect["checkpoints"], f"checkpoints {moved} ≠ {expect['checkpoints']}")
    for key in ("twists", "steps", "hunches", "people"):
        if key in expect:
            want = expect[key]
            have = _count(out, key)
            ok = have == want if isinstance(want, int) else want[0] <= have <= want[1]
            check(ok, f"{key}: {have}, expected {want}")
    if "step_kinds" in expect:
        kinds = sorted(s["kind"] for s in out.get("steps") or [])
        check(kinds == sorted(expect["step_kinds"]), f"step kinds {kinds} ≠ {sorted(expect['step_kinds'])}")
    if "dues" in expect:
        dues = sorted(d for d in (s.get("due") for s in out.get("steps") or []) if d)
        check(dues == sorted(expect["dues"]), f"due dates {dues} ≠ {sorted(expect['dues'])}")
    if "waiting" in expect:
        check(bool(out.get("waiting_on")) == expect["waiting"], f"waiting {bool(out.get('waiting_on'))} ≠ {expect['waiting']}")
    if "cliffhanger" in expect:
        check(bool(out.get("cliffhanger")) == expect["cliffhanger"], f"cliffhanger {bool(out.get('cliffhanger'))} ≠ {expect['cliffhanger']}")
    return CaseScore(name, points, got, misses)


def score_find_story(name: str, out: dict[str, Any], expect: dict[str, Any]) -> CaseScore:
    want_story, want_unsure = expect.get("story"), sorted(expect.get("unsure_between") or [])
    ok = out.get("story") == want_story and (want_story is not None or sorted(out.get("unsure_between") or []) == want_unsure)
    return CaseScore(name, 1, int(ok), [] if ok else [f"story {out.get('story')} / unsure {out.get('unsure_between')} ≠ {want_story} / {want_unsure}"])


def score_next_openings(name: str, out: dict[str, Any], expect: dict[str, Any]) -> CaseScore:
    openings = out.get("openings") or []
    points, got, misses = 0, 0, []

    def check(ok: bool, what: str) -> None:
        nonlocal points, got
        points += 1
        got += int(ok)
        if not ok:
            misses.append(what)

    check(len(openings) == 3, f"{len(openings)} openings, expected 3")
    if expect.get("a_follow_up"):
        check(any(o["step"]["kind"] == "followUp" for o in openings), "no opening reaches out to anyone")
    if expect.get("mentions"):
        words = " ".join(f"{o['opening']} {o['step']['title']}" for o in openings).lower()
        hit = [w for w in expect["mentions"] if w.lower() in words]
        check(bool(hit), f"none of {expect['mentions']} came up")
    if "dues" in expect:
        dues = sorted(d for d in (o["step"].get("due") for o in openings) if d)
        check(all(d in expect["dues"] for d in dues), f"due dates {dues} not among {expect['dues']}")
    return CaseScore(name, points, got, misses)


def score_suggest_chance(name: str, out: dict[str, Any], expect: dict[str, Any]) -> CaseScore:
    move = int(out.get("chance", 0)) - int(expect["from"])
    direction = "up" if move > 0 else "down" if move < 0 else "same"
    misses = []
    ok_dir = direction == expect["direction"] or (expect["direction"] == "same" and abs(move) <= 2)
    if not ok_dir:
        misses.append(f"moved {move:+d}, expected {expect['direction']}")
    ok_size = abs(move) <= expect.get("most", 20)
    if not ok_size:
        misses.append(f"moved {move:+d}, at most {expect.get('most', 20)} expected")
    return CaseScore(name, 2, int(ok_dir) + int(ok_size), misses)


SCORERS = {"margin_notes": score_margin_notes, "find_story": score_find_story, "next_openings": score_next_openings, "suggest_chance": score_suggest_chance}
