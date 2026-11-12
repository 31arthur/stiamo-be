"""The story editor: comes looking for you when a story goes quiet, and gets you writing again.

Each evening (with the evening plan) it looks at every story against its own pace, and for each workspace sends at most one message: the
step of the ladder that story has reached (see domain/editor.py). Every message is a question that takes a tap: did that step happen, which
of three openings, what would have gone wrong, is this still the ending. Taps do real things: an opening books its step, a step is marked
done, a hunch is settled, a story is rested or ended. "Not now" leaves it for its own pace. Once a week the writers' room sums up each
workspace's stories. Nothing here writes a chapter: chapters are the person's.
"""

from __future__ import annotations

import html
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

from stiamo_common.errors import ValidationFailed

from app.application.aijobs import job_key, runner_for
from app.application.ports import DataGateway, LlmClient, SettingsRepository, UserSettings
from app.application.scope import ScopedData
from app.application.services import Who, local_today, selection_of
from app.domain import editor as ed
from app.domain import interviewer as iv
from app.domain.lenses import REACTION_WORDS, phrase
from app.domain.phone import Style
from app.domain.story_jobs import NEXT_OPENINGS, same_name

log = logging.getLogger(__name__)

Announce = Callable[[Who, Callable[[Style, int], str], str, list[list[dict[str, str]]]], Awaitable[int]]
NAMES = {"personal": "Personal", "sb": "S&B", "fsa": "FSA", "media": "Media"}
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
#: Questions not answered within this long are out of date.
KEEP_DAYS = 14
LOG_SIZE = 200


async def survey_of(data: DataGateway, today: str) -> list[dict[str, Any]]:
    """Every story the data shows, with its chapters, open steps, momentum and push score, highest score first."""
    steps = [{**r, "_table": t} for t in ("tasks", "follow-ups") for r in await data.records(t) if (r.get("outcome") or {}).get("status", "open") == "open"]
    out = []
    for story in await data.records("stories"):
        chapters = await data.chapters(story["id"])
        mine = [r for r in steps if (r.get("related") or r.get("subject") or {}) == {"kind": "story", "id": story["id"]}]
        m = ed.momentum(story, chapters, today)
        out.append({"story": story, "chapters": chapters, "steps": mine, "momentum": m, "score": ed.push_score(story, m, mine, today)})
    return sorted(out, key=lambda v: v["score"], reverse=True)


def editor_settings(raw: dict[str, Any] | None) -> dict[str, Any]:
    e = (raw or {}).get("settings") or {}
    return {"enabled": e.get("enabled", True) is not False, "roomDay": e.get("roomDay") if e.get("roomDay") in DAYS else "sun"}


def _b(text: str, data: str) -> dict[str, str]:
    return {"text": text, "callback_data": data}


def _fmt(lines: list[str]) -> Callable[[Style, int], str]:
    """Lines with **bold** marks, written for Telegram (HTML) or as plain text."""

    def write(style: Style, limit: int) -> str:
        out = []
        for line in lines:
            parts = html.escape(line, quote=False).split("**") if style == "telegram" else line.split("**")
            out.append("".join(f"<b>{p}</b>" if i % 2 else p for i, p in enumerate(parts)))
        text = "\n".join(out)
        return text if len(text) <= limit else text[: limit - 1] + "…"

    return write


