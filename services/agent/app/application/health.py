"""Setup and health: every connection the app depends on, with whether it is working and what to do if not.

Each check says ok, warn (works, but something is missing), error (will not work) or off (not set up, and fine without). A check that can be tried
names the test to run. Nothing here changes anything: it reads what is set and what has happened (the delivery log, the watches).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from app.application.ports import DataGateway, DeliveryLog, SettingsRepository, UserSettings
from app.application.reminders import VOICE_KEY, reminder_settings, valid_call_user
from app.application.services import Who
from app.domain.agents import WORKSPACES
from app.domain.llm import PROVIDER_IDS, AiError
from app.domain.mailwatch import NAMES

STATES = ("error", "warn", "ok", "off")


def check(id: str, group: str, label: str, state: str, detail: str, fix: str | None = None, test: dict[str, str] | None = None) -> dict[str, Any]:
    return {"id": id, "group": group, "label": label, "state": state, "detail": detail, "fix": fix, "test": test}


class HealthService:
    def __init__(
        self,
        settings: SettingsRepository,
        agents: Any,
        channels: Any,
        watch: Any,
        deliveries: DeliveryLog,
        data_factory: Callable[[str], DataGateway],
        public_url: str,
        app_url: str,
        clock_now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._settings, self._agents, self._channels, self._watch, self._deliveries, self._data = settings, agents, channels, watch, deliveries, data_factory
        self._public, self._app, self._now = public_url, app_url, clock_now

    async def run(self, who: Who, token: str) -> dict[str, Any]:
        s = await self._settings.get(who.user_id) or UserSettings(who.user_id)
        data = self._data(token)
        try:
            accounts = await data.mail_accounts()
        except AiError:
            accounts = None  # the mail service is away
        items = [*await self._ai(who), *await self._phone(who, s, accounts), *self._mail(accounts), *await self._watches(who, token, accounts), *await self._google(data), *await self._alerts(who, s), *self._server()]
        items.sort(key=lambda i: STATES.index(i["state"]))
        problems = sum(1 for i in items if i["state"] in ("error", "warn"))
        return {"items": items, "problems": problems, "errors": sum(1 for i in items if i["state"] == "error"), "checkedAt": self._now().isoformat(timespec="seconds")}

    # --- the model -----------------------------------------------------------------------------------------------

    async def _ai(self, who: Who) -> list[dict[str, Any]]:
        view = await self._agents.view(who)
        connected = [p for p in PROVIDER_IDS if view["providers"].get(p, {}).get("connected")]
        chat = [a for a in view["agents"] if a.get("kind") != "workflow" or a.get("id", "").startswith("watch-")]
        without = [a["name"] for a in chat if not a.get("selection")]
        if not connected:
            ai = check("model", "AI", "AI model", "error", "No AI provider is connected, so no agent can answer or read mail.", "/agents/settings")
        elif without:
            ai = check("model", "AI", "AI model", "warn", f"{', '.join(connected)} connected, but {len(without)} agent{'s' if len(without) != 1 else ''} {'have' if len(without) != 1 else 'has'} no model yet ({', '.join(without[:3])}{'…' if len(without) > 3 else ''}).", "/agents/settings")
        else:
            ai = check("model", "AI", "AI model", "ok", f"{', '.join(connected)} connected; every agent has a model.")
        dec = view.get("decision") or {}
        jev = check("jev", "AI", "Decision model (Jev)", "ok" if dec.get("enabled") else "off", "Chooses the right agent for each message." if dec.get("enabled") else "Off: the assistant picks agents by keywords instead. Turn it on under Providers and models.", None if dec.get("enabled") else "/agents/settings")
        return [ai, jev]

    # --- the phone -------------------------------------------------------------------------------------------------

    async def _phone(self, who: Who, s: UserSettings, accounts: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        channels = await self._channels.all_for(who)
        bots = [c for c in channels if c["kind"] == "telegram"]
        out: list[dict[str, Any]] = []
        if not bots:
            out.append(check("telegram", "Phone", "Telegram", "error", "No Telegram bot is connected, so alerts, reminders and plans can't reach you.", "phone"))
        for c in bots:
            ws = c.get("workspace")
            purpose = {"mail": " · mail alerts", "planner": " · daily plan"}.get(c.get("role") or "", "")
            label = f"Telegram · {NAMES.get(ws, ws)}{purpose}" + ("" if purpose else " assistant") if ws else "Telegram · general bot"
            sees = f"Sees only {NAMES.get(ws, ws)}." if ws else "Sees every workspace: tasks, plans and reminders of all of them. Limit it to one workspace if it is for one."
            paired = len(c.get("paired") or [])
            if paired:
                out.append(check(f"tg-{c['id']}", "Phone", label, "ok", f"@{c.get('username', '')} · {paired} chat{'s' if paired != 1 else ''} linked. {sees}", None, {"kind": "channel", "id": c["id"]}))
            else:
                out.append(check(f"tg-{c['id']}", "Phone", label, "warn", f"@{c.get('username', '')} is connected but no chat is linked. Press Start in the bot (or send it the code), and it will answer.", "phone"))
        have_ws = {c.get("workspace") for c in bots if c.get("paired")}
        general = any(not c.get("workspace") and c.get("paired") for c in bots)
        for ws in sorted({a.get("workspace") for a in accounts or [] if a.get("workspace") in WORKSPACES}):
            if ws not in have_ws:
                out.append(check(f"ws-{ws}", "Phone", f"{NAMES[ws]} alerts", "ok" if general else "error", f"No {NAMES[ws]} bot of its own: its alerts go to your general bot." if general else f"No Telegram chat is linked for {NAMES[ws]}, so its alerts have nowhere to go.", "phone"))
        key = s.keys.get(VOICE_KEY)
        out.append(check("deepgram", "Phone", "Voice (Deepgram)", "ok" if key else "off", "Key set. Voice notes and spoken reminders can be used." if key else "Not set. Needed only for voice notes and spoken reminders (Developer → Environment).", None if key else "/agents/environment", {"kind": "secret", "id": "deepgram"} if key else None))
        cfg = reminder_settings(s.reminders)
        if cfg["call"]:
            tried = [d for d in await self._deliveries.recent(who.user_id, 200) if d.kind == "call"]
            worked = any(d.ok for d in tried)
            if not valid_call_user(cfg["callUser"]):
                out.append(check("call", "Phone", "Telegram call", "error", "Calls are on, but there is no username or number to ring.", "phone"))
            else:
                out.append(check("call", "Phone", "Telegram call", "ok" if worked else "warn", f"Rings {cfg['callUser']}." if worked else f"Rings {cfg['callUser']}, but no call has gone through yet. Send /start to @CallMeBot_txtbot, allow calls in Telegram, then test it.", None if worked else "phone", {"kind": "call", "id": "call"}))
        else:
            out.append(check("call", "Phone", "Telegram call", "off", "Off. Switch it on under Call reminders to be rung about priority mail."))
        return out

    # --- mail ------------------------------------------------------------------------------------------------------------

    @staticmethod
    def _mail(accounts: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        if accounts is None:
            return [check("mail", "Mail", "Mail service", "error", "The mail service didn't answer, so mail can't be read or watched.")]
        if not accounts:
            return [check("mail", "Mail", "Mailboxes", "warn", "No mailbox is connected yet. Connect one under Mail to have it watched.", "/mail")]
        return [
            check(f"box-{a['id']}", "Mail", f"Mailbox · {a.get('label') or a.get('address')}", "error" if a.get("error") else "ok", a["error"] if a.get("error") else f"{a.get('address', '')} · {NAMES.get(a.get('workspace'), a.get('workspace'))}", "/mail" if a.get("error") else None, {"kind": "mailbox", "id": str(a["id"])})
            for a in accounts
        ]

    async def _watches(self, who: Who, token: str, accounts: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        if not accounts:
            return []
        out = []
        for w in (await self._watch.view(who, token))["workspaces"]:
            label = f"Mail watch · {w['name']}"
            if not w["enabled"]:
                out.append(check(f"watch-{w['workspace']}", "Mail", label, "off", "Paused."))
            elif not w["model"]:
                out.append(check(f"watch-{w['workspace']}", "Mail", label, "error", "It has no model to read mail with.", f"/agents/{w['agentId']}/settings"))
            elif w["error"]:
                out.append(check(f"watch-{w['workspace']}", "Mail", label, "error", str(w["error"])[:160], f"/agents/{w['agentId']}"))
            elif not w["listening"]:
                out.append(check(f"watch-{w['workspace']}", "Mail", label, "warn", "Starting: it isn't listening to the mailbox yet.", f"/agents/{w['agentId']}"))
            else:
                out.append(check(f"watch-{w['workspace']}", "Mail", label, "ok", f"Listening to {len(w['mailboxes'])} mailbox{'es' if len(w['mailboxes']) != 1 else ''}."))
        return out

    # --- Google: the calendar and the contacts -----------------------------------------------------------------------

    @staticmethod
    async def _google(data: DataGateway) -> list[dict[str, Any]]:
        links = await data.google_links()
        out: list[dict[str, Any]] = []
        for key, label, page, why in (
            ("calendar", "Google Calendar", "/calendar", "the day plan and Today can't see your meetings"),
            ("contacts", "Google Contacts", "/contacts", "tasks and follow-ups can't pick a contact"),
        ):
            if key not in links:
                continue
            status = links[key]
            if status is None:
                out.append(check(key, "Google", label, "warn", f"The calendar service didn't answer, so {why}."))
                continue
            accounts = status.get("accounts") or []
            if not status.get("configured"):
                out.append(check(key, "Google", label, "off", "Google sign-in isn't set up on the server yet (the Google client id and secret). See the setup guide."))
            elif not accounts:
                out.append(check(key, "Google", label, "off", f"No Google account is connected, so {why}.", page))
            else:
                broken = [a for a in accounts if a.get("status") != "ok"]
                empty = [a for a in accounts if a.get("status") == "ok" and key == "contacts" and not a.get("contacts")]
                names = ", ".join(sorted({a.get("email", "") for a in broken}))
                if broken:
                    out.append(check(key, "Google", label, "error", f"{names} needs to be connected again" + (f": {str(broken[0].get('error'))[:120]}" if broken[0].get("error") else "."), page))
                elif empty:
                    out.append(check(key, "Google", label, "warn", f"{empty[0].get('email', '')} is connected but no contacts were read. Check that the People API is enabled in Google Cloud, then sync again.", page))
                elif key == "contacts":
                    out.append(check(key, "Google", label, "ok", f"{sum(int(a.get('contacts') or 0) for a in accounts)} contacts from {len(accounts)} account{'s' if len(accounts) != 1 else ''}."))
                else:
                    shown = sum(1 for a in accounts for c in a.get("calendars") or [] if c.get("enabled"))
                    out.append(check(key, "Google", label, "ok", f"{len(accounts)} account{'s' if len(accounts) != 1 else ''}, {shown} calendar{'s' if shown != 1 else ''} shown."))
                    out += HealthService._sending(status, accounts)
        return out

    @staticmethod
    def _sending(status: dict[str, Any], accounts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Whether each workspace with a Google account has somewhere its schedule goes, so a plan you approve also lands in Google."""
        if status.get("sends") is False:
            return [check("calendar-send", "Google", "Schedule to Google", "warn", "The calendar service can't act for you yet, so nothing is sent to Google. Plans you approve stay in Stiamo.", "/calendar")]
        out: list[dict[str, Any]] = []
        for ws in sorted({a.get("workspace") for a in accounts if a.get("status") == "ok" and a.get("workspace")}):
            mine = [a for a in accounts if a.get("workspace") == ws]
            sends = any(c.get("export") for a in mine for c in a.get("calendars") or [])
            name = NAMES.get(ws, ws)
            if sends:
                out.append(check(f"calendar-send-{ws}", "Google", f"{name} schedule to Google", "ok", "Its meetings, tasks, calls and approved plans are added to Google."))
            else:
                out.append(check(f"calendar-send-{ws}", "Google", f"{name} schedule to Google", "warn", f"{name} has a Google account but nowhere to send its schedule. In Calendars, press “Send my schedule here” on one of its calendars.", "/calendar"))
        return out

    # --- did it arrive ---------------------------------------------------------------------------------------------------

    async def _alerts(self, who: Who, s: UserSettings) -> list[dict[str, Any]]:
        counts = await self._deliveries.counts(who.user_id, self._now() - timedelta(hours=24))
        if counts["failed"]:
            return [check("deliveries", "Delivery", "Messages to your phone", "warn", f"{counts['failed']} of the last day's {counts['sent'] + counts['failed']} messages didn't arrive. See the log below for why.", "log", {"kind": "alert", "id": "sample"})]
        detail = f"{counts['sent']} sent in the last day, all delivered." if counts["sent"] else "Nothing sent in the last day."
        return [check("deliveries", "Delivery", "Messages to your phone", "ok", detail, None, {"kind": "alert", "id": "sample"})]

    def _server(self) -> list[dict[str, Any]]:
        return [
            check("public-url", "Server", "Telegram can reach the server", "ok" if self._public.startswith("https://") else "error", "Webhooks point at the server's https address." if self._public.startswith("https://") else "AGENT_PUBLIC_URL isn't an https address, so Telegram can't send messages to the bot."),
            check("app-url", "Server", "Link in alerts", "ok" if self._app.startswith("https://") else "warn", f"Alerts open the email at {self._app}." if self._app.startswith("https://") else "AGENT_APP_URL isn't set, so alerts have no “Open the email” button."),
        ]
