"""Headlines for the owner, three at a time: what the news agents found, each with a button that opens the article, and a button for the next three.

The stories come from the news page (application/news.py), already ranked by how much they matter to the owner. Chittu only chooses what to show (a workspace, a
word, latest or most important); the code lays it out, so the same three never come twice and every link is the article's own.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.application.news import NewsService
from app.application.services import Who

PAGE = 3
SORTS = {"i": "importance", "l": "latest"}


def _age(published: str | None, now: datetime) -> str:
    try:
        d = datetime.fromisoformat(str(published).replace("Z", "+00:00"))
    except ValueError:
        return ""
    hours = int((now - d).total_seconds() // 3600)
    if hours < 1:
        return "just now"
    if hours < 24:
        return f"{hours}h ago"
    return f"{hours // 24}d ago" if hours < 24 * 7 else f"{d.day} {d.strftime('%b')}"


def page(stories: list[dict[str, Any]], offset: int, sort: str, workspace: str | None, now: datetime) -> tuple[str, list[list[dict[str, str]]]]:
    """The text and buttons for stories[offset:offset+3]: numbered, with the source and how old, an Open button for each article, and More when there are more."""
    shown = stories[offset : offset + PAGE]
    if not shown:
        return "That's all the headlines I have for now.", []
    head = f"📰 Headlines {offset + 1}–{offset + len(shown)} of {len(stories)} · {'latest first' if sort == 'latest' else 'most important first'}"
    lines, rows = [head, ""], []
    for n, s in enumerate(shown, start=offset + 1):
        meta = " · ".join(x for x in (s.get("source"), _age(s.get("publishedAt"), now)) if x)
        lines += [f"{n}. {s.get('title')}", f"   {meta}" if meta else "", ""]
        link = str(s.get("link") or "")
        if link.startswith(("http://", "https://")):
            rows.append([{"text": f"📖 {n} · {str(s.get('title'))[:34]}", "url": link}])
    left = len(stories) - offset - len(shown)
    rows.append(([{"text": f"📰 {min(PAGE, left)} more", "callback_data": f"nw:m:{'l' if sort == 'latest' else 'i'}:{offset + PAGE}:{workspace or '-'}"}] if left > 0 else []) + [{"text": "✖ Enough", "callback_data": "nw:x:0"}])
    return "\n".join(x for i, x in enumerate(lines) if x or i == 1).rstrip(), rows


class HeadlineDesk:
    def __init__(self, news: NewsService, send: Any, now: Any = lambda: datetime.now(UTC)) -> None:
        self._news, self._send, self._now = news, send, now

    async def stories(self, who: Who, workspace: str | None, sort: str, days: int, query: str = "") -> list[dict[str, Any]]:
        found = (await self._news.top(who, workspace, 20, days, sort))["items"]
        q = query.strip().lower()
        return [s for s in found if q in f"{s.get('title')} {s.get('description') or ''}".lower()] if q else found

    async def show(self, who: Who, *, workspace: str | None = None, sort: str = "importance", days: int = 3, query: str = "", offset: int = 0, channel_id: str | None = None) -> dict[str, Any]:
        """Sends three headlines (from `offset`) to the chat the owner is in. Returns how many were shown and how many are left."""
        stories = await self.stories(who, workspace, sort, days, query)
        if not stories:
            return {"shown": 0, "left": 0, "total": 0}
        text, rows = page(stories, offset, sort, workspace, self._now())
        await self._send(who, text, rows, channel_id)
        shown = len(stories[offset : offset + PAGE])
        return {"shown": shown, "left": max(0, len(stories) - offset - shown), "total": len(stories)}

    async def press(self, who: Who, action: str, rest: str) -> Any:
        """The buttons under a headlines message: the next three, or enough."""
        if action == "x":
            return "👍 Say headlines whenever you want more."
        s, _, tail = rest.partition(":")
        off, _, ws = tail.partition(":")
        sort = SORTS.get(s, "importance")
        workspace = None if ws in ("", "-") else ws
        stories = await self.stories(who, workspace, sort, 3 if sort == "importance" else 7)
        return page(stories, int(off) if off.isdigit() else PAGE, sort, workspace, self._now())
