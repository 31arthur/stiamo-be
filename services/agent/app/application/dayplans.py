"""The evening plan: the daily planner is asked to plan tomorrow, what it proposes is kept until the owner approves it, and approving it books the time blocks."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from stiamo_common.errors import Conflict, NotFound, ValidationFailed

from app.application.availdata import AvailabilityData
from app.application.ports import DataGateway, DayPlan, DayPlanRepository, SettingsRepository, UserSettings
from app.application.services import AgentService, Who
from app.domain.agents import WORKSPACES
from app.domain.dayplan import (
    busy_blocks,
    call_only_blocks,
    check_blocks,
    clock,
    day_label,
    hearings_on,
    is_open,
    meetings_on,
)
from app.domain.dial import phone_text
from app.domain.llm import AiError
from app.domain.mailwatch import NAMES
from app.domain.phone import Style, render

log = logging.getLogger(__name__)

PLANNER = "daily-planner"
#: Most days end at nine in the evening, so that is when tomorrow gets planned.
DEFAULT_TIME = "21:00"
DEFAULT_ZONE = "Asia/Kolkata"
STATUSES = ("proposed", "applied", "dismissed")
#: The workspaces that plan their own day each evening, unless the person chose others. An empty choice is one plan across everything.
PLAN_WORKSPACES = ("personal", "sb", "fsa")


def planner_settings(raw: dict[str, Any] | None) -> dict[str, Any]:
    """What is set for the evening run, with the defaults filled in."""
    p = raw or {}
    chosen = p.get("workspaces")
    workspaces = [w for w in (chosen if isinstance(chosen, list) else PLAN_WORKSPACES) if w in WORKSPACES]
    return {"enabled": bool(p.get("enabled")), "time": p.get("time") or DEFAULT_TIME, "timezone": p.get("timezone") or DEFAULT_ZONE, "channels": p.get("channels", True) is not False, "workspaces": workspaces}


def plan_buttons(plan_id: str) -> list[list[dict[str, str]]]:
    """Under a day plan on the phone: approve it (books the times, and Google's calendar gets them too) or drop it. 64 bytes is Telegram's limit."""
    return [[{"text": "✅ Approve", "callback_data": f"pl:a:{plan_id}"}, {"text": "✖️ Not this", "callback_data": f"pl:x:{plan_id}"}]]


def approval_text(out: dict[str, Any]) -> str:
    """What happened when a plan was approved, in full: what is on the planner, what reached Google, what was left out and what to do next."""
    p = out["plan"]
    ws = NAMES.get(p.get("workspace") or "", "")
    n = out["booked"]
    lines = [f"✅ Plan approved{' · ' + ws if ws else ''} · {day_label(p['day'])}", ""]
    if n:
        lines.append(f"🗓️ {n} item{'s are' if n != 1 else ' is'} now on your planner:")
        lines += [f"• {b['from']}–{b['to']}  {'📞' if b['kind'] == 'followUp' else '📝'} {b['title']}" + (f" · {phone_text(b.get('contact'))}" if phone_text(b.get("contact")) else "") for b in sorted(p["blocks"], key=lambda b: b["start"])]
    else:
        lines.append("🗓️ Nothing was booked: everything in it was finished or removed since the plan was made.")
    if out.get("skipped") and n:
        k = out["skipped"]
        lines += ["", f"ℹ️ {k} item{'s were' if k != 1 else ' was'} left out: finished or removed since the plan was made."]
    cal = out.get("calendar")
    if n:
        sent = (cal or {}).get("sent") or {}
        errors = (cal or {}).get("errors") or []
        lines.append("")
        if cal is None:
            lines.append("⚠️ Google Calendar didn't answer just now. Your times are saved here and will reach Google by themselves within a few minutes.")
        elif sent.get("added") or sent.get("changed"):
            lines.append(f"📆 Google Calendar updated: {sent.get('added', 0)} added, {sent.get('changed', 0)} changed.")
        elif not errors:
            lines.append("📆 Google Calendar was already up to date, or this workspace has no calendar set to receive its schedule (Calendar → “Send my schedule here”).")
        if errors:
            lines.append(f"⚠️ Google said: {str(errors[0])[:140]}")
    lines += ["", "👉 Nothing more to do. Mark things done as you go, here or in Stiamo, and say “plan tomorrow” whenever you want a fresh plan."]
    return "\n".join(lines)


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
        announce: Callable[[Who, Callable[[Style, int], str], str, str, list[dict[str, Any]]], Awaitable[object]] | None = None,
        clock_now: Callable[[], datetime] = lambda: datetime.now(UTC),
        ask: Callable[[Who, str, str, str], Awaitable[int]] | None = None,
        editor: Callable[[Who, str, list[str]], Awaitable[Any]] | None = None,
    ) -> None:
        self._plans, self._settings, self._agent, self._data, self._announce, self._now, self._ask = plans, settings, agent, data_factory, announce, clock_now, ask
        self._editor = editor

    # --- what is set ----------------------------------------------------------------------------------------

    async def settings(self, who: Who) -> dict[str, Any]:
        s = await self._settings.get(who.user_id)
        return planner_settings(s.planner if s else None)

    async def set_settings(self, who: Who, enabled: bool, at: str, timezone: str, channels: bool, workspaces: list[str] | None = None) -> dict[str, Any]:
        parse_time(at)
        zone_of(timezone)
        if workspaces is not None and any(w not in WORKSPACES for w in workspaces):
            raise ValidationFailed("That isn't one of your workspaces.")
        s = await self._settings.get(who.user_id) or UserSettings(who.user_id, who.email)
        keep = planner_settings(s.planner)["workspaces"] if workspaces is None else list(dict.fromkeys(workspaces))
        s.planner = {"enabled": enabled, "time": at, "timezone": timezone, "channels": channels, "workspaces": keep}
        await self._settings.save(s)
        return planner_settings(s.planner)

    # --- making a plan -------------------------------------------------------------------------------------

    def _view(self, p: DayPlan) -> dict[str, Any]:
        iso = lambda d: d.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z") if d else None  # noqa: E731
        return {"id": p.id, "day": p.day, "status": p.status, "briefing": p.briefing, "blocks": [{**b, "from": clock(b["start"]), "to": clock(b["start"] + b["duration"])} for b in p.blocks], "workspace": p.workspace, "trigger": p.trigger, "card": p.card, "runId": p.run_id, "createdAt": iso(p.created_at), "decidedAt": iso(p.decided_at)}

    async def list(self, who: Who, limit: int = 24, workspace: str | None = None) -> dict[str, Any]:
        return {"settings": await self.settings(who), "plans": [self._view(p) for p in await self._plans.recent(who.user_id, limit, workspace)]}

    async def generate(self, who: Who, token: str, *, day: str | None = None, trigger: str = "manual", force: bool = False, announce: bool = False, workspace: str = "") -> dict[str, Any]:
        """Asks the daily planner to plan the day (tomorrow, unless told), and keeps what it proposes. With a `workspace` it plans that workspace's
        own work around everything else on the day. A plan already approved is kept unless `force`."""
        if workspace and workspace not in WORKSPACES:
            raise ValidationFailed("That isn't one of your workspaces.")
        zone = (await self.settings(who))["timezone"]
        day = day or tomorrow_in(self._now(), zone)
        try:
            date.fromisoformat(day)
        except ValueError as exc:
            raise ValidationFailed("Give the day as YYYY-MM-DD.") from exc
        existing = await self._plans.for_day(who.user_id, day, workspace)
        if existing and existing.status == "applied" and not force:
            raise Conflict("That day's plan was already approved. Ask for a new one to replace it.")
        scope = " for the " + NAMES.get(workspace, workspace) + " workspace only: plan its tasks and calls, around everything else already on the day" if workspace else ""
        run = await self._agent.chat(who, token, PLANNER, f"Plan {day}{scope}. Give me the briefing, and propose the schedule for the work that fits.", channel="the evening plan", trigger=trigger, plan_for=workspace or None)
        schedule = next((f for f in reversed(run.found) if f.get("kind") == "schedule" and f.get("date") == day), None)
        blocks = [{k: b[k] for k in ("kind", "id", "title", "start", "duration", "contact") if k in b} for b in (schedule or {}).get("blocks", [])]
        card = next((f for f in reversed(run.found) if f.get("kind") == "briefing" and f.get("date") == day), None)
        plan = DayPlan(existing.id if existing else str(uuid.uuid4()), who.user_id, day, "proposed", run.answer or "", blocks, None, trigger, existing.created_at if existing else self._now(), card=card, workspace=workspace)
        await self._plans.save(plan)
        if announce and self._announce and (await self.settings(who))["channels"]:
            try:
                await self._announce(who, self._message(plan), workspace, plan.id, [b for b in plan.blocks if b["kind"] == "followUp"])
            except Exception:  # noqa: BLE001  telling a phone must never undo a plan that was made
                log.exception("could not send the plan for %s", day)
        return self._view(plan)

    async def evening(self, who: Who, token: str) -> list[dict[str, Any]]:
        """The evening run: each chosen workspace plans tomorrow (one across everything when none is chosen), and each plan goes to that
        workspace's own bot. A workspace with nothing to plan is left out, one that fails doesn't stop the others, and a day already
        approved is left as it is."""
        chosen = (await self.settings(who))["workspaces"] or [""]
        made: list[dict[str, Any]] = []
        failed: Exception | None = None
        for ws in chosen:
            try:
                if ws and not await self._has_work(who, token, ws):
                    continue
                if ws and self._ask is not None:
                    try:  # events that might leave time for calls are asked about first, as short questions on the workspace's bot
                        await self._ask(who, token, tomorrow_in(self._now(), (await self.settings(who))["timezone"]), ws)
                    except Exception:  # noqa: BLE001  not asking is no reason not to plan
                        log.exception("could not ask about the day for %s", ws)
                made.append(await self.generate(who, token, trigger="evening", force=False, announce=True, workspace=ws))
            except Conflict:
                continue
            except Exception as exc:  # noqa: BLE001
                failed = exc
                log.exception("the evening plan for %s failed", ws or "everything")
        if self._editor is not None:
            try:  # then the story editor: a quiet story's next question, and on its day the writers' room
                await self._editor(who, token, [w for w in chosen if w])
            except Exception:  # noqa: BLE001  the plans went out; the editor failing is not theirs
                log.exception("the story editor failed")
        if failed is not None and not made:
            raise failed
        return made

    async def _has_work(self, who: Who, token: str, workspace: str) -> bool:
        """Whether a workspace has anything open to plan, so an empty one doesn't cost a model run each evening."""
        data = self._data(token)
        try:
            tasks, follow_ups, meetings, hearings = await data.records("tasks"), await data.records("follow-ups"), await data.records("meetings"), await data.hearings()
        except AiError:
            return True  # can't tell: plan anyway
        day = tomorrow_in(self._now(), (await self.settings(who))["timezone"])
        own = lambda r: is_open(r) and r.get("workspace", workspace) == workspace  # noqa: E731
        return any(own(r) for r in (*tasks, *follow_ups)) or any(own(m) for m in meetings_on(day, meetings)) or (workspace == "sb" and bool(hearings_on(day, hearings)))

    @staticmethod
    def _message(p: DayPlan) -> Callable[[Style, int], str]:
        """The evening plan, written for whichever chat it is sent to."""
        found: list[dict[str, Any]] = [p.card] if p.card else [{"kind": "briefing", "date": p.day, "headline": "", "sections": [], "free": []}]
        if p.blocks:
            found.append({"kind": "schedule", "buttons": True, "blocks": [{"from": clock(b["start"]), "title": b["title"], "phone": phone_text(b.get("contact"))} for b in sorted(p.blocks, key=lambda b: b["start"])]})
        title = f"{NAMES.get(p.workspace, p.workspace)} plan for" if p.workspace else "Plan for"
        return lambda style, limit: render(p.briefing, found, limit, "Daily planner", style, title=title)

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
        data = AvailabilityData(self._data(token), (await self._settings.get(who.user_id) or UserSettings(who.user_id)).availability)  # (what was said about time inside events counts here too)
        try:
            tasks, follow_ups = await data.records("tasks"), await data.records("follow-ups")
            hearings, meetings = await data.hearings(), await data.records("meetings")
            calendar = await data.calendar_events(p.day, p.day)
        except AiError as exc:
            raise ValidationFailed(str(exc)) from exc
        skip = {f"{b['kind']}:{b['id']}" for b in p.blocks}
        problems = check_blocks(p.blocks, busy_blocks(p.day, hearings, meetings, tasks, follow_ups, skip=skip, calendar=calendar), call_only_blocks(p.day, meetings, calendar))
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
        # Booked in Stiamo; now Google's calendar for this workspace should hold it too, without waiting for the next background sync.
        calendar = await data.calendar_sync() if booked else None
        return {"plan": self._view(p), "booked": booked, "skipped": skipped, "calendar": calendar}

    async def dismiss(self, who: Who, plan_id: str) -> dict[str, Any]:
        p = await self._mine(who, plan_id)
        if p.status != "proposed":
            raise Conflict("That plan has already been dealt with.")
        p.status, p.decided_at = "dismissed", self._now()
        await self._plans.save(p)
        return self._view(p)
