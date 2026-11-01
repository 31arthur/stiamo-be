"""The attention pack: what the mail agents have put in front of the owner, waiting for them."""

from typing import Any

from app.application.runner import Tool, ToolOutput
from app.application.toolpacks import PackContext, ToolPack
from app.domain.llm import ToolSpec

ORDER = {"high": 0, "normal": 1, "low": 2}


class AttentionPack(ToolPack):
    name = "attention"

    async def tools(self, ctx: PackContext) -> list[Tool]:
        if ctx.attention is None:
            return []
        read = ctx.attention

        async def waiting(_: dict[str, Any]) -> ToolOutput:
            items = sorted(await read(), key=lambda i: ORDER.get(i["priority"], 1))[:25]
            return ToolOutput({"count": len(items), "items": [{k: v for k, v in i.items() if v not in (None, "", {})} for i in items]})

        return [
            Tool(
                ToolSpec("waiting_in_attention", "What the mail agents have put in the Attention queue: emails that need the owner, each with a summary, a priority, the action it asks for and its date, and events found in mail that wait to be approved for the calendar.", {"type": "object", "properties": {}}),
                lambda _: "Read the Attention queue",
                waiting,
            )
        ]
