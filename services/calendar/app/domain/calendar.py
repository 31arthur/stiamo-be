"""Google calendar events and Stiamo's meetings and hearings, and how one becomes the other. Pure: no network, no framework."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

WORKSPACES = ("personal", "sb", "fsa", "media")
#: What Stiamo writes on an event it created in Google, so it is not read back in as someone else's event.
MARK = "stiamoRef"
#: How long a hearing with no time of day, or one with no length, is taken to take.
DEFAULT_HEARING_MINUTES = 60
DEFAULT_MEETING_MINUTES = 30
PALETTE = ("#4285F4", "#0F9D58", "#F4B400", "#DB4437", "#8E5CE6", "#00ACC1", "#F06292", "#8D6E63")


def zone(name: str | None, default: str = "UTC") -> ZoneInfo:
    try:
        return ZoneInfo(name or default)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return ZoneInfo(default)


@dataclass
class Account:
    """One Google account a person connected, for one workspace. Its refresh token is what lets the service read it without them."""

    id: str
    user_id: str
    workspace: str
    email: str
    refresh_token: str
    #: ok, or reconnect when Google no longer accepts the token (access was withdrawn).
    status: str
    error: str
    created_at: datetime
    last_sync_at: datetime | None = None


@dataclass
class Source:
    """One calendar inside a Google account (the main one, a shared team one, a holidays one…)."""

    id: str
    account_id: str
    user_id: str
    google_id: str
    name: str
    color: str
    #: owner, writer, reader or freeBusyReader.
    role: str
    timezone: str
    #: Whether its events are shown in Stiamo.
    enabled: bool = True
    #: Whether Stiamo's meetings and hearings for this workspace are sent here. At most one source per workspace.
    export: bool = False
    #: Where Google's changes feed left off, so the next read is only what changed.
    sync_token: str | None = None
    synced_through: datetime | None = None

    @property
    def writable(self) -> bool:
        return self.role in ("owner", "writer")


@dataclass
class Event:
    id: str
    user_id: str
    source_id: str
    google_id: str
    title: str
    description: str
    location: str
    all_day: bool
    #: UTC. For an all-day event: midnight of the first day, and midnight after the last.
    start: datetime
    end: datetime
    link: str
    meet: str
    attendees: int = 0
    updated: str = ""


@dataclass
class Link:
    """A Stiamo meeting or hearing that was sent to Google, and the event it became."""

    user_id: str
    ref: str
    source_id: str
    google_id: str
    digest: str


@dataclass
class Prefs:
    user_id: str
    timezone: str = "Asia/Kolkata"


# --- Google -> Stiamo ----------------------------------------------------------------------------------------------


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def event_from_google(raw: dict[str, Any], source: Source) -> Event | None:
    """An event as Stiamo keeps it, or None for one that isn't to be shown: cancelled, malformed, or one Stiamo itself sent."""
    if raw.get("status") == "cancelled" or not raw.get("id"):
        return None
    if ((raw.get("extendedProperties") or {}).get("private") or {}).get(MARK):
        return None
    start, end = raw.get("start") or {}, raw.get("end") or {}
    try:
        if "date" in start:
            first = datetime.combine(date.fromisoformat(start["date"]), time.min, UTC)
            last = datetime.combine(date.fromisoformat(end.get("date") or start["date"]), time.min, UTC)
            if last <= first:
                last = first + timedelta(days=1)
            all_day, s, e = True, first, last
        else:
            s = _utc(start["dateTime"])
            e = _utc(end["dateTime"]) if "dateTime" in end else s + timedelta(minutes=DEFAULT_MEETING_MINUTES)
            all_day = False
    except (KeyError, ValueError):
        return None
    meet = raw.get("hangoutLink") or next((p.get("uri", "") for p in (raw.get("conferenceData") or {}).get("entryPoints", []) if p.get("entryPointType") == "video"), "")
    return Event(
        id=f"{source.id}:{raw['id']}"[:200], user_id=source.user_id, source_id=source.id, google_id=raw["id"], title=(raw.get("summary") or "(no title)")[:300],
        description=(raw.get("description") or "")[:2000], location=(raw.get("location") or "")[:300], all_day=all_day, start=s, end=e,
        link=raw.get("htmlLink") or "", meet=meet or "", attendees=len(raw.get("attendees") or []), updated=raw.get("updated") or "",
    )


def view(e: Event, tz: str) -> dict[str, Any]:
    """An event as the web app draws it, on the person's own clock: the day, and minutes after midnight."""
    z = zone(tz)
    out: dict[str, Any] = {"id": e.id, "sourceId": e.source_id, "title": e.title, "location": e.location, "allDay": e.all_day, "link": e.link, "meet": e.meet, "attendees": e.attendees}
    if e.all_day:
        out["date"] = e.start.date().isoformat()
        out["endDate"] = (e.end.date() - timedelta(days=1)).isoformat()
        return out
    s, f = e.start.astimezone(z), e.end.astimezone(z)
    out["date"] = s.date().isoformat()
    last = max(s, f - timedelta(minutes=1))
    out["endDate"] = last.date().isoformat()
    out["startMinute"] = s.hour * 60 + s.minute
    out["endMinute"] = (f.hour * 60 + f.minute) if f.date() == s.date() else 24 * 60
    return out


