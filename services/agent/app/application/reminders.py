"""Reminders about calls that weren't marked done: a message on the phone, and a spoken one on Telegram.

One loop per person, held in memory and started again with the service from everyone who has the reminders on. Each minute it looks at
today's timed calls; when a run of them has ended and some are still open, and nothing else is on, it reminds (see domain/nudge.py for when).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from stiamo_common.errors import ValidationFailed

from app.application.availdata import AvailabilityData
from app.application.dayplans import planner_settings, zone_of
from app.application.ports import CallApi, DataGateway, SettingsRepository, UserSettings
from app.application.services import Who
from app.domain.dayplan import clock, hearings_on, is_open
from app.domain.dial import call_button
from app.domain.nudge import DEFAULT_CALL_VOICE, call_speech, due_run, free_now
from app.domain.nudge import runs as nudge_runs
from app.domain.phone import Style, render

log = logging.getLogger(__name__)

VOICE_KEY = "deepgram"


BAD_USER = "To be rung, give your Telegram username (like @yourname) or your phone number with its country code (like +919876543210)."


def valid_call_user(user: str) -> bool:
    return (user.startswith("@") and len(user) > 2) or (user.startswith("+") and len(user) > 6)


def reminder_settings(raw: dict[str, Any] | None) -> dict[str, Any]:
    r = raw or {}
    return {
        "enabled": bool(r.get("enabled")),
        "call": bool(r.get("call")),
        "callUser": r.get("callUser") or "",
        "callVoice": r.get("callVoice") or DEFAULT_CALL_VOICE,
    }


class ReminderService:
    def __init__(
        self,
        settings: SettingsRepository,
        data_factory: Callable[[str], DataGateway],
        tokens: Any,
        caller: CallApi,
        announce: Callable[[Who, Callable[[Style, int], str], list[list[dict[str, str]]] | None], Awaitable[int]],
        clock_now: Callable[[], datetime] = lambda: datetime.now(UTC),
        deliveries: Any = None,
        app_url: str = "",
    ) -> None:
        self._deliveries, self._app_url = deliveries, app_url
        self._settings, self._data, self._tokens, self._caller, self._announce, self._now = settings, data_factory, tokens, caller, announce, clock_now

    async def _load(self, who: Who) -> UserSettings:
        s = await self._settings.get(who.user_id) or UserSettings(who.user_id)
        s.email = who.email or s.email
        return s

    # --- what is set ---------------------------------------------------------------------------------------

    async def view(self, who: Who) -> dict[str, Any]:
        s = await self._load(who)
        return {**reminder_settings(s.reminders), "timezone": planner_settings(s.planner)["timezone"]}

    async def set_settings(self, who: Who, enabled: bool, call: bool = False, call_user: str = "", call_voice: str = "") -> dict[str, Any]:
        user = call_user.strip()
        if call and not valid_call_user(user):
            raise ValidationFailed(BAD_USER)
        s = await self._load(who)
        s.reminders = {**s.reminders, "enabled": enabled, "call": call, "callUser": user, "callVoice": call_voice.strip() or DEFAULT_CALL_VOICE}
        await self._settings.save(s)
        return await self.view(who)

    # --- reminding -------------------------------------------------------------------------------------------

    async def _ring(self, s: UserSettings, text: str) -> bool:
        """Rings the person on Telegram and reads the reminder, when that is on. A call that can't be placed leaves the written message to do."""
        cfg = reminder_settings(s.reminders)
        if not cfg["call"] or not cfg["callUser"]:
            return False
        try:
            await self._caller.call(cfg["callUser"], text, cfg["callVoice"], 2)
        except ValidationFailed as exc:
            log.warning("could not ring %s: %s", s.user_id, exc)
            await self._record(s.user_id, False, cfg["callUser"], exc.message, text)
            return False
        await self._record(s.user_id, True, cfg["callUser"], "", text)
        return True

    async def _record(self, user_id: str, ok: bool, target: str, error: str, summary: str) -> None:
        if self._deliveries is not None:
            try:
                await self._deliveries.add(user_id, "call", "call", target, ok, error, summary[:200])
            except Exception:  # noqa: BLE001  keeping a record must never be why a call fails
                log.info("could not keep a delivery record")

    async def check(self, who: Who) -> dict[str, Any] | None:
        """Reminds now if a reminder is due: returns what was said ({calls, step}), or None. Called every minute, and by hand."""
        s = await self._load(who)
        cfg = reminder_settings(s.reminders)
        if not cfg["enabled"]:
            return None
        local = self._now().astimezone(zone_of(planner_settings(s.planner)["timezone"]))
        day, minute = local.date().isoformat(), local.hour * 60 + local.minute
        data = AvailabilityData(self._data(self._tokens.mint(who.user_id, who.email)), s.availability)  # a call can come inside an event they said they can take calls in
        follow_ups = await data.records("follow-ups")
        state = s.reminders.get("state") or {}
        runs = dict(state.get("runs") or {}) if state.get("day") == day else {}
        run = due_run(minute, day, follow_ups, runs)
        if run is None:
            return None
        hearings, meetings, tasks = await data.hearings(), await data.records("meetings"), await data.records("tasks")
        calendar = await data.calendar_events(day, day)
        if not free_now(minute, day, run["pending"], hearings_on(day, hearings), meetings, tasks, follow_ups, calendar):
            return None  # busy: it will come when the minute is free
        cases = {c["id"]: c for c in await data.cases()}
        items = [self._item(c, cases) for c in run["pending"]]
        n = len(items)
        card = {"kind": "reminder", "date": day, "headline": f"{n} call{'s' if n != 1 else ''} still pending", "items": items}
        await self._announce(who, lambda style, limit: render("", [card], limit, "Stiamo", style), reminder_buttons(run["key"], run["pending"], self._app_url))
        rang = await self._ring(s, call_speech(run["step"], run["pending"]))
        runs[run["key"]] = {"step": run["step"] + 1, "last": minute}
        s.reminders = {**s.reminders, "state": {"day": day, "runs": runs}}
        await self._settings.save(s)
        return {"calls": [c.get("id") for c in run["pending"]], "step": run["step"] + 1, "rang": rang}

    @staticmethod
    def _item(call: dict[str, Any], cases: dict[str, Any]) -> dict[str, Any]:
        rel = call.get("related") or {}
        case = cases.get(call.get("caseId") or (rel.get("id") if rel.get("kind") == "case" else None)) or {}
        due = call.get("due") or {}
        return {"type": "followUp", "id": call.get("id"), "title": call.get("title"), "time": clock(due.get("time")), "channel": call.get("channel"), "contact": call.get("contact"), "caseRef": case.get("reference"), "tries": len(call.get("attempts") or []) or None}

    async def test_call(self, who: Who, call_user: str | None = None) -> dict[str, Any]:
        """Rings now, to check the call works before it is needed. Unlike a real reminder it says why when it can't (CallMeBot's own answer).
        Rings `call_user` if given, so it can be tried before it is saved; otherwise the saved one."""
        s = await self._load(who)
        cfg = reminder_settings(s.reminders)
        user = (call_user if call_user is not None else cfg["callUser"]).strip()
        if not valid_call_user(user):
            raise ValidationFailed(BAD_USER)
        try:
            await self._caller.call(user, "Stiamo. This is a test call. If you can hear this, your call reminders are working.", cfg["callVoice"], 1)
        except ValidationFailed as exc:
            await self._record(who.user_id, False, user, exc.message, "Test call")
            raise
        await self._record(who.user_id, True, user, "", "Test call")
        return {"called": True, "user": user}

    async def test(self, who: Who) -> dict[str, Any]:
        """A sample reminder to the connected chats (and a call, when calls are on), so it can be seen before it is needed."""
        s = await self._load(who)
        card = {"kind": "reminder", "date": "", "headline": "A test reminder", "items": [{"type": "followUp", "title": "Call the advocate"}, {"type": "followUp", "title": "Chase the labour office"}], "footer": "This is how a reminder will look."}
        reached = await self._announce(who, lambda style, limit: render("", [card], limit, "Stiamo", style), None)
        rang = await self._ring(s, call_speech(0, [{"title": "Call the advocate"}, {"title": "Chase the labour office"}]))
        return {"sent": reached, "rang": rang}


