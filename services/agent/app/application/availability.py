"""Time inside an event that can still be used.

A visit to the hospital from 9 to 12 is an event the planner must not schedule over, but the person can take calls and follow-ups during it.
The planner can't know that, so it **asks**: one short question on the workspace's bot with three buttons (calls are fine / free for anything /
busy). The answer is remembered for that event and for events with that title, so a recurring one is only asked once. A "calls" answer opens
the window to follow-ups but not to heavy tasks; "free" opens it to everything; "busy" keeps it closed.

The answers are applied by `AvailabilityData`, which marks each event and meeting with its `mode`; everything that reads the day (the planner,
the plan check, the call reminders) then treats it the same way.
"""

from __future__ import annotations

import html
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from stiamo_common.errors import Conflict, ValidationFailed

from app.application.availdata import AvailabilityData, view
from app.application.ports import DataGateway, SettingsRepository, UserSettings
from app.application.scope import ScopedData
from app.application.services import Who
from app.domain.dayplan import clock, day_label, is_open, normalize_title
from app.domain.phone import Style

log = logging.getLogger(__name__)

MODES = {"c": "calls", "f": "free", "b": "busy"}
#: An event shorter than this isn't worth a question. At most this many questions are asked for one day.
MIN_ASK = 60
MAX_ASK = 3
#: Answers and questions are forgotten after this long.
KEEP = timedelta(days=45)
#: Courts and hearings are never free time.
NEVER = ("hearing", "court", "tribunal")

EMOJI = (
    (("hospital", "clinic", "doctor", "dentist", "checkup", "check-up"), "🏥"),
    (("flight", "airport", "fly"), "✈️"),
    (("train", "station"), "🚆"),
    (("drive", "travel", "trip", "commute", "journey"), "🚗"),
    (("lunch", "dinner", "breakfast"), "🍽️"),
    (("gym", "workout", "yoga", "run"), "🏋️"),
    (("school", "class", "lecture", "exam"), "🏫"),
    (("wedding", "party", "birthday", "function"), "🎉"),
    (("bank", "office visit"), "🏦"),
    (("workshop", "training", "conference", "seminar"), "🎓"),
)


def event_emoji(title: str) -> str:
    low = (title or "").lower()
    return next((e for words, e in EMOJI if any(w in low for w in words)), "🗓️")


# --- asking and remembering --------------------------------------------------------------------------------------------------

Announce = Callable[[Who, Callable[[Style, int], str], str, list[list[dict[str, str]]]], Awaitable[int]]


def question_text(q: dict[str, Any], tomorrow: bool = True) -> Callable[[Style, int], str]:
    """Short, like a person asking: what, when, and the one thing wanted."""

    def write(style: Style, limit: int) -> str:
        bold = (lambda s: f"<b>{html.escape(s)}</b>") if style == "telegram" else (lambda s: s)
        when = "tomorrow" if tomorrow else day_label(q["day"])
        return f"{event_emoji(q['title'])} {bold(q['title'])}, {when} {clock(q['start'])}–{clock(q['end'])}\nFree for calls then? 📞"

    return write


def buttons(short: str) -> list[list[dict[str, str]]]:
    return [
        [{"text": "📞 Calls are fine", "callback_data": f"av:c:{short}"}, {"text": "💻 Free for anything", "callback_data": f"av:f:{short}"}],
        [{"text": "🙅 Busy, leave it", "callback_data": f"av:b:{short}"}],
    ]


def confirmation(r: dict[str, Any]) -> str:
    """What was understood and what happens next, in three or four short lines."""
    meaning = {
        "calls": "📞 Calls and follow-ups go inside it; no heavy tasks.",
        "free": "💻 Counts as free time for everything.",
        "busy": "🙅 Kept clear, as before.",
    }[r["mode"]]
    lines = [f"✅ Noted · {event_emoji(r['title'])} {r['title']} · {day_label(r['day'])} {clock(r['start'])}–{clock(r['end'])}", meaning, f"🧠 Remembered for “{r['title']}”."]
    if r.get("remaining"):
        lines.append(f"⏳ {r['remaining']} more question{'s' if r['remaining'] != 1 else ''}, then I'll update {day_label(r['day'])}'s plan.")
    elif r.get("applied"):
        lines.append(f"ℹ️ {day_label(r['day'])}'s plan is already approved, so it stays. Say “plan tomorrow” to redo it.")
    elif r.get("plan") is not None:
        plan = r["plan"]
        inside = [b for b in plan["blocks"] if b["kind"] == "followUp" and b["start"] < r["end"] and r["start"] < b["start"] + b["duration"]]
        lines.append(f"🔁 Re-planned {day_label(r['day'])}: the new plan is below." + (f" {len(inside)} call{'s' if len(inside) != 1 else ''} inside the window." if r["mode"] == "calls" and inside else ""))
    elif r.get("replan_failed"):
        lines.append(f"⚠️ Couldn't re-plan ({r['replan_failed']}). Say “plan tomorrow” and I'll use it.")
    return "\n".join(lines)


