"""The insight pack: what is known about a person, how they'll take some news, and learning more about them (a note, a comparison,
the people in a news item or a mail). Notes, lenses and new people are proposed; predictions are kept so they can be scored later."""

from __future__ import annotations

import uuid
from typing import Any

from app.application.cast_insight import construct, find_person, people_in, predict, profile_lines, settle
from app.application.runner import Tool, ToolOutput
from app.application.toolpacks import PackContext, ToolPack
from app.application.toolpacks.common import text
from app.domain import proposals as p
from app.domain.interviewer import sketch
from app.domain.lenses import REACTION_WORDS, REACTIONS
from app.domain.llm import ToolSpec

S = {"type": "string"}


def _props(**fields: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": fields}


class InsightPack(ToolPack):
    name = "insight"

    async def tools(self, ctx: PackContext) -> list[Tool]:
        data = ctx.data
        runners = ctx.jobs or (lambda name: None)

        def safe(run):
            async def wrapped(a: dict[str, Any]) -> ToolOutput:
                try:
                    return await run(a)
                except p.Bad as exc:
                    return ToolOutput({"error": str(exc)})

            return wrapped

        async def person(a: dict[str, Any], key: str = "person") -> tuple[dict[str, Any] | None, ToolOutput | None]:
            c, many = await find_person(data, text(a.get(key)))
            if c is None:
                if many:
                    return None, ToolOutput({"ask": [{"id": x["id"], "name": x.get("name"), "organisation": x.get("organisation")} for x in many], "note": "More than one person fits. Ask which."})
                return None, ToolOutput({"error": f"Nobody in the cast is called “{text(a.get(key))}”. Add them with add_to_cast first."})
            return c, None

        async def story_for(c: dict[str, Any], key: str) -> dict[str, Any] | None:
            stories = await data.records("stories")
            if key:
                k = key.lower()
                return next((s for s in stories if s["id"] == key), None) or next((s for s in stories if k in str(s.get("title", "")).lower()), None)
            mine = {pt.get("storyId") for pt in await data.records("parts") if pt.get("characterId") == c["id"]}
            return max((s for s in stories if s["id"] in mine), key=lambda s: s.get("stakes") or 3, default=None)

        async def character_profile(a: dict[str, Any]) -> ToolOutput:
            c, err = await person(a)
            if err:
                return err
            prof = c.get("profile") or {}
            return ToolOutput({"name": c.get("name"), "sketch": sketch(c.get("name") or "", prof), "lenses": profile_lines(prof) or "nothing recorded yet",
                               "trackRecord": prof.get("trackRecord"), "reads": prof.get("reads"), "unsureAbout": prof.get("conflicts") or []})

        async def how_will_they_take_it(a: dict[str, Any]) -> ToolOutput:
            c, err = await person(a)
            if err:
                return err
            news = p.long_text(a.get("news"), "The news")
            if not news:
                raise p.Bad("Say what the news is.")
            out = await predict(data, runners, ctx.decisions, ctx.decision_model, c, news, await story_for(c, text(a.get("story"))), ctx.today)
            if "error" not in out:
                out["note"] = ("Say the likely reaction and how sure (votes or probability), the moves, then offer the message as a draft for them to "
                               "change. Say it's a prediction: when they tell you how it went, call how_they_took_it so it's scored.")
            return ToolOutput(out)

        async def how_they_took_it(a: dict[str, Any]) -> ToolOutput:
            c, err = await person(a)
            if err:
                return err
            reaction = p.pick(a.get("reaction"), REACTIONS, "The reaction")
            if reaction is None:
                raise p.Bad(f"Say how they took it: one of {', '.join(REACTIONS)}.")
            story = await story_for(c, text(a.get("story")))
            out = await settle(data, c, reaction, ctx.today, (story or {}).get("id"))
            said = out["settled"]
            if said is None:
                return ToolOutput({"noted": f"{c.get('name')} {REACTION_WORDS[reaction]}. Kept as evidence; there was no prediction to score."})
            verdict, who, guess = said
            return ToolOutput({"noted": f"{c.get('name')} {REACTION_WORDS[reaction]}.", "prediction": f"{'your' if who == 'you' else 'my'} guess “{REACTION_WORDS.get(guess or '', guess)}” was {verdict}"})

        async def note_about_person(a: dict[str, Any]) -> ToolOutput:
            if ctx.propose is None:
                return ToolOutput({"error": "Notes can only be proposed in a chat."})
            c, err = await person(a)
            if err:
                return err
            said = p.long_text(a.get("text"), "The note")
            if not said:
                raise p.Bad("Give the note in the person's own words.")
            story = await story_for(c, text(a.get("story")))
            if story is None:
                raise p.Bad(f"{c.get('name')} isn't in a story yet. Give their part in one with add_to_cast first.")
            sid = str(uuid.uuid4())
            record = {"id": sid, "characterId": c["id"], "storyId": story["id"], "date": ctx.today, "text": said[:300], "quote": said, "source": "you"}
            title = f"Note about {c.get('name')}"
            lines = [f"In “{story.get('title')}”: {said[:300]}", "Kept as a scene, in your words; it's read for what it says about them tonight."]
            pid = await ctx.propose("scene.create", title, lines, [{"table": "scenes", "id": sid, "create": True, "patch": record}])
            return ToolOutput({"proposed": True, "id": pid, "note": "Nothing is saved until the person approves. Say so."},
                              [{"kind": "proposal", "id": pid, "action": "scene.create", "title": title, "lines": lines}])

        async def compare_people(a: dict[str, Any]) -> ToolOutput:
            if ctx.propose is None:
                return ToolOutput({"error": "Comparisons can only be proposed in a chat."})
            first, err = await person(a, "first")
            if err:
                return err
            second, err = await person(a, "second")
            if err:
                return err
            answer = p.long_text(a.get("text"), "The answer")
            if not answer:
                raise p.Bad("Give the person's answer in their own words.")
            got = await construct(runners, first.get("name") or "", second.get("name") or "", answer)
            if not got:
                return ToolOutput({"error": "I couldn't take a clear difference from that. Ask them to say it as “X does …, Y does …”."})
            lid = str(uuid.uuid4())
            ops: list[dict[str, Any]] = [{"table": "lenses", "id": lid, "create": True, "patch": {"id": lid, "name": got["name"], "poleA": got["pole_a"], "poleB": got["pole_b"]}}]
            pole = {"a": got["pole_a"], "b": got["pole_b"], "middle": "in between"}
            lines = [f"A lens of your own: {got['name']}: {got['pole_a']} ↔ {got['pole_b']}"]
            for who, sits in ((first, got["first_sits"]), (second, got["second_sits"])):
                eid = str(uuid.uuid4())
                ops.append({"table": "evidence", "id": eid, "create": True, "patch": {
                    "id": eid, "characterId": who["id"], "lens": f"custom:{lid}", "label": sits, "source": "answer", "method": "compare",
                    "date": ctx.today, "quote": answer[:500]}})
                lines.append(f"{who.get('name')}: {pole[sits]}")
            pid = await ctx.propose("lens.create", f"Lens: {got['name']}", lines, ops)
            return ToolOutput({"proposed": True, "id": pid, "note": "Nothing is saved until the person approves. Say so."},
                              [{"kind": "proposal", "id": pid, "action": "lens.create", "title": f"Lens: {got['name']}", "lines": lines}])

        async def people_in_text(a: dict[str, Any]) -> ToolOutput:
            if ctx.propose is None:
                return ToolOutput({"error": "People can only be proposed in a chat."})
            said = text(a.get("text"))
            if not said:
                raise p.Bad("Give the text: the news item or the mail.")
            found = await people_in(runners, said)
            if found.get("error"):
                return ToolOutput(found)
            if not found["people"]:
                return ToolOutput({"none": True, "note": "Nobody is named in it."})
            story = None
            if text(a.get("story")):
                k = text(a.get("story")).lower()
                story = next((s for s in await data.records("stories") if s["id"] == text(a.get("story")) or k in str(s.get("title", "")).lower()), None)
            ops, lines = [], []
            for x in found["people"]:
                body = {"id": str(uuid.uuid4()), "name": x["name"], **({"phones": x["phones"]} if x["phones"] else {}), **({"emails": x["emails"]} if x["emails"] else {}),
                        **({"organisation": x["organisation"]} if x.get("organisation") else {}), **({"title": x["title"]} if x.get("title") else {})}
                if story:
                    body.update(storyId=story["id"], part=x.get("title") or "")
                ops.append({"cast": body})
                lines.append(f"{x['name']}" + (f", {x['title']}" if x.get("title") else "") + (f", {x['organisation']}" if x.get("organisation") else "")
                             + (f" · {', '.join(x['phones'])}" if x["phones"] else ""))
            if story:
                lines.append(f"Each joins “{story.get('title')}”. Someone already known by the same number is reused.")
            if found["dropped"]:
                lines.append(f"Left out {len(found['dropped'])} detail(s) that weren't in the text word for word.")
            title = f"Add {len(ops)} {'person' if len(ops) == 1 else 'people'} to your cast"
            pid = await ctx.propose("cast.join", title, lines, ops)
            return ToolOutput({"proposed": True, "id": pid, "note": "Nothing is saved until the person approves. Say so."},
                              [{"kind": "proposal", "id": pid, "action": "cast.join", "title": title, "lines": lines}])

        return [
            Tool(ToolSpec("character_profile", "What is known about a person: a short sketch, each lens with how sure it is, their track record from your calls, and how well past predictions about them did.",
                          {**_props(person=S), "required": ["person"]}), lambda a: f"Read what's known about {text(a.get('person'))}", character_profile),
            Tool(ToolSpec("how_will_they_take_it", "Before breaking news to someone: how they'll most likely take it (from what's recorded about them), how to approach it, and a draft message. The prediction is kept so it can be scored.",
                          {**_props(person=S, news={**S, "description": "The news, in a sentence or two."}, story=S), "required": ["person", "news"]}),
                 lambda a: f"Predicted how {text(a.get('person'))} will take it", safe(how_will_they_take_it)),
            Tool(ToolSpec("how_they_took_it", "After news was broken: how the person actually took it. Scores the last prediction about them and keeps the reaction as evidence.",
                          {**_props(person=S, reaction={"type": "string", "enum": list(REACTIONS)}, story=S), "required": ["person", "reaction"]}),
                 lambda a: f"Noted how {text(a.get('person'))} took it", safe(how_they_took_it)),
            Tool(ToolSpec("note_about_person", "When the person tells you something about someone (a moment, what they want, how they behave), propose keeping it as a scene in their words. It's read for what it says about them.",
                          {**_props(person=S, text={**S, "description": "The person's own words."}, story=S), "required": ["person", "text"]}),
                 lambda a: f"Proposed a note about {text(a.get('person'))}", safe(note_about_person)),
            Tool(ToolSpec("compare_people", "When the person says how two people differ, propose it as a lens of their own (two ends in their words) with where each sits.",
                          {**_props(first=S, second=S, text={**S, "description": "Their answer, in their words."}), "required": ["first", "second", "text"]}),
                 lambda a: "Proposed a lens of your own", safe(compare_people)),
            Tool(ToolSpec("people_in_text", "The people named in a news item or a mail, proposed for the cast with only the numbers and addresses that are really in the text. Optionally into a story.",
                          {**_props(text=S, story=S), "required": ["text"]}), lambda a: "Read the people in the text", safe(people_in_text)),
        ]
