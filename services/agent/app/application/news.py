"""The news page: the stories an agent found relevant, ranked, newest and most important first."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from stiamo_common.errors import NotFound

from app.application.ports import SettingsRepository, WorkflowRepository
from app.application.services import Who
from app.domain.workflows import rank_news

DEFAULT_LIMIT = 20
#: The page never shows more than this at a time.
MAX_LIMIT = 20


class NewsService:
    def __init__(self, repo: WorkflowRepository, settings: SettingsRepository) -> None:
        self._repo, self._settings = repo, settings

    async def top(self, who: Who, workspace: str | None, limit: int = DEFAULT_LIMIT, days: int = 7, sort: str = "importance") -> dict[str, Any]:
        """The stories, in one of two rankings: `importance` (how much it matters, then how recent) or `latest` (how recent). Never more than 20."""
        s = await self._settings.get(who.user_id)
        agents = {w.id: w for w in await self._repo.all(who.user_id) if (w.config.get("source") or {}).get("kind") == "feed"}
        now = datetime.now(UTC)
        out: list[dict[str, Any]] = []
        for r in await self._repo.queue(who.user_id, ["published"], limit=3000):
            w = agents.get(r.agent_id)
            d = r.data or {}
            if w is None or d.get("kind") != "news":
                continue
            where = (s.workspaces.get(w.id) if s else None) or []
            if workspace and where and workspace not in where:
                continue
            try:
                published = datetime.fromisoformat(str(d.get("publishedAt") or "").replace("Z", "+00:00"))
            except ValueError:
                published = r.created_at
            if now - published > timedelta(days=days):
                continue
            out.append({
                "agentId": r.agent_id, "itemId": r.item_id, "title": d.get("title") or r.title, "source": d.get("source"), "link": d.get("link"), "description": d.get("description"),
                "imageUrl": d.get("imageUrl"), "publishedAt": d.get("publishedAt"), "importance": d.get("importance"), "weight": d.get("weight") or 2, "lang": d.get("lang"),
                "feed": d.get("feed"), "relevance": d.get("relevance"), "rank": round(rank_news(d, now), 4),
            })
        if sort == "latest":
            out.sort(key=lambda a: (a["publishedAt"] or "", a["weight"]), reverse=True)
        else:
            out.sort(key=lambda a: (a["weight"], a["publishedAt"] or "", a["relevance"] or 0), reverse=True)
        return {"items": out[: min(limit, MAX_LIMIT)], "total": len(out), "sort": sort, "generatedAt": now.isoformat(timespec="seconds").replace("+00:00", "Z")}

    async def hide(self, who: Who, agent_id: str, item_id: str) -> None:
        entry = await self._repo.entry(who.user_id, agent_id, item_id)
        if entry is None or entry.status != "published":
            raise NotFound("That story isn't on the page.")
        await self._repo.settle(who.user_id, agent_id, item_id, "dismissed")