class AvailabilityService:
    def __init__(self, settings: SettingsRepository, data_factory: Callable[[str], DataGateway], announce: Announce | None, plans: Any = None, clock_now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._settings, self._data, self._announce, self._plans, self._now = settings, data_factory, announce, plans, clock_now

    async def _load(self, who: Who) -> UserSettings:
        return await self._settings.get(who.user_id) or UserSettings(who.user_id, who.email)

    def _prune(self, v: dict[str, Any]) -> None:
        floor = (self._now() - KEEP).date().isoformat()
        v["events"] = {k: x for k, x in v["events"].items() if (x[1] if isinstance(x, list) and len(x) > 1 else "9999") >= floor}
        v["asked"] = {k: x for k, x in v["asked"].items() if x.get("day", "9999") >= floor}

    # --- the answers that are kept ---------------------------------------------------------------------------------

    async def rules(self, who: Who) -> list[dict[str, str]]:
        return [{"title": t, "mode": m} for t, m in sorted(view((await self._load(who)).availability)["rules"].items())]

    async def forget(self, who: Who, title: str) -> None:
        s = await self._load(who)
        v = view(s.availability)
        v["rules"].pop(normalize_title(title), None)
        v["events"] = {k: x for k, x in v["events"].items() if not (isinstance(x, list) and len(x) > 2 and x[2] == normalize_title(title))}
        s.availability = v
        await self._settings.save(s)

    # --- asking ----------------------------------------------------------------------------------------------------

    async def undecided(self, who: Who, token: str, day: str, workspace: str) -> list[dict[str, Any]]:
        """The long events and meetings of a workspace on a day that nothing has been said about yet, longest first."""
        s = await self._load(who)
        data = AvailabilityData(ScopedData(self._data(token), workspace), s.availability)
        found: list[dict[str, Any]] = []
        for e in await data.calendar_events(day, day):
            if e.get("allDay") or e.get("mode") or e.get("date") != day or e.get("startMinute") is None:
                continue
            end = int(e.get("endMinute") or e["startMinute"])
            found.append({"key": f"cal:{e.get('id')}", "title": str(e.get("title") or "Event"), "start": int(e["startMinute"]), "end": end})
        for m in await data.records("meetings"):
            slot = m.get("slot") or {}
            if m.get("mode") or not is_open(m) or slot.get("date") != day or slot.get("start") is None:
                continue
            found.append({"key": f"meeting:{m.get('id')}", "title": str(m.get("title") or "Meeting"), "start": int(slot["start"]), "end": int(slot["start"]) + int(slot.get("duration") or 30)})
        asked = {(q.get("key"), q.get("day")) for q in view(s.availability)["asked"].values()}
        ok = [f for f in found if f["end"] - f["start"] >= MIN_ASK and (f["key"], day) not in asked and not any(w in f["title"].lower() for w in NEVER)]
        return sorted(ok, key=lambda f: f["start"] - f["end"])[:MAX_ASK]

    async def ask(self, who: Who, token: str, day: str, workspace: str) -> int:
        """Asks about the events that need it, each as a short message with buttons on the workspace's planner bot. Returns how many were asked."""
        if self._announce is None:
            return 0
        questions = await self.undecided(who, token, day, workspace)
        if not questions:
            return 0
        s = await self._load(who)
        v = view(s.availability)
        sent = 0
        for q in questions:
            short = uuid.uuid4().hex[:8]
            item = {**q, "day": day, "workspace": workspace}
            try:
                reached = await self._announce(who, question_text(item), workspace, buttons(short))
            except Exception:  # noqa: BLE001  not being able to ask is not a reason to stop planning
                log.exception("could not ask about %s", q["title"])
                continue
            if reached:
                v["asked"][short] = item
                sent += 1
        self._prune(v)
        s.availability = v
        await self._settings.save(s)
        return sent

    # --- an answer ---------------------------------------------------------------------------------------------------

    async def answer(self, who: Who, token: str, code: str, short: str) -> dict[str, Any]:
        """Keeps what was said, and when it was the last question for that day, plans the day again (a plan not yet approved is replaced)."""
        mode = MODES.get(code)
        s = await self._load(who)
        v = view(s.availability)
        q = v["asked"].pop(short, None)
        if mode is None or q is None:
            raise ValidationFailed("That question is out of date. Say “plan tomorrow” and I'll ask again if it still matters.")
        norm = normalize_title(q["title"])
        v["events"][q["key"]] = [mode, q["day"], norm]
        v["rules"][norm] = mode
        remaining = sum(1 for x in v["asked"].values() if x.get("day") == q["day"] and x.get("workspace") == q["workspace"])
        self._prune(v)
        s.availability = v
        await self._settings.save(s)
        out: dict[str, Any] = {**q, "mode": mode, "remaining": remaining}
        if remaining or self._plans is None or q["day"] < self._now().date().isoformat():
            return out
        try:
            out["plan"] = await self._plans.generate(who, token, day=q["day"], trigger="answer", force=False, announce=True, workspace=q["workspace"])
        except Conflict:
            out["applied"] = True
        except Exception as exc:  # noqa: BLE001  the answer is kept; only the new plan failed
            out["replan_failed"] = str(getattr(exc, "message", None) or exc)[:120]
        return out
