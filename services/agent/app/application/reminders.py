"""Reminders about calls that weren't marked done: a message on the phone, and a spoken one on Telegram.

One loop per person, held in memory and started again with the service from everyone who has the reminders on. Each minute it looks at
today's timed calls; when a run of them has ended and some are still open, and nothing else is on, it reminds (see domain/nudge.py for when).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from stiamo_common.errors import ValidationFailed

from app.application.dayplans import planner_settings, zone_of
from app.application.ports import CallApi, DataGateway, SettingsRepository, UserSettings, VoiceApi
from app.application.services import Who
from app.domain.dayplan import clock, hearings_on
from app.domain.nudge import DEFAULT_CALL_VOICE, DEFAULT_VOICE, call_speech, due_run, free_now, speech
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
        "voice": r.get("voice", True) is not False,
        "model": r.get("model") or DEFAULT_VOICE,
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
        voice: VoiceApi,
        caller: CallApi,
        announce: Callable[[Who, Callable[[Style, int], str]], Awaitable[int]],
        announce_voice: Callable[[Who, bytes, str], Awaitable[int]],
        clock_now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._settings, self._data, self._tokens, self._voice, self._caller, self._announce, self._announce_voice, self._now = settings, data_factory, tokens, voice, caller, announce, announce_voice, clock_now

    async def _load(self, who: Who) -> UserSettings:
        s = await self._settings.get(who.user_id) or UserSettings(who.user_id)
        s.email = who.email or s.email
        return s

    # --- what is set ---------------------------------------------------------------------------------------

    async def view(self, who: Who) -> dict[str, Any]:
        s = await self._load(who)
        key = s.keys.get(VOICE_KEY)
        return {**reminder_settings(s.reminders), "voiceKey": {"connected": bool(key)}, "timezone": planner_settings(s.planner)["timezone"]}

    async def set_settings(self, who: Who, enabled: bool, voice: bool, call: bool = False, call_user: str = "", call_voice: str = "") -> dict[str, Any]:
        user = call_user.strip()
        if call and not valid_call_user(user):
            raise ValidationFailed(BAD_USER)
        s = await self._load(who)
        s.reminders = {**s.reminders, "enabled": enabled, "voice": voice, "call": call, "callUser": user, "callVoice": call_voice.strip() or DEFAULT_CALL_VOICE}
        await self._settings.save(s)
        return await self.view(who)

    # --- reminding -------------------------------------------------------------------------------------------

    async def _speak(self, s: UserSettings, text: str) -> bytes | None:
        """The words aloud, when the person has spoken reminders on and a Deepgram key. A voice that fails leaves the written message to do."""
        cfg = reminder_settings(s.reminders)
        key = s.keys.get(VOICE_KEY)
        if not cfg["voice"] or not key:
            return None
        try:
            return await self._voice.speak(key, text, cfg["model"])
        except ValidationFailed:
            log.warning("could not make the voice reminder for %s", s.user_id)
            return None

    async def _ring(self, s: UserSettings, text: str) -> bool:
        """Rings the person on Telegram and reads the reminder, when that is on. A call that can't be placed leaves the written message to do."""
        cfg = reminder_settings(s.reminders)
        if not cfg["call"] or not cfg["callUser"]:
            return False
        try:
            await self._caller.call(cfg["callUser"], text, cfg["callVoice"], 2)
        except ValidationFailed as exc:
            log.warning("could not ring %s: %s", s.user_id, exc)
            return False
        return True

    async def check(self, who: Who) -> dict[str, Any] | None:
        """Reminds now if a reminder is due: returns what was said ({calls, step}), or None. Called every minute, and by hand."""
        s = await self._load(who)
        cfg = reminder_settings(s.reminders)
        if not cfg["enabled"]:
            return None
        local = self._now().astimezone(zone_of(planner_settings(s.planner)["timezone"]))
        day, minute = local.date().isoformat(), local.hour * 60 + local.minute
        data = self._data(self._tokens.mint(who.user_id, who.email))
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
        card = {"kind": "reminder", "date": day, "headline": f"{n} call{'s' if n != 1 else ''} still pending", "items": items, "footer": "Mark them done in Stiamo when you have."}
        await self._announce(who, lambda style, limit: render("", [card], limit, "Stiamo", style))
        audio = await self._speak(s, speech(run["step"], run["pending"]))
        if audio:
            await self._announce_voice(who, audio, f"{n} call{'s' if n != 1 else ''} still pending")
        rang = await self._ring(s, call_speech(run["step"], run["pending"]))
        runs[run["key"]] = {"step": run["step"] + 1, "last": minute}
        s.reminders = {**s.reminders, "state": {"day": day, "runs": runs}}
        await self._settings.save(s)
        return {"calls": [c.get("id") for c in run["pending"]], "step": run["step"] + 1, "spoken": bool(audio), "rang": rang}

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
        await self._caller.call(user, "Stiamo. This is a test call. If you can hear this, your call reminders are working.", cfg["callVoice"], 1)
        return {"called": True, "user": user}

    async def test(self, who: Who) -> dict[str, Any]:
        """A sample reminder to the connected chats, so the voice can be heard before it is needed."""
        s = await self._load(who)
        text = speech(0, [{"title": "Call the advocate"}, {"title": "Chase the labour office"}])
        card = {"kind": "reminder", "date": "", "headline": "A test reminder", "items": [{"type": "followUp", "title": "Call the advocate"}, {"type": "followUp", "title": "Chase the labour office"}], "footer": "This is how a reminder will look."}
        reached = await self._announce(who, lambda style, limit: render("", [card], limit, "Stiamo", style))
        audio = await self._speak(s, text)
        if audio:
            await self._announce_voice(who, audio, "A test reminder")
        rang = await self._ring(s, call_speech(0, [{"title": "Call the advocate"}, {"title": "Chase the labour office"}]))
        return {"sent": reached, "spoken": bool(audio), "rang": rang}


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
