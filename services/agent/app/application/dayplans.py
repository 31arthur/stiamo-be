"""The evening plan: the daily planner is asked to plan tomorrow, what it proposes is kept until the owner approves it, and approving it books the time blocks."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from stiamo_common.errors import Conflict, NotFound, ValidationFailed

from app.application.ports import DataGateway, DayPlan, DayPlanRepository, SettingsRepository, UserSettings
from app.application.services import AgentService, Who
from app.domain.dayplan import busy_blocks, check_blocks, clock, is_open
from app.domain.llm import AiError
from app.domain.phone import Style, render

log = logging.getLogger(__name__)

PLANNER = "daily-planner"
#: Most days end at nine in the evening, so that is when tomorrow gets planned.
DEFAULT_TIME = "21:00"
DEFAULT_ZONE = "Asia/Kolkata"
STATUSES = ("proposed", "applied", "dismissed")


def planner_settings(raw: dict[str, Any] | None) -> dict[str, Any]:
    """What is set for the evening run, with the defaults filled in."""
    p = raw or {}
    return {"enabled": bool(p.get("enabled")), "time": p.get("time") or DEFAULT_TIME, "timezone": p.get("timezone") or DEFAULT_ZONE, "channels": p.get("channels", True) is not False}


def zone_of(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
        raise ValidationFailed("That isn't a time zone the service knows. Use a name like Asia/Kolkata.") from exc


def parse_time(value: str) -> tuple[int, int]:
    try:
        h, m = value.split(":")
        hh, mm = int(h), int(m)
    except ValueError as exc:
        raise ValidationFailed("Give the time as hours and minutes, like 21:00.") from exc
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValidationFailed("Give the time as hours and minutes, like 21:00.")
    return hh, mm


def seconds_until(now: datetime, at: str, zone: str) -> float:
    """How long until the next time the clock in `zone` shows `at`."""
    z = zone_of(zone)
    hh, mm = parse_time(at)
    local = now.astimezone(z)
    target = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if target <= local:
        target = (local + timedelta(days=1)).replace(hour=hh, minute=mm, second=0, microsecond=0)
    return (target - local).total_seconds()


def tomorrow_in(now: datetime, zone: str) -> str:
    return (now.astimezone(zone_of(zone)).date() + timedelta(days=1)).isoformat()


class DayPlanService:
    def __init__(
        self,
        plans: DayPlanRepository,
        settings: SettingsRepository,
        agent: AgentService,
        data_factory: Callable[[str], DataGateway],
        announce: Callable[[Who, Callable[[Style, int], str]], Awaitable[object]] | None = None,
        clock_now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._plans, self._settings, self._agent, self._data, self._announce, self._now = plans, settings, agent, data_factory, announce, clock_now

    # --- what is set ----------------------------------------------------------------------------------------

    async def settings(self, who: Who) -> dict[str, Any]:
        s = await self._settings.get(who.user_id)
        return planner_settings(s.planner if s else None)

    async def set_settings(self, who: Who, enabled: bool, at: str, timezone: str, channels: bool) -> dict[str, Any]:
        parse_time(at)
        zone_of(timezone)
        s = await self._settings.get(who.user_id) or UserSettings(who.user_id, who.email)
        s.planner = {"enabled": enabled, "time": at, "timezone": timezone, "channels": channels}
        await self._settings.save(s)
        return planner_settings(s.planner)

    # --- making a plan -------------------------------------------------------------------------------------

    def _view(self, p: DayPlan) -> dict[str, Any]:
        iso = lambda d: d.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z") if d else None  # noqa: E731
        return {"id": p.id, "day": p.day, "status": p.status, "briefing": p.briefing, "blocks": [{**b, "from": clock(b["start"]), "to": clock(b["start"] + b["duration"])} for b in p.blocks], "trigger": p.trigger, "card": p.card, "runId": p.run_id, "createdAt": iso(p.created_at), "decidedAt": iso(p.decided_at)}

    async def list(self, who: Who, limit: int = 7) -> dict[str, Any]:
        return {"settings": await self.settings(who), "plans": [self._view(p) for p in await self._plans.recent(who.user_id, limit)]}

    async def generate(self, who: Who, token: str, *, day: str | None = None, trigger: str = "manual", force: bool = False, announce: bool = False) -> dict[str, Any]:
        """Asks the daily planner to plan the day (tomorrow, unless told), and keeps what it proposes. A plan already approved is kept unless `force`."""
        zone = (await self.settings(who))["timezone"]
        day = day or tomorrow_in(self._now(), zone)
        try:
            date.fromisoformat(day)
        except ValueError as exc:
            raise ValidationFailed("Give the day as YYYY-MM-DD.") from exc
        existing = await self._plans.for_day(who.user_id, day)
        if existing and existing.status == "applied" and not force:
            raise Conflict("That day's plan was already approved. Ask for a new one to replace it.")
        run = await self._agent.chat(who, token, PLANNER, f"Plan {day}. Give me the briefing, and propose the schedule for the work that fits.", channel="the evening plan", trigger=trigger)
        schedule = next((f for f in reversed(run.found) if f.get("kind") == "schedule" and f.get("date") == day), None)
        blocks = [{k: b[k] for k in ("kind", "id", "title", "start", "duration")} for b in (schedule or {}).get("blocks", [])]
        card = next((f for f in reversed(run.found) if f.get("kind") == "briefing" and f.get("date") == day), None)
        plan = DayPlan(existing.id if existing else str(uuid.uuid4()), who.user_id, day, "proposed", run.answer or "", blocks, None, trigger, existing.created_at if existing else self._now(), card=card)
        await self._plans.save(plan)
        if announce and self._announce and (await self.settings(who))["channels"]:
            try:
                await self._announce(who, self._message(plan))
            except Exception:  # noqa: BLE001  telling a phone must never undo a plan that was made
                log.exception("could not send the plan for %s", day)
        return self._view(plan)

    @staticmethod
    def _message(p: DayPlan) -> Callable[[Style, int], str]:
        """The evening plan, written for whichever chat it is sent to."""
        found: list[dict[str, Any]] = [p.card] if p.card else [{"kind": "briefing", "date": p.day, "headline": "", "sections": [], "free": []}]
        if p.blocks:
            found.append({"kind": "schedule", "blocks": [{"from": clock(b["start"]), "title": b["title"]} for b in sorted(p.blocks, key=lambda b: b["start"])]})
        return lambda style, limit: render(p.briefing, found, limit, "Daily planner", style, title="Plan for")

    # --- deciding ------------------------------------------------------------------------------------------

    async def _mine(self, who: Who, plan_id: str) -> DayPlan:
        p = await self._plans.get(who.user_id, plan_id)
        if p is None:
            raise NotFound("No such plan.")
        return p

    async def apply(self, who: Who, token: str, plan_id: str) -> dict[str, Any]:
        """Books the proposed blocks: a time on each task, a time on each call. Checked again first, in case the day changed since."""
        p = await self._mine(who, plan_id)
        if p.status != "proposed":
            raise Conflict("That plan has already been dealt with.")
        data = self._data(token)
        try:
            tasks, follow_ups = await data.records("tasks"), await data.records("follow-ups")
            hearings, meetings = await data.hearings(), await data.records("meetings")
            calendar = await data.calendar_events(p.day, p.day)
        except AiError as exc:
            raise ValidationFailed(str(exc)) from exc
        skip = {f"{b['kind']}:{b['id']}" for b in p.blocks}
        problems = check_blocks(p.blocks, busy_blocks(p.day, hearings, meetings, tasks, follow_ups, skip=skip, calendar=calendar))
        if problems:
            raise Conflict("The day has changed since this was planned: " + " ".join(problems[:3]) + " Ask for a new plan.")
        by = {"task": {t["id"]: t for t in tasks if is_open(t)}, "followUp": {f["id"]: f for f in follow_ups if is_open(f)}}
        booked, skipped = 0, 0
        try:
            for b in p.blocks:
                record = by[b["kind"]].get(b["id"])
                if record is None:
                    skipped += 1
                    continue
                if b["kind"] == "task":
                    await data.save_record("tasks", {**record, "slot": {"date": p.day, "start": b["start"], "duration": b["duration"]}})
                else:
                    await data.save_record("follow-ups", {**record, "due": {"date": p.day, "time": b["start"]}, "duration": b["duration"]})
                booked += 1
        except AiError as exc:
            raise ValidationFailed(str(exc)) from exc
        p.status, p.decided_at = "applied", self._now()
        await self._plans.save(p)
        return {"plan": self._view(p), "booked": booked, "skipped": skipped}

    async def dismiss(self, who: Who, plan_id: str) -> dict[str, Any]:
        p = await self._mine(who, plan_id)
        if p.status != "proposed":
            raise Conflict("That plan has already been dealt with.")
        p.status, p.decided_at = "dismissed", self._now()
        await self._plans.save(p)
        return self._view(p)
