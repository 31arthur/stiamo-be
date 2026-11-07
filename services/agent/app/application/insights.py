"""Insights across mail, news and the practice's own work, for one workspace or all of them.

The numbers come straight from what the agents have kept (their ledgers) and from the practice's records, so they cost nothing and are always
there. A written digest on top of them is made on request with the person's own model, and kept for the day so asking again is free.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from stiamo_common.errors import ValidationFailed

from app.application.dayplans import planner_settings, zone_of
from app.application.ports import (
    DataGateway,
    LedgerEntry,
    LlmClient,
    SettingsRepository,
    UserSettings,
    WorkflowRepository,
)
from app.application.scope import ScopedData
from app.application.services import Who, selection_of
from app.domain.agents import WORKSPACES
from app.domain.insights import connections, mail_block, news_block, work_block
from app.domain.llm import AiError, Message

_MAIL_ID = re.compile(r"^[^:]+:\d+:\d+$")
DAYS = (7, 30, 90)
#: Entries read for a period: plenty for a busy inbox, and a bound on what one request costs.
MAX_ENTRIES = 20000


def _entry(e: LedgerEntry) -> dict[str, Any]:
    return {"agent_id": e.agent_id, "item_id": e.item_id, "title": e.title, "excerpt": e.excerpt, "label": e.label, "status": e.status, "created_at": e.created_at.isoformat() if e.created_at else None, "data": e.data or {}}


class InsightService:
    def __init__(
        self,
        workflows: WorkflowRepository,
        settings: SettingsRepository,
        data_factory: Callable[[str], DataGateway],
        llm_factory: Callable[[str, str], LlmClient],
        cache: dict[tuple[str, str, int, str], dict[str, Any]],
        clock_now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._workflows, self._settings, self._data, self._llm, self._cache, self._now = workflows, settings, data_factory, llm_factory, cache, clock_now

    async def _load(self, who: Who) -> UserSettings:
        s = await self._settings.get(who.user_id) or UserSettings(who.user_id)
        s.email = who.email or s.email
        return s

    async def compute(self, who: Who, token: str, workspace: str | None, days: int) -> dict[str, Any]:
        if days not in DAYS:
            raise ValidationFailed(f"Choose {', '.join(map(str, DAYS))} days.")
        if workspace is not None and workspace not in WORKSPACES:
            raise ValidationFailed("That isn't one of your workspaces.")
        s = await self._load(who)
        tz = zone_of(planner_settings(s.planner)["timezone"])
        now = self._now().astimezone(tz)
        today = now.date().isoformat()
        data: DataGateway = self._data(token)
        try:
            accounts = await data.mail_accounts()
        except AiError:
            accounts = []
        box_ws = {str(a.get("id")): a.get("workspace") for a in accounts}
        agents = {w.id: w for w in await self._workflows.all(who.user_id)}

        def home(agent_id: str) -> str:
            """The workspace an agent works for: where it was placed, else its mailbox's, else the S&B desk's."""
            placed = s.workspaces.get(agent_id) or []
            if placed:
                return placed[0]
            w = agents.get(agent_id)
            for acc in ((w.config.get("source") or {}).get("accountIds") or []) if w else []:
                if box_ws.get(str(acc)):
                    return str(box_ws[str(acc)])
            return "sb"

        since = (now - timedelta(days=days)).astimezone(UTC)
        rows = [_entry(e) for e in await self._workflows.since(who.user_id, since, MAX_ENTRIES)]
        if workspace:
            rows = [r for r in rows if home(r["agent_id"]) == workspace]
        mail = [r for r in rows if _MAIL_ID.match(r["item_id"])]
        news = [r for r in rows if r["item_id"].startswith("feed:") and r["status"] == "published"]

        scoped: DataGateway = ScopedData(data, workspace) if workspace else data

        async def records(table: str) -> list[dict[str, Any]]:
            try:
                return await scoped.records(table)
            except AiError:
                return []

        try:
            cases, hearings = await scoped.cases(), await scoped.hearings()
        except AiError:
            cases, hearings = [], []
        tasks, follow_ups, meetings = await records("tasks"), await records("follow-ups"), await records("meetings")
        return {
            "workspace": workspace or "all", "days": days, "today": today, "generatedAt": self._now().isoformat(timespec="seconds"),
            "mail": mail_block(mail, today, days, tz), "news": news_block(news, today, days, tz, now), "work": work_block(tasks, follow_ups, meetings, hearings, today, days, tz),
            "connections": connections([r for r in mail if r["status"] in ("queued", "event_proposed")], news, cases, hearings, today),
        }

    # --- the written digest ------------------------------------------------------------------------------------------

    @staticmethod
    def _brief(m: dict[str, Any]) -> dict[str, Any]:
        """What the model is shown: the numbers and the few items behind them, not every row."""
        mail, news, work = m["mail"], m["news"], m["work"]
        return {
            "workspace": m["workspace"], "days": m["days"], "today": m["today"],
            "mail": {k: mail[k] for k in ("total", "needsYou", "urgent", "cleared", "events")} | {"topSenders": mail["topSenders"][:4], "dueSoon": mail["dueSoon"][:5]},
            "news": {"total": news["total"], "topics": news["topics"][:6], "top": news["top"][:4], "byImportance": news["byImportance"]},
            "work": {"tasks": {k: v for k, v in work["tasks"].items() if k != "overdueList"} | {"overdue": work["tasks"]["overdueList"][:4]}, "followUps": work["followUps"], "hearings": work["hearings"], "meetings": work["meetings"], "load": work["load"]},
            "connections": [c["text"] for c in m["connections"][:5]],
        }

    async def digest(self, who: Who, token: str, workspace: str | None, days: int, force: bool = False) -> dict[str, Any]:
        """A short written reading of the numbers: what stands out, and what to do about it. Kept for the day, so asking again costs nothing."""
        metrics = await self.compute(who, token, workspace, days)
        key = (who.user_id, workspace or "all", days, metrics["today"])
        if not force and key in self._cache:
            return {**self._cache[key], "cached": True}
        s = await self._load(who)
        sel, _ = selection_of(s, "insights")
        if not sel or not sel.get("modelId"):
            raise ValidationFailed("Choose a provider and a model first (Developer → an agent → Settings): the digest is written by your own model.")
        system = (
            f"You write a short briefing for {who.name}, who runs a legal and advocacy practice, a software consultancy and their own affairs, from the figures below about their email, news and work. "
            "Say what stands out and what to do about it. Be specific, use only the figures and items given, and never invent a name, number or date. "
            'Answer with JSON only: {"summary": "<three or four plain sentences>", "highlights": ["<up to five short points, each naming a real figure or item>"], "suggestions": ["<up to four concrete next actions>"]}.'
        )
        client = self._llm(sel["providerId"], s.keys[sel["providerId"]])
        reply = await client.chat(model=sel["modelId"], messages=[Message("system", system), Message("user", json.dumps(self._brief(metrics), ensure_ascii=False))], tools=[], thinking=sel.get("thinking", "off"))
        parsed = self._parse(reply.text)
        if parsed is None:
            raise ValidationFailed("The model's digest couldn't be read. Try again.")
        out = {**parsed, "generatedAt": self._now().isoformat(timespec="seconds"), "cached": False}
        self._cache[key] = {k: v for k, v in out.items() if k != "cached"}
        return out

    @staticmethod
    def _parse(text: str) -> dict[str, Any] | None:
        m = re.search(r"\{.*\}", text or "", re.DOTALL)
        if not m:
            return None
        try:
            d = json.loads(m.group(0))
        except ValueError:
            return None
        summary = str(d.get("summary") or "").strip()
        if not summary:
            return None
        listed = lambda v, n: [str(x).strip()[:300] for x in (v if isinstance(v, list) else []) if str(x).strip()][:n]  # noqa: E731
        return {"summary": summary[:1200], "highlights": listed(d.get("highlights"), 5), "suggestions": listed(d.get("suggestions"), 4)}
