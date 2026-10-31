"""The tools the agent can use: finding cases, reading one, finding the documents that belong with it, and asking a decision model."""

import re
from datetime import date
from typing import Any

from app.application.ports import DataGateway, DecisionClient
from app.application.runner import Tool, ToolOutput
from app.domain.llm import AiError, ToolSpec

STATUSES = ("active", "on_hold", "disposed", "closed")
STATUS_LABELS = {"active": "Active", "on_hold": "On hold", "disposed": "Disposed", "closed": "Closed"}

_DRIVE_ID = re.compile(r"/d/([\w-]{15,})|[?&]id=([\w-]{15,})")


def _text(v: Any) -> str:
    return v.strip() if isinstance(v, str) else ""


def _limit(v: Any, default: int) -> int:
    return min(25, max(1, int(v))) if isinstance(v, (int, float)) and not isinstance(v, bool) else default


def drive_file_id(address: str | None) -> str | None:
    m = _DRIVE_ID.search(address or "")
    return (m.group(1) or m.group(2)) if m else None


def case_search_terms(c: dict[str, Any]) -> list[str]:
    """What identifies a case in a letter: our number, the court's number, its title, the client and the other side."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in (c.get("reference"), c.get("courtNumber"), c.get("title"), c.get("client"), c.get("opposingParty")):
        t = " ".join(str(raw or "").split())
        if len(t) >= 3 and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out


def _haystack(c: dict[str, Any]) -> str:
    keys = ("reference", "courtNumber", "title", "client", "opposingParty", "court", "stage", "practiceArea", "summary")
    return " ".join(str(c.get(k) or "") for k in keys).lower()


def match_cases(cases: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    """Cases that contain every word of the query; if none do, the ones that contain the most of them."""
    words = [w for w in query.lower().split() if len(w) >= 2]
    if not words:
        return cases
    scored = [(c, sum(1 for w in words if w in _haystack(c))) for c in cases]
    scored = [(c, n) for c, n in scored if n > 0]
    best = max((n for _, n in scored), default=0)
    full = [x for x in scored if x[1] == len(words)]
    chosen = full or [x for x in scored if x[1] == best]
    return [c for c, _ in sorted(chosen, key=lambda x: -x[1])]


def _next_hearing(case_id: str, hearings: list[dict[str, Any]], today: str) -> dict[str, Any] | None:
    mine = [h for h in hearings if case_id in [h.get("caseId"), *(h.get("alsoCaseIds") or [])] and (h.get("outcome") or {}).get("status") == "open" and str(h.get("date", "")) >= today]
    return min(mine, key=lambda h: (h.get("date", ""), h.get("time") or 0), default=None)


def _case_card(c: dict[str, Any], hearings: list[dict[str, Any]], today: str) -> dict[str, Any]:
    nxt = _next_hearing(c["id"], hearings, today)
    card = {
        "kind": "case", "id": c["id"], "reference": c.get("reference", ""), "title": c.get("title", ""),
        "status": STATUS_LABELS.get(c.get("status", ""), c.get("status", "")),
        "court": c.get("court"), "courtNumber": c.get("courtNumber"), "client": c.get("client"),
        "opposingParty": c.get("opposingParty"), "stage": c.get("stage"),
    }
    if nxt:
        card["nextHearing"] = {"date": nxt["date"], "purpose": nxt.get("purpose", "")}
    return {k: v for k, v in card.items() if v is not None}


def _doc_card(f: dict[str, Any], filed_under: list[str], matched: list[str] | None = None) -> dict[str, Any]:
    card = {
        "kind": "document", "id": f["id"], "name": f.get("name", ""), "mimeType": f.get("mimeType", ""),
        "modifiedTime": f.get("modifiedTime"), "webViewLink": f.get("webViewLink"), "size": f.get("size"),
        "filedUnder": filed_under, "matched": matched,
    }
    return {k: v for k, v in card.items() if v not in (None, [])}


DECISION_GUIDE = (
    "Ask a decision model typed questions about some text, and get probabilities back instead of prose. Use it when you must pick between options "
    "(choice), judge whether something holds (noul, the probability of yes) or place something on an ordered scale (score). "
    "Put everything the question needs in `state`. Give every question `type`, `instructions` and `criteria`: for choice an object of option -> description, "
    "for noul an object with `true` and `false` descriptions, for score an array of descriptions from lowest to highest."
)


async def build_tools(*, data: DataGateway, today: str, decisions: DecisionClient | None = None, decision_model: str | None = None) -> list[Tool]:
    """The tools, ready to run against this person's data. The decision tool exists only when a decision model is set up."""
    cases = await data.cases()
    hearings = await data.hearings()
    attachments = await data.attachments()
    drive_ok = await data.drive_available()
    by_ref = {c["id"]: c for c in cases}

    def find_case(key: str) -> dict[str, Any] | None:
        return by_ref.get(key) or next((c for c in cases if c.get("reference") == key), None)

    def filed_under(file_id: str) -> list[str]:
        refs: list[str] = []
        for a in attachments:
            owner = a.get("owner") or {}
            if owner.get("kind") == "case" and drive_file_id((a.get("link") or {}).get("url")) == file_id:
                ref = (by_ref.get(owner.get("id")) or {}).get("reference")
                if ref and ref not in refs:
                    refs.append(ref)
        return refs

    def filed_docs(case_id: str) -> list[dict[str, Any]]:
        return [a for a in attachments if (a.get("owner") or {}).get("kind") == "case" and (a.get("owner") or {}).get("id") == case_id and a.get("link")]

    async def search_cases(a: dict[str, Any]) -> ToolOutput:
        status = _text(a.get("status"))
        pool = [c for c in cases if c.get("status") == status] if status in STATUSES else cases
        hits = match_cases(pool, _text(a.get("query")))[: _limit(a.get("limit"), 10)]
        cards = [_case_card(c, hearings, today) for c in hits]
        return ToolOutput({"count": len(cards), "cases": cards}, cards)

    async def get_case(a: dict[str, Any]) -> ToolOutput:
        c = find_case(_text(a.get("id")))
        if not c:
            return ToolOutput({"error": "No case has that id. Search for it first."})
        mine = sorted((h for h in hearings if c["id"] in [h.get("caseId"), *(h.get("alsoCaseIds") or [])]), key=lambda h: h.get("date", ""))
        card = _case_card(c, hearings, today)
        return ToolOutput(
            {
                **card, "summary": c.get("summary"), "filedOn": c.get("filedOn"),
                "hearings": [{"date": h.get("date"), "purpose": h.get("purpose"), "recorded": (h.get("outcome") or {}).get("status") != "open", "order": h.get("order")} for h in mine],
                "documents": [{"name": d.get("name"), "link": (d.get("link") or {}).get("url")} for d in filed_docs(c["id"])],
            },
            [card],
        )

    async def case_documents(a: dict[str, Any]) -> ToolOutput:
        c = find_case(_text(a.get("id")))
        if not c:
            return ToolOutput({"error": "No case has that id. Search for it first."})
        filed = [
            _doc_card({"id": drive_file_id(d["link"].get("url")) or d["id"], "name": d.get("name"), "mimeType": d.get("mime"), "webViewLink": d["link"].get("url"), "modifiedTime": d.get("createdAt")}, [c["reference"]])
            for d in filed_docs(c["id"])
        ]
        suggested: list[dict[str, Any]] = []
        if drive_ok:
            terms = case_search_terms(c)
            have = {f["id"] for f in filed}
            matches = await data.drive_suggest(terms) if terms else []
            suggested = [_doc_card(m, filed_under(m["id"]), m.get("matched")) for m in matches if m["id"] not in have][:8]
        return ToolOutput(
            {"case": c["reference"], "filed": [f["name"] for f in filed], "suggestedFromDrive": [{"name": s["name"], "matched": s.get("matched"), "modifiedTime": s.get("modifiedTime")} for s in suggested], "driveConnected": drive_ok},
            [*filed, *suggested],
        )

    async def find_documents(a: dict[str, Any]) -> ToolOutput:
        if not drive_ok:
            return ToolOutput({"error": "Google Drive is not connected, so documents cannot be searched."})
        files = (await data.drive_search(_text(a.get("query"))))[: _limit(a.get("limit"), 8)]
        cards = [_doc_card(f, filed_under(f["id"])) for f in files]
        return ToolOutput({"count": len(cards), "documents": [{"name": c["name"], "modifiedTime": c.get("modifiedTime"), "filedUnder": c.get("filedUnder", [])} for c in cards]}, cards)

    obj = "object"
    tools = [
        Tool(
            ToolSpec(
                "search_cases",
                "Find cases. Matches the words against the case number, the court's number, title, client, opposing party, court, stage and summary. With no query it lists cases, optionally only those with a given status.",
                {"type": obj, "properties": {"query": {"type": "string", "description": 'Words to look for, e.g. "NHAI Alur" or "WP 1290/2026".'}, "status": {"type": "string", "enum": list(STATUSES)}, "limit": {"type": "integer", "description": "How many to return (default 10, at most 25)."}}},
            ),
            lambda a: f"Searched cases for “{_text(a.get('query'))}”" if _text(a.get("query")) else "Listed cases",
            search_cases,
        ),
        Tool(
            ToolSpec("get_case", "Everything on one case: its details, summary, hearings (with dates and purposes) and the documents filed under it. Use the id from search_cases.", {"type": obj, "properties": {"id": {"type": "string", "description": "The case id."}}, "required": ["id"]}),
            lambda a: f"Opened case {_text(a.get('id'))}",
            get_case,
        ),
        Tool(
            ToolSpec("case_documents", "The documents that belong with a case: those already filed under it, and letters in the Google Drive that mention its number, court number, title or parties.", {"type": obj, "properties": {"id": {"type": "string", "description": "The case id."}}, "required": ["id"]}),
            lambda a: f"Looked for documents on case {_text(a.get('id'))}",
            case_documents,
        ),
        Tool(
            ToolSpec("find_documents", "Search the letters and documents in the Google Drive by what is in them (names and contents). Every word must appear. Newest first when no words are given.", {"type": obj, "properties": {"query": {"type": "string", "description": 'Words in the document, e.g. "wage breakdown Alur". Put a phrase in quotes.'}, "limit": {"type": "integer", "description": "How many (default 8, at most 25)."}}}),
            lambda a: f"Searched Drive for “{_text(a.get('query'))}”" if _text(a.get("query")) else "Looked at the newest documents in Drive",
            find_documents,
        ),
    ]

    if decisions is not None and decision_model:

        async def decide(a: dict[str, Any]) -> ToolOutput:
            questions = a.get("questions")
            if not isinstance(questions, dict) or not questions:
                return ToolOutput({"error": "Give at least one question."})
            try:
                answers = await decisions.decide(model=decision_model, state=a.get("state", ""), questions=questions)
            except AiError as exc:
                return ToolOutput({"error": str(exc)})
            return ToolOutput({"answers": answers})

        tools.append(
            Tool(
                ToolSpec(
                    "decide", DECISION_GUIDE,
                    {
                        "type": obj,
                        "properties": {
                            "state": {"type": "string", "description": "The text to decide about, with everything needed."},
                            "questions": {"type": obj, "description": "Question name -> {type, instructions, criteria}."},
                        },
                        "required": ["state", "questions"],
                    },
                ),
                lambda a: f"Asked the decision model ({', '.join(map(str, (a.get('questions') or {}).keys())) or 'no questions'})",
                decide,
            )
        )
    return tools


def today_iso() -> str:
    return date.today().isoformat()
