"""Gauging a person, end to end: new scenes are labelled each evening (router, then one coder per lens), "how will they take it?" is
answered from the record (three votes, a playbook in code, a model only for the words), and people are picked out of a news item or a
mail. Every model step is a small job on its own model (see domain/insight_jobs.py); everything that judges is code.
"""

from __future__ import annotations

import logging
import uuid
from collections import Counter
from collections.abc import Callable
from typing import Any

from app.application.aijobs import JobRunner
from app.application.ports import DataGateway, DecisionClient
from app.domain.insight_jobs import CONSTRUCT, LENS_CODERS, LENS_ROUTER, PEOPLE_IN_TEXT, REACTION, WORDING
from app.domain.interviewer import sketch
from app.domain.lenses import REACTION_WORDS, REACTIONS, moves_for, phrase
from app.domain.story_jobs import same_name

log = logging.getLogger(__name__)
Runners = Callable[[str], JobRunner | None]
#: How many scenes one evening labels (each takes a router call and one per lens it touches).
CODE_PER_RUN = 25
VOTES = 3


def evidence_id(scene_id: str, lens: str) -> str:
    """The same scene and lens always give the same id, so labelling again never doubles evidence."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"stiamo:evidence:{scene_id}:{lens}"))


async def code_scenes(data: DataGateway, runners: Runners, limit: int = CODE_PER_RUN) -> dict[str, Any]:
    """Labels the scenes not yet read: which lenses each speaks to, then each of those lenses. A scene is marked read only when the
    router answered, so one a model failed on is tried again next evening."""
    waiting = [s for s in await data.records("scenes") if not s.get("coded")][:limit]
    if not waiting:
        return {"coded": 0, "evidence": 0}
    router = runners(LENS_ROUTER.name)
    if router is None:
        return {"coded": 0, "evidence": 0, "waiting": len(waiting), "reason": "no model is chosen for the lens jobs"}
    names = {c["id"]: c.get("name", "") for c in await data.records("characters")}
    titles = {s["id"]: s.get("title", "") for s in await data.records("stories")}
    coded = made = 0
    for sc in waiting:
        given = {"name": names.get(sc.get("characterId"), "them"), "story": titles.get(sc.get("storyId"), ""), "scene": sc.get("text") or "", "quote": sc.get("quote")}
        routed = await router.run(LENS_ROUTER, given)
        if not routed.ok:
            continue
        for lens in routed.output["lenses"]:
            coder = runners(LENS_CODERS[lens].name)
            if coder is None:
                continue
            got = await coder.run(LENS_CODERS[lens], given)
            if got.ok and got.output.get("label"):
                await data.save_record("evidence", {
                    "id": evidence_id(sc["id"], lens), "characterId": sc["characterId"], "storyId": sc.get("storyId"), "lens": lens,
                    "label": got.output["label"], "source": "scene", "method": "scene", "strength": got.output.get("strength") or "clear",
                    "date": sc.get("date"), "quote": got.output.get("quote"), "sceneId": sc["id"]})
                made += 1
        await data.save_record("scenes", {**sc, "coded": True})
        coded += 1
    return {"coded": coded, "evidence": made, "waiting": len(waiting) - coded}


# --- finding a person -------------------------------------------------------------------------------------------------


async def find_person(data: DataGateway, key: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """A character by id, name, or what a story calls them. Returns (the one, or None; the candidates when more than one fits)."""
    characters = await data.records("characters")
    hit = next((c for c in characters if c.get("id") == key), None)
    if hit:
        return hit, []
    parts = await data.records("parts")
    found = [c for c in characters if same_name(key, c.get("name")) or any(
        same_name(key, k) for p in parts if p.get("characterId") == c["id"] for k in p.get("knownAs") or [])]
    return (found[0], []) if len(found) == 1 else (None, found)


def profile_lines(profile: dict[str, Any] | None) -> str:
    """The profile as the reaction job reads it: lens, label, how sure, how much evidence."""
    lines = []
    for lens, v in ((profile or {}).get("lenses") or {}).items():
        extra = f" and {phrase(lens, v['also'])}" if v.get("also") else ""
        conflict = f" (in conflict: {' vs '.join(v['conflict'])})" if v.get("conflict") else ""
        lines.append(f"{lens.replace('_', ' ')}: {phrase(lens, v['label'])}{extra} ({v.get('confidence')}, {v.get('evidence')} pieces){conflict}")
    return "\n".join(lines)


# --- how will they take it -------------------------------------------------------------------------------------------------


async def predict(data: DataGateway, runners: Runners, decisions: DecisionClient | None, decision_model: str | None,
                  character: dict[str, Any], news: str, story: dict[str, Any] | None, today: str) -> dict[str, Any]:
    profile = character.get("profile") or {}
    name = character.get("name") or "them"
    evidence = [e for e in await data.records("evidence") if e.get("characterId") == character["id"] and e.get("source") != "prediction"]
    relevant = sorted((e for e in evidence if e.get("lens") in ("under_pressure", "wants", "moved_by") and e.get("quote")), key=lambda e: e.get("date") or "", reverse=True)
    scenes = [f"{e.get('date')}: {e['quote']}" for e in relevant[:5]]
    if len(scenes) < 3:
        own = sorted((s for s in await data.records("scenes") if s.get("characterId") == character["id"]), key=lambda s: s.get("date") or "", reverse=True)
        scenes += [f"{s.get('date')}: {s.get('text')}" for s in own[: 5 - len(scenes)]]
    given = {"name": name, "profile": profile_lines(profile), "scenes": scenes, "news": news}

    votes: list[str] = []
    probabilities: dict[str, float] | None = None
    if decisions is not None and decision_model:
        try:
            answer = (await decisions.decide(model=decision_model, state=REACTION.render(given), questions={"reaction": {
                "type": "choice", "instructions": "How will this person most likely take the news?",
                "criteria": {r: REACTION_WORDS[r] for r in REACTIONS}}}))["answers"]["reaction"]
            probabilities = {k: round(float(v), 2) for k, v in (answer.get("probabilities") or {}).items() if k in REACTIONS}
            if answer.get("choice") in REACTIONS:
                votes = [answer["choice"]]
        except Exception:  # noqa: BLE001  the decision model is a better way, not the only one
            log.info("the decision model couldn't predict; asking the reaction job")
    if not votes:
        runner = runners(REACTION.name)
        if runner is None:
            return {"error": "No model is chosen for predictions."}
        for _ in range(VOTES):
            r = await runner.run(REACTION, given)
            if r.ok and r.output.get("reaction"):
                votes.append(r.output["reaction"])
    if not votes:
        return {"error": "The prediction didn't come back usable. Try again, or ask a different model."}
    reaction, n = Counter(votes).most_common(1)[0]
    sure = probabilities.get(reaction) if probabilities else n / len(votes)
    lenses = profile.get("lenses") or {}
    wants = (lenses.get("wants") or {}).get("label")
    lever = (lenses.get("moved_by") or {}).get("label")
    moves = moves_for(reaction, wants, lever)
    rates = (profile.get("trackRecord") or {}).get("answerRate") or {}
    channel = max(rates.items(), key=lambda kv: kv[1])[0] if rates else "call"
    message = None
    writer = runners(WORDING.name)
    if writer is not None:
        w = await writer.run(WORDING, {"name": name, "channel": channel, "moves": moves, "news": news})
        message = w.output.get("message") if w.ok else None
    pid = str(uuid.uuid4())
    await data.save_record("evidence", {"id": pid, "characterId": character["id"], "storyId": (story or {}).get("id"), "lens": "under_pressure",
                                        "label": reaction if reaction != "takes_it_well" else "solves", "source": "prediction", "method": "what_would",
                                        "predictedBy": "model", "verdict": "pending", "date": today, "about": news[:500]})
    out: dict[str, Any] = {"person": name, "reaction": REACTION_WORDS[reaction], "label": reaction, "sure": round(sure, 2),
                           "votes": dict(Counter(votes)), "moves": moves, "channel": channel, "message": message, "predictionId": pid,
                           "sketch": sketch(name, profile), "basedOn": len(evidence)}
    if (profile.get("trackRecord") or {}).get("bestHours"):
        out["bestHours"] = profile["trackRecord"]["bestHours"]
    if probabilities:
        out["probabilities"] = probabilities
    if (profile.get("reads") or {}).get("model"):
        r = profile["reads"]["model"]
        out["myRecord"] = f"{r['right']} of {r['of']} earlier predictions about {name} were right"
    return out


async def settle(data: DataGateway, character: dict[str, Any], reaction: str, today: str, story_id: str | None = None,
                 prediction: dict[str, Any] | None = None) -> dict[str, Any]:
    """What really happened: the latest pending prediction about them is scored, and the reaction becomes evidence of its own."""
    pending = [e for e in await data.records("evidence") if e.get("characterId") == character["id"] and e.get("source") == "prediction" and e.get("verdict") == "pending"]
    target = prediction or (max(pending, key=lambda e: e.get("date") or "") if pending else None)
    said = None
    if target is not None:
        guess = target.get("label")
        right = guess == reaction or (reaction == "takes_it_well" and guess == "solves")
        await data.save_record("evidence", {**target, "verdict": "right" if right else "wrong"})
        said = ("right" if right else "wrong", target.get("predictedBy"), guess)
    if reaction in REACTIONS and reaction != "takes_it_well":
        await data.save_record("evidence", {"id": str(uuid.uuid4()), "characterId": character["id"], "storyId": story_id or (target or {}).get("storyId"),
                                            "lens": "under_pressure", "label": reaction, "source": "answer", "method": "debrief", "specific": True,
                                            "date": today, "about": (target or {}).get("about")})
    return {"settled": said}


# --- people in a text, and comparing two ---------------------------------------------------------------------------------


async def people_in(runners: Runners, text: str) -> dict[str, Any]:
    runner = runners(PEOPLE_IN_TEXT.name)
    if runner is None:
        return {"error": "No model is chosen for reading people out of a text."}
    r = await runner.run(PEOPLE_IN_TEXT, {"text": text})
    return {"people": r.output.get("people") or [], "dropped": r.dropped, "ok": r.ok}


async def construct(runners: Runners, first: str, second: str, answer: str) -> dict[str, Any] | None:
    runner = runners(CONSTRUCT.name)
    if runner is None:
        return None
    r = await runner.run(CONSTRUCT, {"first": first, "second": second, "answer": answer})
    return r.output if r.ok else None
