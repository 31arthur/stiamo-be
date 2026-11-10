"""Reaching someone by phone from a message: the number in a contact line, and a link that opens the phone's dialer.

Telegram doesn't allow a `tel:` link on a button, so the button opens a tiny page on the app's own address (`call.html`) that hands the number to the
dialer. That works the same on an iPhone and on Android, and the number travels in the address' fragment, which is never sent to a server.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

PHONE = re.compile(r"\+?\d[\d\s().-]{6,}\d")
EMAIL = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")


def phone_text(contact: str | None) -> str | None:
    """The first thing in a contact line that looks like a phone number, as written ("+91 98100 22114")."""
    m = PHONE.search(contact or "")
    return m.group(0).strip() if m else None


def phone_of(contact: str | None) -> str | None:
    """The same, as the digits (and a leading +) a dialer takes."""
    t = phone_text(contact)
    return re.sub(r"[\s().-]", "", t) if t else None


def name_of(contact: str | None) -> str:
    """What is left of a contact line without its number or address: the name."""
    rest = EMAIL.sub("", PHONE.sub("", contact or ""))
    return re.sub(r"^[\s·,;:|–—-]+|[\s·,;:|–—-]+$", "", rest).strip()


def call_url(app_url: str, contact: str | None, title: str = "") -> str | None:
    """The address that opens the dialer for this contact, or None without a number or a secure app address (Telegram only takes https)."""
    number = phone_of(contact)
    if not number or not app_url.startswith("https://"):
        return None
    name = name_of(contact) or title
    return f"{app_url.rstrip('/')}/call.html#n={quote(number, safe='+')}&name={quote(name[:60])}"


def call_button(app_url: str, contact: str | None, title: str = "") -> dict[str, str] | None:
    url = call_url(app_url, contact, title)
    if url is None:
        return None
    who = name_of(contact) or title or "them"
    return {"text": f"📞 Call {who[:24]}", "url": url}


def call_rows(app_url: str, calls: list[dict[str, Any]], limit: int = 4) -> list[list[dict[str, str]]]:
    """A "Call …" button for each call that has a number (one per number), to put under a message about calls."""
    rows: list[list[dict[str, str]]] = []
    seen: set[str] = set()
    for c in calls:
        number = phone_of(c.get("contact"))
        button = call_button(app_url, c.get("contact"), str(c.get("title") or ""))
        if number and number not in seen and button:
            seen.add(number)
            rows.append([button])
        if len(rows) >= limit:
            break
    return rows
