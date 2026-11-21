"""Time zones: where the owner is, and what a time means. Pure: no network, no framework.

Everything stored (tasks, calls, meetings, hearings, and what is synced to the calendar) is kept on the owner's *home* clock, India time. The owner may be elsewhere
for a while ("I'm in Qatar till Monday"): then their day, their "today", their good morning and good night, and the times they say all follow where they are, and every
time is converted here, in code, between the clock they are on and the one it is stored in. The model never works out a time difference or a date change.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo, available_timezones

HOME = "Asia/Kolkata"

#: Places people say, and the zone they mean (the ones a model may not know by their IANA name, and the countries with one main zone).
ALIASES = {
    "india": HOME, "ist": HOME, "hyderabad": HOME, "delhi": HOME, "mumbai": HOME, "chennai": HOME, "kolkata": HOME, "bangalore": HOME, "bengaluru": HOME,
    "qatar": "Asia/Qatar", "doha": "Asia/Qatar", "brazil": "America/Sao_Paulo", "brasil": "America/Sao_Paulo", "sao paulo": "America/Sao_Paulo", "rio": "America/Sao_Paulo", "rio de janeiro": "America/Sao_Paulo",
    "uae": "Asia/Dubai", "dubai": "Asia/Dubai", "abu dhabi": "Asia/Dubai", "saudi": "Asia/Riyadh", "saudi arabia": "Asia/Riyadh", "riyadh": "Asia/Riyadh", "kuwait": "Asia/Kuwait", "bahrain": "Asia/Bahrain", "oman": "Asia/Muscat", "muscat": "Asia/Muscat",
    "singapore": "Asia/Singapore", "malaysia": "Asia/Kuala_Lumpur", "kuala lumpur": "Asia/Kuala_Lumpur", "thailand": "Asia/Bangkok", "bangkok": "Asia/Bangkok", "indonesia": "Asia/Jakarta", "jakarta": "Asia/Jakarta",
    "philippines": "Asia/Manila", "manila": "Asia/Manila", "hong kong": "Asia/Hong_Kong", "japan": "Asia/Tokyo", "tokyo": "Asia/Tokyo", "korea": "Asia/Seoul", "south korea": "Asia/Seoul", "china": "Asia/Shanghai", "beijing": "Asia/Shanghai",
    "nepal": "Asia/Kathmandu", "kathmandu": "Asia/Kathmandu", "sri lanka": "Asia/Colombo", "colombo": "Asia/Colombo", "bangladesh": "Asia/Dhaka", "dhaka": "Asia/Dhaka", "pakistan": "Asia/Karachi", "karachi": "Asia/Karachi",
    "uk": "Europe/London", "britain": "Europe/London", "england": "Europe/London", "london": "Europe/London", "ireland": "Europe/Dublin", "france": "Europe/Paris", "paris": "Europe/Paris", "germany": "Europe/Berlin", "berlin": "Europe/Berlin",
    "geneva": "Europe/Zurich", "switzerland": "Europe/Zurich", "spain": "Europe/Madrid", "italy": "Europe/Rome", "netherlands": "Europe/Amsterdam", "belgium": "Europe/Brussels", "brussels": "Europe/Brussels", "turkey": "Europe/Istanbul",
    "usa": "America/New_York", "us": "America/New_York", "new york": "America/New_York", "washington": "America/New_York", "chicago": "America/Chicago", "los angeles": "America/Los_Angeles", "california": "America/Los_Angeles", "san francisco": "America/Los_Angeles",
    "canada": "America/Toronto", "toronto": "America/Toronto", "mexico": "America/Mexico_City", "argentina": "America/Argentina/Buenos_Aires", "buenos aires": "America/Argentina/Buenos_Aires", "chile": "America/Santiago", "colombia": "America/Bogota",
    "south africa": "Africa/Johannesburg", "johannesburg": "Africa/Johannesburg", "kenya": "Africa/Nairobi", "nairobi": "Africa/Nairobi", "egypt": "Africa/Cairo", "cairo": "Africa/Cairo", "nigeria": "Africa/Lagos", "lagos": "Africa/Lagos",
    "australia": "Australia/Sydney", "sydney": "Australia/Sydney", "melbourne": "Australia/Melbourne", "new zealand": "Pacific/Auckland", "auckland": "Pacific/Auckland",
}
#: How a zone is said back: "7:45 PM Qatar", not "Asia/Qatar".
LABELS = {HOME: "India", "Asia/Qatar": "Qatar", "America/Sao_Paulo": "São Paulo", "Asia/Dubai": "Dubai", "Europe/London": "London", "America/New_York": "New York", "Asia/Singapore": "Singapore"}


def _plain(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower().replace("_", " ").replace(",", " ").split())


@lru_cache(maxsize=1)
def _zones() -> dict[str, str]:
    """Every IANA name, keyed by its city part in plain lower case ("new york" -> America/New_York)."""
    out: dict[str, str] = {}
    for name in sorted(available_timezones()):
        if "/" in name and not name.startswith(("Etc/", "SystemV/", "US/", "Canada/", "Brazil/")):
            out.setdefault(_plain(name.rsplit("/", 1)[-1]), name)
    return out


def resolve_zone(text: str) -> str | None:
    """A place, a city, a zone name or an abbreviation as a real zone ("Brazil" -> America/Sao_Paulo), or None when it can't be told (so the owner is asked)."""
    raw = str(text or "").strip()
    if not raw:
        return None
    if "/" in raw or raw.upper() in ("UTC", "GMT"):
        try:
            ZoneInfo(raw)
            return raw
        except Exception:  # noqa: BLE001
            pass
    key = _plain(re.sub(r"\b(time ?zone|timezone|time|standard|local)\b", " ", raw, flags=re.I))
    if key in ALIASES:
        return ALIASES[key]
    return _zones().get(key)


