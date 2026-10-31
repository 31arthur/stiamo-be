"""The Drive pack: searching the letters and documents in the Google Drive by what is in them."""

from typing import Any

from app.application.runner import Tool, ToolOutput
from app.application.toolpacks import PackContext, ToolPack
from app.application.toolpacks.common import doc_card, filing, limit, text
from app.domain.llm import ToolSpec


class DrivePack(ToolPack):
    name = "drive"
    variables = {"drive": 'Whether the Google Drive is connected: "connected" or "not connected".'}

    async def context(self, ctx: PackContext) -> dict[str, Any]:
        return {"drive": "connected" if await ctx.data.drive_available() else "not connected"}

    async def tools(self, ctx: PackContext) -> list[Tool]:
        data = ctx.data
        drive_ok = await data.drive_available()
        files = await filing(data)

        async def find_documents(a: dict[str, Any]) -> ToolOutput:
            if not drive_ok:
                return ToolOutput({"error": "Google Drive is not connected, so documents cannot be searched."})
            found = (await data.drive_search(text(a.get("query"))))[: limit(a.get("limit"), 8)]
            cards = [doc_card(f, files.of(f["id"])) for f in found]
            return ToolOutput({"count": len(cards), "documents": [{"name": c["name"], "modifiedTime": c.get("modifiedTime"), "filedUnder": c.get("filedUnder", [])} for c in cards]}, cards)

        return [
            Tool(
                ToolSpec(
                    "find_documents",
                    "Search the letters and documents in the Google Drive by what is in them (names and contents). Every word must appear. Newest first when no words are given.",
                    {"type": "object", "properties": {"query": {"type": "string", "description": 'Words in the document, e.g. "wage breakdown Alur". Put a phrase in quotes.'}, "limit": {"type": "integer", "description": "How many (default 8, at most 25)."}}},
                ),
                lambda a: f"Searched Drive for “{text(a.get('query'))}”" if text(a.get("query")) else "Looked at the newest documents in Drive",
                find_documents,
            )
        ]
