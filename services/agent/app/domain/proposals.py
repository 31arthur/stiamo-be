"""What an agent may propose to add or change, checked before the person sees it. Pure: no network, no framework.

An agent never writes. It proposes; the person approves; only then is the record saved. These helpers turn what a model typed
(which can be anything) into values the practice service accepts, or say in plain words what is wrong so the model can fix it.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

ORGANIZATIONS = ("NUMW", "TUMWU", "PMLU", "MRC", "TYP", "SA")
CASE_TYPES = ("pow", "public_interest", "industrial_dispute", "minimum_wages", "workmen_compensation", "other")
CASE_TYPE_LABELS = {"pow": "Payment of Wages", "public_interest": "Public interest", "industrial_dispute": "Industrial dispute", "minimum_wages": "Minimum wages", "workmen_compensation": "Workmen's compensation", "other": "Other"}
WORKSPACES = ("personal", "sb", "fsa", "media")
PRIORITIES = ("low", "normal", "high", "urgent")
CHANNELS = ("call", "message", "email", "visit")
STATUSES = {"open": "open", "done": "done", "cancelled": "cancelled"}
MAX_STEPS = 20
MAX_TEXT = 2000


class Bad(ValueError):
    """Something a model gave is not usable. The message says what to change."""


def words(v: Any, name: str, *, required: bool = False, most: int = 300) -> str | None:
    s = " ".join(v.split()) if isinstance(v, str) else ""
    if not s:
        if required:
            raise Bad(f"{name} is needed.")
        return None
    if len(s) > most:
        raise Bad(f"{name} is too long (at most {most} characters).")
    return s


def long_text(v: Any, name: str) -> str | None:
    s = v.strip() if isinstance(v, str) else ""
    if len(s) > MAX_TEXT:
        raise Bad(f"{name} is too long (at most {MAX_TEXT} characters).")
    return s or None


def day(v: Any, name: str, *, required: bool = False) -> str | None:
    if v in (None, ""):
        if required:
            raise Bad(f"{name} is needed, as YYYY-MM-DD.")
        return None
    try:
        return date.fromisoformat(str(v).strip()[:10]).isoformat()
    except ValueError as exc:
        raise Bad(f"{name} must be a date like 2026-11-05.") from exc


def minutes(v: Any, name: str) -> int | None:
    """A time of day, as minutes after midnight, or as "HH:MM"."""
    if v in (None, ""):
        return None
    if isinstance(v, str) and ":" in v:
        try:
            h, m = v.strip().split(":")[:2]
            v = int(h) * 60 + int(m)
        except ValueError as exc:
            raise Bad(f"{name} must be a time like 14:30.") from exc
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= int(v) <= 1439:
        raise Bad(f"{name} must be a time of day (minutes after midnight, or HH:MM).")
    return int(v)


def length(v: Any, name: str, default: int) -> int:
    if v in (None, ""):
        return default
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not 5 <= int(v) <= 8 * 60:
        raise Bad(f"{name} must be between 5 and 480 minutes.")
    return int(v)


def pick(v: Any, options: tuple[str, ...], name: str, default: str | None = None) -> str | None:
    if v in (None, ""):
        return default
    s = str(v).strip()
    match = next((o for o in options if o.lower() == s.lower()), None)
    if match is None:
        raise Bad(f"{name} must be one of: {', '.join(options)}.")
    return match


def clock(m: int | None) -> str:
    return "" if m is None else f"{m // 60:02d}:{m % 60:02d}"


def day_label(iso: str | None) -> str:
    return datetime.fromisoformat(iso).strftime("%a %d %b").replace(" 0", " ") if iso else ""


def lines(*parts: str | None) -> list[str]:
    return [p for p in parts if p]


def find_by(records: list[dict[str, Any]], key: str, *fields: str) -> dict[str, Any] | None:
    """A record by its id, or by the exact value of one of `fields` (a case's reference number), ignoring case."""
    k = key.strip().lower()
    if not k:
        return None
    for r in records:
        if str(r.get("id", "")).lower() == k:
            return r
    return next((r for r in records if any(str(r.get(f) or "").lower() == k for f in fields)), None)
