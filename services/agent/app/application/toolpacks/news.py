"""The news pack: headlines from the news agents, three at a time, each with a button that opens the article."""

from typing import Any

from app.application.runner import Tool, ToolOutput
from app.application.toolpacks import PackContext, ToolPack
from app.application.toolpacks.cards import card
from app.application.toolpacks.common import limit, text
from app.domain.llm import ToolSpec

OBJ = "object"
S = {"type": "string"}


class NewsPack(ToolPack):
    name = "news"

    async def tools(self, ctx: PackContext) -> list[Tool]:
        desk, who = ctx.headlines, ctx.who
        if desk is None or who is None:
            return []

        async def show_news(a: dict[str, Any]) -> ToolOutput:
            sort = "latest" if text(a.get("sort")) == "latest" else "importance"
            workspace = text(a.get("workspace")) or None
            days = limit(a.get("days"), 3, 30)
            query = text(a.get("topic"))
            offset = max(0, int(a.get("offset") or 0)) if str(a.get("offset") or "0").isdigit() else 0
            if ctx.queue is not None and ctx.channel == "Telegram":
                out = await desk.show(who, workspace=workspace, sort=sort, days=days, query=query, offset=offset, channel_id=ctx.channel_id)
                if not out["total"]:
                    return ToolOutput({**out, "next": "There are no headlines for that. Say so in one line, and offer a wider time or another topic."})
                return ToolOutput({**out, "next": "The headlines are ALREADY sent with an Open button for each and a button for the next three. Reply with at most one short line (for example how many are left)."})
            found = (await desk.stories(who, workspace, sort, days, query))[offset : offset + 3]
            return ToolOutput({"count": len(found), "stories": [{"title": s["title"], "source": s["source"], "link": s["link"], "publishedAt": s["publishedAt"]} for s in found],
                               "next": "Give these as numbered lines, each the title as a link to its own link, with the source. Three only; offer the next three."})

        return [Tool(
            ToolSpec("show_news", card(
                "The headlines the owner's news agents found, three at a time, most important first (or latest first). On Telegram each article comes with a button that opens it and a button for the next three.",
                use=["The owner asks for the news, the headlines, what is happening, or news about a topic or a workspace: “any news?”, “headlines”, “news on labour codes”."],
                avoid=["Never write headlines from memory or invent links: only what this tool gives. Never more than three at once."],
                args={"topic": "A word to look for in the title or summary.", "workspace": "Only that workspace's news (sb, fsa, personal…).", "sort": "importance (default) or latest.", "days": "How far back (default 3).", "offset": "Start after this many (to continue: 3, 6…)."},
                returns="On Telegram {shown, left, total}; elsewhere {stories: [{title, source, link, publishedAt}]}. {total: 0} means no headlines.",
                fails=["No headlines: say so and offer a wider time."],
                effect="Read only. On Telegram it sends the message itself.",
            ), {"type": OBJ, "properties": {"topic": S, "workspace": S, "sort": {"type": "string", "enum": ["importance", "latest"]}, "days": {"type": "integer"}, "offset": {"type": "integer"}}}),
            lambda a: "Looked at the headlines",
            show_news,
        )]
