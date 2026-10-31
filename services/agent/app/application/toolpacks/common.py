"""Helpers the packs share: reading tool arguments, and shaping cases and documents into the cards a person is shown."""

import re
from typing import Any

from app.application.ports import DataGateway

STATUSES = ("active", "on_hold", "disposed", "closed")
STATUS_LABELS = {"active": "Active", "on_hold": "On hold", "disposed": "Disposed", "closed": "Closed"}

_DRIVE_ID = re.compile(r"/d/([\w-]{15,})|[?&]id=([\w-]{15,})")


def text(v: Any) -> str:
    """A string argument, or an empty one. A model's arguments can be anything."""
    return v.strip() if isinstance(v, str) else ""


def limit(v: Any, default: int, most: int = 25) -> int:
    return min(most, max(1, int(v))) if isinstance(v, (int, float)) and not isinstance(v, bool) else default


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


def hearings_of(case_id: str, hearings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted((h for h in hearings if case_id in [h.get("caseId"), *(h.get("alsoCaseIds") or [])]), key=lambda h: (h.get("date", ""), h.get("time") or 0))


def next_hearing(case_id: str, hearings: list[dict[str, Any]], today: str) -> dict[str, Any] | None:
    return next((h for h in hearings_of(case_id, hearings) if (h.get("outcome") or {}).get("status") == "open" and str(h.get("date", "")) >= today), None)


def case_card(c: dict[str, Any], hearings: list[dict[str, Any]], today: str) -> dict[str, Any]:
    """A case as it is shown to a person (and told to the model): the front of its file."""
    nxt = next_hearing(c["id"], hearings, today)
    card: dict[str, Any] = {
        "kind": "case", "id": c["id"], "reference": c.get("reference", ""), "title": c.get("title", ""),
        "status": STATUS_LABELS.get(c.get("status", ""), c.get("status", "")),
        "court": c.get("court"), "courtNumber": c.get("courtNumber"), "client": c.get("client"),
        "opposingParty": c.get("opposingParty"), "stage": c.get("stage"),
    }
    if nxt:
        card["nextHearing"] = {"date": nxt["date"], "purpose": nxt.get("purpose", "")}
    return {k: v for k, v in card.items() if v is not None}


def doc_card(f: dict[str, Any], filed_under: list[str], matched: list[str] | None = None) -> dict[str, Any]:
    card = {
        "kind": "document", "id": f["id"], "name": f.get("name", ""), "mimeType": f.get("mimeType", ""),
        "modifiedTime": f.get("modifiedTime"), "webViewLink": f.get("webViewLink"), "size": f.get("size"),
        "filedUnder": filed_under, "matched": matched,
    }
    return {k: v for k, v in card.items() if v not in (None, [])}


class Filing:
    """Which cases a Drive file is filed under (a link to it, attached to a case)."""

    def __init__(self, cases: list[dict[str, Any]], attachments: list[dict[str, Any]]) -> None:
        self._refs: dict[str, list[str]] = {}
        by_id = {c["id"]: c for c in cases}
        for a in attachments:
            owner = a.get("owner") or {}
            fid = drive_file_id((a.get("link") or {}).get("url"))
            ref = (by_id.get(owner.get("id")) or {}).get("reference")
            if owner.get("kind") == "case" and fid and ref and ref not in self._refs.setdefault(fid, []):
                self._refs[fid].append(ref)

    def of(self, file_id: str) -> list[str]:
        return self._refs.get(file_id, [])


async def filing(data: DataGateway) -> Filing:
    return Filing(await data.cases(), await data.attachments())
