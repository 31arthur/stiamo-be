"""The cases pack: finding cases, reading one, and the documents that belong with it."""

from typing import Any

from app.application.runner import Tool, ToolOutput
from app.application.toolpacks import PackContext, ToolPack
from app.application.toolpacks.common import (
    STATUSES,
    case_card,
    case_search_terms,
    doc_card,
    drive_file_id,
    filing,
    hearings_of,
    limit,
    match_cases,
    text,
)
from app.domain.llm import ToolSpec

OBJ = "object"


class CasesPack(ToolPack):
    name = "cases"
    variables = {"cases": "How many cases the practice has."}

    async def context(self, ctx: PackContext) -> dict[str, Any]:
        return {"cases": len(await ctx.data.cases())}

    async def tools(self, ctx: PackContext) -> list[Tool]:
        data, today = ctx.data, ctx.today
        cases, hearings, attachments = await data.cases(), await data.hearings(), await data.attachments()
        drive_ok = await data.drive_available()
        by_ref = {c["id"]: c for c in cases}
        files = await filing(data)

        def find_case(key: str) -> dict[str, Any] | None:
            return by_ref.get(key) or next((c for c in cases if c.get("reference") == key), None)

        def filed_docs(case_id: str) -> list[dict[str, Any]]:
            return [a for a in attachments if (a.get("owner") or {}).get("kind") == "case" and (a.get("owner") or {}).get("id") == case_id and a.get("link")]

        async def search_cases(a: dict[str, Any]) -> ToolOutput:
            status = text(a.get("status"))
            pool = [c for c in cases if c.get("status") == status] if status in STATUSES else cases
            hits = match_cases(pool, text(a.get("query")))[: limit(a.get("limit"), 10)]
            cards = [case_card(c, hearings, today) for c in hits]
            return ToolOutput({"count": len(cards), "cases": cards}, cards)

        async def get_case(a: dict[str, Any]) -> ToolOutput:
            c = find_case(text(a.get("id")))
            if not c:
                return ToolOutput({"error": "No case has that id. Search for it first."})
            card = case_card(c, hearings, today)
            return ToolOutput(
                {
                    **card, "summary": c.get("summary"), "filedOn": c.get("filedOn"),
                    "hearings": [{"date": h.get("date"), "purpose": h.get("purpose"), "recorded": (h.get("outcome") or {}).get("status") != "open", "order": h.get("order")} for h in hearings_of(c["id"], hearings)],
                    "documents": [{"name": d.get("name"), "link": (d.get("link") or {}).get("url")} for d in filed_docs(c["id"])],
                },
                [card],
            )

        async def case_documents(a: dict[str, Any]) -> ToolOutput:
            c = find_case(text(a.get("id")))
            if not c:
                return ToolOutput({"error": "No case has that id. Search for it first."})
            filed = [
                doc_card({"id": drive_file_id(d["link"].get("url")) or d["id"], "name": d.get("name"), "mimeType": d.get("mime"), "webViewLink": d["link"].get("url"), "modifiedTime": d.get("createdAt")}, [c["reference"]])
                for d in filed_docs(c["id"])
            ]
            suggested: list[dict[str, Any]] = []
            if drive_ok:
                terms = case_search_terms(c)
                have = {f["id"] for f in filed}
                matches = await data.drive_suggest(terms) if terms else []
                suggested = [doc_card(m, files.of(m["id"]), m.get("matched")) for m in matches if m["id"] not in have][:8]
            return ToolOutput(
                {"case": c["reference"], "filed": [f["name"] for f in filed], "suggestedFromDrive": [{"name": s["name"], "matched": s.get("matched"), "modifiedTime": s.get("modifiedTime")} for s in suggested], "driveConnected": drive_ok},
                [*filed, *suggested],
            )

        return [
            Tool(
                ToolSpec(
                    "search_cases",
                    "Find cases. Matches the words against the case number, the court's number, title, client, opposing party, court, stage and summary. With no query it lists cases, optionally only those with a given status.",
                    {"type": OBJ, "properties": {"query": {"type": "string", "description": 'Words to look for, e.g. "NHAI Alur" or "WP 1290/2026".'}, "status": {"type": "string", "enum": list(STATUSES)}, "limit": {"type": "integer", "description": "How many to return (default 10, at most 25)."}}},
                ),
                lambda a: f"Searched cases for “{text(a.get('query'))}”" if text(a.get("query")) else "Listed cases",
                search_cases,
            ),
            Tool(
                ToolSpec("get_case", "Everything on one case: its details, summary, hearings (with dates and purposes) and the documents filed under it. Use the id from search_cases.", {"type": OBJ, "properties": {"id": {"type": "string", "description": "The case id."}}, "required": ["id"]}),
                lambda a: f"Opened case {text(a.get('id'))}",
                get_case,
            ),
            Tool(
                ToolSpec("case_documents", "The documents that belong with a case: those already filed under it, and letters in the Google Drive that mention its number, court number, title or parties.", {"type": OBJ, "properties": {"id": {"type": "string", "description": "The case id."}}, "required": ["id"]}),
                lambda a: f"Looked for documents on case {text(a.get('id'))}",
                case_documents,
            ),
        ]