def label(zone: str) -> str:
    return LABELS.get(zone) or _plain(zone.rsplit("/", 1)[-1]).title()


def offset_label(zone: str, when: datetime | None = None) -> str:
    """+03:00 for a zone now."""
    off = (when or datetime.now(ZoneInfo("UTC"))).astimezone(ZoneInfo(zone)).utcoffset() or timedelta(0)
    minutes = int(off.total_seconds() // 60)
    return f"{'+' if minutes >= 0 else '-'}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d}"


def convert(day: str, minutes: int, src: str, dst: str) -> tuple[str, int]:
    """A wall-clock time (a day and minutes after midnight) on the `src` clock, as the `dst` clock reads at that moment. The day changes when it must."""
    if src == dst:
        return day, minutes
    base = datetime.combine(date.fromisoformat(day), datetime.min.time()) + timedelta(minutes=minutes)
    there = base.replace(tzinfo=ZoneInfo(src)).astimezone(ZoneInfo(dst))
    return there.date().isoformat(), there.hour * 60 + there.minute


def now_in(zone: str, now: datetime) -> datetime:
    return now.astimezone(ZoneInfo(zone))


def today_in(zone: str, now: datetime) -> str:
    return now_in(zone, now).date().isoformat()


def where_is_the_owner(planner: dict | None, now: datetime) -> tuple[str, str]:
    """(the zone they are on, their home zone). The planner settings hold both: `timezone` is where they are, `home` where their records are kept (India), and
    `backOn` the day (on the clock they are on) they return, after which they are home again without being asked."""
    p = planner or {}
    home = str(p.get("home") or HOME)
    here = str(p.get("timezone") or home)
    back = p.get("backOn")
    if here != home and back:
        try:
            if today_in(here, now) >= str(back):
                return home, home
        except Exception:  # noqa: BLE001
            return home, home
    return here, home


def say(day: str, minutes: int, src: str, home: str = HOME) -> str:
    """A time as it is said when it is not on the owner's clock: "7:45 PM Qatar (10:15 PM India)"."""
    h12 = lambda m: f"{(m // 60) % 12 or 12}:{m % 60:02d} {'AM' if (m // 60) % 24 < 12 else 'PM'}"  # noqa: E731
    if src == home:
        return h12(minutes)
    d2, m2 = convert(day, minutes, src, home)
    extra = f", {date.fromisoformat(d2).strftime('%a')} {date.fromisoformat(d2).day} {date.fromisoformat(d2).strftime('%b')}" if d2 != day else ""
    return f"{h12(minutes)} {label(src)} ({h12(m2)} {label(home)}{extra})"


#: Where a phone's country code usually puts someone, for a contact whose zone was not set. Only the main zone of each country: the owner can set another.
PHONE_ZONES = {
    "+91": HOME, "+974": "Asia/Qatar", "+971": "Asia/Dubai", "+966": "Asia/Riyadh", "+965": "Asia/Kuwait", "+973": "Asia/Bahrain", "+968": "Asia/Muscat", "+55": "America/Sao_Paulo",
    "+44": "Europe/London", "+353": "Europe/Dublin", "+1": "America/New_York", "+65": "Asia/Singapore", "+60": "Asia/Kuala_Lumpur", "+61": "Australia/Sydney", "+64": "Pacific/Auckland",
    "+49": "Europe/Berlin", "+33": "Europe/Paris", "+41": "Europe/Zurich", "+34": "Europe/Madrid", "+39": "Europe/Rome", "+31": "Europe/Amsterdam", "+32": "Europe/Brussels", "+90": "Europe/Istanbul",
    "+977": "Asia/Kathmandu", "+94": "Asia/Colombo", "+880": "Asia/Dhaka", "+92": "Asia/Karachi", "+62": "Asia/Jakarta", "+63": "Asia/Manila", "+66": "Asia/Bangkok", "+852": "Asia/Hong_Kong",
    "+81": "Asia/Tokyo", "+82": "Asia/Seoul", "+86": "Asia/Shanghai", "+27": "Africa/Johannesburg", "+254": "Africa/Nairobi", "+20": "Africa/Cairo", "+234": "Africa/Lagos", "+52": "America/Mexico_City",
}


def phone_e164(raw: str, default_code: str = "+91") -> str | None:
    """A phone number as +<country><number>, or None when it cannot be one. A bare ten-digit number is taken to be Indian."""
    s = "".join(ch for ch in str(raw or "") if ch.isdigit() or ch == "+")
    if s.startswith("00"):
        s = "+" + s[2:]
    if not s.startswith("+"):
        s = s.lstrip("0")
        s = (default_code + s) if len(s) == 10 else ("+" + s)
    digits = s[1:]
    return s if digits.isdigit() and 8 <= len(digits) <= 15 else None


def zone_from_location(text: str | None) -> str | None:
    """The zone a place means ("Doha, Qatar" -> Asia/Qatar): the whole text, then its parts from the country back to the city, or None when none is known."""
    raw = str(text or "").strip()
    if not raw:
        return None
    found = resolve_zone(raw)
    if found:
        return found
    for part in reversed([p.strip() for p in re.split(r"[,;/\n]", raw) if p.strip()]):
        found = resolve_zone(part)
        if found:
            return found
    return None


def zone_from_phone(number: str | None) -> str | None:
    """The zone a country code usually means (the longest matching code), or None."""
    n = phone_e164(number or "")
    if not n:
        return None
    for k in sorted(PHONE_ZONES, key=len, reverse=True):
        if n.startswith(k):
            return PHONE_ZONES[k]
    return None


def wa_link(number: str, message: str) -> str:
    """A link that opens WhatsApp to this number with the message already written."""
    from urllib.parse import quote

    return f"https://wa.me/{(phone_e164(number) or '').lstrip('+')}?text={quote(message)}"
