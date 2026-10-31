"""What the agents have used: tokens, and cost where the provider says it, for every run of every agent."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.application.ports import RunLog, UsageRow, WorkflowRepository
from app.application.registry import AgentRegistry
from app.application.services import Who
from app.application.usage_totals import Bucket

DAILY_DAYS = 30
TRANSACTIONS = 50


def _iso(d: datetime) -> str:
    return d.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class UsageService:
    def __init__(self, runs: RunLog, registry: AgentRegistry, workflows: WorkflowRepository) -> None:
        self._runs, self._registry, self._workflows = runs, registry, workflows

    async def _names(self, who: Who) -> dict[str, str]:
        names = {a.id: a.name for a in self._registry.all()}
        names.update({w.id: w.name for w in await self._workflows.all(who.user_id)})
        return names

    def _row(self, r: UsageRow, names: dict[str, str]) -> dict[str, Any]:
        return {
            "id": r.id, "at": _iso(r.at), "agentId": r.agent_id, "agentName": r.agent_name or names.get(r.agent_id) or "A deleted agent", "kind": r.kind, "trigger": r.trigger,
            "status": r.status, "provider": r.provider, "model": r.model, "inputTokens": r.input_tokens, "outputTokens": r.output_tokens, "cost": r.cost, "items": r.items, "durationMs": r.duration_ms,
        }

    async def summary(self, who: Who, days: int | None, agent_id: str | None) -> dict[str, Any]:
        now = datetime.now(UTC)
        names = await self._names(who)
        everything = await self._runs.usage_rows(who.user_id, agent_id=agent_id)
        window = [r for r in everything if days is None or r.at >= now - timedelta(days=days)]
        total, all_time = Bucket(), Bucket()
        for r in everything:
            all_time.add(r)
        by_agent: dict[str, tuple[Bucket, UsageRow]] = {}
        by_model: dict[tuple[str, str], Bucket] = {}
        for r in window:
            total.add(r)
            b, _ = by_agent.setdefault(r.agent_id, (Bucket(), r))
            b.add(r)
            by_model.setdefault((r.provider, r.model), Bucket()).add(r)
        span = min(days or DAILY_DAYS, 90)
        daily = {(now - timedelta(days=i)).date().isoformat(): Bucket() for i in range(span - 1, -1, -1)}
        for r in window:
            d = daily.get(r.at.astimezone(UTC).date().isoformat())
            if d is not None:
                d.add(r)
        return {
            "windowDays": days, "total": total.json(), "allTime": all_time.json(),
            "byAgent": sorted(
                ({"agentId": a, "name": last.agent_name or names.get(a) or "A deleted agent", "kind": last.kind, **b.json()} for a, (b, last) in by_agent.items()),
                key=lambda x: (x["cost"] or 0, x["inputTokens"] + x["outputTokens"]), reverse=True,
            ),
            "byModel": sorted(({"provider": p, "model": m, **b.json()} for (p, m), b in by_model.items()), key=lambda x: x["inputTokens"] + x["outputTokens"], reverse=True),
            "daily": [{"date": d, **b.json()} for d, b in daily.items()],
        }

    async def transactions(self, who: Who, days: int | None, agent_id: str | None, before: datetime | None, limit: int = TRANSACTIONS) -> dict[str, Any]:
        since = datetime.now(UTC) - timedelta(days=days) if days else None
        names = await self._names(who)
        rows = await self._runs.usage_rows(who.user_id, since=since, agent_id=agent_id, before=before, limit=limit + 1)
        page = rows[:limit]
        return {"items": [self._row(r, names) for r in page], "nextBefore": _iso(page[-1].at) if len(rows) > limit else None}

    async def for_agent(self, who: Who, agent_id: str) -> dict[str, Any]:
        """The totals for the Overview of one agent."""
        b = Bucket()
        for r in await self._runs.usage_rows(who.user_id, agent_id=agent_id):
            b.add(r)
        return b.json()
