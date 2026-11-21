"""Contacts read from Google, and which workspace each belongs to. Pure: no network, no framework.

A Google account is linked to a workspace; one account can be linked to several workspaces (each link has its own copy of the
contacts, so a workspace is complete on its own) and a workspace can have several accounts.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.domain.calendar import WORKSPACES

__all__ = ["WORKSPACES", "ContactAccount", "Contact", "contact_from_person", "merge_across", "view"]

MAX_NOTES = 1000


@dataclass
class ContactAccount:
    """A Google account linked to a workspace for its contacts. Only its refresh token is kept, sealed."""

    id: str
    user_id: str
    workspace: str
    email: str
    refresh_token: str
    status: str = "ok"  # ok | reconnect
    error: str = ""
    created_at: datetime | None = None
    last_sync_at: datetime | None = None
    #: How many contacts the last read found.
    count: int = 0


@dataclass
class Contact:
    id: str
    user_id: str
    account_id: str
    workspace: str
    google_id: str
    name: str
    organisation: str = ""
    title: str = ""
    emails: list[dict[str, str]] = field(default_factory=list)
    phones: list[dict[str, str]] = field(default_factory=list)
    notes: str = ""
    photo: str = ""
    #: Lower case text of everything a search looks in.
    search: str = ""
    #: When this contact first appeared here, and whether that was the first ever read of the link (so everyone already in Google at the start is not "new").
    first_seen_at: datetime | None = None
    baseline: bool = False
    #: When Google last changed it (its own "update time"): a new contact has a recent one, and so has an edited one.
    google_updated: str = ""
    #: What the owner knows about them, kept here and never written back to Google: free tags, and a profile (sector, relationship, tier, language, how we met, interests).
    tags: list[str] = field(default_factory=list)
    profile: dict[str, Any] = field(default_factory=dict)
    #: Where they are (a zone name), so a message reaches them in their working hours.
    timezone: str = ""
    #: The owner said not to build a rapport plan for them.
    rapport_excluded: bool = False
    #: Where they are, as written in their address in Google ("Doha, Qatar"). The zone is worked out from it when none was set.
    location: str = ""


def _labelled(items: Any, kind: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for it in items if isinstance(items, list) else []:
        value = str((it or {}).get("value") or "").strip()
        if value and all(value != o["value"] for o in out):
            out.append({"value": value[:200], "label": str(it.get("formattedType") or it.get("type") or "").strip()[:40]})
    return out[:10]


def contact_from_person(account: ContactAccount, person: dict[str, Any]) -> Contact | None:
    """A Google person as a contact, or None for one that was deleted or has nothing to show (no name, address, number or organisation)."""
    if (person.get("metadata") or {}).get("deleted") or not person.get("resourceName"):
        return None
    names = person.get("names") or []
    primary = next((n for n in names if (n.get("metadata") or {}).get("primary")), names[0] if names else {})
    orgs = person.get("organizations") or []
    org = orgs[0] if orgs else {}
    emails, phones = _labelled(person.get("emailAddresses"), "email"), _labelled(person.get("phoneNumbers"), "phone")
    organisation, title = str(org.get("name") or "").strip()[:200], str(org.get("title") or "").strip()[:200]
    name = str(primary.get("displayName") or "").strip()[:200] or (emails[0]["value"] if emails else "") or organisation or (phones[0]["value"] if phones else "")
    if not name:
        return None
    photos = [p for p in person.get("photos") or [] if p.get("url") and not p.get("default")]
    bios = person.get("biographies") or []
    notes = str((bios[0] if bios else {}).get("value") or "").strip()[:MAX_NOTES]
    places = person.get("addresses") or []
    place = places[0] if places else {}
    location = str(place.get("formattedValue") or ", ".join(str(place.get(k) or "").strip() for k in ("city", "region", "country") if str(place.get(k) or "").strip())).replace("\n", ", ").strip()[:200]
    search = " ".join([name, organisation, title, *[e["value"] for e in emails], *[p["value"] for p in phones], notes, location]).lower()
    updated = next((str(s.get("updateTime")) for s in (person.get("metadata") or {}).get("sources") or [] if s.get("updateTime")), "")
    return Contact(
        f"{account.id}:{hashlib.sha1(str(person['resourceName']).encode()).hexdigest()[:20]}", account.user_id, account.id, account.workspace, str(person["resourceName"]),
        name, organisation, title, emails, phones, notes, str(photos[0]["url"]) if photos else "", search, google_updated=updated, location=location,
    )


def view(c: Contact, workspaces: list[str] | None = None, accounts: list[str] | None = None) -> dict[str, Any]:
    iso = lambda d: d.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z") if d else None  # noqa: E731
    return {
        "id": c.id, "name": c.name, "organisation": c.organisation, "title": c.title, "emails": c.emails, "phones": c.phones, "notes": c.notes, "photo": c.photo,
        "workspaces": workspaces or [c.workspace], "accounts": accounts or [],
        "firstSeenAt": iso(c.first_seen_at), "isNew": bool(c.first_seen_at) and not c.baseline, "googleUpdated": c.google_updated or None,
        "tags": c.tags, "profile": c.profile, "timezone": c.timezone, "rapportExcluded": c.rapport_excluded, "location": c.location,
    }


def merge_across(contacts: list[Contact], emails_by_account: dict[str, str]) -> list[dict[str, Any]]:
    """For all workspaces at once: the same Google contact linked into several workspaces is one person, shown once with every workspace it is in."""
    seen: dict[tuple[str, str], tuple[Contact, list[str]]] = {}
    for c in contacts:
        key = (emails_by_account.get(c.account_id, c.account_id), c.google_id)
        if key in seen:
            if c.workspace not in seen[key][1]:
                seen[key][1].append(c.workspace)
        else:
            seen[key] = (c, [c.workspace])
    out = [view(c, sorted(ws), [key[0]]) for key, (c, ws) in seen.items()]
    return sorted(out, key=lambda v: v["name"].lower())
