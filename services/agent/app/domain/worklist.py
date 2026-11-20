"""The worklist: the queue of action items Chittu works through with the owner, one at a time. Pure: no network, no framework, no model.

A worklist is made once, by a request (the morning, a question, or a list a model gave as JSON), and then *the code* walks it: each button, each word the
owner types, each item that is settled somewhere else moves it on, with no model call in between. It keeps its place and its meaning over time (a pause, a night,
a restart change nothing) until the owner makes a new request, which replaces it (or adds to it, if that is what the request says).

An entry is {ref, state, note, cs}: the action item's ref, where the entry is, a few words on why it is on the list (given by whoever made the list), and the change
set of what was done to it, so that it can be undone. States: pending, shown (the one in front of the owner), done, moved, skipped, snoozed, gone (settled elsewhere or
deleted since the list was made).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.domain.actionitems import ActionItem

MAX_ENTRIES = 60
MAX_NOTE = 140
PENDING, SHOWN, DONE, MOVED, SKIPPED, SNOOZED, GONE = "pending", "shown", "done", "moved", "skipped", "snoozed", "gone"
SETTLED = (DONE, MOVED, SKIPPED, SNOOZED, GONE)
#: How a request may meet the list that is already there: replace it, add to its end, or put the new entries next in line.
MODES = ("replace", "append", "next")
#: What the owner can say instead of pressing a button.
WORDS = {
    "skip": ("next", "skip", "skip it", "skip this", "pass"),
    "done": ("done", "did it", "finished", "completed", "mark done", "mark it done", "done it"),
    "tomorrow": ("tomorrow", "later", "not today", "push it", "push to tomorrow", "snooze"),
    "stop": ("stop", "pause", "enough", "that's enough", "thats enough", "not now", "hold on"),
    "resume": ("continue", "resume", "go on", "keep going", "carry on", "where were we", "start", "go", "next one", "show me"),
    "left": ("left", "how many left", "how many more", "remaining", "what's left", "whats left", "how many"),
    "undo": ("undo", "undo that", "put it back", "oops", "that was a mistake"),
    "trash": ("trash", "trash it", "delete", "delete it", "bin", "bin it", "bin this", "trash this")
}


def classify_words(text: str) -> str | None:
    """Which of the owner's queue words a message is, if it is one (the whole message, ignoring case and trailing punctuation)."""
    t = " ".join(str(text or "").lower().replace("!", "").replace(".", "").replace("?", "").split())
    return next((k for k, words in WORDS.items() if t in words), None)


@dataclass
class Spec:
    """What a request asks the worklist to be: a title, how to meet the list that is there, and the entries, in order."""

    title: str
    mode: str
    entries: list[dict[str, Any]] = field(default_factory=list)
    #: Refs that were asked for and could not be used, and why, so the caller can say so.
    dropped: list[dict[str, str]] = field(default_factory=list)


def parse_spec(raw: Any, known: dict[str, ActionItem], *, title: str = "", mode: str = "replace") -> Spec:
    """Reads a list a model gave as JSON ({title, mode, items: [{ref, note}]}) and checks every entry against the real, open items. A model may order the list and say
    why each is there; it can never put something on it that is not an item, or the same thing twice. Never raises: bad entries are dropped and named."""
    raw = raw if isinstance(raw, dict) else {"items": raw}
    chosen = str(raw.get("mode") or mode)
    spec = Spec(" ".join(str(raw.get("title") or title or "Your list").split())[:60] or "Your list", chosen if chosen in MODES else "replace")
    seen: set[str] = set()
    rows = raw.get("items") if isinstance(raw.get("items"), list) else []
    for row in rows:
        ref, note = (row.get("ref"), row.get("note")) if isinstance(row, dict) else (row, None)
        ref = str(ref or "").strip()
        if not ref:
            continue
        if ref in seen:
            spec.dropped.append({"ref": ref, "why": "it was on the list twice"})
        elif ref not in known:
            spec.dropped.append({"ref": ref, "why": "there is no such item (look it up with find_items and use its ref)"})
        elif known[ref].status != "open":
            spec.dropped.append({"ref": ref, "why": f"it is already {known[ref].status}"})
        elif len(spec.entries) >= MAX_ENTRIES:
            spec.dropped.append({"ref": ref, "why": f"a list holds at most {MAX_ENTRIES} items"})
        else:
            seen.add(ref)
            spec.entries.append(entry(ref, " ".join(str(note or "").split())[:MAX_NOTE] or None))
    return spec


def entry(ref: str, note: str | None = None) -> dict[str, Any]:
    return {"ref": ref, "state": PENDING, **({"note": note} if note else {})}


def counts(entries: list[dict[str, Any]]) -> dict[str, int]:
    c = {s: 0 for s in (PENDING, SHOWN, DONE, MOVED, SKIPPED, SNOOZED, GONE)}
    for e in entries:
        c[e["state"]] = c.get(e["state"], 0) + 1
    c["total"] = len(entries) - c[GONE]  # what the numbering counts: items that are still real
    c["left"] = c[PENDING] + c[SHOWN]
    return c


def position(entries: list[dict[str, Any]], index: int) -> tuple[int, int]:
    """Where an entry is, for "3/10": its place among the entries that are not gone, and how many there are."""
    live = [i for i, e in enumerate(entries) if e["state"] != GONE]
    return (live.index(index) + 1 if index in live else len(live)), len(live)


def next_index(entries: list[dict[str, Any]], after: int = -1) -> int | None:
    """The next entry still to be shown: the first pending one after `after`, else the first pending one anywhere (a skipped list wraps round only to pending)."""
    for i in range(after + 1, len(entries)):
        if entries[i]["state"] == PENDING:
            return i
    return next((i for i, e in enumerate(entries) if e["state"] == PENDING), None)


def merge(entries: list[dict[str, Any]], new: list[dict[str, Any]], mode: str, cursor: int = 0) -> list[dict[str, Any]]:
    """The list with new entries met in the way the request said. An item already on the list (and not settled) is not added twice. `next` puts them straight after
    the one in front of the owner; `append` at the end; `replace` is the new entries alone."""
    if mode == "replace":
        return [dict(e) for e in new]
    have = {e["ref"] for e in entries if e["state"] in (PENDING, SHOWN)}
    fresh = [e for e in new if e["ref"] not in have]
    out = [dict(e) for e in entries]
    if mode == "next":
        at = cursor + 1 if 0 <= cursor < len(out) else len(out)
        return out[:at] + fresh + out[at:]
    return out + fresh