def reminder_buttons(key: str, pending: list[dict[str, Any]], app_url: str = "") -> list[list[dict[str, str]]]:
    """Under a reminder: for each call, done and (when it has a number) a button that opens the phone's dialer; and to be reminded again in half
    an hour or have them all moved to tomorrow. The ids fit Telegram's 64 bytes."""
    rows = []
    for c in pending[:4]:
        row = [{"text": f"✅ {str(c.get('title') or 'Call')[:26]}", "callback_data": f"rm:d:{c['id']}"}]
        if call := call_button(app_url, c.get("contact"), str(c.get("title") or "")):
            row.append(call)
        rows.append(row)
    rows.append([{"text": "⏰ In 30 min", "callback_data": f"rm:s:{key}"}, {"text": "📅 Tomorrow", "callback_data": f"rm:t:{key}"}])
    return rows


class ReminderActions:
    """What the buttons under a reminder do: mark a call done, remind again in half an hour, or move the run's calls to tomorrow."""

    SNOOZE = 30

    def __init__(self, settings: SettingsRepository, data_factory: Callable[[str], DataGateway], clock_now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._settings, self._data, self._now = settings, data_factory, clock_now

    async def press(self, who: Who, token: str, action: str, ident: str) -> str:
        """Does it and says what happened, in words for the chat."""
        s = await self._settings.get(who.user_id) or UserSettings(who.user_id)
        local = self._now().astimezone(zone_of(planner_settings(s.planner)["timezone"]))
        day, minute = local.date().isoformat(), local.hour * 60 + local.minute
        data = self._data(token)
        follow_ups = await data.records("follow-ups")
        if action == "d":
            rec = next((f for f in follow_ups if f.get("id") == ident), None)
            if rec is None:
                return "I can't find that call any more."
            if not is_open(rec):
                return "That one is already done."
            tries = [*(rec.get("attempts") or []), {"date": day, "minute": minute, "result": "reached", "channel": rec.get("channel") or "call"}]
            await data.save_record("follow-ups", {**rec, "attempts": tries, "outcome": {**(rec.get("outcome") or {}), "status": "done", "recordedAt": self._now().isoformat(timespec="seconds")}})
            return f"✅ Done: {rec.get('title')}."
        run = next((r for r in nudge_runs(day, follow_ups) if r["key"] == ident), None)
        pending = [c for c in (run or {}).get("calls", []) if is_open(c)]
        if run is None or not pending:
            return "Those calls are done or have moved."
        if action == "s":
            state = dict(s.reminders.get("state") or {})
            runs = dict(state.get("runs") or {}) if state.get("day") == day else {}
            runs[ident] = {**runs.get(ident, {}), "until": minute + self.SNOOZE}
            s.reminders = {**s.reminders, "state": {"day": day, "runs": runs}}
            await self._settings.save(s)
            return f"⏰ I'll remind you again at {clock(minute + self.SNOOZE)}."
        tomorrow = (local.date() + timedelta(days=1)).isoformat()
        for c in pending:
            await data.save_record("follow-ups", {**c, "due": {**(c.get("due") or {}), "date": tomorrow}})
        n = len(pending)
        return f"📅 Moved {n} call{'s' if n != 1 else ''} to tomorrow."


class ReminderManager:
    """The minute-by-minute check, one loop per person."""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[None]] = {}
        #: Seconds between checks: replaced by a test.
        self.interval = 60.0

    def running(self, user_id: str) -> bool:
        return user_id in self._tasks

    def ensure(self, user_id: str, check: Callable[[], Awaitable[object]]) -> None:
        self.stop(user_id)
        self._tasks[user_id] = asyncio.get_running_loop().create_task(self._loop(user_id, check))

    def stop(self, user_id: str) -> None:
        t = self._tasks.pop(user_id, None)
        if t:
            t.cancel()

    async def close(self) -> None:
        tasks = list(self._tasks.values())
        for uid in list(self._tasks):
            self.stop(uid)
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t

    async def _loop(self, user_id: str, check: Callable[[], Awaitable[object]]) -> None:
        while True:
            await asyncio.sleep(self.interval)
            try:
                await check()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001  one failed minute must not end the reminders
                log.exception("the reminder check for %s failed", user_id)
