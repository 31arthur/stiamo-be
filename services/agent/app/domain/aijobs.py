"""AI jobs: small, single-purpose model calls with a written contract, so they work on whatever model is plugged in, cheap ones included.

A job is one narrow task (find which story a message is about; read margin notes from a chapter). Its contract says what it sees, the exact
JSON shape it returns, and how code checks the answer. The model never has the last word: `check` keeps only what holds up (an id that
exists, a date that parses, a quote that is really in the text) and reports what it dropped. A reply that can't be used at all is a
`Problem`: the runner asks once more, telling the model what was wrong, and after that falls back to the job's safe answer, so a weak model
gives fewer suggestions, never wrong records.

Pure: no network. The runner is application/aijobs.py.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any


class Problem(Exception):
    """The reply can't be used as it is. The message is told to the model on the retry, so write it for the model."""


@dataclass
class Checked:
    """What survived the checks, and a line for each thing that was dropped (shown to the person, kept for the test set)."""

    output: dict[str, Any]
    dropped: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Job:
    #: How settings and the test set refer to it, and the key its own model choice is kept under (`job:<name>`).
    name: str
    #: Bumped whenever the prompt or the checks change, so test results are compared like with like.
    version: str
    #: One line, for the settings page.
    purpose: str
    system: str
    #: (input, ideal reply) pairs shown to the model before the real input. Small models follow examples far better than rules.
    examples: tuple[tuple[dict[str, Any], dict[str, Any]], ...]
    #: The input as the message the model reads.
    render: Callable[[dict[str, Any]], str]
    #: The model's parsed JSON and the input → what holds up. Raises Problem when nothing usable came back.
    check: Callable[[dict[str, Any], dict[str, Any]], Checked]
    #: The answer when the model fails twice: always safe (nothing invented), usually empty.
    fallback: Callable[[dict[str, Any]], dict[str, Any]]


@dataclass
class JobResult:
    output: dict[str, Any]
    #: False when the fallback was used: the model's answer never held up.
    ok: bool
    attempts: int
    dropped: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    model: str = ""
    #: The raw replies, kept so a failure can be read back and turned into a test case.
    replies: list[str] = field(default_factory=list)


# --- reading a reply ------------------------------------------------------------------------------------------------


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def parse_json(text: str) -> dict[str, Any]:
    """The JSON object in a reply, however it was wrapped: bare, in a code fence, or with words around it."""
    t = (text or "").strip()
    fenced = _FENCE.search(t)
    if fenced:
        t = fenced.group(1).strip()
    start = t.find("{")
    if start < 0:
        raise Problem("Your reply had no JSON object. Reply with one JSON object only.")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(t)):
        ch = t[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    out = json.loads(t[start:i + 1])
                except json.JSONDecodeError as exc:
                    raise Problem(f"Your JSON didn't parse ({exc.msg}). Reply with one valid JSON object only.") from exc
                if not isinstance(out, dict):
                    raise Problem("Reply with a JSON object, not a list.")
                return out
    raise Problem("Your JSON object wasn't closed. Reply with one complete JSON object only.")


# --- checking pieces of a reply -------------------------------------------------------------------------------------


_QUOTES = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'", "“": '"', "”": '"', "„": '"', "–": "-", "—": "-", "…": "..."})


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "").translate(_QUOTES).lower()
    return " ".join(t.split())


def find_quote(quote: Any, text: str, min_chars: int = 4) -> str | None:
    """The quote as it really appears in the text, or None if it isn't there. Case, curly quotes, dashes and spacing don't matter;
    words do. This is the rule that stops a model inventing: anything it says must be pinned to words the person wrote."""
    if not isinstance(quote, str):
        return None
    q = _norm(quote).strip(" .\"'")
    if len(q) < min_chars:
        return None
    # Map positions in the normalised text back to the original, so the exact original words are returned.
    original = text or ""
    chars: list[str] = []
    where: list[int] = []
    prev_space = True
    for i, ch in enumerate(unicodedata.normalize("NFKC", original)):
        c = ch.translate(_QUOTES).lower()
        if c.isspace():
            if prev_space:
                continue
            c, prev_space = " ", True
        else:
            prev_space = False
        for piece in c:
            chars.append(piece)
            where.append(i)
    flat = "".join(chars)
    at = flat.find(q)
    if at < 0:
        return None
    src = unicodedata.normalize("NFKC", original)
    return src[where[at]:where[at + len(q) - 1] + 1]


def one_of(value: Any, allowed: tuple[str, ...], default: str | None = None) -> str | None:
    v = str(value).strip().lower().replace(" ", "_").replace("-", "_") if value is not None else ""
    return v if v in allowed else default


def iso_day(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip()[:10]).isoformat()
    except ValueError:
        return None


def short(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    t = " ".join(value.split())
    if not t:
        return None
    return t if len(t) <= limit else t[: limit - 1].rstrip() + "…"


def items(value: Any, limit: int) -> list[dict[str, Any]]:
    """A list of objects from the reply, at most `limit` (a model that lists twenty steps is padding)."""
    return [v for v in value if isinstance(v, dict)][:limit] if isinstance(value, list) else []


def calendar(today: str, days: int = 21) -> str:
    """A day-by-day table for the prompt. Small models get "next Monday" wrong when they count; they get it right when they look it up."""
    start = date.fromisoformat(today)
    rows = []
    for n in range(days):
        d = start + timedelta(days=n)
        label = "today" if n == 0 else "tomorrow" if n == 1 else ""
        rows.append(f"{d.strftime('%a %d %b')} = {d.isoformat()}" + (f" ({label})" if label else ""))
    return "\n".join(rows)


def as_messages(job: Job, inputs: dict[str, Any]) -> list[tuple[str, str]]:
    """The conversation sent for a job: the contract, each example as a turn of its own, then the real input. (role, text) pairs."""
    out: list[tuple[str, str]] = [("system", job.system)]
    for given, ideal in job.examples:
        out.append(("user", job.render(given)))
        out.append(("assistant", json.dumps(ideal, ensure_ascii=False)))
    out.append(("user", job.render(inputs)))
    return out