class EditorService:
    def __init__(self, settings: SettingsRepository, data_factory: Callable[[str], DataGateway], llm_factory: Callable[[str, str], LlmClient],
                 announce: Announce | None, clock_now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._settings, self._data, self._llm, self._announce, self._now = settings, data_factory, llm_factory, announce, clock_now

    async def _load(self, who: Who) -> UserSettings:
        return await self._settings.get(who.user_id) or UserSettings(who.user_id, who.email)

    def _today(self, s: UserSettings) -> str:
        return local_today(s, self._now()).isoformat()

    # --- reading the stories ----------------------------------------------------------------------------------------

    async def survey(self, who: Who, token: str, workspace: str | None = None) -> list[dict[str, Any]]:
        s = await self._load(who)
        return await survey_of(ScopedData(self._data(token), workspace) if workspace else self._data(token), self._today(s))

    async def overview(self, who: Who, token: str) -> list[dict[str, Any]]:
        s = await self._load(who)
        memory = (s.editor or {}).get("stories") or {}
        return [{"id": v["story"]["id"], "title": v["story"].get("title"), "workspace": v["story"].get("workspace"), "state": v["momentum"].state,
                 "quietDays": v["momentum"].quiet_days, "paceDays": v["momentum"].pace_days, "rung": v["momentum"].rung, "score": v["score"],
                 "why": ed.why(v["story"], v["momentum"], v["steps"]), "snoozedUntil": (memory.get(v["story"]["id"]) or {}).get("snoozed")}
                for v in await self.survey(who, token)]

    async def to_push(self, who: Who, token: str, workspace: str | None, limit: int = 3) -> list[dict[str, Any]]:
        return [v for v in await self.survey(who, token, workspace) if v["score"] > 0][:limit]

    # --- the evening ------------------------------------------------------------------------------------------------

    async def evening(self, who: Who, token: str, workspaces: list[str] | None = None) -> list[dict[str, Any]]:
        """For each workspace with stories: at most one ladder message, and on the writers' room day, the week in a message."""
        s = await self._load(who)
        if not editor_settings(s.editor)["enabled"] or self._announce is None:
            return []
        today = self._today(s)
        try:  # new scenes are read for what they say about people first, so tonight's questions use them
            await self.code_now(who, token)
        except Exception:  # noqa: BLE001
            log.exception("could not read the new scenes")
        everything = await self.survey(who, token)
        chosen = sorted({v["story"].get("workspace") or "personal" for v in everything})
        if workspaces and any(workspaces):
            chosen = [w for w in chosen if w in workspaces]
        sent: list[dict[str, Any]] = []
        for ws in chosen:
            views = [v for v in everything if (v["story"].get("workspace") or "personal") == ws]
            try:
                sent += await self._run_workspace(who, token, ws, today, views)
            except Exception:  # noqa: BLE001  one workspace failing doesn't stop the others
                log.exception("the editor failed for %s", ws)
            try:
                sent += await self._interview(who, token, ws, today, views)
            except Exception:  # noqa: BLE001
                log.exception("the interviewer failed for %s", ws)
        return sent

    def _runners(self, s: UserSettings):
        return lambda name: runner_for(selection_of(s, job_key(name))[0], s.keys, self._llm)

    async def code_now(self, who: Who, token: str) -> dict[str, Any]:
        """Reads the scenes not yet read: which lenses each speaks to, and a label on each, as evidence."""
        from app.application.cast_insight import code_scenes

        s = await self._load(who)
        return await code_scenes(self._data(token), self._runners(s))

    # --- the interviewer ------------------------------------------------------------------------------------------

    async def _interview(self, who: Who, token: str, ws: str, today: str, views: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not views:
            return []
        s = await self._load(who)
        e = dict(s.editor or {})
        if (e.get("interviewed") or {}).get(ws) == today:
            return []
        data = ScopedData(self._data(token), ws)
        stories = {v["story"]["id"]: v["story"] for v in views}
        quiet = {v["story"]["id"] for v in views if v["momentum"].rung >= 1}
        parts = [p for p in await data.records("parts") if p.get("storyId") in stories]
        evidence = await data.records("evidence")
        scenes = await data.records("scenes")
        calls = [f for f in await data.records("follow-ups") if f.get("characterId")]
        people = []
        for c in await data.records("characters"):
            mine = [(p, stories[p["storyId"]]) for p in parts if p.get("characterId") == c["id"]]
            if not mine:
                continue
            people.append(iv.Person(c, mine, [x for x in evidence if x.get("characterId") == c["id"]], [x for x in scenes if x.get("characterId") == c["id"]],
                                    [f for f in calls if f.get("characterId") == c["id"]], {sid for _, st in mine for sid in [st["id"]] if sid in quiet}))
        memory = dict(e.get("people") or {})
        week = date.fromisoformat(today).isocalendar()
        week_key = f"{week[0]}-W{week[1]:02d}"
        questions = iv.plan(people, today, memory, (e.get("compareWeek") == week_key))
        sent = []
        for q in questions:
            short = uuid.uuid4().hex[:8]
            c = q.person.character
            rows: list[list[dict[str, str]]] = []
            opts = list(q.options)
            for i in range(0, len(opts), 2):
                rows.append([_b(label, f"iv:{i + j}:{short}") for j, (_, label) in enumerate(opts[i:i + 2])])
            rows.append([_b("⏭ Skip them for a week", f"iv:x:{short}")])
            lines = [f"🎭 **{c.get('name')}**" + (f" · {q.story.get('title')}" if q.story else ""), "", q.text]
            asked = {"short": short, "kind": "interview", "method": q.method, "lens": q.lens, "character": c["id"], "name": c.get("name"),
                     "story": (q.story or {}).get("id"), "options": [o for o, _ in opts], "specific": q.specific,
                     "scene": (q.scene or {}).get("id"), "prediction": (q.prediction or {}).get("id"), "about": f"bad news about “{(q.story or {}).get('title', '')}”"}
            if not await self._ask(who, ws, lines, rows, asked, today):
                continue
            mem = dict(memory.get(c["id"]) or {})
            mem["on"] = today
            if q.method in ("sheet", "why", "moment", "what_would"):
                mem[q.method] = today
            memory[c["id"]] = mem
            sent.append({"workspace": ws, "person": c["id"], "method": q.method, "lens": q.lens})
        s = await self._load(who)
        e = dict(s.editor or {})
        e["people"] = memory
        e["interviewed"] = {**(e.get("interviewed") or {}), ws: today}
        if any(x["method"] == "compare" for x in sent):
            e["compareWeek"] = week_key
        s.editor = e
        await self._settings.save(s)
        return sent

    async def interview_press(self, who: Who, token: str, action: str, short: str) -> str:
        s = await self._load(who)
        e = dict(s.editor or {})
        q = (e.get("asked") or {}).get(short)
        if q is None or q.get("kind") != "interview":
            raise ValidationFailed("That question is out of date. I'll ask again another day if it still matters.")
        today = self._today(s)
        data = self._data(token)
        name = q.get("name") or "them"

        async def done(text: str, rest: bool = False) -> str:
            s2 = await self._load(who)
            e2 = dict(s2.editor or {})
            e2["asked"] = {k: x for k, x in (e2.get("asked") or {}).items() if k != short}
            if rest:
                mem = dict((e2.get("people") or {}).get(q["character"]) or {})
                mem["rest"] = (date.fromisoformat(today) + timedelta(days=iv.REST_DAYS)).isoformat()
                e2["people"] = {**(e2.get("people") or {}), q["character"]: mem}
            e2["log"] = [*(e2.get("log") or []), {"person": q["character"], "day": today, "kind": q.get("method"), "answer": action}][-LOG_SIZE:]
            s2.editor = e2
            await self._settings.save(s2)
            return text

        if action == "x":
            return await done(f"⏭ I'll leave {name} for a week.", rest=True)
        if not action.isdigit() or int(action) >= len(q.get("options") or []):
            return await done("That answer isn't one of the options any more.")
        label = q["options"][int(action)]
        lens = q.get("lens") or ""
        if q["method"] == "debrief_prediction":
            from app.application.cast_insight import settle

            character = next((c for c in await data.records("characters") if c.get("id") == q["character"]), {"id": q["character"]})
            prediction = next((x for x in await data.records("evidence") if x.get("id") == q.get("prediction")), None)
            out = await settle(data, character, label, today, q.get("story"), prediction)
            verdict = out["settled"]
            tail = f" The guess (“{REACTION_WORDS.get(verdict[2] or '', verdict[2])}”) was {verdict[0]}." if verdict else ""
            return await done(f"📝 Noted: {name} {REACTION_WORDS.get(label, label)}.{tail}")
        if label == "depends":
            return await done(f"OK: it depends. I'll keep watching what {name} does.")
        record: dict[str, Any] = {"id": str(uuid.uuid4()), "characterId": q["character"], "storyId": q.get("story"), "lens": lens, "date": today,
                                  "specific": bool(q.get("specific")), "sceneId": q.get("scene")}
        if q["method"] == "what_would":
            record.update(source="prediction", method="what_would", predictedBy="you", verdict="pending", about=q.get("about"),
                          label=label if label != "takes_it_well" else "solves")
            await data.save_record("evidence", record)
            return await done(f"🔮 Your guess is kept: {name} {REACTION_WORDS.get(label, label)}. I'll ask how it really went, and score it.")
        record.update(source="answer", method=q["method"] if q["method"] in ("quick_tap", "debrief", "check", "sheet") else "quick_tap", label=label)
        await data.save_record("evidence", record)
        character = next((c for c in await data.records("characters") if c.get("id") == q["character"]), None)
        from app.domain.interviewer import sketch

        known = f"\n\nWhat I know now: {sketch(name, (character or {}).get('profile'))}" if character else ""
        return await done(f"📝 Noted: {name} {phrase(lens, label)}.{known}")

    async def _run_workspace(self, who: Who, token: str, ws: str, today: str, views: list[dict[str, Any]]) -> list[dict[str, Any]]:
        s = await self._load(who)
        e = dict(s.editor or {})
        memory: dict[str, Any] = dict(e.get("stories") or {})
        out: list[dict[str, Any]] = []
        for v in views:
            memory[v["story"]["id"]] = ed.reset_if_moved(memory.get(v["story"]["id"]) or {}, v["momentum"])
        if (e.get("sent") or {}).get(ws) != today:
            due = [(ed.rung_to_send(v["momentum"], memory.get(v["story"]["id"]) or {}, today), v) for v in views]
            due = [(r, v) for r, v in due if r]
            if due:
                rung, v = max(due, key=lambda x: (x[0], x[1]["score"]))
                if await self._send_rung(who, token, s, ws, rung, v, today):
                    memory[v["story"]["id"]] = ed.remember(memory.get(v["story"]["id"]) or {}, v["momentum"], rung, today)
                    e["sent"] = {**(e.get("sent") or {}), ws: today}
                    out.append({"workspace": ws, "story": v["story"]["id"], "rung": rung})
        s = await self._load(who)  # asked questions were saved while sending
        e = {**(s.editor or {}), "stories": memory, "sent": e.get("sent") or (s.editor or {}).get("sent") or {}}
        s.editor = e
        await self._settings.save(s)
        if DAYS[date.fromisoformat(today).weekday()] == editor_settings(e)["roomDay"] and (e.get("room") or {}).get(ws) != today and views:
            if await self._writers_room(who, ws, views, today):
                s = await self._load(who)
                s.editor = {**(s.editor or {}), "room": {**((s.editor or {}).get("room") or {}), ws: today}}
                await self._settings.save(s)
                out.append({"workspace": ws, "room": True})
        return out

    async def run_now(self, who: Who, token: str, workspace: str | None = None) -> list[dict[str, Any]]:
        """The evening's editor, now: what it would send, it sends (still at most one message per workspace per day)."""
        return await self.evening(who, token, [workspace] if workspace else None)

    # --- the ladder -------------------------------------------------------------------------------------------------

    async def _ask(self, who: Who, ws: str, lines: list[str], buttons: list[list[dict[str, str]]], q: dict[str, Any], today: str) -> bool:
        if not await self._announce(who, _fmt(lines), ws, buttons):  # type: ignore[misc]
            return False
        s = await self._load(who)
        e = dict(s.editor or {})
        floor = (date.fromisoformat(today) - timedelta(days=KEEP_DAYS)).isoformat()
        asked = {k: x for k, x in (e.get("asked") or {}).items() if x.get("day", today) >= floor}
        asked[q["short"]] = {**q, "day": today, "workspace": ws}
        e["asked"] = asked
        s.editor = e
        await self._settings.save(s)
        return True

    async def _send_rung(self, who: Who, token: str, s: UserSettings, ws: str, rung: int, v: dict[str, Any], today: str) -> bool:
        story, chapters, m = v["story"], v["chapters"], v["momentum"]
        title, short = story.get("title", ""), uuid.uuid4().hex[:8]
        head = [f"📖 **{title}** · {m.quiet_days} days without a chapter", f"Previously: {ed.previously(story, chapters)}", ""]
        later = _b("💤 Not now", f"ed:z:{short}")
        q: dict[str, Any] = {"short": short, "story": story["id"], "title": title, "rung": rung}
        if rung == 1:
            hunch = ed.due_hunch(chapters, m.pace_days, today)
            step = ed.oldest_step(v["steps"])
            if hunch:
                c, h = hunch
                q.update(kind="hunch", chapter=c["id"], hunch=h["id"])
                return await self._ask(who, ws, [*head, f"Your hunch from {c.get('date')}: “{h.get('text')}”. Did it come true?"],
                                       [[_b("✅ It did", f"ed:hy:{short}"), _b("❌ It didn't", f"ed:hn:{short}")], [_b("⏳ Not yet", f"ed:hw:{short}"), later]], q, today)
            if step:
                q.update(kind="step", step=step["id"], table=step.get("_table", "tasks"))
                return await self._ask(who, ws, [*head, f"Did this happen: “{step.get('title')}”?"],
                                       [[_b("✅ Done", f"ed:sd:{short}"), _b("⏳ Not yet", f"ed:sw:{short}"), _b("🗑 Drop it", f"ed:sx:{short}")], [later]], q, today)
            q.update(kind="open")
            return await self._ask(who, ws, [*head, "What's happened since?"],
                                   [[_b("✍️ I'll write a chapter", f"ed:w:{short}")], [_b("🤷 Nothing yet", f"ed:n:{short}"), _b("⏳ Waiting on someone", f"ed:wt:{short}")], [later]], q, today)
        if rung == 2:
            openings = await self._openings(s, v, today)
            q.update(kind="openings", openings=openings)
            lines = [*head, "Three ways the next chapter could start. Tap one and I'll book its step:", ""]
            lines += [f"{i + 1}. {o['opening']}" for i, o in enumerate(openings)]
            row = [_b(str(i + 1), f"ed:o{i}:{short}") for i in range(len(openings))]
            return await self._ask(who, ws, lines, [row, [_b("✍️ I'll write it", f"ed:w:{short}"), later]], q, today)
        if rung == 3:
            ending = story.get("ending") or "the ending you want"
            q.update(kind="premortem")
            rows = [[_b(label, f"ed:p{code}:{short}")] for code, label in ed.PREMORTEM]
            return await self._ask(who, ws, [*head, f"Imagine it's a month from now and “{ending}” didn't happen. What went wrong?"], [*rows, [later]], q, today)
        q.update(kind="ending")
        ending = story.get("ending") or "(no ending written yet)"
        return await self._ask(who, ws, [*head, f"Is “{ending}” still the ending you want?"],
                               [[_b("👍 Keep it", f"ed:ekeep:{short}"), _b("✍️ Rewrite it", f"ed:erew:{short}")],
                                [_b("🛌 Rest this story", f"ed:erest:{short}"), _b("🏁 The End", f"ed:eend:{short}")], [later]], q, today)

    async def _openings(self, s: UserSettings, v: dict[str, Any], today: str) -> list[dict[str, Any]]:
        story, chapters = v["story"], v["chapters"]
        people = []
        for c in reversed(chapters):
            for p in c.get("people") or []:
                if p.get("name") and not any(same_name(p["name"], x) for x in people):
                    people.append(p["name"])
        here = ed.here_of(story)
        last = max(chapters, key=lambda c: (c.get("date") or "", c.get("number") or 0)) if chapters else None
        runner = runner_for(selection_of(s, job_key(NEXT_OPENINGS))[0], s.keys, self._llm)
        if runner is not None:
            result = await runner.run(NEXT_OPENINGS, {
                "story": {"title": story.get("title"), "ending": story.get("ending")}, "today": today, "quiet_days": v["momentum"].quiet_days,
                "here": here.get("title") if here else None, "waiting": (story.get("waitingOn") or {}).get("on"), "people": people[:8],
                "open_steps": [x.get("title") for x in v["steps"]][:6], "last": (last or {}).get("prose", "")[:1500]})
            if result.ok and len(result.output["openings"]) >= 2:
                return result.output["openings"]
        return ed.fallback_openings(story, people, today)

    async def _writers_room(self, who: Who, ws: str, views: list[dict[str, Any]], today: str) -> bool:
        week = (date.fromisoformat(today) - timedelta(days=7)).isoformat()
        moved = [(v, sum(1 for c in v["chapters"] if (c.get("date") or "") > week)) for v in views]
        moved = [(v, n) for v, n in moved if n]
        quiet = [v for v in views if v["momentum"].rung >= 1]
        hunches = [(v, h) for v in views for c in v["chapters"] for h in c.get("hunches") or [] if h.get("cameTrue") is None]
        top = next((v for v in views if v["score"] > 0), None)
        lines = [f"📚 **Writers' room · {NAMES.get(ws, ws)}** · the week to {today}", ""]
        lines.append("✍️ Moved: " + (", ".join(f"{v['story'].get('title')} ({n})" for v, n in moved) if moved else "nothing this week"))
        if quiet:
            lines.append("🌫️ Gone quiet: " + ", ".join(f"{v['story'].get('title')} ({v['momentum'].quiet_days} days)" for v in quiet))
        if hunches:
            lines.append("🔮 Hunches waiting: " + "; ".join(f"“{h.get('text')}” ({v['story'].get('title')})" for v, h in hunches[:3]))
        buttons: list[list[dict[str, str]]] = []
        if top:
            step = ed.oldest_step(top["steps"])
            lines += ["", f"🎯 Your next hour: **{top['story'].get('title')}** ({ed.why(top['story'], top['momentum'], top['steps'])})"
                      + (f". Next step: {step.get('title')}" if step else "")]
            short = uuid.uuid4().hex[:8]
            buttons = [[_b("💡 Ways to start", f"ed:r2:{short}")]]
            return await self._ask(who, ws, lines, buttons, {"short": short, "story": top["story"]["id"], "title": top["story"].get("title"), "rung": 0, "kind": "room"}, today)
        return bool(await self._announce(who, _fmt(lines), ws, buttons))  # type: ignore[misc]

    # --- a tap ------------------------------------------------------------------------------------------------------

    async def press(self, who: Who, token: str, action: str, short: str) -> str | tuple[str, list[list[dict[str, str]]]]:
        s = await self._load(who)
        e = dict(s.editor or {})
        asked = dict(e.get("asked") or {})
        q = asked.get(short)
        if q is None:
            raise ValidationFailed("That question is out of date. I'll ask again if the story is still quiet.")
        data = self._data(token)
        story = next((x for x in await data.records("stories") if x.get("id") == q["story"]), None)
        if story is None:
            raise ValidationFailed("That story isn't there any more.")
        title = story.get("title", "")
        today = self._today(s)
        pace = ed.PACE_DAYS.get(story.get("pacing") or "steady", 14)
        write = f"✍️ Send me the chapter here, starting with “{title}:”. I'll read the margin notes and you approve them."

        async def done(text: str, snooze: int | None = None, keep: bool = False) -> str:
            s2 = await self._load(who)
            e2 = dict(s2.editor or {})
            if not keep:
                e2["asked"] = {k: x for k, x in (e2.get("asked") or {}).items() if k != short}
            if snooze:
                mem = dict((e2.get("stories") or {}).get(story["id"]) or {})
                mem["snoozed"] = (date.fromisoformat(today) + timedelta(days=snooze)).isoformat()
                e2["stories"] = {**(e2.get("stories") or {}), story["id"]: mem}
            e2["log"] = [*(e2.get("log") or []), {"story": story["id"], "day": today, "kind": q.get("kind"), "answer": action}][-LOG_SIZE:]
            s2.editor = e2
            await self._settings.save(s2)
            return text

        if action == "z":
            return await done(f"💤 I'll leave “{title}” for {pace} days.", snooze=pace)
        if action == "w":
            return await done(write)
        if action == "n":
            return await done(f"Noted. I'll look in on “{title}” again in {pace} days.", snooze=pace)
        if action == "wt":
            return await done(f"⏳ Tell me who or what it's waiting on, and until when, starting with “{title}:” (for example “{title}: waiting on the bank until Friday”). I won't nudge while it waits.")
        if action in ("sd", "sw", "sx"):
            table = q.get("table", "tasks")
            row = next((r for r in await data.records(table) if r.get("id") == q.get("step")), None)
            if row is None:
                return await done("That step isn't there any more.")
            if action == "sw":
                return await done(f"OK, “{row.get('title')}” stays open. I'll ask again in {pace} days.", snooze=pace)
            status = "done" if action == "sd" else "cancelled"
            await data.save_record(table, {**row, "outcome": {"status": status, "recordedAt": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")}})
            if action == "sd":
                return await done(f"✅ Marked done: {row.get('title')}.\n\nWhat came of it? Start with “{title}:” and it becomes the next chapter.")
            return await done(f"🗑 Dropped: {row.get('title')}.")
        if action in ("hy", "hn", "hw"):
            chapter = next((c for c in await data.chapters(story["id"]) if c.get("id") == q.get("chapter")), None)
            if chapter is None:
                return await done("That chapter isn't there any more.")
            hunches = []
            for h in chapter.get("hunches") or []:
                if h.get("id") == q.get("hunch"):
                    h = {**h, "cameTrue": True} if action == "hy" else {**h, "cameTrue": False} if action == "hn" else \
                        {**h, "checkOn": (date.fromisoformat(today) + timedelta(days=pace)).isoformat()}
                hunches.append(h)
            await data.save_record("chapters", {**chapter, "hunches": hunches})
            if action == "hw":
                return await done(f"⏳ I'll ask about it again in {pace} days.")
            settled = [h for c in await data.chapters(story["id"]) for h in c.get("hunches") or [] if h.get("cameTrue") is not None]
            right = sum(1 for h in settled if h.get("cameTrue"))
            return await done(f"🔮 Noted. Your hunches in “{title}”: {right} of {len(settled)} came true.")
        if action.startswith("o") and action[1:].isdigit():
            openings = q.get("openings") or []
            n = int(action[1:])
            if n >= len(openings):
                return await done("That opening isn't there any more.")
            made = await self._book(data, story, openings[n]["step"], today)
            return await done(f"📌 Booked: {made}\n\n“{openings[n]['opening']}” When it happens, write it up starting with “{title}:”.")
        if action == "r2":
            v = next((x for x in await self.survey(who, token) if x["story"]["id"] == story["id"]), None)
            if v is None:
                return await done("That story isn't there any more.")
            openings = await self._openings(s, v, today)
            new_short = uuid.uuid4().hex[:8]
            s3 = await self._load(who)
            e3 = dict(s3.editor or {})
            e3["asked"] = {**(e3.get("asked") or {}), new_short: {"short": new_short, "story": story["id"], "title": title, "rung": 2, "kind": "openings", "openings": openings, "day": today, "workspace": q.get("workspace")}}
            s3.editor = e3
            await self._settings.save(s3)
            text = "\n".join([f"💡 Ways to start the next chapter of “{title}”. Tap one and I'll book its step:", "", *[f"{i + 1}. {o['opening']}" for i, o in enumerate(openings)]])
            return await done(text), [[_b(str(i + 1), f"ed:o{i}:{new_short}") for i in range(len(openings))]]
        if action.startswith("p"):
            code = action[1:]
            people = [p.get("name") for c in await data.chapters(story["id"]) for p in c.get("people") or [] if p.get("name")]
            tomorrow = (date.fromisoformat(today) + timedelta(days=1)).isoformat()
            if code == "they":
                who_ = people[-1] if people else None
                made = await self._book(data, story, {"kind": "followUp", "title": f"Chase {who_ or 'them'} on “{title}”", "due": tomorrow, "contact": who_, "channel": "call"}, today)
                return await done(f"Then the move is to chase. 📌 Booked: {made}")
            if code == "me":
                made = await self._book(data, story, {"kind": "task", "title": f"Next push on “{title}”", "due": tomorrow}, today)
                return await done(f"Then it's yours to move. 📌 Booked: {made}")
            if code == "no":
                return await done(f"Then the story may need a plan B. When you see it, write it as a twist, starting with “{title}:”.")
            return await done(f"Tell me what you think went wrong, starting with “{title}:”. It becomes a chapter, and its margin notes the risks.")
        if action.startswith("e"):
            choice = action[1:]
            if choice == "keep":
                return await done(f"👍 Keeping “{story.get('ending') or 'the ending'}”. I'll leave it for {2 * pace} days.", snooze=2 * pace)
            if choice == "rew":
                return await done(f"✍️ Tell me the new ending, like “{title}: the new ending is …”, and I'll propose the change.")
            state = "resting" if choice == "rest" else "ended"
            record = {**story, "state": state, **({"endedOn": today} if state == "ended" else {})}
            await data.save_record("stories", record)
            if state == "resting":
                return await done(f"🛌 “{title}” is resting. I won't ask about it until you write in it again.")
            return await done(f"🏁 The End: “{title}”. An epilogue can still follow, whenever there's one to write.")
        return await done("That button is from an older message.")

    async def _book(self, data: DataGateway, story: dict[str, Any], step: dict[str, Any], today: str) -> str:
        """An opening's step, made at once: the tap was the approval. A call to someone in the cast carries their number."""
        ref = {"kind": "story", "id": story["id"]}
        ws = story.get("workspace") or "personal"
        sid = str(uuid.uuid4())
        due = step.get("due")
        when = f" · {date.fromisoformat(due).strftime('%a %d %b')}" if due else ""
        if step.get("kind") == "followUp":
            contact, character = step.get("contact"), None
            if contact:
                parts = [p for p in await data.records("parts") if p.get("storyId") == story["id"]]
                chars = {c["id"]: c for c in await data.records("characters")}
                character = next((chars[p["characterId"]] for p in parts if p.get("characterId") in chars and (same_name(contact, chars[p["characterId"]].get("name"))
                                  or any(same_name(contact, k) for k in p.get("knownAs") or []))), None)
            record = {"id": sid, "title": step["title"], "channel": step.get("channel") or "call", "workspace": ws, "duration": 15, "subject": ref,
                      "notes": f"From the story “{story.get('title')}”", "outcome": {"status": "open"}}
            if character:
                record["characterId"] = character["id"]
                record["contact"] = f"{character['name']} · {character['phones'][0]}" if character.get("phones") else character["name"]
            elif contact:
                record["contact"] = contact
            if due:
                record["due"] = {"date": due}
            await data.save_record("follow-ups", record)
            return f"{(record.get('channel') or 'call').capitalize()}: {step['title']}{when}"
        record = {"id": sid, "title": step["title"], "workspace": ws, "priority": "normal", "estimate": 30, "related": ref,
                  "notes": f"From the story “{story.get('title')}”", "outcome": {"status": "open"}}
        if due:
            record["dueDate"] = due
        await data.save_record("tasks", record)
        return f"Task: {step['title']}{when}"

    # --- settings ---------------------------------------------------------------------------------------------------

    async def settings(self, who: Who) -> dict[str, Any]:
        return editor_settings((await self._load(who)).editor)

    async def set_settings(self, who: Who, enabled: bool, room_day: str) -> dict[str, Any]:
        if room_day not in DAYS:
            raise ValidationFailed("The writers' room day is one of mon, tue, wed, thu, fri, sat, sun.")
        s = await self._load(who)
        s.editor = {**(s.editor or {}), "settings": {"enabled": enabled, "roomDay": room_day}}
        await self._settings.save(s)
        return editor_settings(s.editor)