# --- Stiamo -> Google ----------------------------------------------------------------------------------------------


def _timed(day: str, start: int, length: int, tz: str) -> tuple[dict[str, str], dict[str, str]]:
    z = zone(tz)
    s = datetime.combine(date.fromisoformat(day), time.min, z) + timedelta(minutes=start)
    f = s + timedelta(minutes=length)
    return {"dateTime": s.isoformat(), "timeZone": tz}, {"dateTime": f.isoformat(), "timeZone": tz}


def _all_day(day: str) -> tuple[dict[str, str], dict[str, str]]:
    d = date.fromisoformat(day)
    return {"date": d.isoformat()}, {"date": (d + timedelta(days=1)).isoformat()}


def body_for_meeting(m: dict[str, Any], tz: str) -> dict[str, Any]:
    slot = m.get("slot") or {}
    start, end = _timed(slot["date"], int(slot.get("start") or 0), int(slot.get("duration") or DEFAULT_MEETING_MINUTES), tz)
    notes = [x for x in (m.get("purpose"), f"With {m['counterpart']}" if m.get("counterpart") else None, m.get("link")) if x]
    body = {"summary": m.get("title") or "Meeting", "description": "\n".join(notes), "start": start, "end": end, "extendedProperties": {"private": {MARK: f"meeting:{m['id']}"}}}
    if m.get("location"):
        body["location"] = m["location"]
    return body


def body_for_hearing(h: dict[str, Any], case: dict[str, Any] | None, tz: str) -> dict[str, Any]:
    ref = (case or {}).get("reference") or ""
    title = f"Hearing: {h.get('purpose') or 'listed'}" + (f" ({ref})" if ref else "")
    if h.get("time") is None:
        start, end = _all_day(h["date"])
    else:
        start, end = _timed(h["date"], int(h["time"]), int(h.get("duration") or DEFAULT_HEARING_MINUTES), tz)
    notes = [x for x in ((case or {}).get("title"), f"{(case or {}).get('court')}" if (case or {}).get("court") else None, (case or {}).get("courtNumber")) if x]
    body = {"summary": title, "description": "\n".join(notes), "start": start, "end": end, "extendedProperties": {"private": {MARK: f"hearing:{h['id']}"}}}
    if h.get("forum"):
        body["location"] = h["forum"]
    return body


def digest(body: dict[str, Any]) -> str:
    """Changes when anything Stiamo sends changes, so an event is only updated in Google when it has to be."""
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:20]


def desired_events(meetings: list[dict[str, Any]], hearings: list[dict[str, Any]], cases: list[dict[str, Any]], tz: str, today: date, back_days: int = 7) -> dict[str, tuple[str, dict[str, Any]]]:
    """What should be in Google right now: ref -> (workspace, event body). Open meetings and hearings from a week ago onwards."""
    floor = (today - timedelta(days=back_days)).isoformat()
    by_id = {c["id"]: c for c in cases}
    out: dict[str, tuple[str, dict[str, Any]]] = {}
    for m in meetings:
        if (m.get("outcome") or {}).get("status", "open") == "cancelled" or (m.get("slot") or {}).get("date", "") < floor:
            continue
        out[f"meeting:{m['id']}"] = (m.get("workspace") or "personal", body_for_meeting(m, tz))
    for h in hearings:
        if (h.get("outcome") or {}).get("status", "open") == "cancelled" or h.get("date", "") < floor:
            continue
        out[f"hearing:{h['id']}"] = ("sb", body_for_hearing(h, by_id.get(h.get("caseId") or ""), tz))
    return out


@dataclass
class Plan:
    """What a sync has to do to Google to match Stiamo."""

    insert: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)  # (ref, source_id, body)
    update: list[tuple[Link, dict[str, Any]]] = field(default_factory=list)
    delete: list[Link] = field(default_factory=list)


def plan_export(desired: dict[str, tuple[str, dict[str, Any]]], links: dict[str, Link], target: dict[str, str]) -> Plan:
    """Compares what should be in Google with what Stiamo already sent. `target` is workspace -> the source to send to; a workspace
    with none sends nothing (and anything sent earlier for it is taken down again)."""
    plan = Plan()
    for ref, (workspace, body) in desired.items():
        source_id = target.get(workspace)
        link = links.get(ref)
        if source_id is None:
            if link:
                plan.delete.append(link)
            continue
        if link is None:
            plan.insert.append((ref, source_id, body))
        elif link.source_id != source_id:
            plan.delete.append(link)
            plan.insert.append((ref, source_id, body))
        elif link.digest != digest(body):
            plan.update.append((link, body))
    plan.delete += [link for ref, link in links.items() if ref not in desired]
    return plan
